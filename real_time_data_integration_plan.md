# Phase 1 – Real-Time Data Integration Plan

**Goal:** Integrate real-time order book and trade data from Bybit into the `ClusterAggregator`, making it accessible for the `SignalGenerator`.

---

## I. Data Stream Refinement

### 1. Introduce `OrderBookEvent` in `src/ws_orderbook.py`

*   **Description:** Define a new class, `OrderBookEvent`, to encapsulate essential order book information. This event will contain the symbol, timestamp, mid-price, and order book imbalance.
*   **File:** [`src/ws_orderbook.py`](src/ws_orderbook.py)
*   **Proposed Code Addition:**

    ```python
    class OrderBookEvent:
        def __init__(self, exchange: str, symbol: str, timestamp: int, mid_price: float, imbalance: float):
            self.exchange = exchange
            self.symbol = symbol
            self.timestamp = timestamp
            self.mid_price = mid_price
            self.imbalance = imbalance

        def __repr__(self):
            return f"OrderBookEvent(symbol={self.symbol}, ts={self.timestamp}, mid_price={self.mid_price}, imbalance={self.imbalance})"
    ```

### 2. Modify `OrderBookManager` in `src/ws_orderbook.py` to push `OrderBookEvent` to queue

*   **Description:** After processing snapshot and delta updates, the `OrderBookManager` will calculate the mid-price and order book imbalance using the `OrderBook` instance's methods (`get_mid_price` and `get_imbalance_ratio`). It will then create an `OrderBookEvent` and place it into the `self.queue` (an `asyncio.Queue` passed during initialization).
*   **File:** [`src/ws_orderbook.py`](src/ws_orderbook.py)
*   **Proposed Code Modification (within `OrderBookManager.orderbook_ws_consumer`):**

    ```python
    # ... inside orderbook_ws_consumer, after a successful snapshot or delta update ...
    if symbol_name in self.order_books:
        ob = self.order_books[symbol_name]
        mid_price = ob.get_mid_price()
        imbalance = ob.get_imbalance_ratio()
        ob_event = OrderBookEvent(
            exchange="bybit", # Or whatever exchange is being used
            symbol=symbol_name,
            timestamp=ob.last_update_timestamp,
            mid_price=mid_price,
            imbalance=imbalance
        )
        await self.queue.put(ob_event)
        logger.debug(f"OrderBookEvent put to queue for {symbol_name}: {ob_event}")
    ```

### 3. `src/ws_trades.py`

*   **Description:** No changes are required for `TradeStreamManager` as it already correctly places `TradeEvent` objects into the `self.queue`.
*   **File:** [`src/ws_trades.py`](src/ws_trades.py)

---

## II. ClusterAggregator Enhancement

### 1. Import new event types in `src/cluster_aggregator.py`

*   **Description:** Add imports for `TradeEvent` and `OrderBookEvent`.
*   **File:** [`src/cluster_aggregator.py`](src/cluster_aggregator.py)
*   **Proposed Code Addition (near other imports):**

    ```python
    from .ws_trades import TradeEvent
    from .ws_orderbook import OrderBookEvent # Assuming OrderBookEvent is defined in ws_orderbook.py
    ```

### 2. Update `ClusterAggregator` to handle `TradeEvent` and `OrderBookEvent`

*   **Description:**
    *   Modify the `ingest` method to act as a dispatcher. It will check the type of the incoming event and call a specific handler method.
    *   Implement new methods: `_ingest_trade_event` and `_ingest_orderbook_event`.
    *   Add internal data structures (`self.trade_imbalances`, `self.orderbook_mid_prices`, `self.orderbook_imbalances`) to store the latest processed data for each symbol.
