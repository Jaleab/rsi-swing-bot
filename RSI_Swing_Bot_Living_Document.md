# 📈 RSI Swing Bot – Living Document

*A complete record of strategy design, backtesting, testing, and deployment roadmap. Updated as the system evolves.*

---

## 0 — High-level summary

*   **Goal:** augment RSI swing strategy with (A) real-time liquidation events → aggregated into clusters (support/resistance bands), (B) “liquidity sweep” detection for high-probability entries, and (C) fallback/confidence rules so we don’t miss entries. The bot also provides a live terminal status dashboard using `rich` and persists key metrics to CSV.
*   **Feeds:** Bybit `allLiquidation` WebSocket (primary), plus Pybit HTTP for OHLCV. (Note: Bybit `allLiquidation` stream is known to be very sparse/unreliable, leading to infrequent events).
*   **Modes:** `SIM_MODE` (paper on live mainnet data) and `LIVE_MODE` (real orders). Bot must support both.
*   **Backtest:** reconstruct clusters from historical liquidation records and replay events during backtest to simulate real-time cluster snapshots.
*   **Resilience:** in-memory cluster snapshot, async loops (market loop + liquidity loop), local persistence for dedup & recovery.
*   **Deliverables:** new modules + updated backtest/gridsearch integration + Docker & monitoring updates + live terminal status display.

---

## 1.1 — System Architecture

```
         ┌────────────────┐
         │ Bybit Exchange │
         └───────┬────────┘
                 │  OHLCV + WebSocket Liquidations
     ┌───────────┴──────────┐
     │ Data Acquisition      │
     │ fetch_ohlcv + ws_cons │
     └───────────┬──────────┘
                 │ normalized data
         ┌───────┴────────┐
         │ ClusterAggregator│
         │ (impact, sweep)  │
         └───────┬────────┘
                 │
     ┌───────────┴──────────┐
     │ SignalGenerator       │
     │ (RSI + Cluster logic) │
     └───────────┬──────────┘
                 │
          ┌──────┴──────┐
          │ Logging + CSV│
          │  + Monitoring│
          └──────────────┘
```

---

## 1.2 — Repo layout (proposed)

```
rsi_swing_bot/
│── README.md
│── .env.example
│── requirements.txt
│── docker-compose.yml
│── Dockerfile
│── src/
│   ├── __main__.py
│   ├── config.py
│   ├── fetch_candles.py
│   ├── ws_liquidation.py        # websocket feed handlers (Bybit/Binance)
│   ├── cluster_aggregator.py    # cluster building + sliding window
│   ├── rsi_calc.py
│   ├── signal_generator.py
│   ├── executor_bot.py
│   ├── sim_engine.py            # sim mode helpers for live data (paper trades)
│   ├── backtest/
│   │    ├── backtest_engine.py
│   │    └── cluster_reconstruction.py  # from historical liquidation records
│   ├── storage.py               # DB or local file wrappers (sqlite/postgres)
│   └── monitor.py
│── tests/
│   ├── test_cluster.py
│   ├── test_ws.py
│   └── test_signal_generator.py
│── docs/
│   └── architecture.md
```

---

## 2 — Configuration (config.py)

Define all parameters in one place and load from `.env` or JSON:

```python
# config.py (example)
class Config:
    EXCHANGE = "bybit"
    SYMBOL = "SOL/USDT"
    TIMEFRAME = "1m"
    SIM_MODE = True            # True = simulate paper trades on live data
    POSITION_USDT = 10
    RSI_LENGTH = 18
    RSI_OVERSOLD = 34
    RSI_OVERBOUGHT = 77

    # Trade Sizing
    RISK_PER_TRADE = 0.005     # 0.5% of account balance per trade

    # Cluster params
    BIN_MODE = "percent"       # "absolute" or "percent"
    BIN_PCT = 0.002            # 0.2% of price per bin (used for dynamic bins)
    BIN_ABS = 0.5              # fallback absolute USD per bin
    SLIDING_WINDOW_S = 300     # seconds to aggregate liquidations (e.g., 5 minutes)
    SWEEP_THRESHOLD_FACTOR = 2.0  # sweep detection threshold vs average cluster volume
    MIN_SWEEP_VOLUME_USDT = 50.0  # ensure sweeps have minimum economic size

    # Confidence weights
    W_RSI = 0.50
    W_CLUSTER = 0.30
    W_SWEEP = 0.15
    W_PROX = 0.05

    COOLDOWN_SECONDS = 60
    MAX_OPEN_POSITIONS = 1
```

