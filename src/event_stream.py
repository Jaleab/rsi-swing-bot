import asyncio
import logging
from typing import List, AsyncGenerator, Union, Optional, Any

from src.ws_trades import TradeStreamManager
from src.ws_orderbook import OrderBookManager
from src.ws_liquidation import bybit_ws_consumer
from src.config import Config # Import Config to get symbols
from src.events import SimulatedLiquidationEvent, OrderBookEvent, TradeEvent # Import from new events module
from src.cluster_aggregator import ClusterAggregator
from src.status_tracker import StatusTracker

logger = logging.getLogger(__name__)

class EventStream:
    """
    A real-time event stream that aggregates events from WebSockets.
    In simulation mode, this class is bypassed, and events are processed directly
    from a pre-generated list. In live mode, it manages WebSocket connections
    and pushes events to a queue.
    """
    def __init__(self, event_queue: asyncio.Queue, cluster_aggregator: ClusterAggregator, config: Config, status_tracker: StatusTracker):
        self.event_queue = event_queue
        self.cluster_aggregator = cluster_aggregator
        self.config = config
        self.status_tracker = status_tracker
        self.trade_stream_manager = TradeStreamManager(self.config.SYMBOLS, self.event_queue, self.status_tracker)
        self.order_book_manager = OrderBookManager(self.config.SYMBOLS, self.event_queue, self.status_tracker)
        self.running = False

    async def start(self):
        """Starts background WebSocket consumers and queue processing for live mode."""
        if not self.config.SIM_MODE:
            self.running = True
            asyncio.create_task(self.trade_stream_manager.trade_ws_consumer(self.status_tracker))
            asyncio.create_task(self.order_book_manager.orderbook_ws_consumer(self.status_tracker))
            asyncio.create_task(bybit_ws_consumer(self.event_queue, self.config.SYMBOLS, self.status_tracker))
            asyncio.create_task(self.cluster_aggregator.periodic_save())
            logger.info("EventStream started — WebSocket consumers running in background.")
        else:
            self.running = True
            logger.info("EventStream is in SIM_MODE. WebSocket consumers bypassed.")

    async def stop(self):
        """Stops the event stream."""
        self.running = False
        logger.info("EventStream stopped.")

    async def get_latest_events(self) -> AsyncGenerator[Union[TradeEvent, OrderBookEvent, SimulatedLiquidationEvent], None]:
        """
        Yields the latest events from the aggregated queue for LIVE mode.
        This method is designed to be iterated over in an async for loop.
        In SIM_MODE, this method is not used.
        """
        if self.config.SIM_MODE:
            logger.warning("get_latest_events called in SIM_MODE. This method should not be used in simulation.")
            return # Should not be called in SIM_MODE

        while self.running:
            try:
                event = await self.event_queue.get()
                yield event
            except asyncio.CancelledError:
                logger.info("EventStream get_latest_events cancelled.")
                break
            except Exception as e:
                logger.error(f"Error getting event from queue in live mode: {e}", exc_info=True)
        logger.info("EventStream get_latest_events stopped.")