# RSI Swing Bot Runbook

This document provides a comprehensive guide on how to set up, run, and monitor the RSI Swing Bot in both dry run (simulated) and live trading modes.

## 1. Prerequisites

Before running the bot, ensure you have the following:

*   **Python 3.8+**: Installed on your system.
*   **Git**: For cloning the repository.
*   **Docker (Optional but Recommended)**: For containerized deployment.
*   **Bybit API Keys**: An API key and API secret from Bybit. Create separate keys for testnet and mainnet, and ensure they have the necessary permissions (e.g., read-only for market data, trade permissions for live trading).

## 2. Setup

1.  **Clone the Repository**:
    ```bash
    git clone <repository_url>
    cd rsi_swing_bot
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -e .
    ```
    (This assumes you have a `setup.py` or `pyproject.toml` configured for editable installs. If not, use `pip install -r requirements.txt`).

3.  **Configure Environment Variables (`.env`)**:
    Create a file named `.env` in the root directory of the project. This file will store your sensitive API keys and other environment-specific settings.

    Example `.env` content:
    ```
    BYBIT_API_KEY_MAINNET=YOUR_MAINNET_API_KEY
    BYBIT_API_SECRET_MAINNET=YOUR_MAINNET_API_SECRET
    BYBIT_API_KEY_TESTNET=YOUR_TESTNET_API_KEY
    BYBIT_API_SECRET_TESTNET=YOUR_TESTNET_API_SECRET
    BYBIT_SYMBOLS=BTC/USDT,ETH/USDT # Comma-separated list of symbols
    DRY_RUN=true # Set to 'true' for dry run mode, 'false' for live trading
    SAVE_DATA=true # Set to 'true' to save data to CSV, 'false' otherwise
    DATA_DIR=./data # Directory to save CSV data
    ```
    **Important**: Never commit your `.env` file to version control.

4.  **Configure Bot Settings (`src/config.py`)**:
    Open `src/config.py` to adjust bot parameters.

    *   **`Config.TESTNET`**: Set to `True` to connect to Bybit testnet, or `False` for mainnet. This automatically selects the corresponding API keys from your `.env` file.
    *   **`Config.SIM_MODE` / `Config.LIVE_MODE`**:
        *   For **Dry Run (Simulated) Mode**: Set `Config.SIM_MODE = True` and `Config.LIVE_MODE = False`.
        *   For **Live Trading Mode**: Set `Config.SIM_MODE = False` and `Config.LIVE_MODE = True`.
        *   **Crucially, only ONE of these can be `True` at any given time.**
    *   **`Config.DRY_RUN`**: This flag in `src/config.py` (or `DRY_RUN=true` in `.env`) controls the dry run behavior when `SIM_MODE` is `True`. When `DRY_RUN` is `True` and `SIM_MODE` is `True`, the bot will simulate trades and PnL without placing actual orders.
    *   **`Config.SYMBOLS`**: This should match the `BYBIT_SYMBOLS` in your `.env` file.
    *   **`Config.RISK_PER_TRADE_PERCENT`**: Adjust your risk per trade for dynamic position sizing (e.g., `0.005` for 0.5% of available balance).
    *   **`Config.DEFAULT_POSITION_SIZE_USDT` / `Config.MIN_POSITION_SIZE_USDT`**: Set fallback and minimum position sizes.
    *   Review other parameters like `RSI_LENGTH`, `STOP_LOSS_PERCENT`, `TAKE_PROFIT_PERCENT`, etc., to fine-tune your strategy.

## 3. Running the Bot

### 3.1. Dry Run (Simulated) Mode

This mode allows you to test your strategy with live market data without risking real capital.

**Configuration**:
*   In `src/config.py`:
    ```python
    Config.TESTNET = True  # Or False, if you want to simulate on mainnet data
    Config.SIM_MODE = True
    Config.LIVE_MODE = False
    ```
*   In your `.env` file:
    ```
    DRY_RUN=true
    SAVE_DATA=true # Recommended for analysis
    ```

**Execution**:
```bash
python executor_bot.py
```

**Expected Behavior**:
*   The terminal will display a live status table with various metrics, including `OB Imbalance`, `Trade Imbalance`, `Unrealized PnL`, `Realized PnL`, and `Current Value`.
*   Logs will show simulated trade entries and exits, PnL calculations, and signal generation details.
*   If `SAVE_DATA` is `True`, `status_snapshot.csv` and `trade_log_<symbol>.csv` files will be created/updated in your `DATA_DIR` (default `./data`). These files will contain detailed records of the bot's state and simulated trades.

### 3.2. Live Trading Mode

