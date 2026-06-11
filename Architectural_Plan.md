# Architectural Plan: RSI Swing Bot Evolution

## 0. Executive Summary

This plan outlines the strategic direction for enhancing the RSI Swing Bot, transforming it from a functional prototype into a robust, production-ready trading system. Key areas of focus include enriching data sources, improving signal generation, bolstering execution safety, and significantly upgrading observability and testing capabilities. The plan prioritizes immediate, high-impact changes while laying the groundwork for long-term strategic improvements.

## 1. Phase 1: Immediate Enhancements & Critical Fixes (Next 7 Days)

This phase focuses on addressing the most critical risks and implementing high-ROI changes with relatively low development cost.

### 1.1. Enhance Data Streams: Orderbook & Trades Integration

*   **Objective:** Mitigate data sparsity of Bybit's `allLiquidation` stream and enrich signal generation with real-time order flow data.
*   **Changes:**
    *   **`src/ws_orderbook.py` (New File):** A new module to handle Bybit public Orderbook WebSocket subscriptions.
        *   Connect to `orderbook.50.SYMBOL` for each `Config.SYMBOL`.
        *   Maintain a local, real-time order book snapshot (e.g., using a `SortedDict` for bids/asks).
        *   Provide methods to query orderbook depth, bid/ask spread, and imbalance.
    *   **`src/ws_trades.py` (New File):** A new module for Bybit public Trade WebSocket subscriptions.
        *   Connect to `publicTrade.SYMBOL` for each `Config.SYMBOL`.
        *   Normalize trade events (timestamp, symbol, price, amount, side).
        *   Provide a mechanism to compute instantaneous buy/sell trade imbalance (e.g., within a 1-second window).
    *   **`executor_bot.py`:**
        *   Integrate new WebSocket consumers for orderbook and trades as background tasks.
        *   Pass relevant orderbook and trade metrics to `signal_generator.decide()`.
    *   **`src/signal_generator.py`:**
        *   Update `decide()` method signature to accept new orderbook and trade features (e.g., `orderbook_imbalance`, `trade_aggression`).
        *   Incorporate these features into the confidence scoring, potentially adding `W_ORDERFLOW` and `W_TRADE`.
*   **Verification:**
    *   Logs showing successful connection and processing of orderbook and trade events.
    *   The `rich` status table (from Phase 2) will display new order flow metrics.

### 1.2. Align Freshness & Cluster Windows

*   **Objective:** Ensure `MAX_LIQUIDATION_DATA_LATENCY_SECONDS` is appropriately aligned with `SLIDING_WINDOW_S` to prevent premature RSI-only fallback.
*   **Changes:**
    *   **`src/config.py`:** Set `MAX_LIQUIDATION_DATA_LATENCY_SECONDS = SLIDING_WINDOW_S` (e.g., 3600s). Consider a two-tier approach with short (300s) and long (3600s) memory for clusters.
*   **Verification:**
    *   Monitor logs to ensure `RSI_ONLY` fallback is not triggered due to misaligned windows when liquidation data is indeed recent.

### 1.3. Implement Execution Safety

*   **Objective:** Guard against duplicate orders, partial fills, and enhance order confirmation.
*   **Changes:**
    *   **`executor_bot.py` / `place_order()`:**
        *   Generate a unique `client_order_id` for each order.
        *   Store `client_order_id` and order status in a persistent (e.g., SQLite or in-memory dict with persistence) order tracking mechanism.
        *   Implement post-fill verification: after placing a market order, poll the exchange for up to 3 seconds to confirm fill status and percentage. Log fill details.
        *   Add basic order retry logic (if not already robust).
*   **Verification:**
    *   Simulated trades (in `SIM_MODE`) demonstrating correct `client_order_id` usage and post-fill logging.
    *   Unit tests for the order placement and verification logic.

### 1.4. Implement Synthetic Event Replayer

*   **Objective:** Create a deterministic environment for testing and validation of cluster aggregation and signal generation.
*   **Changes:**
    *   **`src/sim_events_generator.py` (New File):**
        *   A module capable of replaying historical liquidation events (from a CSV or JSON file) with configurable speed.
        *   Ability to generate synthetic sweep events (single-bin burst) for testing sweep detection.
        *   This generator will feed into the `liquidation_event_queue` in `executor_bot.py` during test runs.
    *   **`tests/` (New Test Files):**
        *   Integration tests leveraging `sim_events_generator.py` to feed events and assert expected cluster states and signal outputs.
