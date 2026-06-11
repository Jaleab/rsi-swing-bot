import ccxt.async_support as ccxt_async
from typing import Dict, Any, List, Optional
from src.abstract_exchange import AbstractExchangeClient
from src.config import Config
import logging
import asyncio
import pandas as pd # Import pandas

class BybitExchangeClient(AbstractExchangeClient):
    """
    Bybit implementation of the AbstractExchangeClient using ccxt.async_support.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool, metrics_exporter: Any):
        super().__init__(api_key, api_secret, testnet)
        self.metrics_exporter = metrics_exporter
        
        if Config.SIM_MODE:
            self.exchange = None # No real exchange connection in simulation mode
            logging.info("BybitExchangeClient initialized in SIM_MODE. No actual exchange connection established.")
        else:
            self.exchange = ccxt_async.bybit({
                'apiKey': self._api_key,
                'secret': self._api_secret,
                'options': {
                    'defaultType': 'future', # or 'spot' or 'margin'
                    'recvWindow': Config.RECV_WINDOW, # Set recvWindow in options
                    'timeout': Config.RECV_WINDOW, # Set request timeout in options
                    'adjustForTimeDifference': True, # Enable ccxt's built-in time synchronization
                    'enableRateLimit': True, # Enable rate limiting
                    'verbose': False # Set to True for debugging ccxt
                },
                'urls': {
                    'api': {
                        'public': 'https://api.bybit.com',
                        'private': 'https://api.bybit.com',
                        'test': 'https://api-testnet.bybit.com', # Testnet URL
                    },
                },
            })
            if self._testnet:
                self.exchange.set_sandbox_mode(True)
            
            self.exchange.set_headers({'X-BAPI-RECV-WINDOW': str(Config.RECV_WINDOW)})

    async def close(self):
        """Closes the exchange connection."""
        await self.exchange.close()

    async def _retry_api_call(self, method, *args, **kwargs):
        """
        Retries an API call with exponential backoff.
        """
        max_retries = Config.MAX_RECONNECT_ATTEMPTS  # Using the same config for consistency
        retry_delay = 1  # seconds

        for i in range(max_retries):
            try:
                return await method(*args, **kwargs)
            except (ccxt_async.NetworkError, ccxt_async.ExchangeError) as e:
                logging.warning(f"API call failed ({method.__name__}). Retrying in {retry_delay}s... ({e})")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)  # Exponential backoff, max 60 seconds
            except Exception as e:
                logging.error(f"An unexpected error occurred during API call ({method.__name__}): {e}")
                raise # Re-raise other unexpected errors
        
        logging.error(f"API call ({method.__name__}) failed after {max_retries} retries.")
        return None # Indicate failure after max retries

    async def get_server_time(self) -> int:
        """
        Fetches the current server time from the exchange.
        Returns:
            int: Server time in milliseconds.
        """
        try:
            server_time = await self._retry_api_call(self.exchange.fetch_time)
            if server_time is None:
                return int(time.time() * 1000) # Fallback to local time if failed after retries
            return server_time
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching server time: {e}")
            return int(time.time() * 1000) # Fallback to local time for other errors

    async def get_balance(self, currency: str = 'USDT') -> Dict[str, Any]:
        try:
            balance = await self._retry_api_call(self.exchange.fetch_balance)
            if balance is None:
                return {'total': 0.0, 'free': 0.0, 'used': 0.0}
            if currency in balance:
                return balance[currency]
            return {'total': 0.0, 'free': 0.0, 'used': 0.0}
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching balance: {e}")
            return {'total': 0.0, 'free': 0.0, 'used': 0.0}

    async def place_order(self,
                          symbol: str,
                          side: str,
                          order_type: str,
                          quantity: float,
                          price: Optional[float] = None,
                          params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            order = await self._retry_api_call(self.exchange.create_order, symbol, order_type, side, quantity, price, params)
            if order is None:
                return {}
            return order
        except Exception as e:
            logging.error(f"An unexpected error occurred while placing order: {e}")
            return {}

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        try:
            canceled_order = await self._retry_api_call(self.exchange.cancel_order, order_id, symbol)
            if canceled_order is None:
                return {}
            return canceled_order
        except Exception as e:
            logging.error(f"An unexpected error occurred while canceling order: {e}")
            return {}

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            open_orders = await self._retry_api_call(self.exchange.fetch_open_orders, symbol)
            if open_orders is None:
                return []
            return open_orders
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching open orders: {e}")
            return []

    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        try:
            order_status = await self._retry_api_call(self.exchange.fetch_order, order_id, symbol)
            if order_status is None:
                return {}
            return order_status
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching order status: {e}")
            return {}
    
    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            positions = await self._retry_api_call(self.exchange.fetch_positions, symbols=[symbol] if symbol else None)
            if positions is None:
                return []
            return positions
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching positions: {e}")
            return []

    async def get_order_book(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
        try:
            order_book = await self._retry_api_call(self.exchange.fetch_order_book, symbol, limit)
            if order_book is None:
                return {'bids': [], 'asks': []}
            return order_book
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching order book: {e}")
            return {'bids': [], 'asks': []}

    async def get_recent_trades(self, symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        try:
            recent_trades = await self._retry_api_call(self.exchange.fetch_trades, symbol, limit=limit)
            if recent_trades is None:
                return []
            return recent_trades
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching recent trades: {e}")
            return []

    async def fetch_ohlcv(self, symbol: str, timeframe: str, since: Optional[int] = None, limit: Optional[int] = None) -> List[List[float]]:
        """
        Fetches OHLCV data for a given symbol and timeframe.
        """
        if Config.SIM_MODE:
            # In SIM_MODE, return an empty list or simulated data
            logging.info(f"[{symbol}] SIM_MODE: Returning empty OHLCV data.")
            return pd.DataFrame() # Return empty DataFrame instead of empty list
            
        try:
            ohlcv = await self._retry_api_call(self.exchange.fetch_ohlcv, symbol, timeframe, since, limit)
            if ohlcv is None:
                return []
            return ohlcv
        except Exception as e:
            logging.error(f"An unexpected error occurred while fetching OHLCV for {symbol}: {e}")
            return []