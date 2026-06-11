import pytest
import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from src.config import Config
from src.cluster_aggregator import ClusterAggregator, LiquidationEvent
from src.status_tracker import StatusTracker, PairStatus
from src.position_manager import PositionManager

# Fixture for a clean ClusterAggregator instance with specific sweep settings
@pytest.fixture
def sweep_aggregator_instance():
    temp_config = Config()
    temp_config.SYMBOLS = ["SOL/USDT", "ETH/USDT"] # Define symbols for the mock status tracker
    temp_config.SLIDING_WINDOW_S = 10  # 10 seconds
    temp_config.SWEEP_WINDOW_S = 2     # 2 seconds
    temp_config.BIN_MODE = "absolute"  # Use absolute binning for testing
    temp_config.BIN_ABS = 1.0          # 1.0 USD per bin
    temp_config.SWEEP_THRESHOLD_FACTOR = 2.0 # Higher threshold for clearer sweep detection
    temp_config.MIN_SWEEP_VOLUME_USDT = 500.0 # Minimum volume for a sweep

    # Create mock dependencies for ClusterAggregator
    mock_position_manager = MagicMock(spec=PositionManager) # Mock PositionManager
    mock_status_tracker = StatusTracker(temp_config.SYMBOLS, {s: mock_position_manager for s in temp_config.SYMBOLS}) # Pass symbols and mock position_manager
    mock_event_queue = asyncio.Queue() # Create a real asyncio Queue
    
    return ClusterAggregator(temp_config, mock_status_tracker, mock_event_queue)

@pytest.mark.asyncio
async def test_sweep_detection_high_volume_single_bin(sweep_aggregator_instance):
    """
    Test sweep detection with a single bin receiving high volume within the sweep window.
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # Ingest historical events that will form the 'historical_avg'
    # These events are older than SWEEP_WINDOW_S but within SLIDING_WINDOW_S
    for i in range(5): # 5 events, each 1000 USDT
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=event_time,
            price=price + (i * 0.1), qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        aggregator.ingest(event)
    
    # Ingest a large volume event that should trigger a sweep
    sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 500, # Within sweep window
        price=price, qty=500.0, qty_usdt=50000.0, side="LONG", order_id="sweep_id"
    )
    aggregator.ingest(sweep_event)
    
    is_sweep, bullish, bearish = aggregator.is_sweep_detected(symbol, price)
    assert is_sweep is True
    assert (bullish + bearish) == 50000.0
    # The historical_avg would be 1000.0 * 5 events / 5 events = 1000.0 per event (roughly)
    # The recent volume (50000.0) is much higher than 1000.0 * SWEEP_THRESHOLD_FACTOR (2.0) = 2000.0

@pytest.mark.asyncio
async def test_sweep_detection_below_min_volume(sweep_aggregator_instance):
    """
    Test that a high-factor volume does not trigger a sweep if below MIN_SWEEP_VOLUME_USDT.
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # Ingest historical events
    for i in range(5):
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=event_time,
            price=price + (i * 0.1), qty=1.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        aggregator.ingest(event)
    
    # Ingest an event with volume > threshold_factor but < MIN_SWEEP_VOLUME_USDT
    low_sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 500,
        price=price, qty=10.0, qty_usdt=100.0, side="LONG", order_id="low_sweep_id"
    )
    aggregator.ingest(low_sweep_event)

    is_sweep, bullish, bearish = aggregator.is_sweep_detected(symbol, price)
    assert is_sweep is False # 100.0 USDT is less than MIN_SWEEP_VOLUME_USDT (500.0)