*   **Verification:**
    *   Successful execution of CI/local tests using the synthetic replayer.
    *   Consistent cluster states and signal generation under controlled, reproducible inputs.

### 1.5. Unit/Integration Tests for Cluster Aggregator & Sweep Detection

*   **Objective:** Ensure the core logic of cluster aggregation and sweep detection is robust and handles edge cases correctly.
*   **Changes:**
    *   **`tests/test_cluster_aggregator.py`:**
        *   Add unit tests for `price_to_bin`, `ingest`, `expire_old_events`.
        *   Test various scenarios: single event, multiple events in a bin, events across window boundaries, empty queues.
        *   Add edge case tests for sweep detection (e.g., volume just above/below threshold, price cross-through).
*   **Verification:**
    *   All new tests pass consistently.

### 1.6. Persistence Layer for Aggregated State (ClusterAggregator)

*   **Objective:** Ensure the bot retains its "memory" of liquidation clusters across restarts.
*   **Changes:**
    *   **`src/cluster_aggregator.py`:** Implement serialization (e.g., to `.pkl` or SQLite) of the `ClusterAggregator`'s internal state (bins, events_deque) every N minutes.
    *   **`executor_bot.py`:** On startup, attempt to load the `ClusterAggregator` state.
*   **Verification:**
    *   Restart the bot and verify that cluster information is loaded correctly.

## 2. Phase 2: Observability & Monitoring

This phase focuses on providing clearer insights into the bot's real-time operations and performance.

### 2.1. Refine Per-Pair Status Table (Rich + CSV)

*   **Objective:** Implement the detailed per-pair status table as suggested by the user, providing comprehensive real-time and historical insights.
*   **Changes:**
    *   **`src/status_tracker.py`:**
        *   Update `PairStatus` dataclass with new fields: `last_liq_ts`, `last_liq_age_s`, `events_in_window`, `cluster_vol_usdt`, `top_cluster_price`, `top_cluster_strength`, `support_band`, `resistance_band`, `imbalance_ratio`, `sweep_detected`, `rsi`, `rsi_state`, `signal`, `signal_confidence`, `open_position`, `position_size_usdt`, `position_entry`, `notes`.
        *   Modify `get_display_data()` to format these new columns for the `rich` table.
        *   Modify `save_snapshot_to_csv()` to include all new fields in the CSV output.
    *   **`executor_bot.py`:**
        *   Update `show_table()` to render the new columns.
        *   Update `periodic_save_status_snapshot()` to ensure all new data is captured.
*   **Verification:**
    *   The live terminal table displays all new columns with accurate, real-time data.
    *   `status_snapshot.csv` contains all new columns and data.

### 2.2. Export Prometheus Metrics

*   **Objective:** Provide structured, time-series metrics for external monitoring systems like Prometheus and Grafana.
*   **Changes:**
    *   **`src/metrics.py` (New File):** Define Prometheus metrics (Counters, Gauges) as suggested by the user (e.g., `liq_events_total`, `last_liq_ts`, `cluster_volume`, `top_cluster_strength`, `sweeps_total`, `signals_total`, `open_positions`, `cpu_pct`, `mem_pct`).
    *   **`executor_bot.py` / `src/ws_liquidation.py` / `src/cluster_aggregator.py` / `src/signal_generator.py`:** Instrument these modules to update the Prometheus metrics at relevant points (e.g., on new liquidation event, cluster update, signal generation, position change).
    *   **`docker-compose.yml`:** Add a Prometheus exporter service (e.g., `prometheus_client` for Python) and configure Prometheus to scrape these metrics.
*   **Verification:**
    *   Prometheus can successfully scrape metrics from the bot.
    *   Grafana dashboard (future step) displays relevant metrics.

### 2.3. Implement Structured Logs (JSON)

*   **Objective:** Facilitate easier analysis and integration with log aggregation systems.
*   **Changes:**
    *   **`logging` configuration:** Configure Python's `logging` module to output logs in JSON format for specific event types (e.g., signals, trades).
    *   **`signal_trackers.py` / `log_trade()`:** Modify this function to output signal events as a single JSON line to a dedicated `signals.log` file, including all suggested fields (`ts`, `symbol`, `mode`, `rsi`, `rsi_score`, `cluster_score`, `sweep`, `imbalance_ratio`, `confidence`, `reason`, `entry`, `stop`, `tp`, `position_usdt`).
