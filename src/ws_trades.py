import asyncio
import websockets
import json
import logging
import time as _time
from collections import deque
from typing import Dict, Any, List, Tuple

from src.config import Config
from src.events import TradeEvent

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

class TradeStreamManager:
    """Manages trade streams for multiple symbols and provides trade imbalance."""
    def __init__(self, symbols: List[str], queue: asyncio.Queue, status_tracker):
        self.symbols = symbols
        self.config = Config
        self.trade_queues: Dict[str, deque] = {s: deque(maxlen=self.config.TRADE_IMBALANCE_WINDOW_SIZE) for s in symbols}
        self.queue = queue
        self.status_tracker = status_tracker
        logger.info(f"TradeStreamManager initialized for symbols: {symbols}")

    async def _subscribe_message(self, symbol: str) -> Dict[str, Any]:
        """Generates the subscription message for a given symbol."""
        normalized_symbol = symbol.replace('/', '')
        return {
            "op": "subscribe",
            "args": [f"publicTrade.{normalized_symbol}"]
        }

    async def trade_ws_consumer(self, status_tracker): # Add status_tracker parameter
        """
        Connects to Bybit WebSocket, subscribes to public trade updates,
        and processes them. It uses the symbols initialized with the manager.
        """
        uri = BYBIT_WS_URL
        reconnect_delay = 1
        max_reconnect_delay = 60
        reconnect_attempts = 0 # Initialize reconnect_attempts

        while True:
            try:
                async with websockets.connect(uri) as ws:
                    logger.info(f"Connected to Bybit Trade WebSocket: {uri}. Subscribing to trades for {self.symbols}")
                    
                    # Subscribe to all symbols managed by this instance
                    for symbol in self.symbols:
                        subscribe_msg = await self._subscribe_message(symbol)
                        await ws.send(json.dumps(subscribe_msg))
                        logger.info(f"Sent trade subscription for {symbol}: {subscribe_msg}")

                    reconnect_attempts = 0 # Reset reconnect attempts on successful connection

                    while True:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=self.config.WS_RECEIVE_TIMEOUT_S)
                            data = json.loads(message)

                            if 'op' in data and data['op'] == 'subscribe':
                                logger.info(f"Trade subscription acknowledgment: {data}")
                                continue

                            if data.get('topic') and data['topic'].startswith('publicTrade'):
                                for trade_data in data['data']:
                                    symbol_name = trade_data['s']
                                    if symbol_name.endswith('USDT'):
                                        symbol_name = symbol_name.replace('USDT', '/USDT')

                                    if symbol_name in self.trade_queues:
                                        trade_event = TradeEvent(
                                            exchange="bybit",
                                            symbol=symbol_name,
                                            timestamp=trade_data['T'],
                                            price=float(trade_data['p']),
                                            qty=float(trade_data['v']),
                                            side="buy" if trade_data['S'] == "Buy" else "sell",
                                            trade_id=trade_data['i'],
                                            imbalance=0.0
                                        )
                                        imbalance = self.get_trade_imbalance(symbol_name)
                                        trade_event.imbalance = imbalance
                                        self.trade_queues[symbol_name].append(trade_event)
                                        await self.queue.put(trade_event)
                                        logger.debug(f"Trade event for {symbol_name}: {trade_event}")
                                    else:
                                        logger.warning(f"Received trade for untracked symbol: {symbol_name}")
                            else:
                                logger.debug(f"Received unknown message type or topic: {data.get('topic')}, data: {data}")

                        except asyncio.TimeoutError:
                            pass
                        except websockets.exceptions.ConnectionClosedOK:
                            logger.info("Bybit Trade WebSocket connection closed gracefully.")
                            break
                        except Exception as e:
                            logger.error(f"Error processing Bybit Trade WebSocket message: {e}")
                            status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_TRADES")
                            break

            except websockets.exceptions.ConnectionClosedOK:
                logger.info("Bybit Trade WebSocket connection closed gracefully.")
            except websockets.exceptions.WebSocketException as e:
                logger.error(f"Bybit Trade WebSocket connection error: {e}. Reconnecting in {reconnect_delay}s.")
                status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_TRADES")
            except Exception as e:
                logger.critical(f"Unexpected error in Bybit Trade WebSocket consumer: {e}. Reconnecting in {reconnect_delay}s.")
                status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_TRADES")
            
            reconnect_attempts += 1
            if reconnect_attempts > Config.MAX_RECONNECT_ATTEMPTS:
                logger.critical(f"Bybit Trade WebSocket consumer failed to reconnect after {Config.MAX_RECONNECT_ATTEMPTS} attempts. Exiting consumer.")
                break

            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    def get_trade_imbalance(self, symbol: str) -> float:
        """
        Calculates the trade imbalance ratio for a given symbol within the sliding window.
        A value > 1 suggests more buy volume, < 1 suggests more sell volume.
        """
        if symbol not in self.trade_queues:
            return 1.0

        buy_volume = 0.0
        sell_volume = 0.0
        
        current_time_ms = _time.time() * 1000
        
        while self.trade_queues[symbol] and \
              (current_time_ms - self.trade_queues[symbol][0].timestamp) > (self.config.TRADE_IMBALANCE_WINDOW_SIZE * 1000):
            self.trade_queues[symbol].popleft()

        for trade in self.trade_queues[symbol]:
            if trade.side == "buy":
                buy_volume += trade.qty * trade.price
            else:
                sell_volume += trade.qty * trade.price

        if sell_volume == 0:
            return 1000000.0 if buy_volume > 0 else 1.0
        if buy_volume == 0:
            return 0.0

        return buy_volume / sell_volume