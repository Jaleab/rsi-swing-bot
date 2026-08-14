# RSI Swing Bot

An automated cryptocurrency swing trading bot that combines **RSI momentum signals** with **real-time liquidation cluster analysis** on Bybit. The bot detects high-probability entry zones by aggregating liquidation events into price clusters, identifying liquidity sweeps, and fusing multiple signal factors into a confidence-weighted score.

## How It Works

The bot ingests real-time liquidation data via Bybit WebSocket feeds and groups them into dynamic price bins (clusters). When RSI signals align with strong liquidation clusters or detected liquidity sweeps, the bot generates weighted trade signals:

| Factor | Description | Weight |
|---|---|---|
| **RSI** | Oversold/overbought momentum signal | 50% |
| **Cluster Impact** | Normalized liquidation cluster strength near price | 30% |
| **Sweep** | Short burst of liquidation volume exceeding threshold | 15% |
| **Proximity** | Price distance to nearest cluster centroid | 5% |

Signals are classified as **Strong**, **Medium**, or **Neutral** based on composite confidence scores, with dynamic stop-loss/take-profit levels derived from cluster support/resistance bands.

## Architecture

```
         +----------------+
         | Bybit Exchange |
         +-------+--------+
                 |  OHLCV + WebSocket Liquidations
     +-----------+-----------+
     | Data Acquisition       |
     | fetch_ohlcv + ws_cons  |
     +-----------+-----------+
                 | normalized data
         +-------+--------+
         | ClusterAggregator|
         | (impact, sweep)  |
         +-------+--------+
                 |
     +-----------+-----------+
     | SignalGenerator        |
     | (RSI + Cluster logic)  |
     +-----------+-----------+
                 |
          +------+------+
          | Executor Bot |
          | (positions)  |
          +------+------+
                 |
          +------+------+
          | Monitoring   |
          | Rich + CSV   |
          +-------------+
```

## Features