All numeric defaults included — the gridsearch will later tweak these.

---

## 3 — WebSocket feed handler (ws_liquidation.py)

**Goal:** subscribe to Bybit and Binance liquidation streams; emit normalized events to an asyncio queue.

**Event schema:**

```py
LiquidationEvent = {
  "exchange": "bybit" | "binance",
  "symbol": "SOL/USDT",
  "timestamp": 1670000000000,
  "price": 123.45,              # liquidation price
  "qty": 0.25,
  "qty_usdt": 30.86,            # computed: qty * price
  "side": "LONG" | "SHORT",     # which side got liquidated
  "order_id": "..."             # optional
}
```

**Responsibilities:**

*   Connect, subscribe, parse payloads, normalize to `LiquidationEvent`.
*   Deduplicate using `order_id`/timestamp (store last seen ids in memory or small LRU).
*   Handle reconnects with exponential backoff, resume logic.
*   Push events to `asyncio.Queue` for `cluster_aggregator` to consume.

**Pseudo skeleton:**

```python
async def bybit_ws_consumer(queue, config):
    async with websockets.connect(BYBIT_WS_URL) as ws:
        await ws.send(subscribe_message_for_allLiquidation(config.SYMBOLS))
        async for msg in ws:
            ev = parse_bybit_msg(msg)
            normalized = normalize_bybit_event(ev)
            await queue.put(normalized)
```

(Implement same for Binance `forceOrder` stream.)

---

## 4 — Cluster aggregator (cluster_aggregator.py)

**Goal:** maintain an in-memory “cluster map” (price bin → rolling aggregate of liquidation volume) updated in real-time. Expose fast lookup APIs for `signal_generator`.

**Design choices:**

*   Use dynamic bins => `bin_size = BIN_PCT * current_price` OR absolute `BIN_ABS`.
*   Sliding window: keep events for last `SLIDING_WINDOW_S` seconds and compute sums per bin.
*   Maintain both `total_volume_usdt` per bin and `event_count`.
*   Keep a ring buffer or deque of events for efficient windowing.
*   Optionally persist aggregated snapshot every N seconds to Redis/SQLite for recovery.

**Data structures:**

```py
class ClusterAggregator:
    bins: Dict[int, {'volume': float, 'last_update': ts, 'events': int}]
    events_deque: Deque[LiquidationEvent] # ordered by timestamp
```

**Key functions:**

```py
def price_to_bin(price, bin_size) -> int:
    return int(price // bin_size)

def ingest(event):
    bin_idx = price_to_bin(event.price, bin_size_for(event.price))
    bins[bin_idx].volume += event.qty_usdt
    events_deque.append(event)
    expire_old_events()

def expire_old_events():
    while events_deque and events_deque[0].timestamp < now - SLIDING_WINDOW_S:
        old = events_deque.popleft()
        bins[price_to_bin(old.price)].volume -= old.qty_usdt
        if bins[bin_idx].volume <= 0: del bins[bin_idx]
```

**Queries required by signal generator:**

*   `get_bin_strength_at(price)` → returns normalized strength (volume) and percentile relative to recent bin strengths.
*   `get_top_n_clusters(n, side=None)` → return list of top clusters with volume and centroid price.
*   `is_sweep_detected(bin_idx, last_T_seconds)` → check if a spike occurred: `recent_volume > avg_volume * SWEEP_THRESHOLD_FACTOR AND recent_volume >= MIN_SWEEP_VOLUME_USDT`.

**Normalization:** compute mean/median of bin volumes over sliding window; cluster strength = `volume / median_volume`. A cluster is "strong" if > `cluster_strength_threshold` (e.g., 1.5x median).

---

## 5 — Sweep detection (important to detect "liquidity hunts")

**Approach:** A sweep should be a short, concentrated burst of liquidation volume at a bin while price touches or crosses that bin. Implement:

*   Maintain `bin_time_series` for last `Config.SWEEP_WINDOW_S` seconds (e.g., 30s).
*   Compute `recent_volume = sum(volume in bin in last Config.SWEEP_WINDOW_S)`.
*   Compute `historical_avg = avg(volume in bin in historical window e.g., last Config.HISTORICAL_WINDOW_S, excluding recent window)`.
*   Sweep if `recent_volume >= max(Config.MIN_SWEEP_VOLUME_USDT, historical_avg * Config.SWEEP_THRESHOLD_FACTOR)`.

