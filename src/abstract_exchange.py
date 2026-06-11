import abc
from typing import Dict, Any, List, Optional

class AbstractExchangeClient(abc.ABC):
    """
    Abstract base class for cryptocurrency exchange clients.
    Defines a standardized interface for interacting with any exchange.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool):
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet

    @abc.abstractmethod
    async def get_balance(self, currency: str = 'USDT') -> Dict[str, Any]:
        """
        Retrieves the available and total balance for a specified currency.

        Args:
            currency (str): The currency to retrieve balance for (e.g., 'USDT').

        Returns:
            Dict[str, Any]: A dictionary containing balance information, e.g.,
                            {'total': 1000.0, 'free': 950.0, 'used': 50.0}.
        """
        pass

    @abc.abstractmethod
    async def place_order(self,
                          symbol: str,
                          side: str, # 'buy' or 'sell'
                          order_type: str, # 'market' or 'limit'
                          quantity: float,
                          price: Optional[float] = None,
                          params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Places a new order on the exchange.

        Args:
            symbol (str): Trading pair (e.g., 'BTC/USDT').
            side (str): Order side ('buy' or 'sell').
            order_type (str): Type of order ('market' or 'limit').
            quantity (float): The amount of base currency to buy or sell.
            price (Optional[float]): The price for 'limit' orders. Required for limit orders.
            params (Optional[Dict[str, Any]]): Additional exchange-specific parameters (e.g., {'take_profit': 1.05, 'stop_loss': 0.95}).

        Returns:
            Dict[str, Any]: A dictionary containing order details.
        """
        pass

    @abc.abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Cancels an existing order on the exchange.

        Args:
            order_id (str): The ID of the order to cancel.
            symbol (str): The symbol of the order to cancel.

        Returns:
            Dict[str, Any]: A dictionary containing cancellation confirmation.
        """
        pass

    @abc.abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves a list of active open orders.

        Args:
            symbol (Optional[str]): If provided, retrieves open orders for a specific symbol.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing open order details.
        """
        pass

    @abc.abstractmethod
    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Retrieves the current status of a specific order.

        Args:
            order_id (str): The ID of the order to check.
            symbol (str): The symbol of the order.

        Returns:
            Dict[str, Any]: A dictionary containing the order status and details.
        """
        pass
    
    @abc.abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves details of all or specific open positions.

        Args:
            symbol (Optional[str]): If provided, retrieves positions for a specific symbol.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing position details
                                  (e.g., entry price, quantity, PnL).
        """
        pass

    @abc.abstractmethod
    async def get_order_book(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Retrieves the order book for a given symbol.

        Args:
            symbol (str): The trading pair (e.g., 'BTC/USDT').
            limit (Optional[int]): The number of bids and asks to retrieve.

        Returns:
            Dict[str, Any]: A dictionary containing 'bids' and 'asks' lists.
        """
        pass

    @abc.abstractmethod
    async def get_recent_trades(self, symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves recent trades for a given symbol.

        Args:
            symbol (str): The trading pair (e.g., 'BTC/USDT').
            limit (Optional[int]): The number of recent trades to retrieve.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing trade details.
        """
        pass

    @abc.abstractmethod
    async def execute_order(self, symbol: str, order_type: str, side: str,
                            amount: float, price: Optional[float] = None,
                            position_id: Optional[str] = None,
                            params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes an order on the exchange. Wraps place_order with additional semantics.
        """
        pass

    @abc.abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str, since: Optional[int] = None, limit: Optional[int] = None) -> List[List[float]]:
        """
        Fetches OHLCV data for a given symbol and timeframe.

        Args:
            symbol (str): The trading pair (e.g., 'BTC/USDT').
            timeframe (str): The timeframe (e.g., '1m', '5m', '1h').
            since (Optional[int]): Unix timestamp in milliseconds to fetch data from.
            limit (Optional[int]): The number of candles to retrieve.

        Returns:
            List[List[float]]: A list of OHLCV data, where each inner list contains:
                                [timestamp, open, high, low, close, volume].
        """
        pass