import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from collections import deque
import time # Import time module
import json # Import json module

from src.sim_events_generator import SimEventsGenerator
from src.cluster_aggregator import ClusterAggregator
from src.config import Config
from src.ws_liquidation import LiquidationEvent # Assuming LiquidationEvent is defined here

# Mock StatusTracker for ClusterAggregator initialization
from src.status_tracker import PairStatus # Import PairStatus

class MockStatusTracker:
    def __init__(self, symbols):
        self.status = {s: PairStatus(symbol=s) for s in symbols} # Initialize with actual PairStatus objects
    def update_status(self, symbol, **kwargs):
        s = self.status[symbol]
        for k, v in kwargs.items():
            setattr(s, k, v)

@pytest.fixture
def mock_event_queue():
    return asyncio.Queue()

@pytest.fixture
def mock_order_book_queue():
    return asyncio.Queue()

@pytest.fixture
def mock_trade_stream_queue():
    return asyncio.Queue()

@pytest.fixture
def mock_config():
    cfg = Config()
    cfg.SYMBOLS = ["BTC/USDT", "ETH/USDT"]
    cfg.HISTORICAL_WINDOW_S = 3600
    cfg.SIM_SWEEP_MIN_DELAY_S = 10
    cfg.SIM_SWEEP_MAX_DELAY_S = 30
    return cfg

@pytest.fixture
def mock_status_tracker(mock_config): # Inject mock_config
    return MockStatusTracker(mock_config.SYMBOLS)

@pytest.fixture
def sim_generator(mock_event_queue, mock_order_book_queue, mock_trade_stream_queue, mock_config, mock_status_tracker):
    return SimEventsGenerator(mock_event_queue, mock_config, mock_status_tracker)

@pytest.fixture
def cluster_aggregator(mock_config, mock_status_tracker, mock_event_queue):
    # Use a simplified config for testing
    mock_config.SLIDING_WINDOW_S = 10 # Aggregate over 10 seconds
    mock_config.SWEEP_WINDOW_S = 2    # Sweep window of 2 seconds
    mock_config.BIN_PCT = 0.001       # 0.1% bin size
    mock_config.SWEEP_THRESHOLD_FACTOR = 1.5
    mock_config.MIN_SWEEP_VOLUME_USDT = 50.0 # Adjusted for easier sweep detection in test
    mock_config.CLUSTER_MAX_AGE_S = 60 # Clusters expire after 60 seconds
    mock_config.CLUSTER_PRICE_DISTANCE_PCT = 0.05 # 5% price distance
    mock_config.SAVE_DATA = False # No data saving during tests

    return ClusterAggregator(mock_config, mock_status_tracker, mock_event_queue)

