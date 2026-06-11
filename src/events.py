from typing import Literal
from dataclasses import dataclass

@dataclass
class SimulatedLiquidationEvent:
    symbol: str
    side: Literal['buy', 'sell']
    price: float
    volume: float
    timestamp: float # Unix timestamp in milliseconds

@dataclass
class OrderBookEvent:
    exchange: str
    symbol: str
    timestamp: float # Unix timestamp in milliseconds
    mid_price: float # Added mid_price
    imbalance: float # Added imbalance

@dataclass
class TradeEvent:
    exchange: str
    symbol: str
    timestamp: float # Unix timestamp in milliseconds
    price: float
    qty: float
    side: Literal['buy', 'sell']
    trade_id: str
    imbalance: float # Added imbalance