Also detect "price-touch" condition: check recent price tick history whether price entered the bin within last `SWEEP_WINDOW` seconds.

Return boolean and `sweep_volume_usdt`.

---

## ⚙️ Liquidation Data: Storage, Processing, and Signal Integration

### 1. **Data Ingestion & Storage**

| Component                 | File                        | Function                                                                                                                                     |
| ------------------------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **WebSocket Handler**     | `src/ws_liquidation.py`     | Connects to Bybit’s liquidation stream, normalizes incoming JSON events, and places them into a global `asyncio.Queue`.                      |
| **Cluster Consumer Loop** | `src/cluster_aggregator.py` | Continuously dequeues liquidation events, calls `ClusterAggregator.ingest(event)`, and updates price-bin state for the corresponding symbol. |
| **Event Persistence**     | (optional via `storage.py`) | Each processed liquidation event can be persisted to CSV or SQLite for backtesting or analysis.                                              |

**Key fields stored per event:**

```python
{
    "symbol": "SOLUSDT",
    "price": 229.75,
    "side": "Sell",
    "volume": 25000,
    "timestamp": 1759560784123
}
```

Each new event updates:

*   The rolling window of recent liquidations
*   The cumulative volume per price bin
*   `last_event_timestamp[symbol]` (used for freshness checks)

---

### 2. **Cluster Formation & Retrieval**

The `ClusterAggregator` aggregates liquidation events into **price clusters**:

*   **Aggregation Logic:**
    Events within a configurable bin size (e.g., `$1.0` width) are merged.
*   **Window Management:**
    Old events beyond `Config.SLIDING_WINDOW_S` are pruned.
*   **Output Snapshot:**
    `get_snapshot(symbol)` returns:

    ```python
    {
        "clusters": [...],         # All active clusters
        "top_clusters": [...],     # Ranked by normalized volume
        "median_volume": float,
        "timestamp": int
    }
    ```
*   **Sweep Detection:**
    `is_sweep_detected()` flags short bursts of liquidation activity exceeding a dynamic threshold.

---

### 3. **Integration into Signal Generation**

Within `executor_bot.py` (main loop):

1.  **Freshness Check:**
    Before every tick, the bot verifies:

    ```python
    now - last_event_timestamp[symbol] < Config.MAX_LIQUIDATION_DATA_LATENCY_SECONDS
    ```

    If false → logs warning → enters `RSI_ONLY` fallback mode.

2.  **Data Retrieval:**
    Fetches from `ClusterAggregator`:

    ```python
    cluster_snapshot, sweep_volume = aggregator.get_snapshot(symbol), aggregator.is_sweep_detected(bin_idx, current_price, symbol)
    ```

3.  **Signal Decision:**
    Passes this to:

    ```python
    trading_signal = signal_generator.decide(
        current_price=current_price,
        rsi_df=ohlcv_df, # Pass the entire DataFrame for RSI calculation
        cluster_snapshot=cluster_snapshot,
        sweep_volume=sweep_volume,
        is_liquidation_data_available=(not use_rsi_only_fallback)
    )
    ```

---

### 4. **Signal Logic & Confidence Scoring**

Inside `signal_generator.decide()`:

| Factor                   | Description                                                | Weight             |
| ------------------------ | ---------------------------------------------------------- | ------------------ |
| **RSI Score**            | Derived from overbought/oversold thresholds                | `Config.W_RSI`     |
| **Cluster Impact Score** | Based on normalized cluster strength and proximity         | `Config.W_CLUSTER` |
| **Sweep Volume**         | Boosts confidence when strong sweeps detected              | `Config.W_SWEEP`   |
| **Proximity Score**      | Increases confidence when price is near a cluster centroid | `Config.W_PROX`    |

The final composite confidence is calculated as:

```python
confidence_score = (
    Config.W_RSI * rsi_score +
    Config.W_CLUSTER * cluster_impact_score +
    Config.W_SWEEP * sweep_score +
    Config.W_PROX * proximity_score
)
```

Where:
*   `rsi_score` is 1.0 if an RSI signal is present, else 0.0.
*   `cluster_impact_score` is a value between 0.0 and 1.0, combining cluster strength and proximity.
*   `sweep_score` is 1.0 if a sweep is detected, else 0.0.
*   `proximity_score` is derived from cluster calculations.