*   **Verification:**
    *   `signals.log` contains well-formed JSON lines for each signal.

### 2.4. Real-time Health/Sync Metrics

*   **Objective:** Monitor the health and synchronization of data streams and the system clock.
*   **Changes:**
    *   **`src/metrics.py`:** Add Prometheus gauges for `ws_liq_active`, `ws_orderbook_active`, `ws_trades_active`, and `time_skew_s`.
    *   **`executor_bot.py` / WebSocket consumers:** Instrument these to update the active status of each WebSocket and the time skew.
*   **Verification:**
    *   Prometheus/Grafana displays real-time health metrics.

### 2.5. Phase 2.5: Risk Management Layer

This phase introduces critical safety circuits to protect capital.

#### 2.5.1. Risk Management Module

*   **Objective:** Implement a small, self-contained risk daemon to enforce safety limits.
*   **Changes:**
    *   **`src/risk_manager.py` (New File):**
        *   Monitors `max_concurrent_positions`.
        *   Caps `daily_loss_percentage`.
        *   Enforces `cooldowns` after a series of losses.
    *   **`executor_bot.py`:** Integrate the risk manager to check limits before placing orders and to update its state on position changes.
*   **Verification:**
    *   Simulated scenarios where risk limits are hit, verifying the bot correctly refrains from trading or takes corrective action.

### 2.6. Alerts (Initial Planning)

*   **Objective:** Identify critical events that require immediate notification.
*   **Changes:**
    *   **`src/alerts.py` (New File):** A simple module for sending alerts (e.g., via Slack/Discord webhook).
    *   **`executor_bot.py` / `src/ws_liquidation.py` / `src/risk_manager.py`:** Integrate alert triggers for:
        *   WS disconnect > N seconds.
        *   More than M consecutive failed order placements.
        *   Daily loss threshold exceeded (from `risk_manager.py`).
        *   Node CPU/mem high (via Prometheus alerts).
*   **Verification:**
    *   Test alerts triggered under controlled failure conditions.

### 3. Phase 3: Data Enrichment & Signal Refinement (Medium Priority)

This phase deepens the bot's market intelligence and improves signal quality.

### 3.1. Enrich Cluster Features

*   **Objective:** Provide more nuanced understanding of liquidation clusters.
*   **Changes:**
    *   **`src/cluster_aggregator.py` / `ClusterAggregator`:**
        *   Track `cluster_age`, `decay`, `cumulative_historical_volume` per bin.
        *   Compute `cluster_centroid` and `weighted_variance` for each cluster.
        *   Expose a `cluster_persistence_score` (0..1) combining size, recency, and price-distance.
    *   **`src/signal_generator.py`:** Incorporate these new cluster features into confidence scoring.
*   **Verification:**
    *   New cluster features are logged and reflected in the status table.
    *   Backtesting (with historical data) shows improved signal performance.

### 3.2. Sweep Detection Refinement

*   **Objective:** Make sweep detection more robust and indicative of actual liquidity hunts.
*   **Changes:**
    *   **`src/cluster_aggregator.py` / `is_sweep_detected()`:**
        *   Incorporate price *cross-through* speed and trade side at the moment of sweep.
        *   (Requires Orderbook data from Phase 1) Use orderbook delta at sweep to detect absorption vs aggressive sweep.
*   **Verification:**
    *   Backtesting shows more accurate sweep detection.

### 3.3. Signal Calibration

*   **Objective:** Tailor signal parameters to specific symbols.
*   **Changes:**
    *   **`src/config.py`:** Allow per-symbol configuration for `RSI_LENGTH`, `RSI_OVERSOLD`, `RSI_OVERBOUGHT`, confidence weights, etc.
    *   **`src/signal_generator.py`:** Adapt to use symbol-specific configurations.
*   **Verification:**
    *   Backtesting demonstrates improved performance when using symbol-specific calibrations.

### 3.4. RSI Pipeline Improvements

*   **Objective:** Improve RSI responsiveness and reduce latency.
*   **Changes:**
    *   **`executor_bot.py` / `fetch_ohlcv()`:** Implement a short-term candle builder that processes trade ticks to generate OHLCV data more frequently.
    *   **`src/signal_generator.py`:** Update RSI calculation to use these rolling updates, potentially integrating `TA-lib` or `pandas-ta` with live data ingestion.