**WARNING**: Live trading involves real financial risk. Ensure you have thoroughly tested your strategy in dry run mode and understand all parameters before proceeding.

**Configuration**:
*   In `src/config.py`:
    ```python
    Config.TESTNET = False # Set to True if you intend to live trade on testnet
    Config.SIM_MODE = False
    Config.LIVE_MODE = True
    ```
*   In your `.env` file:
    ```
    DRY_RUN=false # Crucial: set to false for real orders
    SAVE_DATA=true # Recommended for post-trade analysis
    ```

**Execution**:
```bash
python executor_bot.py
```

**Expected Behavior**:
*   The bot will attempt to place real orders on the Bybit exchange based on its signals.
*   The live status table will update with real position information and PnL.
*   Logs will show actual order placement responses from Bybit.
*   `status_snapshot.csv` and `trade_log_<symbol>.csv` files will record live trading activity.

## 4. Monitoring the Bot

The bot provides several ways to monitor its operation and performance:

### 4.1. Live Status Table (Terminal)

The terminal output features a `rich`-powered live table that updates every second, providing a real-time snapshot of the bot's state for each monitored symbol.

**Key Columns to Monitor**:

*   **`Symbol`**: The trading pair (e.g., BTC/USDT).
*   **`Last Liq Age`**: Age of the last liquidation event. A high value indicates stale data.
*   **`Events` / `Cluster Vol` / `Active Bins`**: Metrics related to liquidation cluster aggregation.
*   **`Top Cl Price` / `Top Cl Str`**: Price and strength of the most significant liquidation cluster.
*   **`Support` / `Resistance`**: Dynamically identified support and resistance bands.
*   **`OB Imbalance` / `Trade Imbalance`**: Real-time order book and trade flow imbalances.
*   **`Sweep`**: Indicates if a liquidation sweep has been detected.
*   **`RSI` / `RSI State`**: Current RSI value and its state (oversold, neutral, overbought).
*   **`Signal` / `Confidence`**: The bot's current trading signal and its confidence score (0.0-1.0).
*   **`Open Pos`**: `✅` if an open position exists, `❌` otherwise.
*   **`Pos Size`**: Size of the open position in USDT.
*   **`Entry Price`**: Entry price of the open position.
*   **`Unrealized PnL`**: Profit or Loss for current open positions (simulated or real).
*   **`Realized PnL`**: Cumulative profit or loss from closed positions (simulated or real).
*   **`Current Value`**: Current USDT value of the open position (entry value + unrealized PnL).
*   **`Status`**: General status of the bot for the symbol (e.g., "Idle", "Simulated", "Waiting", "Closed").
*   **`Notes`**: Additional contextual information, including stream health.

### 4.2. Log Files

The bot generates detailed logs to the console and, if `SAVE_DATA=true`, to CSV files.

*   **Console Logs**: Provide real-time operational messages, errors, warnings, and debug information. Pay attention to `CRITICAL` and `ERROR` level messages.
*   **`trade_log_<symbol>.csv`**: Records every simulated or live trade action (entry/exit, PnL, confidence, reason). This is crucial for backtesting and performance review.
*   **`status_snapshot.csv`**: A periodic snapshot of the `PairStatus` for all symbols, useful for historical analysis of the bot's state.

### 4.3. Internal Health Checks

*   **Background Task Monitoring**: The `main` loop continuously checks if critical background tasks (WebSocket consumers, cluster aggregator) are still running. If a task fails or exits unexpectedly, the bot will log a critical error and shut down, indicating a severe issue.
*   **Time Synchronization**: The bot periodically synchronizes its local time with the Bybit server to prevent issues caused by clock skew.

### 4.4. System Resource Monitoring

*   The bot logs CPU and memory usage at intervals defined by `Config.SYSTEM_USAGE_LOG_INTERVAL_S`. High or escalating usage might indicate a resource leak or inefficiency.

## 5. Troubleshooting

*   **"API_KEY or API_SECRET not found"**: Ensure your `.env` file is correctly set up and located in the project root.
*   **"Background task failed/completed unexpectedly"**: Check the logs immediately for the specific exception. This often points to issues with WebSocket connections or data processing.
*   **"No liquidation events received or data is stale"**: This could indicate an issue with the Bybit liquidation WebSocket stream, network connectivity, or the symbol having very low liquidation activity. The bot will fallback to RSI-only mode in this scenario.
*   **"Account balance is 0 or None"**: Ensure your API keys have correct permissions and your account has sufficient balance for the selected `Config.SYMBOLS`.
*   **Indentation Errors**: If you modify the Python code, ensure correct Python indentation.

By following this runbook, you can effectively operate and monitor your RSI Swing Bot.