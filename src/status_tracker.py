import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import pandas as pd
import os
from datetime import datetime
import asyncio
import logging

from .config import Config
from .position_manager import PositionManager

from src.guards import GuardResult # Import GuardResult

logger = logging.getLogger(__name__)

@dataclass
class PositionInfo:
    is_open: bool = False
    size_usdt: float = 0.0
    entry_price: float = 0.0
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    current_value_usdt: float = 0.0

@dataclass
class GuardMetrics:
    triggered_count: Dict[str, int] = field(default_factory=dict) # Counts by guard_name
    last_guard_reason: str = "N/A"
    trades_blocked_count: int = 0 # Total trades blocked by any guard

@dataclass
class PairStatus:
    symbol: str
    last_update_ms: int = 0
    events_count: int = 0
    cluster_volume_usdt: float = 0.0
    active_bins: int = 0
    status: str = "No Data"
    notes: str = "Awaiting events"
    mark_price: Optional[float] = None
    last_mark_price_update_ms: int = 0 # New field to track when mark_price was last updated
    rsi_value: Optional[float] = None
    last_signal_type: str = "NEUTRAL"
    last_signal_confidence: float = 0.0
    last_signal_reason: str = ""
    cluster_impact_score: float = 0.0
    last_signal_direction: int = 0
    consecutive_errors: int = 0
    last_error_timestamp: float = 0.0
    safe_mode_active: bool = False
    safe_mode_until: float = 0.0
    last_trade_timestamp: float = 0.0 # New field to track the last trade time for this symbol
    last_signal_timestamp: float = 0.0 # New field to track the last time a signal was processed for this symbol
    guard_metrics: GuardMetrics = field(default_factory=GuardMetrics) # New field for guard metrics