*   **Verification:**
    *   RSI values update more frequently, leading to more responsive signals.

### 3.5. Signal Fusion Versioning

*   **Objective:** Facilitate easier backtesting and performance attribution for different signal scoring models.
*   **Changes:**
    *   **`src/config.py`:** Add a `SIGNAL_VERSION` parameter.
    *   **`signal_trackers.py` / `log_trade()`:** Include `SIGNAL_VERSION` in the JSON log output for each signal.
*   **Verification:**
    *   Signal logs clearly indicate the version of the signal fusion logic used.

### 4. Phase 4: Long-term Improvements & Advanced Features

This phase explores more advanced techniques and scalability.

### 4.1. ML/Regression Module for Weight Optimization

*   **Objective:** Dynamically optimize confidence scoring weights.
*   **Changes:**
    *   **`src/ml_optimizer.py` (New File):** Module for historical data analysis and weight optimization (e.g., using walk-forward optimization).
    *   **`signal_generator.py`:** Integrate optimized weights.
*   **Verification:**
    *   Improved backtest and live performance through adaptive weighting.

### 4.2. Multi-Symbol Rotation and Resource Scheduler

*   **Objective:** Efficiently manage capital and trading across multiple symbols.
*   **Changes:**
    *   **`src/scheduler.py` (New File):** Logic for capital allocation and symbol rotation based on perceived edge.
    *   **`executor_bot.py`:** Adapt main loop to interact with the scheduler.
*   **Verification:**
    *   Optimized capital utilization and higher overall PnL.

### 4.3. Premium Feed Integration

*   **Objective:** Access higher-fidelity data for improved signal quality (if budget allows).
*   **Changes:**
    *   **`src/ws_premium.py` (New File):** Module for integrating with services like CoinGlass or Kaiko.
    *   **`executor_bot.py`:** Integrate new data streams.
*   **Verification:**
    *   Demonstrably better signal generation and trading performance.

### 4.4. Parallelization Considerations

*   **Objective:** Prepare the architecture for scaling to multi-symbol operation and high-throughput data processing.
*   **Considerations:**
    *   Utilize `asyncio.gather()` judiciously for concurrent tasks.
    *   Explore offloading heavy feature computation to lightweight worker tasks via `asyncio.Queue`.
    *   Investigate shared memory stores (e.g., Redis or Ray) for inter-process communication and state coordination when scaling across multiple processes or machines.

### 5. System Architecture Diagram (Updated)

```mermaid
graph TD
    subgraph Data Acquisition
        BYBIT_WS[Bybit WebSocket] --> WS_LIQ(src/ws_liquidation.py)
        BYBIT_WS --> WS_ORDERBOOK(src/ws_orderbook.py)
        BYBIT_WS --> WS_TRADES(src/ws_trades.py)
        BYBIT_REST[Bybit REST API] --> FETCH_OHLCV(executor_bot.py:fetch_ohlcv)
        BYBIT_REST --> FETCH_OI(executor_bot.py:fetch_open_interest)
    end

    subgraph Core Processing
        WS_LIQ --> LIQ_QUEUE[Liquidation Event Queue]
        WS_ORDERBOOK --> ORDERBOOK_SNAPSHOT[Orderbook Snapshot]
        WS_TRADES --> TRADE_FLOW[Trade Flow Processor]

        LIQ_QUEUE --> CLUSTER_AGG(src/cluster_aggregator.py)
        CLUSTER_AGG --> CLUSTER_STATE[Cluster State (In-memory & Persistent)]
        CLUSTER_AGG --> STATUS_TRACKER[src/status_tracker.py]

        FETCH_OHLCV --> OHLCV_DF[OHLCV DataFrames]
        FETCH_OI --> OI_DATA[Open Interest Data]
    end

    subgraph Signal Generation & Execution
        OHLCV_DF --> SIGNAL_GEN(src/signal_generator.py)
        CLUSTER_STATE --> SIGNAL_GEN
        ORDERBOOK_SNAPSHOT --> SIGNAL_GEN
        TRADE_FLOW --> SIGNAL_GEN
        OI_DATA --> SIGNAL_GEN

        SIGNAL_GEN -- "Signal (with Confidence)" --> EXECUTOR_BOT(executor_bot.py)
        EXECUTOR_BOT -- "Place Order" --> ORDER_TRACKER[Order Tracker (Persistent)]
        ORDER_TRACKER -- "Order Status" --> BYBIT_REST
        EXECUTOR_BOT -- "Get Position" --> BYBIT_REST
        EXECUTOR_BOT -- "Get Balance" --> BYBIT_REST
    end

    subgraph Monitoring & Ops
        STATUS_TRACKER -- "Rich Table Data" --> RICH_LIVE[Rich Live Terminal]
        STATUS_TRACKER -- "CSV Snapshot" --> STATUS_CSV[status_snapshot.csv]
        SIGNAL_GEN -- "Metrics Update" --> PROMETHEUS_EXPORTER[src/metrics.py]
        SIGNAL_GEN -- "JSON Log" --> SIGNALS_LOG[signals.log]
        EXECUTOR_BOT -- "Alert Trigger" --> ALERTS[src/alerts.py]
    end

    subgraph Testing & Validation
        SIM_EVENTS_GEN[src/sim_events_generator.py] --> LIQ_QUEUE
        TEST_SUITE[Python Tests] --> SIM_EVENTS_GEN
        TEST_SUITE --> CLUSTER_AGG
        TEST_SUITE --> SIGNAL_GEN
    end
```

