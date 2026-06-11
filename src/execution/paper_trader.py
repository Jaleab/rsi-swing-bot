import asyncio
import uuid
import logging
import time # Import time for timestamp generation
import numpy as np # Import numpy for dummy data generation
import os # Import os for file path operations
import pandas as pd # Import pandas for DataFrame operations
from datetime import datetime # Import datetime for timestamp generation
from typing import Dict, Any, List, Optional, Literal

from src.config import Config
from src.position import Position
from src.abstract_exchange import AbstractExchangeClient # For type hinting consistency

logger = logging.getLogger(__name__)

class PaperTrader(AbstractExchangeClient):
    """
    A mock exchange client for paper trading. Simulates trade execution,
    manages positions, and tracks PnL without interacting with a real exchange.
    Inherits from AbstractExchangeClient for interface consistency.
    """
    def __init__(self, config: Config, initial_balance: float):
        super().__init__(config.API_KEY, config.API_SECRET, config.TESTNET) # Call parent constructor
        self.config = config
        self.np_random_generator = np.random.default_rng(self.config.SIMULATION_RANDOM_SEED) # Initialize with seed
        self.balance: float = initial_balance
        self.open_positions: Dict[str, Position] = {} # {symbol: Position}
        self.transaction_log: List[Dict[str, Any]] = []
        self.ohlcv_data_buffer: Dict[str, pd.DataFrame] = {} # Buffer to store OHLCV data for analysis
        self.order_id_counter: int = 0
        logger.info(f"PaperTrader initialized with initial balance: {self.balance:.2f} USDT")

    async def get_balance(self, currency: str = 'USDT') -> Dict[str, Any]:
        """Simulates fetching account balance."""
        return {'total': self.balance, 'free': self.balance, 'used': 0.0}

    async def place_order(self, symbol: str, side: Literal['buy', 'sell'], order_type: Literal['market', 'limit'], quantity: float, price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Simulates placing an order."""
        self.order_id_counter += 1
        order_id = f"paper_order_{self.order_id_counter}"
        current_price = price if price is not None else self._get_current_market_price(symbol) # Use market price if limit price not provided

        if current_price == 0:
            logger.error(f"Cannot place order for {symbol}: current market price is 0.")
            return {'status': 'failed', 'info': 'Market price unknown'}

        cost = quantity * current_price
        if side == 'buy' and self.balance < cost:
            logger.warning(f"Insufficient balance to place buy order for {symbol}. Needed {cost:.2f}, available {self.balance:.2f}")
            return {'status': 'rejected', 'info': 'Insufficient balance'}

        # Simulate order execution
        if order_type == 'market':
            logger.debug(f"PaperTrader: Balance before trade: {self.balance:.2f} USDT")
            self.balance -= cost if side == 'buy' else 0 # Deduct cost for buys immediately
            logger.debug(f"PaperTrader: Balance after trade: {self.balance:.2f} USDT")

            # Create or update position
            if symbol not in self.open_positions:
                self.open_positions[symbol] = Position(
                    symbol=symbol,
                    entry_price=current_price,
                    amount=quantity,
                    side='LONG' if side == 'buy' else 'SHORT',
                    entry_timestamp=int(time.time() * 1000),
                    leverage=1 # Paper trader always uses 1x leverage for simplicity
                )
            else:
                # For simplicity, if a position already exists, we average the entry price
                # A more complex paper trader might handle partial fills, scaling in/out
                existing_pos = self.open_positions[symbol]
                if existing_pos.side == ('LONG' if side == 'buy' else 'SHORT'):
                    # Scaling in
                    total_amount = existing_pos.amount + quantity
                    existing_pos.entry_price = ((existing_pos.entry_price * existing_pos.amount) + (current_price * quantity)) / total_amount
                    existing_pos.amount = total_amount
                else:
                    logger.warning(f"Attempted to {side} {symbol} while an opposing position exists. This paper trader does not support hedging or immediate reversals for simplicity.")
                    return {'status': 'rejected', 'info': 'Opposing position exists'}
            
            self.transaction_log.append({
                'order_id': order_id,
                'symbol': symbol,
                'side': side,
                'order_type': order_type,
                'quantity': quantity,
                'price': current_price,
                'timestamp': int(time.time() * 1000),
                'status': 'filled'
            })
            logger.info(f"PaperTrader: Market {side} order for {quantity} {symbol} at {current_price:.2f} filled. Balance: {self.balance:.2f}")
            return {
                'id': order_id,
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'price': current_price,
                'amount': quantity,
                'filled': quantity,
                'remaining': 0,
                'status': 'closed', # Assuming market orders are immediately closed (filled)
                'timestamp': int(time.time() * 1000)
            }
        elif order_type == 'limit':
            logger.warning("Limit orders are not fully simulated in this basic PaperTrader. Assuming immediate fill if market price allows.")
            if (side == 'buy' and current_price <= price) or (side == 'sell' and current_price >= price):
                return await self.place_order(symbol, side, 'market', quantity, price=price, params=params)
            else:
                logger.info(f"PaperTrader: Limit {side} order for {quantity} {symbol} at {price:.2f} not filled at current price {current_price:.2f}.")
                return {'status': 'open', 'info': 'Limit order not yet filled'}
        
        return {'status': 'failed', 'info': 'Unknown error'}

    async def execute_order(self, symbol: str, order_type: str, side: str,
                            amount: float, price: Optional[float] = None,
                            position_id: Optional[str] = None,
                            params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self.place_order(symbol, side, order_type, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Simulates canceling an order."""
        logger.info(f"PaperTrader: Attempted to cancel order {order_id} for {symbol}. (Not fully implemented in basic PaperTrader as orders are assumed filled instantly).")
        return {'status': 'canceled', 'info': 'Order assumed canceled'}

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Simulates fetching open orders."""
        logger.info("PaperTrader: get_open_orders called (no open orders in basic PaperTrader).")
        return []

    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Simulates fetching order status."""
        logger.info(f"PaperTrader: get_order_status for {order_id} (orders are assumed filled instantly).")
        return {'status': 'filled', 'info': 'Order assumed filled'}

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Simulates fetching open positions."""
        if symbol:
            return [self.open_positions[symbol].to_dict()] if symbol in self.open_positions else []
        return [pos.to_dict() for pos in self.open_positions.values()]

    async def _close(self):
        """Simulates closing the connection."""
        logger.info("PaperTrader: _close called. No actual connection to close.")

    async def fetch_ohlcv(self, symbol: str, timeframe: str, status_tracker: Any, limit: int = Config.OHLCV_LIMIT) -> List[List[float]]:
        """
        Simulates fetching OHLCV data.
        Generates dummy OHLCV data based on current price from status_tracker or default sim price.
        """
        s = status_tracker.status[symbol]
        current_price = s.mark_price if s.mark_price is not None else self.config.DEFAULT_SIM_PRICE
        
        if not current_price:
            logger.warning(f"[{symbol}] No current price available in StatusTracker for dummy OHLCV in PaperTrader. Using default sim price {self.config.DEFAULT_SIM_PRICE}")
            current_price = self.config.DEFAULT_SIM_PRICE
        logger.debug(f"[{symbol}] PaperTrader: current_price for dummy OHLCV: {current_price}")

        ohlcv_data = []
        # Generate timestamps for `limit` candles, ending at the current time
        timeframe_minutes = 1 # Default to 1 minute
        if 'm' in timeframe:
            timeframe_minutes = int(timeframe.replace('m', ''))
        elif 'h' in timeframe:
            timeframe_minutes = int(timeframe.replace('h', '')) * 60
        elif 'd' in timeframe:
            timeframe_minutes = int(timeframe.replace('d', '')) * 1440 # 24 hours * 60 minutes

        timeframe_seconds = timeframe_minutes * 60
        
        base_timestamp = int(datetime.now().timestamp() * 1000) - (limit * timeframe_seconds * 1000)

        for i in range(limit):
            timestamp = base_timestamp + (i * timeframe_seconds * 1000)
            # Create some price variation around the current price
            # Introduce more significant and directional price variation for better RSI calculation
            price_change = self.np_random_generator.uniform(-0.02, 0.02) # +/- 2% change
            open_p = current_price * (1 + price_change)
            close_p = open_p * (1 + self.np_random_generator.uniform(-0.01, 0.01)) # +/- 1% change from open
            high_p = max(open_p, close_p) * (1 + self.np_random_generator.uniform(0, 0.005)) # Up to 0.5% higher
            low_p = min(open_p, close_p) * (1 - self.np_random_generator.uniform(0, 0.005)) # Up to 0.5% lower
            
            # Ensure price does not go below a reasonable threshold (e.g., 1.0)
            open_p = max(1.0, open_p)
            close_p = max(1.0, close_p)
            high_p = max(1.0, high_p)
            low_p = max(1.0, low_p)
            volume = 100 + (i * 5) + (self.np_random_generator.uniform(-20, 20)) # Add some randomness to volume

            ohlcv_data.append([timestamp, open_p, high_p, low_p, close_p, volume])
        
        df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        logger.debug(f"[{symbol}] PaperTrader: Generated dummy OHLCV data (last {len(df)} candles). DataFrame empty: {df.empty}")
        self.ohlcv_data_buffer[symbol] = df # Store the generated OHLCV data
        return df

    def save_signal_analysis_data(self, path: str = '/tmp/sim_signal_analysis.csv'):
        """
        Saves the collected OHLCV data for all symbols to a single CSV file.
        """
        if not self.ohlcv_data_buffer:
            logger.warning("No OHLCV data collected to save for signal analysis.")
            return
        
        combined_df = pd.concat(self.ohlcv_data_buffer.values()).sort_index()
        combined_df.to_csv(path)
        logger.info(f"Signal analysis data saved to {path}")

    async def get_order_book(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """Simulates fetching the order book."""
        logger.info(f"PaperTrader: get_order_book called for {symbol}. Returning dummy order book.")
        # Return a simple dummy order book
        return {
            'bids': [[139.9, 10.0], [139.8, 5.0]],
            'asks': [[140.0, 12.0], [140.1, 8.0]],
            'timestamp': int(time.time() * 1000)
        }

    async def get_recent_trades(self, symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Simulates fetching recent trades."""
        logger.info(f"PaperTrader: get_recent_trades called for {symbol}. Returning empty list.")
        return []

    def _get_current_market_price(self, symbol: str) -> float:
        """
        Helper to get the current market price for simulation.
        In a more advanced paper trader, this would come from a mock market data feed.
        For now, it's a placeholder or could retrieve from a global status tracker if available.
        """
        # This is a simplification. In a real scenario, this would come from a mock price feed.
        # For now, we'll return a dummy price or try to get it from StatusTracker if available in context.
        logger.warning(f"PaperTrader: Using dummy market price for {symbol}. This should be replaced with a proper mock market data feed.")
        # If StatusTracker is available globally or passed, use its mark_price
        # from src.status_tracker import StatusTracker # Avoid circular import, get it locally if needed
        # if StatusTracker.instance and symbol in StatusTracker.instance.status:
        #     return StatusTracker.instance.status[symbol].mark_price
        return 0.0 # Indicate price unknown if not explicitly set by the system