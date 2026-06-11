import os
import time
from datetime import datetime

class SimpleMonitor:
    def __init__(self, status_tracker, position_manager, cluster_aggregator, live_tracker, interval_seconds=60, file_path="monitor_output.txt"):
        self.status_tracker = status_tracker
        self.position_manager = position_manager
        self.cluster_aggregator = cluster_aggregator
        self.live_tracker = live_tracker
        self.interval = interval_seconds
        self.file_path = file_path

    def format_table(self):
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        header = f"RSI Swing Bot – Live Snapshot        Last Update: {now}\n"
        header += "=" * 130 + "\n"
        header += f"{'Symbol':<10} {'Price':<10} {'RSI':<8} {'Signal':<12} {'PosSize':<10} {'PnL':<10} {'Errors':<6} {'Guards Triggered':<20} {'Trades Blocked':<16} {'Last Guard Reason':<30}\n"
        header += "-" * 130 + "\n"

        body = ""
        total_pnl = 0

        if self.status_tracker and self.live_tracker:
            for symbol, data in self.status_tracker.status.items():
                price = data.mark_price if data.mark_price is not None else 0.0
                rsi = data.rsi_value if data.rsi_value is not None else 0.0
                signal = data.last_signal_type if data.last_signal_type else 'N/A'
                errors = data.consecutive_errors

                position = self.position_manager.get_open_position(symbol) if self.position_manager else None
                pos_size = position.size if position else 0.0
                pnl = position.unrealized_pnl if position else 0.0

                guards_triggered = len(data.guard_metrics.triggered_count)
                trades_blocked = data.guard_metrics.trades_blocked_count
                last_guard_reason = data.guard_metrics.last_guard_reason if data.guard_metrics.last_guard_reason else 'N/A'
                
                body += f"{symbol:<10} {price:<10.2f} {rsi:<8.2f} {signal:<12} {pos_size:<10.3f} {pnl:<10.2f} {errors:<6} {guards_triggered:<20} {str(trades_blocked):<16} {last_guard_reason:<30}\n"
                total_pnl += pnl

        footer = "=" * 130 + "\n"
        active_symbols = len(self.status_tracker.status) if self.status_tracker else 0
        footer += f"Active Symbols: {active_symbols}   Total PnL: {total_pnl:.2f}\n"

        return header + body + footer

    def run(self):
        # Wait until initial data is populated
        import logging
        monitor_logger = logging.getLogger(__name__)
        monitor_logger.debug("SimpleMonitor waiting for initial data to be populated...")
        while not self.live_tracker.get("initial_data_populated", False):
            time.sleep(1) # Check every second

        monitor_logger.debug("SimpleMonitor: Initial data populated. Starting to write monitor output.")
        while True:
            formatted_table = self.format_table()
            with open(self.file_path, "w") as f:
                f.write(formatted_table)
            # Log the content that was just written to the file
            # This will appear in bot_output.log because simple_monitor runs in a thread
            # and executor_bot.py is configured to capture all logs to bot_output.log.
            # Using logger.debug to avoid cluttering bot_output.log too much.
            monitor_logger.debug(f"SimpleMonitor wrote to {self.file_path}:\n{formatted_table}")

            time.sleep(self.interval)