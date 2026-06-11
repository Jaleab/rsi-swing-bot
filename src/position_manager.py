import logging
from typing import Dict, Any, Optional, Literal, List
from datetime import datetime
from src.config import Config
from src.abstract_exchange import AbstractExchangeClient
from src.position import Position
from src.guards import GuardResult # Import GuardResult
from src.metrics_exporter import MetricsExporter # Import MetricsExporter

class PositionManager:
    """
    Manages position sizing, stop-loss/take-profit calculations, and enforces risk limits.
    """

    def __init__(self, config: Config, exchange_client: AbstractExchangeClient, metrics_exporter_obj: MetricsExporter, status_tracker):
        self.config = config
        self.exchange_client = exchange_client
        self.metrics_exporter = metrics_exporter_obj
        self.status_tracker = status_tracker # Store the StatusTracker object
        self.current_positions: Dict[str, Position] = {}
        logging.info("PositionManager initialized.")

    async def initialize(self):
        """
        Initializes the PositionManager by fetching current open positions from the exchange.
        """
        if self.config.LIVE_MODE:
            try:
                # Assuming get_positions returns a list of dicts, convert them to Position objects
                positions_data = await self.exchange_client.get_positions()
                for p_data in positions_data:
                    # Assuming the structure of p_data is compatible with Position.__init__
                    # Need to map dict keys to Position constructor arguments
                    # This is a simplified mapping, adjust as per actual p_data structure
                    position_type = 'long' if p_data.get('side') == 'Long' else 'short' # Adjust 'side' key and values as per exchange client output
                    position = Position(
                        symbol=p_data['symbol'],
                        size=float(p_data['size']),
                        entry_price=float(p_data['entry_price']),
                        position_type=position_type,
                        timestamp=p_data.get('timestamp', datetime.now().timestamp() * 1000),
                        target_price=float(p_data.get('target_price', 0.0)),
                        stop_price=float(p_data.get('stop_price', 0.0)),
                        order_id=p_data.get('order_id')
                    )
                    self.current_positions[position.symbol] = position
                logging.info(f"Initialized with {len(self.current_positions)} existing positions.")
                logging.info(f"Initialized with {len(self.current_positions)} existing positions.")
            except Exception as e:
                logging.error(f"Failed to fetch initial positions from exchange: {e}")
                self.current_positions = {}
        else:
            logging.info("Running in SIM_MODE, no real positions to fetch.")

    async def determine_position_size(self, symbol: str, current_price: float) -> float:
        """
        Determines the optimal position size in base currency (e.g., BTC for BTC/USDT)
        based on risk parameters and available balance.
        """
        if self.config.SIM_MODE:
            # In simulation, use a fixed USDT amount for simplicity or a simulated balance.
            # For now, let's assume a fixed USDT amount for position sizing in simulation.
            usdt_to_invest = self.config.POSITION_USDT
            logging.debug(f"[{symbol}] Simulation mode: Fixed USDT investment of {usdt_to_invest}")
        else:
            # Live mode: calculate based on available balance and risk per trade
            try:
                balance = await self.exchange_client.get_balance(currency='USDT')
                available_usdt = balance.get('free', 0.0)
                if available_usdt <= 0:
                    logging.warning(f"[{symbol}] No available USDT balance to open a position.")
                    return 0.0

                if self.config.POSITION_USDT > 0:
                    # Use fixed position size if configured
                    usdt_to_invest = min(self.config.POSITION_USDT, available_usdt)
                else:
                    # Calculate position size based on risk per trade
                    risk_amount_usdt = available_usdt * self.config.RISK_PER_TRADE_PERCENT
                    # For now, a very simplified approach: assume stop loss is at MIN_STOP_LOSS_PERCENT
                    # A more advanced PositionManager would take the signal's stop_price into account
                    if self.config.MIN_STOP_LOSS_PERCENT > 0:
                        position_size_usdt = risk_amount_usdt / self.config.MIN_STOP_LOSS_PERCENT
                        usdt_to_invest = min(position_size_usdt, available_usdt)
                    else:
                        usdt_to_invest = available_usdt * 0.1 # Fallback to 10% of available if no SL defined

                logging.info(f"[{symbol}] Live mode: Available USDT: {available_usdt:.2f}, "
                             f"Investing: {usdt_to_invest:.2f} USDT")

            except Exception as e:
                logging.error(f"[{symbol}] Error determining position size: {e}. Using default.")
                usdt_to_invest = self.config.DEFAULT_POSITION_SIZE_USDT # Fallback

        if usdt_to_invest < self.config.MIN_POSITION_SIZE_USDT:
            logging.warning(f"[{symbol}] Calculated position size {usdt_to_invest:.2f} USDT "
                            f"is below minimum {self.config.MIN_POSITION_SIZE_USDT:.2f} USDT.")
            return 0.0

        # Convert USDT amount to base currency quantity
        quantity = usdt_to_invest / current_price
        logging.info(f"[{symbol}] Determined position size: {quantity:.6f} (Base Currency) for {usdt_to_invest:.2f} USDT.")
        return quantity

    def calculate_dynamic_sl_tp(self,
                                signal_price: float,
                                signal_direction: int, # 1 for long, -1 for short
                                support_price: Optional[float],
                                resistance_price: Optional[float]) -> Dict[str, Optional[float]]:
        """
        Calculates dynamic stop-loss and take-profit levels based on signal and cluster data.
        """
        stop_price: Optional[float] = None
        target_price: Optional[float] = None

        if self.config.USE_DYNAMIC_SLTP:
            if signal_direction == 1: # Long position
                # Stop loss at support with buffer, or fixed percentage if no support
                if support_price:
                    stop_price = support_price * (1 - self.config.SL_BUFFER)
                else:
                    stop_price = signal_price * (1 - self.config.STOP_LOSS_PERCENT)
                
                # Take profit at resistance with buffer, or fixed percentage
                if resistance_price:
                    target_price = resistance_price * (1 + self.config.TP_BUFFER)
                else:
                    target_price = signal_price * (1 + self.config.TAKE_PROFIT_PERCENT)

            elif signal_direction == -1: # Short position
                # Stop loss at resistance with buffer, or fixed percentage if no resistance
                if resistance_price:
                    stop_price = resistance_price * (1 + self.config.SL_BUFFER)
                else:
                    stop_price = signal_price * (1 + self.config.STOP_LOSS_PERCENT)

                # Take profit at support with buffer, or fixed percentage
                if support_price:
                    target_price = support_price * (1 - self.config.TP_BUFFER)
                else:
                    target_price = signal_price * (1 - self.config.TAKE_PROFIT_PERCENT)
        
            # Enforce minimum SL and maximum TP percentages
            if stop_price:
                if signal_direction == 1: # Long
                    min_sl_abs = signal_price * (1 - self.config.MIN_STOP_LOSS_PERCENT)
                    stop_price = max(stop_price, min_sl_abs)
                else: # Short
                    min_sl_abs = signal_price * (1 + self.config.MIN_STOP_LOSS_PERCENT)
                    stop_price = min(stop_price, min_sl_abs)
            
            if target_price:
                if signal_direction == 1: # Long
                    max_tp_abs = signal_price * (1 + self.config.MAX_TAKE_PROFIT_PERCENT)
                    target_price = min(target_price, max_tp_abs)
                else: # Short
                    max_tp_abs = signal_price * (1 - self.config.MAX_TAKE_PROFIT_PERCENT)
                    target_price = max(target_price, max_tp_abs)
            
            logging.debug(f"Dynamic SL/TP: Signal Price: {signal_price:.2f}, Signal Dir: {signal_direction}, "
                          f"Support: {support_price:.2f if support_price else 'N/A'}, "
                          f"Resistance: {resistance_price:.2f if resistance_price else 'N/A'}, "
                          f"Calculated SL: {stop_price:.2f if stop_price else 'N/A'}, "
                          f"Calculated TP: {target_price:.2f if target_price else 'N/A'}")

        # If dynamic SL/TP is not used or calculation results in None, fall back to fixed percentages
        if not stop_price:
            stop_price = signal_price * (1 - self.config.STOP_LOSS_PERCENT) if signal_direction == 1 else \
                         signal_price * (1 + self.config.STOP_LOSS_PERCENT)
        if not target_price:
            target_price = signal_price * (1 + self.config.TAKE_PROFIT_PERCENT) if signal_direction == 1 else \
                           signal_price * (1 - self.config.TAKE_PROFIT_PERCENT)

        return {"stop_price": stop_price, "target_price": target_price}

    def has_open_position(self, symbol: str) -> bool:
        """Checks if there is an open position for the given symbol."""
        return symbol in self.current_positions and self.current_positions[symbol].size > 0

    def get_open_position(self, symbol: str) -> Optional[Position]:
        """Returns the open position details for a given symbol, if any."""
        return self.current_positions.get(symbol)

    def update_position(self, symbol: str, position: Position):
        """Updates the internal tracking of an open position."""
        is_new_position = symbol not in self.current_positions
        self.current_positions[symbol] = position
        logging.info(f"[{symbol}] Position updated: {position}")
        if is_new_position:
            self.metrics_exporter.update_open_positions_count(self.get_total_open_positions())

    def remove_position(self, symbol: str):
        """
        Removes a closed position from internal tracking.
        Asserts that the position is in a 'CLOSED' state before removal.
        """
        if symbol in self.current_positions:
            position_to_remove = self.current_positions[symbol]
            assert position_to_remove.state == 'CLOSED', f"[{symbol}] Attempted to remove an OPEN position. Position must be CLOSED first."
            del self.current_positions[symbol]
            logging.info(f"[{symbol}] Position removed from tracking.")
            self.metrics_exporter.update_open_positions_count(self.get_total_open_positions())

    def can_open_position_for_symbol(self, symbol: str) -> GuardResult:
        """
        Checks if a new position can be opened for a specific symbol based on
        MAX_POSITIONS_PER_SYMBOL and TRADE_COOLDOWN_SECONDS.
        Returns a GuardResult.
        """
        # Check max positions per symbol
        if self.has_open_position(symbol) and self.config.MAX_POSITIONS_PER_SYMBOL == 1:
            return GuardResult(
                allowed=False,
                reason=f"Only one open position per symbol is supported. Already have an open position for {symbol}.",
                guard_name="MAX_POSITIONS_PER_SYMBOL",
                details=f"Symbol: {symbol}, Max: {self.config.MAX_POSITIONS_PER_SYMBOL}"
            )
        
        # Check trade cooldown
        current_time = datetime.now().timestamp()
        if self.status_tracker.status[symbol].last_trade_timestamp != 0 and \
           (current_time - self.status_tracker.status[symbol].last_trade_timestamp < self.config.TRADE_COOLDOWN_SECONDS):
            return GuardResult(
                allowed=False,
                reason=f"Trade cooldown active for {symbol}. Last trade was less than {self.config.TRADE_COOLDOWN_SECONDS} seconds ago.",
                guard_name="TRADE_COOLDOWN",
                details=f"Symbol: {symbol}, Last Trade: {self.status_tracker.status[symbol].last_trade_timestamp}, Cooldown: {self.config.TRADE_COOLDOWN_SECONDS}"
            )
        
        return GuardResult(allowed=True, reason="Allowed", guard_name="NONE")

    def can_open_any_position(self) -> GuardResult:
        """
        Checks if a new position can be opened based on MAX_TOTAL_OPEN_POSITIONS.
        Returns a GuardResult.
        """
        if len(self.current_positions) >= self.config.MAX_TOTAL_OPEN_POSITIONS:
            return GuardResult(
                allowed=False,
                reason=f"Max total open positions reached ({len(self.current_positions)}/{self.config.MAX_TOTAL_OPEN_POSITIONS}).",
                guard_name="MAX_TOTAL_OPEN_POSITIONS",
                details=f"Current: {len(self.current_positions)}, Max: {self.config.MAX_TOTAL_OPEN_POSITIONS}"
            )
        return GuardResult(allowed=True, reason="Allowed", guard_name="NONE")

    def get_total_open_positions(self) -> int:
        """Returns the total number of open positions."""
        return len(self.current_positions)

    async def open_position(self,
                            symbol: str,
                            signal_direction: Literal['buy', 'sell'],
                            current_price: float,
                            exchange_client: AbstractExchangeClient,
                            order_type: Literal['market', 'limit'],
                            signal_stats_tracker, # Pass the SignalStatsTracker object for the specific symbol
                            cluster_snapshot: Dict): # Add cluster_snapshot to parameters
        """
        Opens a new position by placing an order on the exchange and updates internal tracking.
        """
        # --- Safety Checks ---
        if self.config.ENABLE_SAFE_MODE and self.status_tracker.status[symbol].safe_mode_active:
            guard_result = GuardResult(allowed=False, reason="Safe mode is active.", guard_name="SAFE_MODE")
            self.status_tracker.update_guard_metrics(symbol, guard_result)
            self.status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
            return

        guard_result_total_pos = self.can_open_any_position()
        if not guard_result_total_pos.allowed:
            self.status_tracker.update_guard_metrics(symbol, guard_result_total_pos)
            self.status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result_total_pos.guard_name})")
            return
        
        guard_result_symbol_pos = self.can_open_position_for_symbol(symbol)
        if not guard_result_symbol_pos.allowed:
            self.status_tracker.update_guard_metrics(symbol, guard_result_symbol_pos)
            self.status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result_symbol_pos.guard_name})")
            return

        side = 'buy' if signal_direction == 'buy' else 'sell'
        quantity = await self.determine_position_size(symbol, current_price)

        if quantity == 0.0:
            guard_result = GuardResult(allowed=False, reason="Position size calculated to be 0.", guard_name="ZERO_QUANTITY")
            self.status_tracker.update_guard_metrics(symbol, guard_result)
            self.status_tracker.update_status(symbol, notes=f"SKIPPED_TRADE ({guard_result.guard_name})")
            return

        # Determine support and resistance from cluster_snapshot
        support_price = None
        resistance_price = None
        if cluster_snapshot and cluster_snapshot["top_clusters"]:
            # Simplified logic: use the centroid of the strongest cluster as a potential support/resistance
            # More advanced logic might consider side-specific clusters or multiple clusters
            # For a long, support is below current price, resistance above. For a short, vice-versa.
            # Here we assume the top cluster is the most relevant, and its centroid acts as a pivot.
            top_cluster = cluster_snapshot["top_clusters"][0]
            if signal_direction == 'buy': # Long position
                if top_cluster["centroid_price"] < current_price:
                    support_price = top_cluster["centroid_price"]
                else:
                    resistance_price = top_cluster["centroid_price"]
            else: # Short position
                if top_cluster["centroid_price"] > current_price:
                    resistance_price = top_cluster["centroid_price"]
                else:
                    support_price = top_cluster["centroid_price"]


        # Calculate dynamic SL/TP
        signal_dir_int = 1 if signal_direction == 'buy' else -1
        sl_tp_levels = self.calculate_dynamic_sl_tp(
            signal_price=current_price,
            signal_direction=signal_dir_int,
            support_price=support_price,
            resistance_price=resistance_price
        )
        calculated_stop_price = sl_tp_levels["stop_price"]
        calculated_target_price = sl_tp_levels["target_price"]

        logging.info(f"[{symbol}] Calculated SL: {calculated_stop_price:.2f}, TP: {calculated_target_price:.2f}")

        try:
            order = await exchange_client.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=current_price if order_type == 'limit' else None,
                params={'takeProfit': calculated_target_price, 'stopLoss': calculated_stop_price} if self.config.USE_DYNAMIC_SLTP else {}
            )
            logging.info(f"[{symbol}] Placed {side} order for {quantity} at {current_price}. Order ID: {order.get('orderId')}")

            # Create a Position object and update internal tracking.
            # The checks for max positions are handled by the can_open_any_position and can_open_position_for_symbol guards.
            position = Position(
                symbol=symbol,
                size=quantity,
                entry_price=current_price,
                position_type='long' if signal_direction == 'buy' else 'short', # Convert 'buy'/'sell' to 'long'/'short'
                timestamp=datetime.now().timestamp() * 1000,
                target_price=calculated_target_price, # Use calculated TP
                stop_price=calculated_stop_price, # Use calculated SL
                order_id=order.get('orderId'),
                state='OPEN' # Explicitly set state to OPEN
            )
            self.update_position(symbol, position)
            self.status_tracker.status[symbol].last_trade_timestamp = datetime.now().timestamp()

            # Update metrics
            self.metrics_exporter.update_open_positions_count(self.get_total_open_positions())
            self.metrics_exporter.update_unrealized_pnl(symbol, position.unrealized_pnl)
            self.metrics_exporter.update_realized_pnl(symbol, position.realized_pnl)
            self.metrics_exporter.update_position_entry_price(symbol, position.entry_price)
            self.metrics_exporter.update_position_target_price(symbol, position.target_price if position.target_price else 0)
            self.metrics_exporter.update_position_stop_price(symbol, position.stop_price if position.stop_price else 0)
            self.metrics_exporter.update_position_current_value_usdt(symbol, position.current_value)
            self.metrics_exporter.update_mark_price(symbol, current_price)

            # Update metrics for target and stop price
            self.metrics_exporter.update_target_price(symbol, calculated_target_price if calculated_target_price else 0)
            self.metrics_exporter.update_stop_price(symbol, calculated_stop_price if calculated_stop_price else 0)

            # Update signal stats with the outcome
            timestamp = datetime.now().isoformat()
            mode = 'sim' if Config.SIM_MODE else 'live'
            trade_type = 'LONG' if signal_direction == 'buy' else 'SHORT'
            signal_type = 'manual_open_position'
            confidence = 1.0
            reason = 'initial_position_open'
            tp_val = calculated_target_price if calculated_target_price else 0
            sl_val = calculated_stop_price if calculated_stop_price else 0

            signal_stats_tracker.add_trade(
                timestamp, mode, trade_type, signal_type,
                current_price, quantity, confidence, reason,
                tp_val, sl_val, symbol
            )

        except Exception as e:
            logging.error(f"[{symbol}] Error placing order: {e}", exc_info=True)
            timestamp = datetime.now().isoformat()
            mode = 'sim' if Config.SIM_MODE else 'live'
            trade_type = 'LONG' if signal_direction == 'buy' else 'SHORT'
            signal_type = 'manual_open_position_failed'
            confidence = 0.0
            reason = f'order_placement_failed: {e}'
            tp_val = calculated_target_price if calculated_target_price else 0
            sl_val = calculated_stop_price if calculated_stop_price else 0

            signal_stats_tracker.add_trade(
                timestamp, mode, trade_type, signal_type,
                current_price, quantity, confidence, reason,
                tp_val, sl_val, symbol
            )