@pytest.mark.asyncio
async def test_sweep_detection_multiple_bins_no_single_sweep(sweep_aggregator_instance):
    """
    Test that multiple events across different bins within the sweep window do not trigger a single bin sweep.
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0 # Define a price for setting mark_price
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # Ingest historical events to provide a baseline for bins 100, 102, and 104
    historical_prices = [100.0, 102.0, 104.0, 100.0, 102.0, 104.0, 100.0, 102.0, 104.0] # Ensure sufficient coverage for the bins of interest
    for i, p in enumerate(historical_prices):
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=event_time,
            price=p, qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        aggregator.ingest(event)
    
    # Ingest multiple events across different bins, each below sweep threshold
    event1 = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 1500,
        price=100.0, qty=15.0, qty_usdt=1500.0, side="LONG", order_id="multi_bin_id1"
    )
    event2 = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 1000,
        price=102.0, qty=15.0, qty_usdt=1500.0, side="LONG", order_id="multi_bin_id2"
    )
    event3 = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 500,
        price=104.0, qty=15.0, qty_usdt=1500.0, side="LONG", order_id="multi_bin_id3"
    )
    aggregator.ingest(event1)
    aggregator.ingest(event2)
    aggregator.ingest(event3)

    # Check that no single bin sweep is detected (as volume is spread)
    for event in [event1, event2, event3]:
        is_sweep, _, _ = aggregator.is_sweep_detected(symbol, event.price)
        assert is_sweep is False # Each event's volume (5000-5200) is not enough to trigger a sweep by itself

@pytest.mark.asyncio
async def test_sweep_detection_just_above_threshold(sweep_aggregator_instance):
    """
    Test sweep detection when volume is just above the threshold factor.
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # Ingest historical events to establish a baseline historical average volume
    # Total historical volume in bin: 5 * 1000.0 = 5000.0
    # Count of historical events in bin: 5
    # historical_avg = 5000.0 / 5 = 1000.0
    for i in range(5):
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=event_time,
            price=price, qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        aggregator.ingest(event)
    
    # Calculate the minimum volume needed to trigger a sweep: historical_avg (1000.0) * SWEEP_THRESHOLD_FACTOR (2.0) = 2000.0
    # Plus, it must be >= MIN_SWEEP_VOLUME_USDT (500.0)
    # So, min sweep volume is 2000.0
    
    # Ingest an event just above this calculated threshold
    sweep_usdt = 2001.0 # Just above 2000.0
    sweep_qty = sweep_usdt / price

    sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 500,
        price=price, qty=sweep_qty, qty_usdt=sweep_usdt, side="LONG", order_id="just_above_id"
    )
    aggregator.ingest(sweep_event)

    is_sweep, bullish, bearish = aggregator.is_sweep_detected(symbol, price)
    assert is_sweep is True
    assert (bullish + bearish) == sweep_usdt

@pytest.mark.asyncio
async def test_sweep_detection_just_below_threshold(sweep_aggregator_instance):
    """
    Test that a volume just below the threshold factor does not trigger a sweep.
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # Ingest historical events
    for i in range(5):
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=event_time,
            price=price, qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        aggregator.ingest(event)
    
    # Calculate max non-sweep volume: historical_avg (1000.0) * SWEEP_THRESHOLD_FACTOR (2.0) - 1 = 1999.0
    # Must also be >= MIN_SWEEP_VOLUME_USDT (500.0)
    max_non_sweep_vol = 1999.0
    sweep_qty = max_non_sweep_vol / price

    no_sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 500,
        price=price, qty=sweep_qty, qty_usdt=max_non_sweep_vol, side="LONG", order_id="just_below_id"
    )
    aggregator.ingest(no_sweep_event)

    is_sweep, bullish, bearish = aggregator.is_sweep_detected(symbol, price)
    assert is_sweep is False

@pytest.mark.asyncio
async def test_sweep_detection_across_sweep_window_boundary(sweep_aggregator_instance):
    """
    Test that events split by the sweep window boundary are correctly evaluated.
    Events older than SWEEP_WINDOW_S should not contribute to the current sweep volume.
    """
    aggregator = sweep_aggregator_instance
    config = aggregator.config
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # Ingest historical events (well outside sweep window)
    for i in range(5):
        event_time = current_time_ms - (config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (config.SWEEP_WINDOW_S * 1000) - 5000
        event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=event_time,
            price=price + (i * 0.1), qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        aggregator.ingest(event)
    
    # Ingest an event just outside the sweep window, but within sliding window
    old_event_usdt = 1000.0
    old_sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol,
        timestamp=current_time_ms - (config.SWEEP_WINDOW_S * 1000) - 100, # Just outside sweep window
        price=price, qty=old_event_usdt/price, qty_usdt=old_event_usdt, side="LONG", order_id="old_sweep_id"
    )
    aggregator.ingest(old_sweep_event)

    # Ingest a new event that, by itself, is not a sweep, but combined with the old one would be
    new_event_usdt = 1500.0 # Not a sweep by itself (vs historical_avg of ~1000 * 2 = 2000)
    new_sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol,
        timestamp=current_time_ms - 100, # Within sweep window
        price=price, qty=new_event_usdt/price, qty_usdt=new_event_usdt, side="LONG", order_id="new_sweep_id"
    )
    aggregator.ingest(new_sweep_event)

    # The old event's volume should not contribute to the current sweep calculation
    is_sweep, bullish, bearish = aggregator.is_sweep_detected(symbol, price)
    
    assert is_sweep is False # New event volume (1500) is less than historical_avg (1000) * factor (2.0) = 2000.0

@pytest.mark.asyncio
async def test_no_sweep_when_historical_avg_is_zero(sweep_aggregator_instance):
    """
    Test that no sweep is detected if historical_avg_volume is zero (e.g., at startup).
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)
    symbol = "SOL/USDT"
    price = 100.0
    aggregator.status_tracker.update_status(symbol=symbol, mark_price=price) # Set mark price for _get_bin_size

    # No historical events ingested, so historical_avg will be 0.0
    
    # Ingest a large event that is > MIN_SWEEP_VOLUME_USDT
    sweep_event = LiquidationEvent(
        exchange="bybit", symbol=symbol, timestamp=current_time_ms - 500,
        price=price, qty=500.0, qty_usdt=50000.0, side="LONG", order_id="sweep_id"
    )
    aggregator.ingest(sweep_event)

    is_sweep, bullish, bearish = aggregator.is_sweep_detected(symbol, price)
    assert is_sweep is True # Should be True because recent_volume (50000.0) > MIN_SWEEP_VOLUME_USDT (500.0) and historical_avg is 0.0