class StatusTracker:
    def __init__(self, symbols: list[str], position_managers: Dict[str, PositionManager]):
        self.status: Dict[str, PairStatus] = {symbol: PairStatus(symbol=symbol) for symbol in symbols}
        self.last_snapshot_save_time = 0.0
        os.makedirs(Config.DATA_DIR, exist_ok=True)
        self.position_lock = asyncio.Lock()
        self.position_managers = position_managers

    def increment_error(self, symbol: str):
        """Increments the error count for a symbol and activates safe mode if threshold is met."""
        if not Config.ENABLE_SAFE_MODE:
            return

        if symbol not in self.status:
            logger.warning(f"[{symbol}] increment_error called for unknown symbol — ignoring (WS connection issue)")
            return

        current_time = time.time()
        s = self.status[symbol]
        s.consecutive_errors += 1
        s.last_error_timestamp = current_time

        # Check if safe mode should be activated
        if s.consecutive_errors >= Config.MAX_ERRORS_PER_INTERVAL:
            if not s.safe_mode_active or current_time > s.safe_mode_until:
                s.safe_mode_active = True
                s.safe_mode_until = current_time + Config.SAFE_MODE_DURATION_S
                self.update_status(symbol, notes=f"SAFE MODE ACTIVE until {datetime.fromtimestamp(s.safe_mode_until).strftime('%H:%M:%S')}")
                logger.warning(f"[{symbol}] SAFE MODE ACTIVATED due to {s.consecutive_errors} errors in {Config.ERROR_THRESHOLD_INTERVAL_S}s. Trading disabled until {datetime.fromtimestamp(s.safe_mode_until).strftime('%H:%M:%S')}")
            
            # Reset error count after activating safe mode to prevent immediate re-trigger
            s.consecutive_errors = 0


    def deactivate_safe_mode(self, symbol: str):
        """Deactivates safe mode for a given symbol and resets error counts."""
        if symbol in self.status:
            s = self.status[symbol]
            if s.safe_mode_active:
                s.safe_mode_active = False
                s.safe_mode_until = 0.0
                s.consecutive_errors = 0
                s.last_error_timestamp = 0.0
                self.update_status(symbol, notes="Safe mode deactivated.")
                logger.info(f"[{symbol}] Safe mode deactivated.")

    def update_status(
        self,
        symbol: str,
        last_update_ms: Optional[int] = None,
        events_count: Optional[int] = None,
        cluster_volume_usdt: Optional[float] = None,
        active_bins: Optional[int] = None,
        mark_price: Optional[float] = None,
        last_signal_type: Optional[str] = None,
        last_signal_confidence: Optional[float] = None,
        last_signal_reason: Optional[str] = None,
        cluster_impact_score: Optional[float] = None,
        rsi_value: Optional[float] = None,
        notes: Optional[str] = None,
    ):
        """Updates the status for a given symbol."""
        current_time = time.time() # Define current_time at the beginning of the method

        if symbol not in self.status:
            self.status[symbol] = PairStatus(symbol=symbol)

        current_status = self.status[symbol]

        if last_update_ms is not None:
            current_status.last_update_ms = last_update_ms
        if events_count is not None:
            current_status.events_count = events_count
        if cluster_volume_usdt is not None:
            current_status.cluster_volume_usdt = cluster_volume_usdt
        if active_bins is not None:
            current_status.active_bins = active_bins
        if mark_price is not None:
            current_status.mark_price = mark_price
        if rsi_value is not None:
            current_status.rsi_value = rsi_value
        if last_signal_type is not None:
            current_status.last_signal_type = last_signal_type
        if last_signal_confidence is not None:
            current_status.last_signal_confidence = last_signal_confidence
        if last_signal_reason is not None:
            current_status.last_signal_reason = last_signal_reason
        if cluster_impact_score is not None:
            current_status.cluster_impact_score = cluster_impact_score
        
        # Prioritize explicit notes, otherwise determine status and notes based on activity
        if notes is not None:
            current_status.notes = notes
        else:
            # Determine overall status and notes based on last_update_ms
            if current_status.last_update_ms == 0:
                current_status.status = "No Data"
                current_status.notes = "Awaiting events"
            else:
                time_since_last_update = (time.time() * 1000 - current_status.last_update_ms) / 1000
                if time_since_last_update > Config.MAX_LIQUIDATION_DATA_LATENCY_SECONDS * 2:
                    current_status.status = "Inactive"
                    current_status.notes = f"Stale ({time_since_last_update:.0f}s)"
                elif time_since_last_update > Config.MAX_LIQUIDATION_DATA_LATENCY_SECONDS:
                    current_status.status = "Low Activity"
                    current_status.notes = f"Slightly stale ({time_since_last_update:.0f}s)"
                else:
                    current_status.status = "Active"
                    current_status.notes = f"Up-to-date ({time_since_last_update:.0f}s)"
        
        # Always update the 'status' field based on activity, even if explicit notes were given.
        # This ensures the overall status (Active/Inactive/Low Activity) is always current.
        if current_status.last_update_ms == 0:
            current_status.status = "No Data"
        else:
            time_since_last_update = (time.time() * 1000 - current_status.last_update_ms) / 1000
            if time_since_last_update > Config.MAX_LIQUIDATION_DATA_LATENCY_SECONDS * 2:
                current_status.status = "Inactive"
            elif time_since_last_update > Config.MAX_LIQUIDATION_DATA_LATENCY_SECONDS:
                current_status.status = "Low Activity"
            else:
                current_status.status = "Active"
        
        # Check and potentially deactivate safe mode if duration has passed
        if current_status.safe_mode_active and time.time() > current_status.safe_mode_until:
            self.deactivate_safe_mode(symbol)

        # Reset consecutive errors if no error for a while
        if current_time - current_status.last_error_timestamp > Config.ERROR_THRESHOLD_INTERVAL_S:
            current_status.consecutive_errors = 0
            current_status.last_error_timestamp = 0.0

    def update_guard_metrics(self, symbol: str, guard_result: GuardResult):
        """Updates guard metrics based on a GuardResult."""
        if symbol not in self.status:
            logger.error(f"[{symbol}] Symbol not found in status tracker for guard metric update.")
            return

        s = self.status[symbol]
        if not guard_result.allowed:
            s.guard_metrics.trades_blocked_count += 1
            s.guard_metrics.last_guard_reason = f"{guard_result.guard_name}: {guard_result.reason}"
            s.guard_metrics.triggered_count[guard_result.guard_name] = \
                s.guard_metrics.triggered_count.get(guard_result.guard_name, 0) + 1
            logger.warning(f"GUARD_BLOCK | symbol={symbol} | guard={guard_result.guard_name} | reason={guard_result.reason} | details={guard_result.details if guard_result.details else 'N/A'}")


    def update_last_signal_direction(self, symbol: str, direction: int):
        """Updates the last signal direction for a given symbol."""
        if symbol in self.status:
            self.status[symbol].last_signal_direction = direction

    def update_last_signal_confidence(self, symbol: str, confidence: float):
        """Updates the last signal confidence for a given symbol."""
        if symbol in self.status:
            self.status[symbol].last_signal_confidence = confidence

    def update_last_signal_reason(self, symbol: str, reason: str):
        """Updates the last signal reason for a given symbol."""
        if symbol in self.status:
            self.status[symbol].last_signal_reason = reason

    def update_cluster_impact_score(self, symbol: str, score: float):
        """Updates the cluster impact score for a given symbol."""
        if symbol in self.status:
            self.status[symbol].cluster_impact_score = score

    def get_position_info(self, symbol: str) -> PositionInfo:
        """Returns key information about the open position for a given symbol."""
        position_manager = self.position_managers.get(symbol)
        if position_manager:
            position = position_manager.get_open_position(symbol)
            if position:
                return PositionInfo(
                    is_open=True,
                    size_usdt=position.size * position.entry_price,
                    entry_price=position.entry_price,
                    take_profit_price=position.target_price if position.target_price else 0.0,
                    stop_loss_price=position.stop_price if position.stop_price else 0.0,
                    current_value_usdt=position.size * (self.status[symbol].mark_price if self.status[symbol].mark_price else position.entry_price)
                )
        return PositionInfo()

    def get_display_data(self) -> Dict[str, Any]:
        """Returns a dictionary suitable for rich.Table display."""
        data = {}
        for symbol, status in self.status.items():
            notes = status.notes
            if status.safe_mode_active:
                notes = f"[red]SAFE MODE ACTIVE[/red] - {notes}"
            
            guards_triggered_str = ", ".join([f"{name}={count}" for name, count in status.guard_metrics.triggered_count.items()])
            if not guards_triggered_str:
                guards_triggered_str = "None"

            data[symbol] = {
                "Symbol": status.symbol,
                "Last Update": datetime.fromtimestamp(status.last_update_ms / 1000).strftime('%H:%M:%S') if status.last_update_ms else "N/A",
                "Price": f"{status.mark_price:.2f}" if status.mark_price else "N/A",
                "Events": str(status.events_count),
                "Cluster Vol": f"{status.cluster_volume_usdt:.2f}",
                "Active Bins": str(status.active_bins),
                "Status": status.status,
                "Notes": notes,
                "Signal": status.last_signal_type,
                "Confidence": f"{status.last_signal_confidence:.2f}",
                "Reason": status.last_signal_reason,
                "Guards Triggered": guards_triggered_str,
                "Trades Blocked": str(status.guard_metrics.trades_blocked_count),
                "Last Guard Reason": status.guard_metrics.last_guard_reason
            }
        return data

    def save_snapshot_to_csv(self):
        """Saves the current status snapshot to a CSV file."""
        if time.time() - self.last_snapshot_save_time >= Config.STATUS_SNAPSHOT_INTERVAL_S:
            filepath = os.path.join(Config.DATA_DIR, "status_snapshot.csv")
            
            snapshot_data = []
            current_timestamp = datetime.now().isoformat()
            for symbol, status in self.status.items():
                snapshot_data.append({
                    "timestamp": current_timestamp,
                    "symbol": status.symbol,
                    "last_update_ms": status.last_update_ms,
                    "events_count": status.events_count,
                    "cluster_volume_usdt": status.cluster_volume_usdt,
                    "active_bins": status.active_bins,
                    "status": status.status,
                    "notes": status.notes,
                    "mark_price": status.mark_price,
                    "rsi_value": status.rsi_value,
                    "last_signal_type": status.last_signal_type,
                    "last_signal_confidence": status.last_signal_confidence,
                    "last_signal_reason": status.last_signal_reason,
                    "safe_mode_active": status.safe_mode_active,
                    "safe_mode_until": status.safe_mode_until,
                    "guards_triggered": str(status.guard_metrics.triggered_count),
                    "trades_blocked": status.guard_metrics.trades_blocked_count,
                    "last_guard_reason": status.guard_metrics.last_guard_reason
                })
            
            df = pd.DataFrame(snapshot_data)
            
            if not os.path.exists(filepath):
                df.to_csv(filepath, index=False, mode='w')
            else:
                df.to_csv(filepath, index=False, mode='a', header=False)
            
            self.last_snapshot_save_time = time.time()

    async def periodic_save_snapshot(self):
        """Periodically saves the status snapshot to CSV."""
        while True:
            await asyncio.sleep(Config.STATUS_SNAPSHOT_INTERVAL_S)
            self.save_snapshot_to_csv()