# Example Usage (for testing)
async def main_test():
    logging.basicConfig(level=logging.INFO)
    from src.config import Config

    # Mock AbstractExchangeClient for testing PositionManager
    class MockExchangeClient(AbstractExchangeClient):
        def __init__(self, api_key: str, api_secret: str, testnet: bool):
            super().__init__(api_key, api_secret, testnet)
            self._balance = {'USDT': {'total': 10000.0, 'free': 9500.0, 'used': 500.0}}
            self._positions = {}

        async def get_balance(self, currency: str = 'USDT') -> Dict[str, Any]:
            logging.info(f"MockExchangeClient: Fetching balance for {currency}")
            return self._balance.get(currency, {'total': 0.0, 'free': 0.0, 'used': 0.0})

        async def place_order(self, symbol: str, side: str, order_type: str, quantity: float, price: Optional[float] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            logging.info(f"MockExchangeClient: Placing {side} {order_type} order for {quantity} {symbol} at {price}")
            return {"orderId": "mock_order_123", "symbol": symbol, "status": "open", "side": side, "amount": quantity}

        async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
            logging.info(f"MockExchangeClient: Cancelling order {order_id} for {symbol}")
            return {"orderId": order_id, "status": "canceled"}

        async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
            logging.info(f"MockExchangeClient: Fetching open orders for {symbol}")
            return []

        async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
            logging.info(f"MockExchangeClient: Fetching status for order {order_id} for {symbol}")
            return {"orderId": order_id, "status": "closed", "filled": 1.0}

        async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
            logging.info(f"MockExchangeClient: Fetching positions for {symbol}")
            return list(self._positions.values())
        
        async def get_order_book(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
            logging.info(f"MockExchangeClient: Fetching order book for {symbol}")
            return {'bids': [[100.0, 10.0]], 'asks': [[101.0, 10.0]]}

        async def get_recent_trades(self, symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
            logging.info(f"MockExchangeClient: Fetching recent trades for {symbol}")
            return []

    # Override config for testing
    Config.LIVE_MODE = True
    Config.SIM_MODE = False
    Config.POSITION_USDT = 100.0
    Config.RISK_PER_TRADE_PERCENT = 0.01
    Config.MIN_STOP_LOSS_PERCENT = 0.005
    Config.MAX_TAKE_PROFIT_PERCENT = 0.03
    Config.USE_DYNAMIC_SLTP = True
    Config.SL_BUFFER = 0.001
    Config.TP_BUFFER = 0.002
    Config.MAX_TOTAL_OPEN_POSITIONS = 2
    Config.MAX_POSITIONS_PER_SYMBOL = 1
    Config.TRADE_COOLDOWN_SECONDS = 10 # For testing

    mock_exchange = MockExchangeClient("test_key", "test_secret", True)
    
    # Mock metrics_exporter for testing
    class MockMetricsExporter:
        def update_open_positions_count(self, count):
            print(f"MockMetricsExporter: Open positions count updated to {count}")

    mock_metrics_exporter = MockMetricsExporter()
    
    # Mock StatusTracker for testing
    class MockStatusTracker:
        def __init__(self):
            self.status = {
                "BTC/USDT": PairStatus(symbol="BTC/USDT", safe_mode_active=False),
                "ETH/USDT": PairStatus(symbol="ETH/USDT", safe_mode_active=False),
            }
    
    mock_status_tracker = MockStatusTracker()
    # Need to import PairStatus from src.status_tracker
    from src.status_tracker import PairStatus

    position_manager = PositionManager(Config, mock_exchange, mock_metrics_exporter, mock_status_tracker)
    await position_manager.initialize()
 
    symbol = "BTC/USDT"
    current_price = 30000.0
 
    # Test position sizing
    can_open_total = position_manager.can_open_any_position()
    can_open_symbol = position_manager.can_open_position_for_symbol(symbol)

    if can_open_total.allowed and can_open_symbol.allowed:
        position_size = await position_manager.determine_position_size(symbol, current_price)
        print(f"Determined position size: {position_size:.6f} BTC")
        # Simulate opening a position with explicit state
        test_position = Position(symbol=symbol, size=position_size, entry_price=current_price, position_type='long', timestamp=123, stop_price=0, target_price=0, state='OPEN')
        position_manager.update_position(symbol, test_position)
        position_manager.status_tracker.status[symbol].last_trade_timestamp = datetime.now().timestamp()
    else:
        print(f"Cannot open new position: Total: {can_open_total.reason}, Symbol: {can_open_symbol.reason}")

    # Test dynamic SL/TP
    signal_price = 30000.0
    signal_direction = 1 # Long
    support = 29500.0
    resistance = 30500.0

    sl_tp = position_manager.calculate_dynamic_sl_tp(signal_price, signal_direction, support, resistance)
    print(f"Dynamic SL/TP for Long: SL={sl_tp['stop_price']:.2f}, TP={sl_tp['target_price']:.2f}")

    # Test open position with dynamic SL/TP
    print("\n--- Test Opening Position with Dynamic SL/TP ---")
    mock_cluster_snapshot = {
        "clusters": [
            {"centroid_price": 29800.0, "volume": 1000.0},
            {"centroid_price": 30200.0, "volume": 800.0},
        ],
        "top_clusters": [
            {"centroid_price": 29800.0, "volume": 1000.0}, # Strongest cluster, acts as support for long
            {"centroid_price": 30200.0, "volume": 800.0},
        ]
    }
    
    # Test Long position
    await position_manager.open_position(
        symbol="BTC/USDT",
        signal_direction='buy',
        current_price=30000.0,
        exchange_client=mock_exchange,
        order_type='market',
        signal_stats_tracker=None, # Not relevant for this test
        cluster_snapshot=mock_cluster_snapshot
    )
    print(f"Open positions: {position_manager.get_total_open_positions()}")

    # Test Short position
    mock_cluster_snapshot_short = {
        "clusters": [
            {"centroid_price": 29800.0, "volume": 800.0},
            {"centroid_price": 30200.0, "volume": 1000.0},
        ],
        "top_clusters": [
            {"centroid_price": 30200.0, "volume": 1000.0}, # Strongest cluster, acts as resistance for short
            {"centroid_price": 29800.0, "volume": 800.0},
        ]
    }
    await position_manager.open_position(
        symbol="ETH/USDT",
        signal_direction='sell',
        current_price=2000.0,
        exchange_client=mock_exchange,
        order_type='market',
        signal_stats_tracker=None, # Not relevant for this test
        cluster_snapshot=mock_cluster_snapshot_short
    )
    print(f"Open positions: {position_manager.get_total_open_positions()}")

    signal_direction = -1 # Short
    sl_tp = position_manager.calculate_dynamic_sl_tp(signal_price, signal_direction, support, resistance)
    print(f"Dynamic SL/TP for Short: SL={sl_tp['stop_price']:.2f}, TP={sl_tp['target_price']:.2f}")

    # Test closing position
    if position_manager.has_open_position(symbol):
        # Must first close the position with a reason
        open_pos = position_manager.get_open_position(symbol)
        if open_pos:
            open_pos.close_position(open_pos.entry_price * 1.01, datetime.now().timestamp() * 1000, "MANUAL_TEST_CLOSE")
            position_manager.remove_position(symbol)
            print(f"Position for {symbol} removed.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main_test())