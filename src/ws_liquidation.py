import asyncio
import websockets
import json
from collections import deque
import time
from typing import List # Import List

# Assuming these are defined elsewhere or passed in config
import logging
from src.config import Config

class LiquidationEvent:
    """
    Represents a single liquidation event from an exchange.

    side semantics:
      - "LONG": A trader's LONG position was force-liquidated.
        This means forced SELLING — bearish market pressure.
        Maps to Bybit "Buy" liquidation event (buyer forced to close = sell).
      - "SHORT": A trader's SHORT position was force-liquidated.
        This means forced BUYING — bullish market pressure.
        Maps to Bybit "Sell" liquidation event (seller forced to close = buy).
    """
    def __init__(self, exchange, symbol, timestamp, price, qty, qty_usdt, side, order_id=None):
        self.exchange = exchange
        self.symbol = symbol
        self.timestamp = timestamp
        self.price = price
        self.qty = qty
        self.qty_usdt = qty_usdt
        self.side = side
        self.order_id = order_id

def subscribe_message_for_allLiquidation(symbols):
    """Generates the subscription message for Bybit v5 allLiquidation stream.
    Topic: allLiquidation.{symbol} (per-symbol in v5)
    """
    args = [f"allLiquidation.{s.replace('/', '')}" for s in symbols]
    return json.dumps({
        "op": "subscribe",
        "args": args
    })

def parse_bybit_msg(msg):
    """Parses a raw Bybit WebSocket message."""
    return json.loads(msg)

def normalize_bybit_event(raw_event):
    """Normalizes a raw Bybit v5 liquidation event to the LiquidationEvent schema.
    v5 format: data is an ARRAY of {'T': ts, 's': symbol, 'S': side, 'v': qty, 'p': price}
    """
    events = []
    data_list = raw_event.get("data", [])
    if not isinstance(data_list, list):
        return events
    
    for item in data_list:
        symbol = item.get("s", "").replace('USDT', '/USDT')
        side = "LONG" if item.get("S") == "Buy" else "SHORT"  # Buy = long liquidated
        price = float(item.get("p", 0))
        qty = float(item.get("v", 0))
        timestamp = item.get("T", 0)
        qty_usdt = price * qty
        
        events.append(LiquidationEvent(
            exchange="bybit",
            symbol=symbol,
            timestamp=timestamp,
            price=price,
            qty=qty,
            qty_usdt=qty_usdt,
            side=side,
            order_id=f"{symbol}_{timestamp}_{side}"
        ))
    return events

async def bybit_ws_consumer(queue: asyncio.Queue, symbols: List[str], status_tracker):
    bybit_url = Config.BYBIT_WS_URL
    """
    Connects to Bybit liquidation stream, normalizes events, and puts them into the queue.
    Handles reconnects and deduplication.
    """
    deduplication_buffer = deque(maxlen=1000) # Store last 1000 order_ids for deduplication
    reconnect_delay = 1
    reconnect_attempts = 0

    while True:
        try:
            async with websockets.connect(bybit_url) as ws:
                logging.info(f"Bybit WebSocket connected. Subscribing to liquidations for {symbols}")
                await ws.send(subscribe_message_for_allLiquidation(symbols))
                reconnect_attempts = 0

                async for msg in ws:
                    raw_event = parse_bybit_msg(msg)

                    # Handle subscription confirmation FIRST (before data filter)
                    if raw_event.get("op") == "subscribe":
                        if raw_event.get("success") == True:
                            logging.info(f"Bybit liquidation subscription confirmed: {raw_event}")
                        else:
                            logging.error(f"Bybit liquidation subscription FAILED: {raw_event}")
                        continue
                    
                    # Filter out non-data messages (heartbeats, pings)
                    if "data" not in raw_event or raw_event.get("op") == "pong":
                        continue

                    for normalized_event in normalize_bybit_event(raw_event):
                        event_id = (normalized_event.order_id, normalized_event.timestamp)
                        if event_id in deduplication_buffer:
                            continue
                        deduplication_buffer.append(event_id)
                        await queue.put(normalized_event)

        except websockets.exceptions.ConnectionClosedOK:
            logging.warning("Bybit WebSocket connection closed gracefully. Reconnecting...")
        except websockets.exceptions.ConnectionClosedError as e:
            logging.error(f"Bybit WebSocket connection closed with error: {e}. Reconnecting...", exc_info=True)
            status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_LIQUIDATION") # Increment error counter
        except Exception as e:
            logging.critical(f"An unexpected error occurred in Bybit WebSocket consumer: {e}. Reconnecting...", exc_info=True)
            status_tracker.increment_error("GLOBAL_WEBSOCKET_BYBIT_LIQUIDATION") # Increment error counter
        
        reconnect_attempts += 1
        if reconnect_attempts > Config.MAX_RECONNECT_ATTEMPTS:
            logging.critical(f"Bybit WebSocket consumer failed to reconnect after {Config.MAX_RECONNECT_ATTEMPTS} attempts. Exiting consumer.")
            break # Exit the consumer loop
        
        await asyncio.sleep(reconnect_delay) # Wait before attempting to reconnect
        reconnect_delay = min(reconnect_delay * 2, 60) # Exponential backoff, max 60 seconds