### 6. Prioritized Short Checklist (Next 7 Days - Actionable Development Tasks)

Based on the user's feedback, here's a prioritized list of concrete actions for the immediate future:

1.  **Integrate Orderbook & Trade Streams:**
    *   Create `src/ws_orderbook.py` and `src/ws_trades.py` to subscribe to Bybit public Orderbook (depth 50) and Trade WebSockets.
    *   Implement data normalization and feed into `executor_bot.py`.
    *   Modify `signal_generator.py` to accept and utilize these new features (e.g., `imbalance_ratio`).
2.  **Refine Per-Pair Status Table & Prometheus Metrics:**
    *   Update `src/status_tracker.py` to include all suggested columns (`last_liq_ts`, `last_liq_age_s`, `events_in_window`, `cluster_vol_usdt`, `top_cluster_price`, `top_cluster_strength`, `support_band`, `resistance_band`, `imbalance_ratio`, `sweep_detected`, `rsi`, `rsi_state`, `signal`, `signal_confidence`, `open_position`, `position_size_usdt`, `position_entry`, `notes`).
    *   Modify `executor_bot.py`'s `show_table` to render these columns.
    *   Ensure `status_snapshot.csv` accurately reflects all new columns.
    *   Create `src/metrics.py` and instrument `executor_bot.py`, `ws_liquidation.py`, `cluster_aggregator.py`, `signal_generator.py` to export Prometheus metrics (`liq_events_total`, `last_liq_ts`, `cluster_volume`, `top_cluster_strength`, `sweeps_total`, `signals_total`, `open_positions`, `cpu_pct`, `mem_pct`).
3.  **Align Freshness & Sliding Windows:**
    *   In `src/config.py`, set `MAX_LIQUIDATION_DATA_LATENCY_SECONDS = SLIDING_WINDOW_S` (e.g., 3600s).
4.  **Implement Synthetic Event Replayer:**
    *   Create `src/sim_events_generator.py` for replaying historical and generating synthetic liquidation events.
    *   Develop initial integration tests that use this replayer to verify cluster aggregation and sweep detection.
5.  **Add Execution Guards:**
    *   Implement idempotent order placement using `client_order_id` and a persistent order tracker.
    *   Add post-fill verification logic (polling exchange for 3 seconds) in `place_order` in `executor_bot.py`.
6.  **Add Structured Signal Logs (JSON):**
    *   Configure logging to write signal events as JSON to `signals.log` with the specified fields.
7.  **Unit/Integration Tests for Cluster Aggregator and Sweep Detection:**
    *   Expand `tests/test_cluster_aggregator.py` to cover new features and edge cases for cluster ingestion, expiry, and sweep detection.
8.  **Implement Persistence for ClusterAggregator State:**
    *   Add logic to `src/cluster_aggregator.py` to periodically serialize its state to disk (e.g., `.pkl` or SQLite).
    *   Modify `executor_bot.py` to load this state on startup.
9.  **Implement Risk Management Layer:**
    *   Create `src/risk_manager.py` to monitor `max_concurrent_positions`, `daily_loss_percentage`, and `cooldowns`.
    *   Integrate `risk_manager.py` into `executor_bot.py` to enforce these safety limits.

This plan aims to systematically address the user's feedback, building a more robust and intelligent trading bot.