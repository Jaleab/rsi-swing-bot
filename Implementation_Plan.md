# Implementation Plan: Exchange API Integration

The primary objective is to integrate the bot with a cryptocurrency exchange API (Bybit, as per current configuration) to enable real-time order placement, management, and account data retrieval. This plan emphasizes abstraction, error handling, and security.

## Phase 1: Abstraction Layer and Client Selection

1.  **Introduce an Exchange Client Abstraction Layer:**
    *   **Goal:** Create a standardized interface for interacting with any cryptocurrency exchange. This will decouple the bot's core logic from exchange-specific implementations, making it easier to switch exchanges or add new ones in the future.
    *   **Action:** Define an `AbstractExchangeClient` (e.g., using Python's `abc` module) with abstract methods for common operations like `get_balance`, `place_order`, `cancel_order`, `get_open_orders`, `get_order_status`, `get_order_book`, `get_recent_trades`, etc.
    *   **File:** [`src/abstract_exchange.py`](src/abstract_exchange.py)

2.  **Implement Bybit-Specific Exchange Client:**
    *   **Goal:** Create a concrete implementation of the `AbstractExchangeClient` for Bybit.
    *   **Action:** Use a well-established Python library like `ccxt` (or Bybit's official SDK if preferred) to interact with the Bybit API. This library should handle low-level API requests, authentication, and basic rate limiting.
    *   **File:** [`src/bybit_exchange.py`](src/bybit_exchange.py)
    *   **Key functionalities:**
        *   **Initialization:** Configure with API keys and select testnet/mainnet based on `Config.TESTNET`.
        *   **Authentication:** Securely use `Config.API_KEY` and `Config.API_SECRET`.
        *   **API Calls:** Wrap `ccxt` methods for placing orders, checking balances, etc.

## Phase 2: Core Trading Functionality

1.  **Account and Position Management:**
    *   **Goal:** Enable the bot to query its available balance and track open positions on the exchange.
    *   **Action:** Implement methods in `BybitExchangeClient` for:
        *   `get_balance(currency='USDT')`: Retrieve available and total balance for a specified currency.
        *   `get_positions(symbol=None)`: Fetch details of all or specific open positions (e.g., entry price, quantity, PnL).

2.  **Order Execution and Management:**
    *   **Goal:** Allow the bot to place and manage various types of orders.
    *   **Action:** Implement methods in `BybitExchangeClient` for:
        *   `place_order(symbol, side, order_type, quantity, price=None, params={})`: Place a new order (e.g., MARKET, LIMIT). `params` will allow for exchange-specific options like `take_profit`, `stop_loss`, `post_only`, `reduce_only`.
        *   `cancel_order(order_id, symbol)`: Cancel a specific open order.
        *   `get_open_orders(symbol=None)`: Retrieve a list of active orders.
        *   `get_order_status(order_id, symbol)`: Get the current status of an order (e.g., filled, partial, canceled).

## Phase 3: Position and Risk Management Layer

1.  **Introduce `PositionManager` Class:**
    *   **Goal:** Centralize position sizing, stop-loss/take-profit calculations, and ensure adherence to configured risk limits.
    *   **Action:** Create a `PositionManager` class that will:
        *   Determine optimal position size based on `Config.RISK_PER_TRADE_PERCENT` and account balance.
        *   Calculate dynamic stop-loss and take-profit levels based on market conditions or signal strength.
        *   Enforce `Config.MAX_OPEN_POSITIONS`.
    *   **Integration:** This manager will sit between the `ExecutorBot` and the `ExchangeClient`.

## Phase 4: Real-time Data and State Synchronization (Recommended Enhancement)

1.  **WebSocket Integration for Real-time Updates:**
    *   **Goal:** Enhance responsiveness by receiving instant updates on order fills, cancellations, and account changes, reducing the need for frequent REST API polling.
    *   **Action:** Create a dedicated `BybitWebSocketClient` that subscribes to relevant private channels (e.g., user trades, orders, positions).
    *   **Integration:** Feed these real-time updates into the bot's internal `StatusTracker` or a new `PositionManager` to maintain an accurate and up-to-date view of the trading state.

## Phase 5: Integration with ExecutorBot

1.  **Update `ExecutorBot` for Live Trading:**
    *   **Goal:** Modify the main `ExecutorBot` to use the newly created `BybitExchangeClient` and `PositionManager` for actual trade execution when `Config.LIVE_MODE` is `True`.
    *   **Action:**
        *   Inject the `BybitExchangeClient` and `PositionManager` instances into `ExecutorBot`.
        *   Modify the trade execution logic to conditionally call `exchange_client.place_order()` instead of simulating orders, using `PositionManager` for sizing and SL/TP.
        *   Ensure simulation logic is still used when `Config.SIM_MODE` is `True`.

2.  **Refine Signal Output:**
    *   **Goal:** Ensure the `Signal` TypedDict in [`src/signal_generator.py`](src/signal_generator.py) provides all necessary parameters for flexible order placement (e.g., `order_type`, `post_only`, `reduce_only` if applicable).

## Phase 6: Testing and Security Best Practices

1.  **Comprehensive Testnet Testing:**
    *   **Goal:** Verify all API interactions and trading logic in a risk-free testnet environment.
    *   **Action:** Conduct extensive testing of order placement, cancellation, modification, balance checks, and position management on Bybit's testnet.

2.  **Security Review:**
    *   **Goal:** Ensure API keys are handled securely.
    *   **Action:** Confirm that API keys are only loaded from environment variables (e.g., `.env`), never hardcoded, and access permissions on the exchange API are set to the least privilege necessary (e.g., trade permissions, not withdrawal). Consider IP whitelisting on the exchange side.

3.  **Enhanced Logging and Monitoring:**
    *   **Goal:** Provide visibility into API interactions and potential issues.
    *   **Action:** Implement detailed logging of all API requests and responses (excluding sensitive data) for debugging and auditing purposes. Integrate Prometheus metrics for API call latency, success/failure rates, and position updates. This ensures Grafana dashboards reflect live trading health.

4.  **Retry / Rate-Limit Handling:**
    *   **Goal:** Make API interactions robust against network issues, invalid requests, and exchange rate limits.
    *   **Action:** Emphasize that all REST API calls should be wrapped in a retry/backoff strategy, especially for order placement, to prevent accidental duplicate orders. Leverage `ccxt`'s built-in rate limit management or implement a custom token bucket algorithm if needed.

5.  **Webhook / Alert System:**
    *   **Goal:** Provide critical alerts for immediate attention.
    *   **Action:** Implement an optional webhook or alert system for critical errors (e.g., order rejections, API downtime, unexpected bot shutdowns) to notify the user.

---

### Proposed Architecture Flow (Mermaid Diagram)

```mermaid
graph TD
    A[ExecutorBot] --> B{Signal Generated?}
    B -- Yes --> C{Live Mode?}
    C -- Yes --> P[PositionManager]
    P -- Determine Order Params --> D[BybitExchangeClient]
    D -- Place Order --> E[Bybit Exchange API]
    E -- Order Confirmation/Update --> D
    D -- Update Internal State --> F[StatusTracker]
    D -- Update PositionManager --> P
    C -- No --> G[Simulation Logic]
    G -- Simulate Order --> F
    F --> H[Decision Making Loop]
    D -- WebSocket Updates (Optional) --> F
    subgraph Exchange Interaction
        D
        E
    end
    subgraph Core Bot Components
        A
        F
        H
        G
        P
    end