- **Multi-factor signal generation** — RSI + liquidation clusters + sweep detection + proximity scoring
- **Real-time liquidation feed** — Bybit WebSocket with automatic reconnection and exponential backoff
- **Dynamic SL/TP** — Stop-loss and take-profit levels derived from cluster support/resistance bands, with fixed-percentage fallback
- **Live terminal dashboard** — Per-symbol status table powered by `rich`, refreshing every second
- **Prometheus + Grafana** — Full observability stack out of the box
- **Simulation mode** — Paper trade on live data without risking capital (`SIM_MODE=true`)
- **Backtesting & grid search** — Historical replay with parameter optimization scripts
- **Cluster state persistence** — Cluster aggregator state survives bot restarts via JSON serialization
- **Risk management guards** — Max positions, drawdown limits, cooldowns, and trade sizing controls
- **TradingView Pine Script** — Companion indicator for cross-validation on charts

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for containerized deployment)
- Bybit testnet API keys ([get them here](https://testnet.bybit.com))

### 1. Clone and configure

```bash
git clone https://github.com/Jaleab/rsi-swing-bot.git
cd rsi-swing-bot
cp .env.example .env
```

Edit `.env` with your Bybit testnet API credentials:

```env
BYBIT_API_KEY=your_testnet_key
BYBIT_API_SECRET=your_testnet_secret
BYBIT_SYMBOLS=SOL/USDT
BYBIT_TESTNET=true
SIM_MODE=true
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run locally

```bash
# Paper trading (simulation)
python executor_bot.py --sim

# Live on testnet
python executor_bot.py
```

### 4. Deploy with Docker (recommended for 24/7 operation)

```bash
docker compose up -d
```

This starts three services:

| Service | Port | Description |
|---|---|---|
| `bot` | 8000 | RSI Swing Bot (Prometheus metrics endpoint) |
| `prometheus` | 9090 | Time-series metrics collection |
| `grafana` | 3000 | Dashboards (admin/admin) |

### 5. Deploy to a VPS

```bash
bash deploy.sh
```

Installs Docker if needed, clones the repo, builds, and starts all services with auto-restart on crash or reboot.

## Project Structure

```
rsi-swing-bot/
├── executor_bot.py          # Main entry point & market loop
├── backtest_rsi.py          # Backtest runner
├── grid_search.py           # Parameter optimization
├── monitor.py               # Docker container watchdog
├── fetch_candles.py         # OHLCV data fetching
├── generate_report.py       # Performance reporting
├── filter_logs.py           # Log analysis
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── deploy.sh
├── .env.example
├── pytest.ini
├── rsi_swing_strategy.pine  # TradingView Pine Script companion
├── src/
│   ├── config.py            # All parameters in one place
│   ├── ws_liquidation.py    # WebSocket liquidation feed
│   ├── cluster_aggregator.py # Price cluster building & sweep detection
│   ├── signal_generator.py  # Multi-factor signal fusion
│   ├── status_tracker.py    # Live terminal status data
│   ├── signal_tracker.py    # Trade logging
│   ├── metrics_exporter.py  # Prometheus metrics
│   ├── position_manager.py  # Position lifecycle
│   ├── position.py          # Position data model
│   ├── guards.py            # Risk guardrails
│   ├── bybit_exchange.py    # Bybit REST/WS client
│   ├── event_stream.py      # Event stream orchestration
│   ├── sim_events_generator.py # Synthetic event replay for sim mode
│   ├── execution/
│   │   └── paper_trader.py  # Paper trading engine
│   ├── analysis/
│   │   └── signal_quality_tracker.py
│   ├── monitor/
│   │   └── simple_monitor.py
│   └── backtest/
│       └── backtester.py    # Backtest engine
├── tests/
│   ├── test_cluster.py
│   ├── test_signal_generator.py
│   └── ...
├── monitoring/
│   ├── prometheus/
│   └── grafana/
├── docs/
├── SOLUSDT_1h.csv           # Sample historical data
└── SOLUSDT_4h.csv
```

## Configuration

All parameters are centralized in `src/config.py` and overridable via `.env`. Key settings:

| Parameter | Default | Description |
|---|---|---|
| `SYMBOLS` | `SOL/USDT` | Trading pairs (semicolon-delimited) |
| `SIM_MODE` | `true` | Paper trading on live data |
| `RSI_LENGTH` | `18` | RSI lookback period |
| `RSI_OVERSOLD` | `34` | Oversold threshold |
| `RSI_OVERBOUGHT` | `77` | Overbought threshold |
| `RISK_PER_TRADE` | `0.005` | 0.5% of balance per trade |
| `SLIDING_WINDOW_S` | `300` | Cluster aggregation window (seconds) |
| `BIN_PCT` | `0.002` | Dynamic bin size (0.2% of price) |
| `SWEEP_THRESHOLD_FACTOR` | `2.0` | Sweep detection sensitivity |
| `STOP_LOSS_PERCENT` | `0.035` | Fixed SL fallback (3.5%) |
| `TAKE_PROFIT_PERCENT` | `0.05` | Fixed TP fallback (5%) |
| `MAX_OPEN_POSITIONS` | `1` | Concurrent position limit |

## Monitoring

The bot exports Prometheus metrics on port `8000` and includes a pre-configured Grafana dashboard:

- **Liquidation events** — total count, per-symbol event rates
- **Cluster metrics** — volume, active bins, top cluster strength
- **Signal metrics** — direction, confidence, total signals generated
- **Position metrics** — open count, unrealized/realized PnL, entry/SL/TP levels
- **System health** — CPU, memory, WebSocket connection status, time skew

## Backtesting

```bash
# Run backtest on historical data
python backtest_rsi.py

# Grid search for optimal parameters
python grid_search.py
```

Historical data files (`SOLUSDT_1h.csv`, `SOLUSDT_4h.csv`) are included for SOL/USDT.

## Testing

```bash
pytest
```

Tests cover cluster aggregation, signal generation, and sweep detection logic. CI runs via GitHub Actions (`.github/workflows/tests.yml`).

## TradingView Companion

`rsi_swing_strategy.pine` provides a simplified Pine Script v5 implementation for visual cross-validation on TradingView charts. It mirrors the core RSI entry/exit logic with configurable SL/TP percentages.

## Disclaimer

This software is for educational and research purposes only. Cryptocurrency trading involves substantial risk of loss. The authors are not responsible for any financial losses incurred from using this bot. Always start with `SIM_MODE=true` and testnet before considering live trading. Past backtest results do not guarantee future performance.

## License

See repository for license details.
