# Architecture Overview - RSI Swing Bot

## Workflow Diagram

```mermaid
graph TD
    A[Bybit WS] --> B(ws_liquidation)
    C[Binance WS] --> B
    B --> D(asyncio.Queue event_q)
    D --> E[cluster_aggregator]
    E --> F[signal_generator]
    G[OHLCV CCXT] --> H[rsi_calc]
    H --> F
    F -- Signal --> I[executor_bot]
    I -- Order --> J{sim_engine / exchange}