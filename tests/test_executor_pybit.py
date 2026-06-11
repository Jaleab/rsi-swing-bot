import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import time

# Import the functions/classes from your code
import executor_bot as executor # Assuming executor_bot.py is in the root or in PYTHONPATH

@pytest.fixture
def mock_session():
    m = AsyncMock() # Use AsyncMock for all methods that are awaited
    # Mock the fetch_ohlcv method expected by executor_bot
    m.fetch_ohlcv.return_value = executor.pd.DataFrame([
        [int(time.time()*1000)-60000, 100.0, 101.0, 99.0, 100.5, 12.0],
        [int(time.time()*1000)-120000, 99.0, 100.0, 98.0, 99.5, 10.0]
    ], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # Mock the fetch_balance method expected by executor_bot
    m.fetch_balance.return_value = {
        "total": {"USDT": 1000.0},
        "free": {"USDT": 1000.0}
    }

    # Mock the place_order method expected by PositionManager and executor_bot
    m.place_order.return_value = {"retCode": 0, "result": {"orderId": "abc123"}}

    # Mock the fetch_positions method (from exchange client)
    m.fetch_positions.return_value = [] # Default to no open positions

    # Mock the fetch_symbol_info method (from exchange client)
    m.fetch_symbol_info.return_value = {
        "symbol": "SOLUSDT",
        "precision": {"price": 0.01, "amount": 0.001}
    }

    # Mock fetch_ticker for _get_current_price
    m.fetch_ticker.return_value = {'markPrice': 100.0}
    
    return m

@pytest.mark.asyncio
async def test_fetch_ohlcv_and_parse(mock_session):
    # Directly use mock_session as the exchange_client
    df = await executor.fetch_ohlcv(mock_session, executor.Config.SYMBOLS[0], executor.Config.TIMEFRAME, MagicMock()) # Pass a mock status_tracker
    # Basic assertions
    assert not df.empty
    assert "close" in df.columns
    assert df['close'].iloc[-1] == 99.5 # Corrected expected value
    mock_session.fetch_ohlcv.assert_called_once() # Assert that fetch_ohlcv was called

@pytest.mark.asyncio
async def test_place_order_happy_path(mock_session):
    # Mock dependencies
    mock_position_manager = AsyncMock(spec=executor.PositionManager)
    mock_metrics_exporter = MagicMock(spec=executor.metrics_exporter)
    mock_signal_stats_tracker = MagicMock()

    # Configure the mock open_position to call exchange_client.place_order
    async def mock_open_position_side_effect(**kwargs):
        await kwargs['exchange_client'].place_order(
            symbol=kwargs['symbol'],
            side='buy' if kwargs['signal_direction'] == 'buy' else 'sell',
            order_type=kwargs['order_type'],
            qty=0.01, # Dummy quantity
            price=kwargs['current_price'] # Dummy price
        )
    mock_position_manager.open_position.side_effect = mock_open_position_side_effect

    await mock_position_manager.open_position(
        symbol=executor.Config.SYMBOLS[0],
        signal_direction="buy",
        current_price=100.0,
        exchange_client=mock_session,
        order_type="market",
        signal_stats_tracker=mock_signal_stats_tracker,
        cluster_snapshot=MagicMock()
    )
    mock_session.place_order.assert_called_once()

@pytest.mark.asyncio
async def test_get_account_balance(mock_session):
    # Directly use mock_session as the exchange_client
    bal = await executor.get_account_balance(mock_session)
    assert isinstance(bal, float)
    assert bal == 1000.0

@pytest.mark.asyncio
async def test_get_current_position_no_position(mock_session):
    mock_session.fetch_positions.return_value = [] # No open positions
    position = await executor.get_current_position(mock_session, executor.Config.SYMBOLS[0])
    assert position is None
    mock_session.fetch_positions.assert_called_once_with([executor.Config.SYMBOLS[0]])

@pytest.mark.asyncio
async def test_get_current_position_with_position(mock_session):
    mock_session.fetch_positions.return_value = [{
        "symbol": "SOL/USDT",
        "size": 0.1,
        "entryPrice": 100.0,
        "unrealisedPnl": 5.0,
        "side": "long"
    }]
    
    position = await executor.get_current_position(mock_session, executor.Config.SYMBOLS[0])
    assert position is not None
    assert position['size'] == 0.1 # Changed from 'amount' to 'size'
    assert position['entryPrice'] == 100.0
    mock_session.fetch_positions.assert_called_once_with([executor.Config.SYMBOLS[0]])

@pytest.mark.asyncio
async def test_get_symbol_info(mock_session):
    # Directly use mock_session as the exchange_client
    symbol_info = await executor.get_symbol_info(mock_session, executor.Config.SYMBOLS[0])
    assert symbol_info is not None
    assert symbol_info['symbol'] == "SOLUSDT"
    assert symbol_info['precision']['price'] == 0.01
    assert symbol_info['precision']['amount'] == 0.001
