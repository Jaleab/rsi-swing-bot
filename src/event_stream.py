import asyncio
import logging
from typing import List, AsyncGenerator, Union, Optional, Any

from src.ws_trades import TradeStreamManager
from src.ws_orderbook import OrderBookManager
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
        """Starts the event stream, primarily WebSocket consumers for live mode."""
        if not self.config.SIM_MODE:
            self.running = True
            await asyncio.gather(
                self.trade_stream_manager.trade_ws_consumer(self.status_tracker),
                self.order_book_manager.orderbook_ws_consumer(self.status_tracker), # Pass status_tracker here
                self.cluster_aggregator.periodic_save(), # Start periodic saving for live mode
                self.cluster_aggregator._run_queue_consumer() # Start the queue consumer for live mode
            )
        else:
            self.running = True # Still set running to True for consistency, though it won't do much in sim mode
            logger.info("EventStream is in SIM_MODE. WebSocket consumers and queue processing are bypassed.")

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