"""
Equity curve tracking with circuit breaker logic.

Monitors cumulative PnL, drawdown, and equity curve trend.
Returns GuardResult when trading should be halted.
"""
import logging
from typing import List, Optional
from datetime import datetime
from collections import deque

from src.guards import GuardResult

logger = logging.getLogger(__name__)


class EquityTracker:
    """Tracks account equity over time and enforces drawdown / trend limits."""

    def __init__(self, config):
        self.config = config
        self.starting_equity: float = 0.0
        self.peak_equity: float = 0.0
        self.current_equity: float = 0.0
        self.equity_history: deque = deque(maxlen=100)  # (timestamp, equity)
        self.trading_halted: bool = False
        self.halt_reason: str = ""

        # Per-trade PnL tracking
        self.trade_pnls: List[float] = []

    def initialize(self, starting_equity: float):
        self.starting_equity = starting_equity
        self.peak_equity = starting_equity
        self.current_equity = starting_equity
        self.equity_history.append((datetime.now().timestamp(), starting_equity))

    def update(self, realized_pnl: float, unrealized_pnl: float = 0.0):
        """Update equity after each event/candle."""
        self.current_equity = self.starting_equity + realized_pnl + unrealized_pnl
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        self.equity_history.append((datetime.now().timestamp(), self.current_equity))

    def record_trade(self, pnl: float):
        """Record a closed trade for drawdown tracking."""
        self.trade_pnls.append(pnl)
        self.update(self._total_realized_pnl())

    def _total_realized_pnl(self) -> float:
        return sum(self.trade_pnls)

    def _current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def _equity_sma(self, period: int = 20) -> float:
        """Calculate SMA of equity over last N data points."""
        if len(self.equity_history) < period:
            return self.current_equity
        recent = [e[1] for e in list(self.equity_history)[-period:]]
        return sum(recent) / len(recent)

    def check_circuit_breaker(self) -> Optional[GuardResult]:
        """
        Check all circuit breaker conditions.
        Returns GuardResult if trading should be halted, None if OK.
        """
        if not self.config.ENABLE_DRAWDOWN_BREAKER:
            return None

        if self.trading_halted:
            return GuardResult(
                allowed=False,
                reason=f"Trading halted: {self.halt_reason}",
                guard_name="DRAWDOWN_HALT",
                details=f"Halt reason: {self.halt_reason}"
            )

        # 1. Max Drawdown Check
        dd = self._current_drawdown_pct()
        if dd >= self.config.MAX_DRAWDOWN_PCT and self.peak_equity > 0:
            self.trading_halted = True
            self.halt_reason = f"Max drawdown {dd:.1%} exceeded {self.config.MAX_DRAWDOWN_PCT:.1%}"
            logger.critical(f"CIRCUIT BREAKER: {self.halt_reason}")
            return GuardResult(
                allowed=False,
                reason=self.halt_reason,
                guard_name="MAX_DRAWDOWN",
                details=f"Peak: {self.peak_equity:.2f}, Current: {self.current_equity:.2f}, DD: {dd:.2%}"
            )

        # 2. Equity Curve SMA Filter
        if self.config.ENABLE_EQUITY_CURVE_FILTER and len(self.equity_history) >= self.config.EQUITY_SMA_PERIOD:
            sma = self._equity_sma(self.config.EQUITY_SMA_PERIOD)
            if self.current_equity < sma:
                return GuardResult(
                    allowed=False,
                    reason=f"Equity {self.current_equity:.2f} below SMA({self.config.EQUITY_SMA_PERIOD}) {sma:.2f}",
                    guard_name="EQUITY_CURVE_FILTER",
                    details=f"Current: {self.current_equity:.2f}, SMA: {sma:.2f}"
                )

        # 3. Consecutive Loss Streak
        if len(self.trade_pnls) >= 5:
            last_5 = self.trade_pnls[-5:]
            if all(p < 0 for p in last_5):
                self.trading_halted = True
                self.halt_reason = "5 consecutive losing trades"
                logger.critical(f"CIRCUIT BREAKER: {self.halt_reason}")
                return GuardResult(
                    allowed=False,
                    reason=self.halt_reason,
                    guard_name="LOSS_STREAK",
                    details=f"Last 5 trades: {[round(p,2) for p in last_5]}"
                )

        return None

    def get_summary(self) -> dict:
        return {
            "starting_equity": self.starting_equity,
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "drawdown_pct": round(self._current_drawdown_pct() * 100, 2),
            "total_return_pct": round((self.current_equity - self.starting_equity) / self.starting_equity * 100, 2) if self.starting_equity > 0 else 0,
            "total_trades": len(self.trade_pnls),
            "trading_halted": self.trading_halted,
            "halt_reason": self.halt_reason,
        }
