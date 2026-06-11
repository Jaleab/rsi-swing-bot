import asyncio
import websockets
import json
import logging
import time
from collections import defaultdict, deque
from sortedcontainers import SortedDict # Using sortedcontainers for efficient order book management
from typing import Dict, Any, List, Tuple, Literal

from src.config import Config
from src.events import OrderBookEvent # Import OrderBookEvent from new events module

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Set logger level back to INFO

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

# Removed local OrderBookEvent class definition, now imported from src.events

class OrderBook:
    """Manages a local order book for a given symbol."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids = SortedDict()  # {price: quantity} - sorted descending for bids
        self.asks = SortedDict()  # {price: quantity} - sorted ascending for asks
        self.last_update_id = -1
        self.last_update_timestamp = 0
        logger.info(f"Initialized OrderBook for {symbol}")

    def update_from_snapshot(self, snapshot: Dict[str, Any]):
        """Updates the order book from a snapshot (initial data)."""
        self.bids.clear()
        self.asks.clear()
        for bid in snapshot['b']:
            price, qty = float(bid[0]), float(bid[1])
            self.bids[price] = qty
        for ask in snapshot['a']:
            price, qty = float(ask[0]), float(ask[1])
            self.asks[price] = qty
        self.last_update_id = snapshot['u']
        self.last_update_timestamp = snapshot.get('ts', 0) # Safely get 'ts', default to 0 if not present
        logger.debug(f"OrderBook for {self.symbol} updated from snapshot. Bids: {len(self.bids)}, Asks: {len(self.asks)}")

    def update_from_delta(self, delta: Dict[str, Any]):
        """Updates the order book from a delta update."""
        start_time = time.perf_counter() # Start timing
        initial_bids_len = len(self.bids)
        initial_asks_len = len(self.asks)

        update_id = delta['u']
        if update_id <= self.last_update_id:
            logger.warning(f"Received old update for {self.symbol}: {update_id} <= {self.last_update_id}. Skipping.")
            return

        self.last_update_id = update_id
        self.last_update_timestamp = delta.get('ts', 0) # Safely get 'ts', default to 0 if not present

        bids_modified = 0
        for bid in delta['b']:
            price, qty = float(bid[0]), float(bid[1])
            if qty == 0:
                if price in self.bids:
                    del self.bids[price]
                    bids_modified += 1
            else:
                if price not in self.bids or self.bids[price] != qty: # Only count as modified if value changes
                    self.bids[price] = qty
                    bids_modified += 1
        
        asks_modified = 0
        for ask in delta['a']:
            price, qty = float(ask[0]), float(ask[1])
            if qty == 0:
                if price in self.asks:
                    del self.asks[price]
                    asks_modified += 1
            else:
                if price not in self.asks or self.asks[price] != qty: # Only count as modified if value changes
                    self.asks[price] = qty
                    asks_modified += 1
        
        duration = (time.perf_counter() - start_time) * 1000 # Duration in milliseconds
        logger.info(f"OrderBook for {self.symbol} updated from delta {update_id} in {duration:.3f}ms. "
                    f"Bids: {initial_bids_len}->{len(self.bids)} ({bids_modified} modified), "
                    f"Asks: {initial_asks_len}->{len(self.asks)} ({asks_modified} modified).")

    def get_top_n_levels(self, n: int = 5) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """Returns the top N bid and ask levels."""
        # For bids, we want the highest prices, so iterate in reverse order
        top_bids = [(price, qty) for price, qty in reversed(self.bids.items())][:n]
        # For asks, we want the lowest prices, which is the natural order of SortedDict
        top_asks = [(price, qty) for price, qty in self.asks.items()][:n]
        return top_bids, top_asks

    def get_mid_price(self) -> float:
        """Calculates the mid-price of the order book."""
        if not self.bids or not self.asks:
            return 0.0 # Or raise an error, depending on desired behavior
        best_bid = self.bids.peekitem(-1)[0] # Highest bid price
        best_ask = self.asks.peekitem(0)[0]  # Lowest ask price
        return (best_bid + best_ask) / 2

    def get_imbalance_ratio(self, depth: int = 5) -> float:
        """
        Calculates the order book imbalance ratio based on cumulative volume.
        A value > 1 suggests more buy pressure, < 1 suggests more sell pressure.
        """
        top_bids, top_asks = self.get_top_n_levels(depth)
        
        bid_volume = sum(qty for price, qty in top_bids)
        ask_volume = sum(qty for price, qty in top_asks)

        if ask_volume == 0:
            return float('inf') if bid_volume > 0 else 1.0 # Avoid division by zero
        if bid_volume == 0:
            return 0.0 # No bids, extreme sell pressure

        return bid_volume / ask_volume

class OrderBookManager:
    """Manages order books for multiple symbols."""
    def __init__(self, symbols: List[str], queue: asyncio.Queue, status_tracker):
        self.order_books: Dict[str, OrderBook] = {s: OrderBook(s) for s in symbols}
        self.queue = queue # Use the provided queue
        self.config = Config # Store config for WS_RECEIVE_TIMEOUT_S
        self.status_tracker = status_tracker # Store status_tracker
        logger.info(f"OrderBookManager initialized for symbols: {symbols}")

    def get_imbalance(self, symbol: str) -> float:
        """
        Retrieves the order book imbalance ratio for a given symbol.
        """
        if symbol in self.order_books:
            return self.order_books[symbol].get_imbalance_ratio()
        else:
            logger.warning(f"Attempted to get imbalance for untracked symbol: {symbol}")
            return 0.0 # Return a default value or raise an error

    async def _subscribe_message(self, symbol: str) -> Dict[str, Any]:
        """Generates the subscription message for a given symbol."""
        # Bybit uses SYMBOL (e.g., BTCUSDT) not SYMBOL/QUOTE (e.g., BTC/USDT)
        normalized_symbol = symbol.replace('/', '')
        return {
            "op": "subscribe",
            "args": [f"orderbook.50.{normalized_symbol}"] # Depth 50 as requested
        }

    async def orderbook_ws_consumer(self, status_tracker): # Add status_tracker parameter
        """
        Connects to Bybit WebSocket, subscribes to order book updates,
        and manages local order books. It uses the symbols initialized with the manager.
        """
        uri = BYBIT_WS_URL
        reconnect_delay = 1
        max_reconnect_delay = 60
        reconnect_attempts = 0

        while True:
            try:
                async with websockets.connect(uri) as ws:
                    logger.info(f"Connected to Bybit OrderBook WebSocket: {uri}. Subscribing to order books for {self.order_books.keys()}")
                    
                    # Subscribe to all symbols managed by this instance
                    for symbol in self.order_books.keys():
                        subscribe_msg = await self._subscribe_message(symbol)
                        await ws.send(json.dumps(subscribe_msg))
                        logger.info(f"Sent orderbook subscription for {symbol}: {subscribe_msg}")

                    reconnect_attempts = 0 # Reset reconnect delay on successful connection

                    while True:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=self.config.WS_RECEIVE_TIMEOUT_S)
                            data = json.loads(message)

                            if 'op' in data and data['op'] == 'subscribe':
                                logger.info(f"Orderbook subscription acknowledgment: {data}")
                                continue

                            if data.get('type') == 'snapshot':
                                symbol_name = data['data']['s']
                                if symbol_name.endswith('USDT'): # Assuming USDT pairs, convert back to BTC/USDT format
                                    symbol_name = symbol_name.replace('USDT', '/USDT')
                                
                                if symbol_name in self.order_books:
                                    ob = self.order_books[symbol_name]
                                    ob.update_from_snapshot(data['data'])
                                    mid_price = ob.get_mid_price()
                                    imbalance = ob.get_imbalance_ratio()
                                    ob_event = OrderBookEvent(
                                        exchange="bybit",
                                        symbol=symbol_name,
                                        timestamp=ob.last_update_timestamp,
                                        mid_price=mid_price,
                                        imbalance=imbalance
                                    )
                                    await self.queue.put(ob_event)
                                    logger.debug(f"Orderbook snapshot received for {symbol_name}, event put to queue.")
                                else:
                                    logger.warning(f"Received snapshot for untracked symbol: {symbol_name}")
                            elif data.get('type') == 'delta':
                                symbol_name = data['data']['s']
                                if symbol_name.endswith('USDT'): # Assuming USDT pairs, convert back to BTC/USDT format
                                    symbol_name = symbol_name.replace('USDT', '/USDT')
                                
                                if symbol_name in self.order_books:
                                    ob = self.order_books[symbol_name]
                                    ob.update_from_delta(data['data'])
                                    mid_price = ob.get_mid_price()
                                    imbalance = ob.get_imbalance_ratio()
                                    ob_event = OrderBookEvent(
                                        exchange="bybit",
                                        symbol=symbol_name,
                                        timestamp=ob.last_update_timestamp,
                                        mid_price=mid_price,
                                        imbalance=imbalance
                                    )
                                    await self.queue.put(ob_event)
                                    logger.debug(f"Orderbook delta received for {symbol_name}, event put to queue.")
                                else:
                                    logger.warning(f"Received delta for untracked symbol: {symbol_name}")
                            else:
                                logger.debug(f"Received unknown message type: {data.get('type')}, data: {data}")

                        except asyncio.TimeoutError:
                            # logger.debug(f"Bybit OrderBook WebSocket ws.recv() timed out after {Config.WS_RECEIVE_TIMEOUT_S}s.")
                            # Bybit sends pings, so no need to send explicitly unless connection is truly dead.
                            pass
                        except websockets.exceptions.ConnectionClosedOK:
                            logger.info("Bybit OrderBook WebSocket connection closed gracefully.")
                            break
                        except Exception as e:
                            logger.error(f"Error processing Bybit OrderBook WebSocket message: {e}. Full message: {data}")
                            break # Break to trigger reconnect

            except websockets.exceptions.ConnectionClosedOK:
                logger.info("Bybit OrderBook WebSocket connection closed gracefully.")
            except websockets.exceptions.WebSocketException as e:
                logger.error(f"Bybit OrderBook WebSocket connection error: {e}. Reconnecting in {reconnect_delay}s.")
                status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_ORDERBOOK") # Increment error counter
            except Exception as e:
                logger.critical(f"Unexpected error in Bybit OrderBook WebSocket consumer: {e}. Reconnecting in {reconnect_delay}s.")
                status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_ORDERBOOK") # Increment error counter
            
            reconnect_attempts += 1
            if reconnect_attempts > Config.MAX_RECONNECT_ATTEMPTS:
                logger.critical(f"Bybit OrderBook WebSocket consumer failed to reconnect after {Config.MAX_RECONNECT_ATTEMPTS} attempts. Exiting consumer.")
                break # Exit the consumer loop

            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)