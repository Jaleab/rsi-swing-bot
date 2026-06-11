import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import time
import logging
import pandas as pd
from typing import Optional

from src.config import Config
from src.position_manager import PositionManager
from src.status_tracker import StatusTracker, PairStatus, GuardMetrics
from src.guards import GuardResult
from src.position import Position
from src.abstract_exchange import AbstractExchangeClient

# Mock AbstractExchangeClient for testing PositionManager
class MockExchangeClient(AbstractExchangeClient):
    def __init__(self, api_key: str, api_secret: str, testnet: bool):
        super().__init__(api_key, api_secret, testnet)
        self._balance = {'USDT': {'total': 10000.0, 'free': 9500.0, 'used': 500.0}}
        self._positions = {}

    async def get_balance(self, currency: str = 'USDT') -> dict:
        return self._balance.get(currency, {'total': 0.0, 'free': 0.0, 'used': 0.0})

    async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None, params: Optional[dict] = None) -> dict:
        # Simulate an order placement
        order_id = f"mock_order_{int(time.time() * 1000)}"
        # For simplicity in testing, we'll assume a successful order immediately creates an open position
        # In a real scenario, this would involve fetching actual open positions after order execution
        self._positions[symbol] = {
            'symbol': symbol,
            'size': quantity,
            'entry_price': price if price else 100, # Using a default price if market order
            'side': 'Long' if side == 'buy' else 'Short',
            'timestamp': datetime.now().timestamp() * 1000
        }
        return {"orderId": order_id, "symbol": symbol, "status": "closed", "side": side, "amount": quantity, "price": price}

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        return {"orderId": order_id, "status": "canceled"}

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        return []

    async def get_order_status(self, order_id: str, symbol: str) -> dict:
        return {"orderId": order_id, "status": "closed", "filled": 1.0}

    async def fetch_positions(self, symbols: Optional[list[str]] = None) -> list[dict]:
        if symbols:
            return [p for s, p in self._positions.items() if s in symbols]
        return list(self._positions.values())
    
    async def get_positions(self) -> list[dict]: # Added implementation for get_positions
        return list(self._positions.values())

    async def get_order_book(self, symbol: str, limit: Optional[int] = None) -> dict:
        return {'bids': [[100.0, 10.0]], 'asks': [[101.0, 10.0]]}

    async def get_recent_trades(self, symbol: str, limit: Optional[int] = None) -> list[dict]:
        return []

    async def fetch_ohlcv(self, symbol: str, timeframe: str, status_tracker: StatusTracker, limit: int = 200): # Added implementation for fetch_ohlcv
        # Return a dummy DataFrame for OHLCV
        return pd.DataFrame({
            'timestamp': [time.time() * 1000 - i * 60000 for i in range(limit)],
            'open': [100.0 + i for i in range(limit)],
            'high': [101.0 + i for i in range(limit)],
            'low': [99.0 + i for i in range(limit)],
            'close': [100.5 + i for i in range(limit)],
            'volume': [1000.0 + i for i in range(limit)],
        })


# Mock MetricsExporter
class MockMetricsExporter:
    def update_open_positions_count(self, count):
        pass # No-op for testing

    def update_unrealized_pnl(self, symbol, pnl):
        pass

    def update_realized_pnl(self, symbol, pnl):
        pass

    def update_position_entry_price(self, symbol, price):
        pass

    def update_position_target_price(self, symbol, price):
        pass

    def update_position_stop_price(self, symbol, price):
        pass

    def update_position_current_value_usdt(self, symbol, value):
        pass

    def update_mark_price(self, symbol, price):
        pass

    def update_target_price(self, symbol, price): # Added missing method
        pass

    def update_stop_price(self, symbol, price): # Added missing method
        pass


