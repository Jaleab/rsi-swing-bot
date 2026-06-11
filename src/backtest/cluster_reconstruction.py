import pandas as pd
from collections import deque
from typing import Dict, Any, Literal, TypedDict, Deque, Optional
import asyncio
import time

from src.config import Config
from src.cluster_aggregator import ClusterAggregator, LiquidationEvent # Assuming LiquidationEvent is also defined in cluster_aggregator for consistency

class ClusterReconstructor:
    def __init__(self, config: Config, cluster_aggregator: ClusterAggregator):
        self.config = config
        self.cluster_aggregator = cluster_aggregator
        self.historical_liquidation_events: Deque[LiquidationEvent] = deque()
        self.last_replayed_timestamp = 0
        self.current_sweep_data: Dict[str, Any] = {
            'is_sweep': False,
            'bullish_sweep_volume': 0.0,
            'bearish_sweep_volume': 0.0
        }

    def load_historical_events(self, file_path: str):
        """
        Loads historical liquidation events from a CSV file.
        Assumes CSV format: timestamp,price,qty,side,exchange,symbol,order_id
        """
        try:
            df = pd.read_csv(file_path)
            # Ensure correct types and convert to LiquidationEvent format
            for _, row in df.iterrows():
                # Basic validation and type conversion
                event: LiquidationEvent = {
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "timestamp": row["timestamp"],
                    "price": row["price"],
                    "qty": row["qty"],
                    "qty_usdt": row["qty"] * row["price"], # Calculate qty_usdt
                    "side": row["side"],
                    "order_id": str(row["order_id"]) if "order_id" in row else ""
                }
                self.historical_liquidation_events.append(event)
            # Sort events by timestamp
            self.historical_liquidation_events = deque(sorted(self.historical_liquidation_events, key=lambda x: x["timestamp"]))
            print(f"Loaded {len(self.historical_liquidation_events)} historical liquidation events from {file_path}")
        except FileNotFoundError:
            print(f"Error: Historical liquidation events file not found at {file_path}")
        except Exception as e:
            print(f"Error loading historical liquidation events: {e}")

    async def replay_events_up_to_timestamp(self, current_candle_timestamp_ms: int):
        """
        Replays historical liquidation events up to the given timestamp,
        ingesting them into the cluster aggregator.
        """
        while self.historical_liquidation_events and \
              self.historical_liquidation_events[0]["timestamp"] <= current_candle_timestamp_ms:
            
            event = self.historical_liquidation_events.popleft()
            if event["timestamp"] >= self.last_replayed_timestamp:
                self.cluster_aggregator.ingest(event)
                self.last_replayed_timestamp = event["timestamp"]
        
        # Update sweep data for current candle based on latest aggregator state
        # Use a representative symbol and price for sweep detection
        if self.config.SYMBOLS:
            sym = self.config.SYMBOLS[0]
            price = self.cluster_aggregator._get_approx_current_price(sym)
            if price and price > 0:
                is_sweep, bull, bear = self.cluster_aggregator.is_sweep_detected(
                    sym, price, current_time_ms_override=current_candle_timestamp_ms
                )
                self.current_sweep_data = {
                    'is_sweep': is_sweep,
                    'bullish_sweep_volume': bull,
                    'bearish_sweep_volume': bear
                }

# Example Usage (for testing)
async def main_test():
    # Override config for testing
    Config.SLIDING_WINDOW_S = 60 # 60 seconds for quick testing
    Config.BIN_PCT = 0.002
    Config.SWEEP_WINDOW_S = 10

    cluster_aggregator = ClusterAggregator(Config)
    reconstructor = ClusterReconstructor(Config, cluster_aggregator)

    # Create a dummy CSV file for testing
    dummy_csv_path = "dummy_liquidation_events.csv"
    with open(dummy_csv_path, "w", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "price", "qty", "side", "exchange", "symbol", "order_id"])
        base_time = int(time.time() * 1000) - 300000 # 5 minutes ago
        for i in range(100):
            ts = base_time + i * 1000 # 1 event per second
            price = 100.0 + (i % 10) * 0.1
            qty = 0.5 + (i % 3) * 0.1
            side = "LONG" if i % 2 == 0 else "SHORT"
            exchange = "bybit"
            symbol = "SOL/USDT"
            order_id = f"test_{i}"
            writer.writerow([ts, price, qty, side, exchange, symbol, order_id])
        
        # Add some events for sweep detection
        sweep_time = base_time + 200000 # 200 seconds later
        sweep_price = 102.5
        for i in range(5):
            ts = sweep_time + i * 100
            price = sweep_price + (i * 0.005)
            qty = 10.0 # Large quantity
            side = "SHORT"
            exchange = "bybit"
            symbol = "SOL/USDT"
            order_id = f"sweep_{i}"
            writer.writerow([ts, price, qty, side, exchange, symbol, order_id])


    reconstructor.load_historical_events(dummy_csv_path)

    # Simulate candles in backtest
    current_candle_timestamp = base_time + 10000 # Start 10 seconds after first event
    for i in range(20):
        print(f"\n--- Simulating Candle at {datetime.fromtimestamp(current_candle_timestamp / 1000)} ---")
        await reconstructor.replay_events_up_to_timestamp(current_candle_timestamp)
        
        # Get snapshot and check for sweeps
        snapshot = cluster_aggregator.get_snapshot("SOL/USDT")
        if snapshot["top_clusters"]:
            top_cluster_bin = snapshot["top_clusters"][0]["bin_idx"]
            sweep_volume = cluster_aggregator.is_sweep_detected(top_cluster_bin, 100.0)
            if sweep_volume:
                print(f"  !!! SWEEP DETECTED in top cluster bin {top_cluster_bin} with volume {sweep_volume:.2f} USDT !!!")
            
            print(f"  Top Cluster: Bin {top_cluster_bin}, Volume={snapshot['top_clusters'][0]['volume']:.2f}, Strength={snapshot['top_clusters'][0]['normalized_strength']:.2f}")

        current_candle_timestamp += 5000 # Move to next candle (5 seconds interval for this test)
        await asyncio.sleep(0.1) # Simulate backtest processing delay

    os.remove(dummy_csv_path) # Clean up dummy file

if __name__ == "__main__":
    # To run this test: python -m asyncio src/backtest/cluster_reconstruction.py
    asyncio.run(main_test())