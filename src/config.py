from typing import List
import os # Import os for environment variables

class Config:
    # --- General Settings ---
    EXCHANGE_NAME: str = "bybit"
    # Semicolon-delimited trading pairs from BYBIT_SYMBOLS env var. Example: "SOL/USDT;BTC/USDT"
    SYMBOLS: List[str] = os.environ.get("BYBIT_SYMBOLS", "SOL/USDT;BTC/USDT").split(';')
    TIMEFRAME: str = "5m"         # 5m minimum for RSI swing signals (was 1m)
    SIM_MODE: bool = os.environ.get("SIM_MODE", "False").lower() == "true"  # Overridden by --sim flag
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "DEBUG")          # Default logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    # --- API and Exchange Parameters ---
    EXCHANGE_CLIENT: str = "bybit" # Exchange client to use (e.g., "bybit")
    API_KEY: str = os.environ.get("BYBIT_API_KEY", "YOUR_API_KEY")    # Bybit API Key
    API_SECRET: str = os.environ.get("BYBIT_API_SECRET", "YOUR_API_SECRET") # Bybit API Secret
    # Corrected: Default to False if BYBIT_TESTNET env var is not set
    TESTNET: bool = os.environ.get("BYBIT_TESTNET", "False").lower() == "true"
    RECV_WINDOW: int = 30000         # Bybit recvWindow for API requests (milliseconds)

    # --- Trading Strategy Parameters ---
    # Position Sizing (Quarter-Kelly fractional risk framework)
    POSITION_USDT: float = float(os.environ.get("POSITION_USDT", "10.0"))
    ENABLE_RISK_SIZING: bool = os.environ.get("ENABLE_RISK_SIZING", "True").lower() == "true"
    LEVERAGE: int = int(os.environ.get("LEVERAGE", "1"))
    RISK_PER_TRADE_PCT: float = float(os.environ.get("RISK_PER_TRADE_PCT", "0.016"))
    MAX_RISK_PER_TRADE_PCT: float = float(os.environ.get("MAX_RISK_PER_TRADE_PCT", "0.03"))
    MAX_POSITION_PCT: float = float(os.environ.get("MAX_POSITION_PCT", "0.25"))
    MIN_POSITION_SIZE_USDT: float = float(os.environ.get("MIN_POSITION_SIZE_USDT", "1.0"))
    ENABLE_VOLATILITY_SCALING: bool = os.environ.get("ENABLE_VOLATILITY_SCALING", "True").lower() == "true"
    VOLATILITY_TARGET_PCT: float = float(os.environ.get("VOLATILITY_TARGET_PCT", "0.02"))

    # Circuit Breakers
    ENABLE_DRAWDOWN_BREAKER: bool = os.environ.get("ENABLE_DRAWDOWN_BREAKER", "True").lower() == "true"
    MAX_DRAWDOWN_PCT: float = float(os.environ.get("MAX_DRAWDOWN_PCT", "0.20"))
    ENABLE_EQUITY_CURVE_FILTER: bool = os.environ.get("ENABLE_EQUITY_CURVE_FILTER", "True").lower() == "true"

    MAX_TOTAL_OPEN_POSITIONS: int = int(os.environ.get("MAX_TOTAL_OPEN_POSITIONS", "3"))