*   **File:** [`src/cluster_aggregator.py`](src/cluster_aggregator.py)
*   **Proposed Code Modification (within `ClusterAggregator` class):**

    ```python
    # Add to __init__
    # ... existing __init__ content ...
    self.trade_imbalances: Dict[str, float] = defaultdict(lambda: 1.0) # Default to balanced
    self.orderbook_mid_prices: Dict[str, float] = defaultdict(lambda: 0.0)
    self.orderbook_imbalances: Dict[str, float] = defaultdict(lambda: 1.0)

    # Modify ingest method
    def ingest(self, event):
        if isinstance(event, LiquidationEvent):
            self._ingest_liquidation_event(event)
        elif isinstance(event, TradeEvent):
            self._ingest_trade_event(event)
        elif isinstance(event, OrderBookEvent):
            self._ingest_orderbook_event(event)
        else:
            logger.warning(f"Unknown event type received by ClusterAggregator: {type(event)}")

    def _ingest_liquidation_event(self, event: LiquidationEvent):
        symbol = event.symbol
        bin_idx = self._price_to_bin(symbol, event.price)

        self.events_deque[symbol].append(event) # events_deque currently stores LiquidationEvents
        self.bins[symbol][bin_idx]["volume"] += event.qty_usdt
        self.bins[symbol][bin_idx]["last_update"] = event.timestamp
        self.bins[symbol][bin_idx]["events"] += 1

        self._expire_old_events(symbol) # This currently only expires LiquidationEvents

        self.status_tracker.update_status(
            symbol=symbol,
            last_update_ms=event.timestamp,
            events_count=len(self.events_deque[symbol]),
            cluster_volume_usdt=sum(b["volume"] for b in self.bins[symbol].values()),
            active_bins=len(self.bins[symbol])
        )

    def _ingest_trade_event(self, event: TradeEvent):
        # Assuming TradeEvent will be updated to carry the imbalance directly.
        # If not, calculation logic would be added here based on event.qty and event.side.
        # For this plan, we assume TradeStreamManager calculates and passes it.
        self.trade_imbalances[event.symbol] = event.imbalance # This line assumes TradeEvent has an 'imbalance' attribute
        logger.debug(f"[{event.symbol}] Trade imbalance updated: {self.trade_imbalances[event.symbol]}")

    def _ingest_orderbook_event(self, event: OrderBookEvent):
        self.orderbook_mid_prices[event.symbol] = event.mid_price
        self.orderbook_imbalances[event.symbol] = event.imbalance
        logger.debug(f"[{event.symbol}] Orderbook mid-price updated: {self.orderbook_mid_prices[event.symbol]}, imbalance: {self.orderbook_imbalances[event.symbol]}")

    # Add public methods to retrieve these values
    def get_trade_imbalance(self, symbol: str) -> float:
        return self.trade_imbalances[symbol]

    def get_orderbook_mid_price(self, symbol: str) -> float:
        return self.orderbook_mid_prices[symbol]

    def get_orderbook_imbalance(self, symbol: str) -> float:
        return self.orderbook_imbalances[symbol]
    ```

### 3. Refine `_expire_old_events` (Optional but Recommended)

*   **Description:** The current `_expire_old_events` only handles `LiquidationEvent`. If `TradeEvent` or `OrderBookEvent` objects are also stored in deques within `ClusterAggregator` for historical analysis, their expiration logic should also be added. For the current scope of just updating the latest imbalance/mid-price, this might not be strictly necessary, but good to keep in mind for future enhancements.
*   **File:** [`src/cluster_aggregator.py`](src/cluster_aggregator.py)

### 4. Modify `cluster_consumer_loop` in `src/cluster_aggregator.py`

*   **Description:** The loop already consumes from `event_queue`. It will now simply pass the event to the updated `aggregator.ingest` method, which acts as a dispatcher.
*   **File:** [`src/cluster_aggregator.py`](src/cluster_aggregator.py)
*   **Proposed Code (within `cluster_consumer_loop` - no changes needed to existing lines):**

    ```python
    # ... inside cluster_consumer_loop ...
    # event = await event_queue.get()
    # aggregator.ingest(event) # This line remains as is, the ingest method handles dispatching
    # ...
    ```

---

## III. Integration with SignalGenerator (Conceptual)

*   **Description:** The `SignalGenerator` (which will be tackled in a later phase) will instantiate the `ClusterAggregator` and then call its new public methods (`get_trade_imbalance`, `get_orderbook_mid_price`, `get_orderbook_imbalance`) to retrieve the real-time data needed for signal generation.
*   **File:** [`src/signal_generator.py`](src/signal_generator.py) (Future modification)

---

## Updated Data Flow Diagram

```mermaid
graph TD
    A[Bybit WebSocket] -->|Order Book Data| B(ws_orderbook.py::OrderBookManager)
    A -->|Trade Data| C(ws_trades.py::TradeStreamManager)
    B -- OrderBookEvent (mid_price, imbalance) --> D(asyncio.Queue)
    C -- TradeEvent (imbalance) --> D
    E[ws_liquidation.py::LiquidationStreamManager] -- LiquidationEvent --> D
    D -->|Unified Event Stream| F(cluster_aggregator.py::ClusterAggregator::cluster_consumer_loop)
    F -->|Dispatch Event| G{ClusterAggregator::ingest()}
    G -- LiquidationEvent --> H[ClusterAggregator::_ingest_liquidation_event()]
    G -- TradeEvent --> I[ClusterAggregator::_ingest_trade_event()]
    G -- OrderBookEvent --> J[ClusterAggregator::_ingest_orderbook_event()]
    H --> K[Liquidation Bins & Sweeps]
    I --> L[Latest Trade Imbalance]
    J --> M[Latest Order Book Mid-Price & Imbalance]
    K & L & M --> N[SignalGenerator (Queries ClusterAggregator)]