**Confidence Level Interpretation:**

| Confidence Range | Level    |
| :--------------- | :------- |
| `0.0 - 0.5`      | Low      |
| `0.5 - 0.75`     | Medium   |
| `> 0.75`         | High     |

Decision rules (simplified):

| Signal Type      | Conditions                                                        |
| ---------------- | ----------------------------------------------------------------- |
| **Strong LONG**  | RSI < `Config.RSI_OVERSOLD` **and** (`cluster_impact_score ≥ Config.CONFIDENCE_HIGH_THRESHOLD` or `sweep_volume` detected) |
| **Medium LONG**  | RSI < `Config.RSI_OVERSOLD` **and** `cluster_impact_score ≥ 0.5`  |
| **Strong SHORT** | RSI > `Config.RSI_OVERBOUGHT` **and** (`cluster_impact_score ≥ Config.CONFIDENCE_HIGH_THRESHOLD` or `sweep_volume` detected) |
| **Medium SHORT** | RSI > `Config.RSI_OVERBOUGHT` **and** `cluster_impact_score ≥ 0.5` |
| **Neutral**      | Otherwise — no action                                             |

Each signal is logged to `trade_log.csv` along with `reason` (e.g., `"RSI+CLUSTER_HIGH_IMPACT"`, `"RSI_ONLY"`).

---

### 6. **Stop Loss & Take Profit Logic**

The bot now implements **dynamic Stop Loss (SL) and Take Profit (TP)** levels, leveraging liquidation cluster data for more adaptive risk management. This logic includes a robust **fallback mechanism** to fixed percentages if dynamic values are unavailable or invalid.

**Mechanism:**
*   When `Config.USE_DYNAMIC_SLTP` is enabled, the bot attempts to set SL/TP based on `support_band` and `resistance_band` derived from the `ClusterAggregator`.
*   Buffers (`Config.SL_BUFFER`, `Config.TP_BUFFER`) are applied to these bands to define the final SL/TP prices.
*   Hard minimum/maximum percentages (`Config.MIN_STOP_LOSS_PERCENT`, `Config.MAX_TAKE_PROFIT_PERCENT`) ensure that SL/TP levels remain within reasonable bounds, even if cluster data is sparse or leads to extreme values.
*   If dynamic bands are unavailable or result in invalid prices, the bot gracefully falls back to fixed percentage-based SL/TP from `Config.STOP_LOSS_PERCENT` and `Config.TAKE_PROFIT_PERCENT`.

**Conceptual Diagram of Dynamic SL/TP:**
```
Current Price (CP)
  ▲
  │
  │   Resistance Band (RB)
  │   ├─── TP = RB ± Config.TP_BUFFER
  │
  │
  │   Support Band (SB)
  │   ├─── SL = SB ± Config.SL_BUFFER
  ▼
```

**New Configuration Parameters (in `src/config.py`):**

```python
# --- Dynamic SL/TP Parameters ---
USE_DYNAMIC_SLTP = False # Enable dynamic stop loss/take profit based on clusters
SL_BUFFER = 0.005 # 0.5% buffer around support/resistance for stop loss
TP_BUFFER = 0.005 # 0.5% buffer around support/resistance for take profit
MIN_STOP_LOSS_PERCENT = 0.01 # 1% as a hard minimum for dynamic SL
MAX_TAKE_PROFIT_PERCENT = 0.05 # 5% as a hard maximum for dynamic TP

# Fallback fixed percentages
STOP_LOSS_PERCENT = 0.035 # 3.5%
TAKE_PROFIT_PERCENT = 0.05 # 5%
```

---

### 7. **Cluster Aggregator State Persistence**

To ensure the bot retains its "memory" of liquidation clusters across restarts, the `ClusterAggregator` will be enhanced with state persistence. This is crucial for maintaining the continuity of market insight derived from liquidation heatmaps.

**Mechanism:**
*   **Serialization**: The internal state of the `ClusterAggregator` (including aggregated `bins` and `events_deque`) will be serialized to a JSON file. During serialization, `events_deque` will be converted to a `list` for compatibility.
*   **Storage**: The serialized state will be stored in the `Config.DATA_DIR` (e.g., `/app/data/cluster_state.json`).
*   **Loading**: Upon bot startup, the `ClusterAggregator` will attempt to load its state from this file, allowing it to resume with its accumulated knowledge of liquidation clusters. The loaded `list` of events will be converted back to a `deque`.
*   **Saving**: The state will be saved periodically during bot operation (`Config.CLUSTER_PERSISTENCE_INTERVAL_S`) and, critically, during a graceful shutdown of the bot.

