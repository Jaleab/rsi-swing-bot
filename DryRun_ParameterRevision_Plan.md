# 🧭 Dry Run & Parameter Revision Plan — RSI Swing Bot

## 1. Objective of the Dry Run

The dry run is designed to validate system integrity, observe live-like trading behavior, and establish a baseline performance profile without financial risk.
Specifically, it aims to:

*   Confirm that all major subsystems (event generator, aggregator, signal logic, position manager, metrics exporter) function as expected in coordination.
*   Evaluate trading signal quality and stability under simulated flow.
*   Identify which parameters most influence performance outcomes.
*   Produce the first measurable performance metrics to guide parameter optimization.

## 2. Environment Setup

*   **Mode**:
    *   `Config.SIM_MODE = True`
    *   `Config.LIVE_MODE = False`
*   **Logging**:
    *   `Config.LOG_LEVEL = DEBUG` to capture granular details.
    *   Logs streamed to `/app/bot.log` (in Docker) or console for local dev.
*   **Symbols**:
    *   Start with a limited set (e.g., BTC/USDT, ETH/USDT).
*   **Execution**:
    *   Run via Docker Compose (`docker compose up --build -d`).
    *   Use `docker cp` to extract logs for review.
*   **Metrics**:
    *   Prometheus exporter active on port 8000.
    *   Grafana dashboards used for live monitoring.
*   **Duration**:
    *   2–6 hours for initial behavioral run; extended runs (24–48h) for baseline establishment.

## 3. Operational Flow

### 1. Initialization and Setup

The bot starts up in a simulated environment (`SIM_MODE=True`).

*   **Configuration**: Loads parameters from [`src/config.py`](src/config.py) and the `.env` file, including symbols to monitor (e.g., BTC/USDT, ETH/USDT), historical window sizes, sweep thresholds, and logging levels.
*   **Event Generation (Simulated)**: The [`src/sim_events_generator.py`](src/sim_events_generator.py) component is initialized.
    *   It first generates a series of **initial historical liquidation events** for each configured symbol, spanning the `Config.HISTORICAL_WINDOW_S` timeframe. These events are crucial for populating the `events_deque` in the `ClusterAggregator`, allowing for a robust `historical_avg_volume` calculation from the outset.
    *   After historical events, it generates initial single events and then continuously generates synthetic liquidation sweeps for each symbol at random intervals, pushing these events into a shared `asyncio.Queue`.
*   **Position Management**: The [`src/position_manager.py`](src/position_manager.py) is initialized to handle opening, tracking, and closing of positions.
*   **Metrics Exporter**: The [`src/metrics_exporter.py`](src/metrics_exporter.py) starts a Prometheus metrics server on port 8000, exposing various bot metrics (e.g., open positions count, PnL).
*   **Cluster Aggregator**: The [`src/cluster_aggregator.py`](src/cluster_aggregator.py) is initialized for each symbol. It's responsible for ingesting liquidation events, maintaining a sliding window of recent events, and detecting "sweeps."

### 2. Event Ingestion and Aggregation

*   **Real-time Event Feed**: In `SIM_MODE`, the `SimEventsGenerator` continuously feeds synthetic liquidation events into a central `asyncio.Queue`. In a live environment, this would be replaced by a WebSocket connection (e.g., [`src/ws_liquidation.py`](src/ws_liquidation.py)) receiving real-time liquidation data from an exchange like Bybit.
*   **Cluster Aggregator Consumption**: The `ClusterAggregator` runs a consumer loop that continuously retrieves events from this queue.
*   **Event Ingestion**: For each incoming event, the `ClusterAggregator` processes it:
    *   It calculates a `bin_size` based on the event's price and configured binning mode (`percent` or `absolute`). This `bin_size` determines the price range for grouping liquidation events.
    *   The event is then assigned to a price `bin_idx`.
    *   The event's volume is added to the corresponding `bin` in a `defaultdict` (`symbol_data["bins"]`), which stores aggregated volume and event counts for each price bin.
    *   The individual event is appended to a `collections.deque` (`symbol_data["events_deque"]`), maintaining a historical record of events within a defined `SLIDING_WINDOW_S`.
    *   Old events outside the `SLIDING_WINDOW_S` are automatically expired from the `events_deque` and their volumes removed from the respective bins.
*   **Status Tracking**: The `ClusterAggregator` also updates the `StatusTracker` for the symbol with the latest price and other relevant data.

### 3. Sweep Detection and Signal Generation

*   **Snapshot Generation**: Periodically, or upon event ingestion, the `ClusterAggregator` generates a "snapshot" of the current market state for each symbol. This involves:
    *   Filtering active bins based on recent activity and minimum volume thresholds.
    *   Calculating metrics like `median_volume`, `support_band`, `resistance_band`, `imbalance_ratio`, `normalized_strength`, and `recent_volume`.
