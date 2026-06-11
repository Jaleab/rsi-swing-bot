import logging
import os
import csv
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class SignalQualityTracker:
    def __init__(self, output_dir: str = "/tmp"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.signal_records_file = os.path.join(output_dir, "signal_records.csv")
        self.trade_results_file = os.path.join(output_dir, "trade_results.csv")
        self._initialize_csv_files()
        logger.info(f"SignalQualityTracker initialized. Outputting to {output_dir}")

    def _initialize_csv_files(self):
        # Initialize signal records CSV
        if not os.path.exists(self.signal_records_file) or os.path.getsize(self.signal_records_file) == 0:
            with open(self.signal_records_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "symbol", "signal_type", "confidence_score",
                    "reason", "current_price", "rsi_value", "cluster_impact_score",
                    "top_cluster_price", "top_cluster_strength", "imbalance_ratio",
                    "orderbook_imbalance", "trade_imbalance", "sweep_detected"
                ])
            logger.info(f"Initialized {self.signal_records_file}")

        # Initialize trade results CSV
        if not os.path.exists(self.trade_results_file) or os.path.getsize(self.trade_results_file) == 0:
            with open(self.trade_results_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "symbol", "entry_price", "exit_price",
                    "position_type", "size", "realized_pnl", "exit_reason",
                    "signal_timestamp", "order_id"
                ])
            logger.info(f"Initialized {self.trade_results_file}")

    def record_signal(self, signal_data: Dict[str, Any]):
        """
        Records detailed information about a generated signal.
        """
        logger.debug(f"Recording signal: {signal_data.get('symbol')} - {signal_data.get('signal_type')}")
        with open(self.signal_records_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                signal_data.get("timestamp"),
                signal_data.get("symbol"),
                signal_data.get("signal_type"),
                signal_data.get("confidence_score"),
                signal_data.get("reason"),
                signal_data.get("current_price"),
                signal_data.get("rsi_value"),
                signal_data.get("cluster_impact_score"),
                signal_data.get("top_cluster_price"),
                signal_data.get("top_cluster_strength"),
                signal_data.get("imbalance_ratio"),
                signal_data.get("orderbook_imbalance"),
                signal_data.get("trade_imbalance"),
                signal_data.get("sweep_detected")
            ])

    def record_trade_result(self, trade_result_data: Dict[str, Any]):
        """
        Records the outcome of a trade (e.g., when a position is closed).
        """
        logger.debug(f"Recording trade result for {trade_result_data.get('symbol')}")
        with open(self.trade_results_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                trade_result_data.get("timestamp"),
                trade_result_data.get("symbol"),
                trade_result_data.get("entry_price"),
                trade_result_data.get("exit_price"),
                trade_result_data.get("position_type"),
                trade_result_data.get("size"),
                trade_result_data.get("realized_pnl"),
                trade_result_data.get("exit_reason"),
                trade_result_data.get("signal_timestamp"),
                trade_result_data.get("order_id")
            ])

    def flush_to_disk(self):
        """
        Ensures all buffered data is written to disk.
        For CSV, this is typically handled by 'a' mode and Python's buffering,
        but this method can be extended for more complex scenarios or explicit flushing.
        """
        logger.info("SignalQualityTracker: Flushing data to disk (CSV files are typically flushed on close/newline).")
        # In this simple CSV implementation, files are opened in 'a' mode and written line by line.
        # Python's default buffering might mean data is not immediately written to the OS.
        # For more critical flushing, one might open with buffering=0 or f.flush() after each write.
        # For this use case, the current behavior is acceptable.