@pytest.mark.asyncio
async def test_synthetic_sweep_detection(sim_generator: SimEventsGenerator, cluster_aggregator: ClusterAggregator, mock_event_queue: asyncio.Queue):
    symbol = "BTC/USDT"
    test_price = 30000.0
    test_volume = 200.0 # Volume above MIN_SWEEP_VOLUME_USDT, adjusted for sweep detection logic
    
    # Ingest historical events for BTC/USDT to establish a baseline
    historical_base_timestamp = int(time.time() * 1000) - (cluster_aggregator.config.SLIDING_WINDOW_S * 1000)
    for i in range(5):
        hist_event_timestamp = historical_base_timestamp + (i * 1000)
        hist_event = LiquidationEvent(
            exchange="bybit", symbol=symbol, timestamp=hist_event_timestamp,
            price=test_price + (i * 0.1), qty=10.0, qty_usdt=1000.0, side="LONG", order_id=f"hist_id{i}"
        )
        cluster_aggregator.ingest(hist_event)
        await asyncio.sleep(0.01)

    # Generate a synthetic sweep and directly ingest events
    events_to_ingest = []
    base_timestamp = int(time.time() * 1000)
    volume_per_event = test_volume / 5 # num_events = 5
    delay_per_event = 1.0 / 5 # duration_s = 1.0

    for i in range(5):
        event_timestamp = base_timestamp + int(i * delay_per_event * 1000)
        event_price = test_price # Ensure all events fall into the same bin
        
        event = LiquidationEvent(
            exchange="bybit",
            symbol=symbol,
            timestamp=event_timestamp,
            price=event_price,
            qty=volume_per_event / event_price, # Approximate qty
            qty_usdt=volume_per_event,
            side="SHORT",
            order_id=f"synthetic_sweep_{base_timestamp}_{i}"
        )
        events_to_ingest.append(event)

    for event in events_to_ingest:
        cluster_aggregator.ingest(event)
        await asyncio.sleep(0.01) # Small delay to simulate async processing

    # Ensure enough time has passed for events to be within the sweep window
    # Use the timestamp of the last ingested event for evaluation
    evaluation_time_ms = events_to_ingest[-1].timestamp + 100 # A little after the last event
    
    # Check for sweep detection
    is_sweep, actual_sweep_volume = cluster_aggregator.is_sweep_detected(symbol, test_price, current_time_ms_override=evaluation_time_ms)

    assert is_sweep is True, "Sweep should be detected"
    assert actual_sweep_volume >= test_volume * 0.9, "Detected sweep volume should be close to generated volume"

    # Test with insufficient volume
    low_volume_events_to_ingest = []
    low_volume_test_price = test_price + 100
    low_volume_test_volume = 50.0 # Below MIN_SWEEP_VOLUME_USDT
    
    for i in range(5):
        event_timestamp = base_timestamp + int(i * delay_per_event * 1000)
        event_price = low_volume_test_price
        
        event = LiquidationEvent(
            exchange="bybit",
            symbol=symbol,
            timestamp=event_timestamp,
            price=event_price,
            qty=low_volume_test_volume / (5 * event_price),
            qty_usdt=low_volume_test_volume / 5,
            side="LONG",
            order_id=f"synthetic_low_volume_{base_timestamp}_{i}"
        )
        low_volume_events_to_ingest.append(event)
        cluster_aggregator.ingest(event)
        await asyncio.sleep(0.01)

    evaluation_time_low_volume_ms = low_volume_events_to_ingest[-1].timestamp + 100

    is_sweep_low, actual_sweep_volume_low = cluster_aggregator.is_sweep_detected(symbol, low_volume_test_price, current_time_ms_override=evaluation_time_low_volume_ms)
    assert is_sweep_low is False, "Sweep should NOT be detected with insufficient volume"

@pytest.mark.asyncio
async def test_historical_event_replay(sim_generator: SimEventsGenerator, cluster_aggregator: ClusterAggregator, mock_event_queue: asyncio.Queue, tmp_path):
    # Create a dummy historical events file
    historical_data = [
        {"exchange": "bybit", "symbol": "ETH/USDT", "timestamp": int(time.time() * 1000) - 5000, "price": 2000.0, "qty": 1.0, "qty_usdt": 2000.0, "side": "LONG", "order_id": "hist1"},
        {"exchange": "bybit", "symbol": "ETH/USDT", "timestamp": int(time.time() * 1000) - 4000, "price": 2000.5, "qty": 0.5, "qty_usdt": 1000.25, "side": "SHORT", "order_id": "hist2"},
        {"exchange": "bybit", "symbol": "ETH/USDT", "timestamp": int(time.time() * 1000) - 3000, "price": 2001.0, "qty": 2.0, "qty_usdt": 4002.0, "side": "LONG", "order_id": "hist3"},
    ]
    file_path = tmp_path / "historical_events.json"
    with open(file_path, 'w') as f:
        json.dump(historical_data, f)
    
    await sim_generator.load_historical_events(file_path)
    assert len(sim_generator.events) == 3

    # Ingest historical events directly
    for event_data in historical_data:
        event = LiquidationEvent(
            exchange=event_data.get("exchange", "bybit"),
            symbol=event_data.get("symbol", "UNKNOWN/USDT"),
            timestamp=event_data.get("timestamp", int(time.time() * 1000)),
            price=float(event_data.get("price", 0.0)),
            qty=float(event_data.get("qty", 0.0)),
            qty_usdt=float(event_data.get("qty_usdt", 0.0)),
            side=event_data.get("side", "LONG"),
            order_id=event_data.get("order_id", "")
        )
        cluster_aggregator.ingest(event)
        await asyncio.sleep(0.01) # Small delay for async
    
    # Allow time for events to be processed
    await asyncio.sleep(1)

    # Verify events are ingested and aggregated
    # Pass the latest timestamp for snapshot generation
    snapshot = cluster_aggregator.get_snapshot("ETH/USDT", current_time_ms_override=historical_data[-1]["timestamp"] + 100) # Pass a time after the last event
    assert snapshot["clusters"], "Clusters should be formed from historical events"
    assert snapshot["median_volume"] > 0, "Median volume should be calculated"
    assert cluster_aggregator.events_deque["ETH/USDT"], "Events deque should contain events"