# Mock StatusTracker for testing, with actual PairStatus instances
class MockStatusTracker(StatusTracker):
    def __init__(self, symbols: list[str]):
        # Mock position_managers as it's not needed for these tests
        super().__init__(symbols, {}) 
        for symbol in symbols:
            self.status[symbol] = PairStatus(symbol=symbol)
        
    def update_guard_metrics(self, symbol: str, guard_result: GuardResult):
        # Override to capture for testing
        super().update_guard_metrics(symbol, guard_result)
        # For testing, we might want to store these for assertion
        if not hasattr(self, '_captured_guard_results'):
            self._captured_guard_results = {}
        if symbol not in self._captured_guard_results:
            self._captured_guard_results[symbol] = []
        self._captured_guard_results[symbol].append(guard_result)

    def get_captured_guard_results(self, symbol: str):
        return getattr(self, '_captured_guard_results', {}).get(symbol, [])


@pytest.fixture
def mock_config():
    Config.SYMBOLS = ["BTC/USDT", "ETH/USDT"]
    Config.SIM_MODE = True
    Config.MAX_TOTAL_OPEN_POSITIONS = 1
    Config.MAX_POSITIONS_PER_SYMBOL = 1
    Config.TRADE_COOLDOWN_SECONDS = 5
    Config.SIGNAL_COOLDOWN_SECONDS = 3
    Config.ENABLE_SAFE_MODE = True
    Config.POSITION_USDT = 100.0 # Default trade size
    return Config

@pytest.fixture
def mock_status_tracker(mock_config):
    return MockStatusTracker(mock_config.SYMBOLS)

@pytest.fixture
def mock_position_manager(mock_config, mock_status_tracker):
    mock_exchange = MockExchangeClient("test_key", "test_secret", True)
    mock_metrics = MockMetricsExporter()
    return PositionManager(mock_config, mock_exchange, mock_metrics, mock_status_tracker)