@pytest.mark.asyncio
async def test_sweep_detection_multiple_symbols(sweep_aggregator_instance):
    """
    Test sweep detection works independently for multiple symbols.
    """
    aggregator = sweep_aggregator_instance
    current_time_ms = int(time.time() * 1000)

    # Ingest historical events for SOL/USDT
    sol_symbol = "SOL/USDT"
    sol_price = 100.0
    aggregator.status_tracker.status[sol_symbol].mark_price = sol_price # Set mark price for _get_bin_size
    for i in range(5):
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=sol_symbol, timestamp=event_time,
            price=sol_price, qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"sol_hist_id{i}"
        )
        aggregator.ingest(event)

    # Ingest historical events for ETH/USDT
    eth_symbol = "ETH/USDT"
    eth_price = 2000.0
    # Ensure ETH/USDT status is initialized before setting mark_price
    aggregator.status_tracker.status[eth_symbol] = aggregator.status_tracker.status.get(eth_symbol, PairStatus(symbol=eth_symbol))
    aggregator.status_tracker.status[eth_symbol].mark_price = eth_price # Set mark price for _get_bin_size
    for i in range(5):
        event_time = current_time_ms - (aggregator.config.SLIDING_WINDOW_S * 1000) + (i * 1000) - (aggregator.config.SWEEP_WINDOW_S * 1000)
        event = LiquidationEvent(
            exchange="bybit", symbol=eth_symbol, timestamp=event_time,
            price=eth_price, qty=0.5, qty_usdt=1000.0, side="SHORT", order_id=f"eth_hist_id{i}"
        )
        aggregator.ingest(event)

    # Trigger sweep for SOL/USDT
    sol_sweep_event = LiquidationEvent(
        exchange="bybit", symbol=sol_symbol, timestamp=current_time_ms - 500,
        price=sol_price, qty=500.0, qty_usdt=50000.0, side="LONG", order_id="sol_sweep_id"
    )
    aggregator.ingest(sol_sweep_event)
    is_sol_sweep, sol_bullish, sol_bearish = aggregator.is_sweep_detected(sol_symbol, sol_price)
    assert is_sol_sweep is True
    assert (sol_bullish + sol_bearish) == 50000.0

    # Ensure no sweep for ETH/USDT (no recent large event)
    is_eth_sweep, _, _ = aggregator.is_sweep_detected(eth_symbol, eth_price)
    assert is_eth_sweep is False
