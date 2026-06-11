import logging
from datetime import datetime
from typing import Literal, Optional, Dict, Any
import csv
import os

TRADE_LOG_FILE = "data/live/live/trade_log.csv"

class Position:
    def __init__(self,
                 symbol: str,
                 size: float,
                 entry_price: float,
                 position_type: Literal['long', 'short'],
                 timestamp: float,
                 target_price: Optional[float] = None,
                 stop_price: Optional[float] = None,
                 order_id: Optional[str] = None,
                 state: Literal['OPEN', 'CLOSED'] = 'OPEN', # New: Position state
                 close_reason: Optional[str] = None): # New: Reason for closing
        
        # Assertions for initial state
        assert size > 0, "Position size must be greater than 0."
        assert position_type in ['long', 'short'], "Position type must be 'long' or 'short'."
        assert state in ['OPEN', 'CLOSED'], "Position state must be 'OPEN' or 'CLOSED'."

        self.symbol = symbol
        self.size = size
        self.entry_price = entry_price
        self.position_type = position_type
        self.unrealized_pnl = 0.0
        self.realized_pnl = 0.0
        self.timestamp = timestamp
        self.target_price = target_price
        self.stop_price = stop_price
        self.order_id = order_id
        self.current_value = size * entry_price # Initial value
        self.state = state
        self.close_reason = close_reason

        logging.debug(f"Position created: {self}")

    def update_unrealized_pnl(self, current_price: float):
        """
        Updates the unrealized PnL of the position.
        """
        if self.position_type == 'long':
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        elif self.position_type == 'short':
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
        logging.debug(f"[{self.symbol}] Unrealized PnL updated to: {self.unrealized_pnl:.2f} at price {current_price:.2f}")

    def update_current_value(self, current_price: float):
        """
        Updates the current market value of the position in USDT.
        """
        self.current_value = self.size * current_price
        logging.debug(f"[{self.symbol}] Current position value updated to: {self.current_value:.2f} USDT at price {current_price:.2f}")

    def close_position(self, closing_price: float, closing_timestamp: float, close_reason: str):
        """
        Calculates realized PnL and hold duration when the position is closed.
        Enforces state transition and requires a close reason.
        """
        assert self.state == 'OPEN', "Cannot close a position that is not OPEN."
        assert close_reason is not None and close_reason != "", "A close reason must be provided."
        
        if self.position_type == 'long':
            self.realized_pnl = (closing_price - self.entry_price) * self.size
        elif self.position_type == 'short':
            self.realized_pnl = (self.entry_price - closing_price) * self.size
        self.unrealized_pnl = 0.0 # Reset unrealized PnL after closing
        self.state = 'CLOSED'
        self.close_reason = close_reason
        
        hold_duration_seconds = (closing_timestamp - self.timestamp) / 1000 # Convert ms to seconds
        logging.info(f"[{self.symbol}] Position closed at {closing_price:.2f}. "
                     f"Realized PnL: {self.realized_pnl:.2f}. "
                     f"Hold Duration: {hold_duration_seconds:.2f} seconds. Reason: {self.close_reason}")
        self.save_trade_to_csv(closing_price, closing_timestamp, hold_duration_seconds)

    def save_trade_to_csv(self, closing_price: float, closing_timestamp: float, hold_duration_seconds: float):
        """
        Saves the completed trade details to a CSV file.
        """
        file_exists = os.path.isfile(TRADE_LOG_FILE)
        with open(TRADE_LOG_FILE, 'a', newline='') as csvfile:
            fieldnames = [
                'timestamp', 'closing_timestamp', 'symbol', 'position_type',
                'size', 'entry_price', 'closing_price', 'realized_pnl',
                'hold_duration_seconds', 'target_price', 'stop_price', 'close_reason'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            writer.writerow({
                'timestamp': self.timestamp,
                'closing_timestamp': closing_timestamp,
                'symbol': self.symbol,
                'position_type': self.position_type,
                'size': self.size,
                'entry_price': self.entry_price,
                'closing_price': closing_price,
                'realized_pnl': self.realized_pnl,
                'hold_duration_seconds': hold_duration_seconds,
                'target_price': self.target_price,
                'stop_price': self.stop_price,
                'close_reason': self.close_reason
            })
        logging.debug(f"Trade details for {self.symbol} saved to {TRADE_LOG_FILE}")

    def __repr__(self):
        return (f"Position(symbol='{self.symbol}', type='{self.position_type}', "
                f"size={self.size}, entry_price={self.entry_price}, "
                f"unrealized_pnl={self.unrealized_pnl}, "
                f"realized_pnl={self.realized_pnl:.2f}, "
                f"target_price={self.target_price if self.target_price is not None else 'None'}, "
                f"stop_price={self.stop_price if self.stop_price is not None else 'None'}, "
                f"state='{self.state}', close_reason='{self.close_reason if self.close_reason else 'N/A'}')")