*   **Sweep Detection**: The `is_sweep_detected` method in [`src/cluster_aggregator.py`](src/cluster_aggregator.py) is the core logic for identifying significant liquidation events:
    *   It calculates `historical_avg_volume` by summing the `qty_usdt` of all events within the `HISTORICAL_WINDOW_S` from the `events_deque` and dividing by the number of events. This ensures a robust average volume for comparison.
    *   A sweep is detected if the `recent_volume` exceeds `historical_avg_volume` multiplied by a `SWEEP_THRESHOLD_FACTOR`.
    *   Additional checks ensure the sweep is within a valid price range and meets a minimum volume threshold (`MIN_SWEEP_VOLUME_USDT`).
*   **Signal Emission**: If a sweep is detected, a signal is generated and passed to the `SignalTracker`.

### 4. Signal Tracking and Position Management

*   **Signal Reception**: The [`src/signal_tracker.py`](src/signal_tracker.py) receives sweep signals.
*   **Trade Logic**: Based on the signal (e.g., `LONG` or `SHORT` sweep), the `executor_bot.py` decides whether to open a position.
*   **Open Position**: If a decision is made to open a position:
    *   The `PositionManager.open_position()` method (now `async def`) is called with details like `symbol`, `signal_direction`, `target_price`, `stop_price`, and `position_size_usdt`.
    *   The `PositionManager` simulates the order placement and updates its internal state to reflect the open position.
    *   Metrics in [`src/metrics_exporter.py`](src/metrics_exporter.py) are updated to reflect the new open position count and other relevant position details (entry price, target price, stop price).
    *   A trade is added to the `SignalStatsTracker` for performance analysis.
*   **Position Tracking**: The `PositionManager` continuously monitors open positions, updating their unrealized PnL and other metrics.
*   **Close Position**: Although not explicitly detailed in the provided context, a complete trading bot would have logic within the `PositionManager` or `executor_bot` to close positions based on target/stop prices, time limits, or inverse signals. This would also update metrics accordingly.

### 5. Metrics and Logging

*   **Detailed Logging**: Throughout its operation, the bot outputs detailed log messages at various levels (`INFO`, `DEBUG`) from different modules (e.g., `sim_events_generator`, `cluster_aggregator`, `position_manager`). These logs are streamed to `bot.log` in the Docker container, providing visibility into its internal workings.
*   **Prometheus Metrics**: The `metrics_exporter` continuously updates Prometheus gauges for key performance indicators such as `bot_open_positions_count`, `bot_position_unrealized_pnl`, `bot_position_realized_pnl`, `bot_position_entry_price`, `bot_position_target_price`, and `bot_position_stop_price`. These metrics can be scraped by a Prometheus server and visualized in Grafana dashboards for real-time monitoring.

## 4. Key Metrics to Monitor

### Trading Metrics:

*   Number of signals detected per symbol
*   Positions opened/closed
*   Average profit per trade, win rate, and drawdown

### Risk Management:

*   Average holding time
*   Stop-loss/take-profit trigger frequency
*   Maximum concurrent open positions

### Performance & Stability:

*   CPU/memory usage
*   Error and warning logs
*   Signal frequency stability over time

## 5. Parameter Identification

Critical tuning parameters include:

| Category              | Parameter                     | Description                                                           |
| :-------------------- | :---------------------------- | :-------------------------------------------------------------------- |
| RSI Logic             | `RSI_PERIOD`, `RSI_OVERBOUGHT`, `RSI_OVERSOLD` | Defines entry/exit sensitivity                                        |
| Sweep Detection       | `SWEEP_THRESHOLD_FACTOR`, `SWEEP_WINDOW_SECONDS` | Controls how strong a liquidation event must be to trigger a signal   |
| Volume/Impact Filters | `VOLUME_THRESHOLD_FACTOR`, `MIN_CLUSTER_IMPACT_SCORE` | Filters noise and low-impact signals                                  |
| Risk Management       | `POSITION_USDT`, `RISK_PER_TRADE_PERCENT`, `MIN_STOP_LOSS_PERCENT`, `MAX_TAKE_PROFIT_PERCENT` | Adjusts position size and profit/loss limits                          |
| Position Logic        | `USE_DYNAMIC_SLTP`, `SL_BUFFER`, `TP_BUFFER`, `MAX_OPEN_POSITIONS` | Governs stop-loss and take-profit mechanics                           |
| Aggregation           | `BIN_MODE`, `BIN_PCT`, `BIN_ABS` | Defines clustering sensitivity by price distance                      |