@pytest.mark.asyncio
async def test_max_total_open_positions_guard(mock_position_manager, mock_status_tracker, mock_config):
    symbol1 = "BTC/USDT"
    current_price = 30000.0

    # 1. Open first position (should be allowed)
    await mock_position_manager.open_position(
        symbol=symbol1,
        signal_direction='buy',
        current_price=current_price,
        exchange_client=mock_position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    assert mock_position_manager.get_total_open_positions() == 1
    assert mock_position_manager.has_open_position(symbol1)
    
    # 2. Attempt to open a second position for a different symbol (should be blocked by MAX_TOTAL_OPEN_POSITIONS = 1)
    symbol2 = "ETH/USDT"
    await mock_position_manager.open_position(
        symbol=symbol2,
        signal_direction='buy',
        current_price=2000.0,
        exchange_client=mock_position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    # Ensure no new position was opened for symbol2
    assert not mock_position_manager.has_open_position(symbol2)
    assert mock_position_manager.get_total_open_positions() == 1 # Still only 1 position

    # Verify guard metrics
    captured_guards_symbol2 = mock_status_tracker.get_captured_guard_results(symbol2)
    assert len(captured_guards_symbol2) > 0
    assert not captured_guards_symbol2[-1].allowed
    assert captured_guards_symbol2[-1].guard_name == "MAX_TOTAL_OPEN_POSITIONS"
    assert "Max total open positions reached" in captured_guards_symbol2[-1].reason
    assert mock_status_tracker.status[symbol2].guard_metrics.trades_blocked_count >= 1
    assert "SKIPPED_TRADE (MAX_TOTAL_OPEN_POSITIONS)" in mock_status_tracker.status[symbol2].notes


@pytest.mark.asyncio
async def test_max_positions_per_symbol_guard(mock_position_manager, mock_status_tracker, mock_config):
    symbol = "BTC/USDT"
    current_price = 30000.0

    # Ensure MAX_POSITIONS_PER_SYMBOL is 1 for this test
    mock_config.MAX_POSITIONS_PER_SYMBOL = 1
    mock_config.MAX_TOTAL_OPEN_POSITIONS = 2 # Allow total positions to be 2 for this test

    # 1. Open first position for the symbol (should be allowed)
    await mock_position_manager.open_position(
        symbol=symbol,
        signal_direction='buy',
        current_price=current_price,
        exchange_client=mock_position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    assert mock_position_manager.get_total_open_positions() == 1
    assert mock_position_manager.has_open_position(symbol)

    # 2. Attempt to open a second position for the SAME symbol (should be blocked by MAX_POSITIONS_PER_SYMBOL = 1)
    await mock_position_manager.open_position(
        symbol=symbol,
        signal_direction='sell', # Even if opposite direction, still blocked
        current_price=current_price + 100,
        exchange_client=mock_position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    # Ensure no new position was opened (or flipped)
    assert mock_position_manager.get_total_open_positions() == 1 # Still only 1 position
    # The existing position should still be the original 'buy' position
    assert mock_position_manager.get_open_position(symbol).position_type == 'long'

    # Verify guard metrics
    captured_guards_symbol = mock_status_tracker.get_captured_guard_results(symbol)
    assert len(captured_guards_symbol) > 0
    assert not captured_guards_symbol[-1].allowed
    assert captured_guards_symbol[-1].guard_name == "MAX_POSITIONS_PER_SYMBOL"
    assert "Already have an open position" in captured_guards_symbol[-1].reason
    assert mock_status_tracker.status[symbol].guard_metrics.trades_blocked_count >= 1
    assert "SKIPPED_TRADE (MAX_POSITIONS_PER_SYMBOL)" in mock_status_tracker.status[symbol].notes


@pytest.mark.asyncio
async def test_trade_cooldown_guard(mock_status_tracker):
    symbol = "BTC/USDT"
    current_price = 30000.0

    class LocalConfig(Config):
        pass
    local_config = LocalConfig()
    local_config.SYMBOLS = ["BTC/USDT", "ETH/USDT"]
    local_config.SIM_MODE = True
    local_config.MAX_TOTAL_OPEN_POSITIONS = 2
    local_config.MAX_POSITIONS_PER_SYMBOL = 1
    local_config.TRADE_COOLDOWN_SECONDS = 5
    local_config.SIGNAL_COOLDOWN_SECONDS = 3
    local_config.ENABLE_SAFE_MODE = True
    local_config.POSITION_USDT = 100.0
    local_config.MIN_POSITION_SIZE_USDT = 1.0

    mock_exchange = MockExchangeClient("test_key", "test_secret", True)
    mock_metrics = MockMetricsExporter()
    position_manager = PositionManager(local_config, mock_exchange, mock_metrics, mock_status_tracker)

    # 1. Open first position for the symbol
    await position_manager.open_position(
        symbol=symbol,
        signal_direction='buy',
        current_price=current_price,
        exchange_client=position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    assert position_manager.get_total_open_positions() == 1
    assert mock_status_tracker.status[symbol].last_trade_timestamp > 0
    assert position_manager.has_open_position(symbol)

    # 2. Close the first position immediately
    open_position = position_manager.get_open_position(symbol)
    if open_position:
        open_position.close_position(close_reason="TEST_CLOSE")
        position_manager.remove_position(symbol)
    
    assert not position_manager.has_open_position(symbol)
    assert position_manager.get_total_open_positions() == 0

    # 3. Attempt to open a new position for the SAME symbol within the cooldown period
    await position_manager.open_position(
        symbol=symbol,
        signal_direction='buy',
        current_price=current_price + 50,
        exchange_client=position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    # Ensure no new position was opened
    assert not position_manager.has_open_position(symbol)
    assert position_manager.get_total_open_positions() == 0

    # Verify guard metrics for the blocked trade (TRADE_COOLDOWN)
    captured_guards_symbol = mock_status_tracker.get_captured_guard_results(symbol)
    assert len(captured_guards_symbol) > 0
    assert not captured_guards_symbol[-1].allowed
    assert captured_guards_symbol[-1].guard_name == "TRADE_COOLDOWN"
    assert "Trade cooldown active" in captured_guards_symbol[-1].reason
    assert mock_status_tracker.status[symbol].guard_metrics.trades_blocked_count >= 1
    assert "SKIPPED_TRADE (TRADE_COOLDOWN)" in mock_status_tracker.status[symbol].notes


    # 4. Advance time beyond cooldown and try again (should now be allowed)
    mock_status_tracker.status[symbol].last_trade_timestamp = datetime.now().timestamp() - local_config.TRADE_COOLDOWN_SECONDS - 1
    
    await position_manager.open_position(
        symbol=symbol,
        signal_direction='buy',
        current_price=current_price + 100,
        exchange_client=position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    # A new position should now be open
    assert position_manager.get_total_open_positions() == 1
    assert position_manager.has_open_position(symbol)
    
    # Verify guard metrics for the allowed trade (should not have a blocked guard result)
    captured_guards_symbol_after_cooldown = mock_status_tracker.get_captured_guard_results(symbol)
    # The last captured guard should still be the TRADE_COOLDOWN from the previous attempt
    assert captured_guards_symbol_after_cooldown[-1].guard_name == "TRADE_COOLDOWN"
    # No new guard should be triggered to block this trade, so trades_blocked_count should not increase
    # This assertion needs careful thought, as update_guard_metrics is called for ALL guard checks, even allowed ones.
    # However, the `trades_blocked_count` only increments if `allowed` is False.
    assert mock_status_tracker.status[symbol].guard_metrics.trades_blocked_count == 1 # Still 1 blocked trade from cooldown
    assert "SKIPPED_TRADE (TRADE_COOLDOWN)" in mock_status_tracker.status[symbol].notes # The notes persist for the last blocked action
    
    # To confirm the trade was allowed, we check if a position was opened.
    assert position_manager.get_open_position(symbol) is not None


@pytest.mark.asyncio
async def test_signal_cooldown_guard(mock_position_manager, mock_status_tracker, mock_config):
    # This test needs to run through executor_bot's market_loop or a mock of it,
    # as signal cooldown is handled there.
    # For now, we'll manually simulate the check.
    symbol = "ETH/USDT"
    current_price = 2000.0
    signal_type = "STRONG_LONG"

    # Ensure SIGNAL_COOLDOWN_SECONDS is set for the test
    mock_config.SIGNAL_COOLDOWN_SECONDS = 3

    s = mock_status_tracker.status[symbol]

    # 1. First signal (should be allowed to process if other guards pass)
    s.last_signal_timestamp = datetime.now().timestamp() - mock_config.SIGNAL_COOLDOWN_SECONDS - 1 # Ensure outside cooldown
    s.last_signal_type = "NEUTRAL" # Different from current signal

    # Simulate signal processing in executor_bot
    # This part would normally call position_manager.open_position, etc.
    # For this test, we just check the guard logic.
    current_time = datetime.now().timestamp()
    if (current_time - s.last_signal_timestamp < mock_config.SIGNAL_COOLDOWN_SECONDS) and \
       (s.last_signal_type == signal_type):
        guard_result = GuardResult(
            allowed=False,
            reason=f"Signal '{signal_type}' ignored due to cooldown.",
            guard_name="SIGNAL_COOLDOWN",
            details=f"Last signal of same type was {current_time - s.last_signal_timestamp:.2f}s ago (min {mock_config.SIGNAL_COOLDOWN_SECONDS}s)."
        )
        mock_status_tracker.update_guard_metrics(symbol, guard_result)
        mock_status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
    else:
        # Simulate successful signal processing (e.g., updating last_signal_timestamp)
        s.last_signal_timestamp = current_time
        s.last_signal_type = signal_type
        mock_status_tracker.update_status(symbol, notes=f"Signal {signal_type} processed.")

    assert "Signal STRONG_LONG processed." in mock_status_tracker.status[symbol].notes
    assert s.last_signal_timestamp == current_time
    assert s.last_signal_type == signal_type

    # 2. Attempt to process same signal immediately (should be blocked by cooldown)
    # Manually advance time by a small amount within cooldown
    current_time_after_short_wait = current_time + (mock_config.SIGNAL_COOLDOWN_SECONDS / 2)
    with patch('time.time', return_value=current_time_after_short_wait):
        if (time.time() - s.last_signal_timestamp < mock_config.SIGNAL_COOLDOWN_SECONDS) and \
           (s.last_signal_type == signal_type):
            guard_result = GuardResult(
                allowed=False,
                reason=f"Signal '{signal_type}' ignored due to cooldown.",
                guard_name="SIGNAL_COOLDOWN",
                details=f"Last signal of same type was {time.time() - s.last_signal_timestamp:.2f}s ago (min {mock_config.SIGNAL_COOLDOWN_SECONDS}s)."
            )
            mock_status_tracker.update_guard_metrics(symbol, guard_result)
            mock_status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
        else:
            s.last_signal_timestamp = time.time()
            s.last_signal_type = signal_type
            mock_status_tracker.update_status(symbol, notes=f"Signal {signal_type} processed.")

    assert "SKIPPED_TRADE (SIGNAL_COOLDOWN)" in mock_status_tracker.status[symbol].notes
    captured_guards_symbol = mock_status_tracker.get_captured_guard_results(symbol)
    assert len(captured_guards_symbol) > 0
    assert not captured_guards_symbol[-1].allowed
    assert captured_guards_symbol[-1].guard_name == "SIGNAL_COOLDOWN"
    assert "ignored due to cooldown" in captured_guards_symbol[-1].reason
    assert mock_status_tracker.status[symbol].guard_metrics.trades_blocked_count >= 1
    assert "SKIPPED_TRADE (SIGNAL_COOLDOWN)" in mock_status_tracker.status[symbol].notes # Assert the notes here

    # 3. Advance time beyond cooldown and process same signal (should be allowed if other guards pass)
    current_time_after_cooldown = current_time + mock_config.SIGNAL_COOLDOWN_SECONDS + 1
    with patch('time.time', return_value=current_time_after_cooldown):
        if (time.time() - s.last_signal_timestamp < mock_config.SIGNAL_COOLDOWN_SECONDS) and \
           (s.last_signal_type == signal_type):
            guard_result = GuardResult(
                allowed=False,
                reason=f"Signal '{signal_type}' ignored due to cooldown.",
                guard_name="SIGNAL_COOLDOWN",
                details=f"Last signal of same type was {time.time() - s.last_signal_timestamp:.2f}s ago (min {mock_config.SIGNAL_COOLDOWN_SECONDS}s)."
            )
            mock_status_tracker.update_guard_metrics(symbol, guard_result)
            mock_status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
        else:
            s.last_signal_timestamp = time.time()
            s.last_signal_type = signal_type
            mock_status_tracker.update_status(symbol, notes=f"Signal {signal_type} processed again.")
    
    assert "Signal STRONG_LONG processed again." in mock_status_tracker.status[symbol].notes
    assert s.last_signal_timestamp == current_time_after_cooldown
    assert s.last_signal_type == signal_type


@pytest.mark.asyncio
async def test_safe_mode_guard(mock_position_manager, mock_status_tracker, mock_config):
    symbol = "BTC/USDT"
    current_price = 30000.0

    # Ensure safe mode is enabled in config
    mock_config.ENABLE_SAFE_MODE = True

    # 1. Activate safe mode for the symbol
    mock_status_tracker.status[symbol].safe_mode_active = True
    mock_status_tracker.status[symbol].safe_mode_until = datetime.now().timestamp() + mock_config.SAFE_MODE_DURATION_S

    # 2. Attempt to open a position (should be blocked by safe mode)
    await mock_position_manager.open_position(
        symbol=symbol,
        signal_direction='buy',
        current_price=current_price,
        exchange_client=mock_position_manager.exchange_client,
        order_type='market',
        signal_stats_tracker=MagicMock(),
        cluster_snapshot={}
    )
    assert not mock_position_manager.has_open_position(symbol)
    assert mock_position_manager.get_total_open_positions() == 0

    # Verify guard metrics
    captured_guards_symbol = mock_status_tracker.get_captured_guard_results(symbol)
    assert len(captured_guards_symbol) > 0
    assert not captured_guards_symbol[-1].allowed
    assert captured_guards_symbol[-1].guard_name == "SAFE_MODE"
    assert "Safe mode is active." in captured_guards_symbol[-1].reason
    assert mock_status_tracker.status[symbol].guard_metrics.trades_blocked_count >= 1
    assert "SKIPPED_TRADE (SAFE_MODE)" in mock_status_tracker.status[symbol].notes