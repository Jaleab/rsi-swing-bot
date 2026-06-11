"""
Position sizing based on Kelly criterion, risk-of-ruin limits, and volatility scaling.

Kelly fraction: f* = (p * b - q) / b
  p = win probability estimate
  b = avg_win / avg_loss (reward-to-risk ratio)
  q = 1 - p

Quarter-Kelly (f*/4) is used for conservative sizing with estimation error buffer.
"""
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float,
                   fraction: float = 0.25) -> float:
    """
    Calculate fractional Kelly bet size.

    Args:
        win_rate: Probability of a winning trade [0, 1]
        avg_win: Average winning trade amount (positive dollars)
        avg_loss: Average losing trade amount (positive dollars)
        fraction: Fraction of full Kelly to use (0.25 = quarter Kelly)

    Returns:
        Fraction of equity to risk per trade [0, 1]
    """
    if avg_loss <= 0:
        return 0.0
    b = abs(avg_win / avg_loss) if avg_loss > 0 else 0
    if b <= 0:
        return 0.0
    q = 1.0 - win_rate
    f_star = (win_rate * b - q) / b
    f_star = max(0.0, min(f_star, 0.5))  # Cap full Kelly at 50%
    return f_star * fraction


def calculate_position_size(
    equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    leverage: int = 1,
    max_position_pct: float = 0.25,
    min_notional: float = 1.0,
) -> float:
    """
    Calculate position size in base currency units from risk parameters.

    The stop distance determines how much we can lose per unit.
    Position size = (equity * risk_pct) / (|entry - stop| * leverage)

    Args:
        equity: Current account equity in quote currency (USDT)
        risk_pct: Fraction of equity to risk on this trade (e.g. 0.016)
        entry_price: Entry price in quote currency
        stop_price: Stop loss price in quote currency
        leverage: Leverage multiplier (1 = spot, 2-3 = mild leverage)
        max_position_pct: Maximum notional exposure as fraction of equity
        min_notional: Minimum notional value to bother trading

    Returns:
        Quantity in base currency (e.g., BTC amount, SOL amount)
    """
    if equity <= 0 or entry_price <= 0 or stop_price <= 0:
        return 0.0

    risk_amount = equity * risk_pct
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        logger.warning("Stop distance is zero — cannot size position.")
        return 0.0

    # Raw units: price moves stop_distance against us, we lose risk_amount
    # With leverage, the same price move is amplified, so reduce size proportionally
    raw_units = risk_amount / (stop_distance * leverage)

    # Notional value
    notional = raw_units * entry_price

    # Apply exposure limit
    max_notional = equity * max_position_pct * leverage
    if notional > max_notional:
        raw_units = max_notional / entry_price
        notional = max_notional
        logger.debug(f"Position capped at {max_position_pct*100:.0f}% exposure: {notional:.2f} USDT")

    if notional < min_notional:
        logger.debug(f"Position notional {notional:.2f} below minimum {min_notional:.2f}")
        return 0.0

    return raw_units


def atr_scaled_size(
    base_units: float,
    atr: float,
    entry_price: float,
    target_vol_pct: float = 0.02,
) -> float:
    """
    Scale position size inversely by volatility.
    In high-volatility periods (ATR/price > target), reduce size.
    In low-volatility periods, increase size (capped at 1.5x base).

    Args:
        base_units: Raw position size from risk calculation
        atr: Average True Range in price units
        entry_price: Current price
        target_vol_pct: Target daily volatility as fraction (0.02 = 2%)

    Returns:
        Adjusted position size in base currency units
    """
    if entry_price <= 0 or atr <= 0:
        return base_units

    current_vol = atr / entry_price
    if current_vol <= 0:
        return base_units

    # Scale inversely: low vol → larger size, high vol → smaller size
    scale = target_vol_pct / current_vol
    scale = max(0.25, min(scale, 2.0))  # Never scale below 25% or above 200%

    adjusted = base_units * scale
    logger.debug(f"ATR scaling: vol={current_vol:.4f}, target={target_vol_pct:.4f}, "
                 f"scale={scale:.2f}, {base_units:.6f} → {adjusted:.6f}")
    return adjusted


class PositionSizer:
    """Encapsulates all position sizing logic with state tracking."""

    def __init__(self, config):
        self.config = config
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0.0
        self.equity_peak = 0.0
        self.current_drawdown = 0.0

    def update_trade_result(self, pnl: float):
        """Track trade outcomes for adaptive Kelly."""
        self.total_pnl += pnl
        if pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

    def get_win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.45  # prior

    def get_dynamic_risk_pct(self) -> float:
        """
        Return risk percentage based on Kelly formula and recent performance.
        Uses a prior of 0.45 win rate until 20+ trades observed.
        """
        total = self.win_count + self.loss_count
        if total < 5:
            return self.config.RISK_PER_TRADE_PCT

        wr = self.get_win_rate()
        # Estimate avg_win/avg_loss from config R:R
        rr = self.config.TAKE_PROFIT_PERCENT / self.config.STOP_LOSS_PERCENT
        k = kelly_fraction(wr, rr, 1.0, fraction=0.25)
        # Blend with prior
        alpha = min(1.0, total / 20.0)
        blended = self.config.RISK_PER_TRADE_PCT * (1 - alpha) + k * alpha
        return max(0.0025, min(blended, self.config.MAX_RISK_PER_TRADE_PCT))

    def check_drawdown_breach(self, current_equity: float) -> bool:
        """Return True if current drawdown exceeds circuit breaker threshold."""
        if current_equity > self.equity_peak:
            self.equity_peak = current_equity
            self.current_drawdown = 0.0
        elif self.equity_peak > 0:
            self.current_drawdown = (self.equity_peak - current_equity) / self.equity_peak
        return self.current_drawdown >= self.config.MAX_DRAWDOWN_PCT

    def size_position(self, equity: float, entry_price: float, stop_price: float,
                      atr: Optional[float] = None) -> float:
        """
        Full position sizing pipeline: Kelly risk → stop-distance sizing → volatility scaling.

        Returns quantity in base currency units.
        """
        risk_pct = self.get_dynamic_risk_pct()
        units = calculate_position_size(
            equity=equity,
            risk_pct=risk_pct,
            entry_price=entry_price,
            stop_price=stop_price,
            leverage=self.config.LEVERAGE,
            max_position_pct=self.config.MAX_POSITION_PCT,
            min_notional=self.config.MIN_POSITION_SIZE_USDT,
        )

        if units > 0 and self.config.ENABLE_VOLATILITY_SCALING and atr and atr > 0:
            units = atr_scaled_size(units, atr, entry_price, self.config.VOLATILITY_TARGET_PCT)

        return units