def subscribe_message_binance_liquidation(symbols: List[str]):
    """Generates the subscription message for Binance liquidation stream."""
    # Binance liquidation stream for futures
    # Topic: !forceOrder@arr
    # Example: {"method": "SUBSCRIBE", "params": ["!forceOrder@arr"], "id": 1}
    # Note: Binance sends all liquidation orders for all symbols on a single stream
    return json.dumps({
        "method": "SUBSCRIBE",
        "params": ["!forceOrder@arr"],
        "id": 1
    })

def normalize_binance_event(raw_event):
    """Normalizes a raw Binance liquidation event to the LiquidationEvent schema."""
    # Example raw event structure (simplified):
    # {
    #     "e": "forceOrder",        // Event Type
    #     "E": 1678886400000,       // Event Time
    #     "o": {
    #         "s": "BTCUSDT",       // Symbol
    #         "S": "BUY",           // Side
    #         "q": "0.01",          // Original Quantity
    #         "p": "20000.0",       // Average Price
    #         "ap": "20000.0",      // Average Price
    #         "X": "MARKET",        // Order Type
    #         "l": "0.01",          // Last Filled Quantity
    #         "z": "0.01",          // Accumulated Filled Quantity
    #         "T": 1678886400000     // Trade Time
    #     }
    # }
    data = raw_event.get("o", {})
    symbol = data.get("s", "").replace('USDT', '/USDT') # Convert BTCUSDT to BTC/USDT
    side = "LONG" if data.get("S") == "BUY" else "SHORT" # Binance 'BUY' means long was liquidated
    price = float(data.get("ap", 0)) # Use average price
    qty = float(data.get("q", 0))
    timestamp = raw_event.get("o", {}).get("E", 0) # Event time in milliseconds, nested under 'o'
    # Binance liquidation stream does not provide an order_id directly for the liquidation event itself.
    # We can create a synthetic one or omit it if not strictly necessary for deduplication in this context.
    # For now, we'll use a combination of timestamp, symbol, side, and price to create a pseudo-ID.
    order_id = f"{timestamp}-{symbol}-{side}-{price}"

    qty_usdt = price * qty

    return LiquidationEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=timestamp,
        price=price,
        qty=qty,
        qty_usdt=qty_usdt,
        side=side,
        order_id=order_id
    )

async def binance_ws_consumer(queue: asyncio.Queue, symbols: List[str]):
    """
    Connects to Binance liquidation stream, normalizes events, and puts them into the queue.
    Handles reconnects and deduplication.
    """
    deduplication_buffer = deque(maxlen=1000) # Store last 1000 pseudo-order_ids for deduplication

    deduplication_buffer = deque(maxlen=1000) # Store last 1000 pseudo-order_ids for deduplication
    reconnect_delay = 1
    reconnect_attempts = 0

    while True:
        try:
            async with websockets.connect(Config.BINANCE_WS_URL) as ws:
                logging.info(f"Binance WebSocket connected. Subscribing to liquidations for {symbols}")
                await ws.send(subscribe_message_binance_liquidation(symbols))
                reconnect_attempts = 0 # Reset attempts on successful connection

                async for msg in ws:
                    raw_event = json.loads(msg)
                    
                    # Handle subscription confirmation
                    if raw_event.get("result") is not None and raw_event.get("id") == 1:
                        logging.info(f"Binance liquidation subscription confirmed: {raw_event}")
                        continue

                    # Filter out non-data messages (e.g., subscription confirmations)
                    if "e" not in raw_event or raw_event["e"] != "forceOrder":
                        continue
                    
                    normalized_event = normalize_binance_event(raw_event)

                    # Simple deduplication based on order_id and timestamp
                    event_id = (normalized_event.order_id, normalized_event.timestamp)
                    if event_id in deduplication_buffer:
                        logging.debug(f"Duplicate event skipped: {event_id}")
                        continue
                    deduplication_buffer.append(event_id)

                    await queue.put(normalized_event)
                    logging.debug(f"Put event to queue: {normalized_event.symbol} - {normalized_event.qty_usdt} USDT {normalized_event.side} at {normalized_event.price}")

        except websockets.exceptions.ConnectionClosedOK:
            logging.warning("Binance WebSocket connection closed gracefully. Reconnecting...")
        except websockets.exceptions.ConnectionClosedError as e:
            logging.error(f"Binance WebSocket connection closed with error: {e}. Reconnecting...", exc_info=True)
            status_tracker.increment_error("GLOBAL_WEBSOCKET_BINANCE_LIQUIDATION") # Increment error counter
        except Exception as e:
            logging.critical(f"An unexpected error occurred in Binance WebSocket consumer: {e}. Reconnecting...", exc_info=True)
            status_tracker.increment_error("GLOBAL_WEBSOCKET_BINANCE_LIQUIDATION") # Increment error counter
        
        reconnect_attempts += 1
        if reconnect_attempts > Config.MAX_RECONNECT_ATTEMPTS:
            logging.critical(f"Binance WebSocket consumer failed to reconnect after {Config.MAX_RECONNECT_ATTEMPTS} attempts. Exiting consumer.")
            break # Exit the consumer loop
        
        await asyncio.sleep(reconnect_delay) # Wait before attempting to reconnect
        reconnect_delay = min(reconnect_delay * 2, 60) # Exponential backoff, max 60 seconds