**Persistence and Restart:**
*   The implemented persistence ensures that the aggregated `cluster_snapshot` (containing `bins`, `volumes`, `support_band`, `resistance_band`, etc.) is preserved across bot restarts. This allows the bot to maintain its "market memory" of liquidation clusters without having to rebuild it from scratch.
*   While the `ClusterAggregator`'s state is preserved, raw WebSocket stream may have temporary gaps or missed events during a restart. The bot's existing logic (e.g., `MAX_LIQUIDATION_DATA_LATENCY_SECONDS` check) is designed to gracefully handle such missing events and fall back to RSI-only mode if the data stream becomes too stale.
*   If historical events were lost or not persisted to CSV, the `ClusterAggregator` will rebuild its state solely from new incoming WebSocket events after a restart.

**New Configuration Parameters (in `src/config.py`):**
```python
# --- Cluster Persistence ---
PERSIST_CLUSTERS: bool = True # Enable/disable cluster state persistence
CLUSTER_STATE_FILE: str = "cluster_state.json" # Filename for saving cluster state
CLUSTER_PERSISTENCE_INTERVAL_S: int = 300 # How often to save cluster state in seconds (e.g., 5 minutes)
```

### 8. **In Summary**

| Aspect                 | Role                                                     | Source                    |
| ---------------------- | -------------------------------------------------------- | ------------------------- |
| **Liquidation Data**   | Increases signal confidence & provides context           | `ws_liquidation.py`       |
| **Cluster Aggregator** | Groups liquidations into dynamic zones                   | `cluster_aggregator.py`   |
| **Signal Generator**   | Fuses RSI + Cluster + Sweep → Confidence Score           | `signal_generator.py`     |
| **Stop/TP Logic**      | Dynamic, based on liquidation clusters & fixed % fallback| `config.py` / `executor_bot.py` |
| **Dry Run Storage**    | Signals saved to `trade_log.csv` with reasons            | `signal_stats_tracker.py` |

---

## 9. **Live Status Dashboard (Rich Library)**

The bot now includes a live, real-time status dashboard displayed directly in the terminal, powered by the `rich` library. This dashboard provides immediate insights into the bot's operation and the status of liquidation event processing for each tracked trading pair.

**Key Features:**

*   **Real-time Updates:** The table refreshes every second, showing the latest metrics without cluttering the terminal.
*   **Per-Symbol Metrics:** Displays information for each configured symbol, including:
    *   `Last Update`: Timestamp of the last processed liquidation event.
    *   `Events`: Total number of liquidation events processed for the symbol within the sliding window.
    *   `Cluster Vol`: Aggregated volume of active liquidation clusters.
    *   `Active Bins`: Number of price bins currently holding liquidation volume.
    *   `Status`: Indication of WebSocket stream activity (`Active`, `Low Activity`, `No Data`).
    *   `Notes`: Additional context regarding stream status (e.g., "Awaiting events", "Slightly stale").
*   **Persistent Snapshot:** In addition to the live display, a `status_snapshot.csv` file is periodically saved to `Config.DATA_DIR`, capturing the same key metrics for historical analysis.

**Implementation Details:**

*   **`src/status_tracker.py`**: Defines the `PairStatus` dataclass to hold per-symbol metrics and the `StatusTracker` class to manage these states and format data for `rich` display.
*   **`executor_bot.py`**: Integrates `rich.Live` to render the dynamic table and schedules `show_table` and `periodic_save_status_snapshot` as background tasks.
*   **`src/cluster_aggregator.py`**: Updates the `StatusTracker` with current metrics as liquidation events are processed.

**Important Note on Bybit Liquidation Data Sparsity:**

It is crucial to understand that the Bybit `allLiquidation` WebSocket stream is often very sparse, especially for less volatile assets or during quiet market periods. This means that the "Events" and "Cluster Vol" metrics in the live table may frequently show zero or low values, and the "Status" might often indicate "No Data" or "Low Activity". This behavior is expected and reflects the actual rate of liquidation events on the exchange, not a malfunction of the bot's data processing or display capabilities.