# NOTE:
# PositionManager currently supports only ONE open position per symbol.
# MAX_POSITIONS_PER_SYMBOL must remain 1 until PositionManager is refactored for multi-position per symbol.
    MAX_POSITIONS_PER_SYMBOL: int = 1 # Maximum number of concurrent open positions for a single symbol
    RISK_PER_TRADE_PERCENT: float = 0.005 # Risk per trade as a percentage of account balance

    # RSI Parameters (aligned with Pine Script prototype and industry standard)
    RSI_LENGTH: int = int(os.environ.get("RSI_LENGTH", "14"))
    RSI_OVERSOLD: int = int(os.environ.get("RSI_OVERSOLD", "30"))
    RSI_OVERBOUGHT: int = int(os.environ.get("RSI_OVERBOUGHT", "70"))
    ENABLE_RSI_EXIT: bool = os.environ.get("ENABLE_RSI_EXIT", "True").lower() == "true"

    # --- Regime Detection ---
    ENABLE_REGIME_FILTER: bool = True
    ADX_LENGTH: int = 14
    ADX_THRESHOLD: int = 25       # Below 25 = ranging (good for RSI), above 25 = trending
    REGIME_FILTER_MODE: str = "RANGING_ONLY"  # "RANGING_ONLY" or "ALL"
    TIMEFRAME: str = "5m"         # 5m minimum for RSI swing signals (was 1m)

    # Cluster Parameters
    BIN_MODE: str = "percent"        # "absolute" or "percent"
    BIN_PCT: float = 0.002           # 0.2% of price per bin (used for dynamic bins)
    BIN_ABS: float = 0.5             # Fallback absolute USD per bin
    SLIDING_WINDOW_S: int = 300      # Seconds to aggregate liquidations (e.g., 5 minutes)
    SWEEP_WINDOW_S: int = 30         # Shorter window for sweep detection (e.g., 30 seconds)
    SWEEP_THRESHOLD_FACTOR: float = 2.0  # Sweep detection threshold vs average cluster volume
    MIN_SWEEP_VOLUME_USDT: float = 5000.0  # Minimum economic size for a sweep to matter

    # Confidence Weights (must sum to 1.0)
    W_RSI: float = 0.455      # 0.50 / 1.10
    W_CLUSTER: float = 0.273  # 0.30 / 1.10
    W_SWEEP: float = 0.136    # 0.15 / 1.10
    W_PROX: float = 0.045     # 0.05 / 1.10
    W_DOMINANCE: float = 0.091 # 0.10 / 1.10

    TRADE_COOLDOWN_SECONDS: int = 60 # Cooldown period between trades for the same symbol in seconds
    SIGNAL_COOLDOWN_SECONDS: int = 5 # Cooldown period to ignore duplicate signals for the same symbol in seconds

    # --- Dynamic SL/TP Parameters ---
    USE_DYNAMIC_SLTP: bool = False   # Enable dynamic stop loss/take profit based on clusters
    SL_BUFFER: float = 0.005         # 0.5% buffer around support/resistance for stop loss
    TP_BUFFER: float = 0.005         # 0.5% buffer around support/resistance for take profit
    MIN_STOP_LOSS_PERCENT: float = 0.01 # 1% as a hard minimum for dynamic SL
    MAX_TAKE_PROFIT_PERCENT: float = 0.05 # 5% as a hard maximum for dynamic TP

    # Fallback Fixed Percentages
    STOP_LOSS_PERCENT: float = 0.035 # 3.5%
    TAKE_PROFIT_PERCENT: float = 0.05 # 5%

    # --- Data Persistence ---
    PERSIST_CLUSTERS: bool = True    # Enable/disable cluster state persistence
    CLUSTER_STATE_FILE: str = "cluster_state.json" # Filename for saving cluster state
    CLUSTER_PERSISTENCE_INTERVAL_S: int = 300 # How often to save cluster state in seconds (e.g., 5 minutes)
    DATA_DIR: str = "data/live/live" # Directory for data files (e.g., for snapshots)

    # --- Status Tracking and Snapshots ---
    MAX_LIQUIDATION_DATA_LATENCY_SECONDS: int = 60 # Max seconds before liquidation data is considered stale
    STATUS_SNAPSHOT_INTERVAL_S: int = 10 # How often to save status snapshot to CSV in seconds

    # --- WebSocket Settings ---
    BYBIT_LIQUIDATION_WS_ENABLED: bool = True  # Now wired into EventStream
    BINANCE_LIQUIDATION_WS_ENABLED: bool = False
    ORDERBOOK_WS_ENABLED: bool = True # Enable orderbook WebSocket
    TRADES_WS_ENABLED: bool = True # Enable trades WebSocket
    WS_RECEIVE_TIMEOUT_S: int = 30   # WebSocket receive timeout in seconds

    # --- Trade Stream Manager Settings ---
    TRADE_IMBALANCE_WINDOW_SIZE: int = 60 # Window size in seconds for trade imbalance calculation

    # --- Simulation Settings ---
    # Corrected: Default to False if USE_SIM_EVENTS_GENERATOR env var is not set
    USE_SIM_EVENTS_GENERATOR: bool = True # Temporarily force to True for debugging
    SIMULATION_RANDOM_SEED: int = int(os.environ.get("SIMULATION_RANDOM_SEED", "42")) # Seed for deterministic simulations
    DEFAULT_SIM_PRICE: float = 20.0  # Default price for simulation mode
    HISTORICAL_WINDOW_S: int = 3600  # Historical window for generating events in seconds (1 hour)
    SIM_SWEEP_MIN_DELAY_S: int = 10  # Minimum delay between synthetic sweeps in seconds
    SIM_SWEEP_MAX_DELAY_S: int = 30  # Maximum delay between synthetic sweeps in seconds
    SIM_SWEEP_NUM_EVENTS: int = 5 # Number of individual events to compose a synthetic sweep
    SIM_SWEEP_DURATION_S: float = 1.0 # Duration over which the individual events of a sweep are spread
    SIM_DURATION_SECONDS: int = 300 # Duration of the simulation in seconds
    SIM_SWEEP_FREQUENCY: float = 0.1 # Frequency of synthetic sweeps in simulation (e.g., 0.1 means 10% chance per second)

    # --- Manual Sweep Injection Settings (for testing) ---
    ENABLE_MANUAL_SWEEP_INJECTION: bool = False
    MANUAL_SWEEP_SYMBOL: str = "SOL/USDT"
    MANUAL_SWEEP_VOLUME_USDT: float = 5_000_000.0
    MANUAL_SWEEP_DIRECTION: str = "buy" # "buy" or "sell"
    MANUAL_SWEEP_PRICE_IMPACT_PCT: float = 0.5 # Percentage impact (e.g., 0.5 for 0.5%)

    # --- Backtesting Settings ---
    BACKTEST_ENABLED: bool = os.environ.get("BACKTEST_ENABLED", "False").lower() == "true"
    BACKTEST_START_DATE: str = os.environ.get("BACKTEST_START_DATE", "2023-01-01 00:00:00")
    BACKTEST_END_DATE: str = os.environ.get("BACKTEST_END_DATE", "2023-01-03 23:59:00")
    BACKTEST_INITIAL_BALANCE: float = float(os.environ.get("BACKTEST_INITIAL_BALANCE", "10000.0"))
    SIMULATION_INITIAL_BALANCE: float = float(os.environ.get("SIMULATION_INITIAL_BALANCE", "10000.0")) # Initial balance for PaperTrader in simulation

    # --- Bot Operation Intervals ---
    MARKET_LOOP_INTERVAL: int = 5    # Interval for the market loop in seconds
    MARKET_LOOP_DELAY: float = 0.1   # Seconds to yield between event loop iterations
    OHLCV_LIMIT: int = 200           # Number of OHLCV candles to fetch
    QUEUE_LOG_INTERVAL: int = 60     # Interval for logging queue sizes in seconds
    OHLCV_UPDATE_INTERVAL_S: int = 60 # Min seconds between OHLCV refreshes (should match TIMEFRAME)

    # --- Monitoring and UI ---
    ENABLE_PROMETHEUS: bool = True
    PROMETHEUS_PORT: int = 8000
    METRICS_UPDATE_INTERVAL_S: int = 5 # How often to update resource metrics in seconds
    ENABLE_RICH_TABLE: bool = True   # Enable/disable rich table display
    MONITOR_INTERVAL_SECONDS: int = 60 # Interval for simple monitor output in seconds

    # --- WebSocket Reconnection Settings ---
    MAX_RECONNECT_ATTEMPTS: int = 10 # Maximum number of reconnection attempts for WebSockets

    # --- WebSocket URLs ---
    # Always use mainnet WS for data (testnet has no liquidation activity)
    WS_DATA_SOURCE: str = os.environ.get("WS_DATA_SOURCE", "mainnet")  # "mainnet" or "testnet"
    _ws_base = "wss://stream.bybit.com/v5/public/linear" if WS_DATA_SOURCE == "mainnet" else "wss://stream-testnet.bybit.com/v5/public/linear"
    BYBIT_WS_URL: str = _ws_base
    BINANCE_WS_URL: str = "wss://fstream.binance.com/ws"

    # --- Safe Mode Settings ---
    ENABLE_SAFE_MODE: bool = True # Enable/disable safe mode
    ERROR_THRESHOLD_INTERVAL_S: int = 300 # Time window in seconds to count errors (e.g., 5 minutes)
    MAX_ERRORS_PER_INTERVAL: int = 5 # Max errors allowed in the interval before safe mode
    SAFE_MODE_DURATION_S: int = 1800 # How long safe mode remains active once triggered (e.g., 30 minutes)
