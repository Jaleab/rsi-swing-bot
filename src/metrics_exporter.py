from prometheus_client import start_http_server, Counter, Gauge
import logging
import time
from typing import List, Dict, Any
import asyncio
from src.config import Config

logger = logging.getLogger(__name__)

class MetricsExporter:
    def __init__(self):
        self.last_metric_timestamp: float = time.time()

        # Core Bot Metrics
        self.bot_rsi = Gauge('bot_rsi', 'RSI value for trading pair', ['symbol'])
        self.bot_latency_sec = Gauge('bot_latency_sec', 'Seconds since last update per symbol', ['symbol'])
        self.bot_events_total = Gauge('bot_events_total', 'Total events processed per symbol', ['symbol'])

        # Signal Metrics
        self.bot_last_signal_direction = Gauge('bot_last_signal_direction', 'Last signal direction (1=BUY, -1=SELL, 0=NEUTRAL) per symbol', ['symbol'])
        self.bot_last_signal_confidence = Gauge('bot_last_signal_confidence', 'Last signal confidence (low, medium, high mapped to 0-1) per symbol', ['symbol'])
        self.bot_last_cluster_impact_score = Gauge('bot_last_cluster_impact_score', 'Last cluster impact score for signal generation per symbol', ['symbol'])

        # Market/Cluster Data Metrics
        self.bot_cluster_count = Gauge('bot_cluster_count', 'Number of liquidation clusters per symbol', ['symbol'])
        self.bot_top_cluster_price = Gauge('bot_top_cluster_price', 'Price of the top liquidation cluster per symbol', ['symbol'])
        self.bot_top_cluster_strength = Gauge('bot_top_cluster_strength', 'Strength of the top liquidation cluster per symbol', ['symbol'])
        self.bot_cluster_volume = Gauge('bot_cluster_volume', 'Total volume of liquidation clusters per symbol', ['symbol'])
        self.bot_active_bins = Gauge('bot_active_bins', 'Number of active price bins per symbol', ['symbol'])
        self.bot_sweep_detected = Gauge('bot_sweep_detected', 'Liquidation sweep detected (1=true, 0=false) per symbol', ['symbol'])
        self.bot_support_band = Gauge('bot_support_band', 'Price of the support band per symbol', ['symbol'])
        self.bot_resistance_band = Gauge('bot_resistance_band', 'Price of the resistance band per symbol', ['symbol'])
        self.bot_imbalance_ratio = Gauge('bot_imbalance_ratio', 'Overall imbalance ratio per symbol', ['symbol'])
        self.bot_orderbook_imbalance = Gauge('bot_orderbook_imbalance', 'Order book imbalance per symbol', ['symbol'])
        self.bot_trade_imbalance = Gauge('bot_trade_imbalance', 'Trade imbalance per symbol', ['symbol'])
        self.bot_cluster_impact_score = Gauge('bot_cluster_impact_score', 'Score representing the impact of clusters on price per symbol', ['symbol'])
        self.bot_last_liq_age_seconds = Gauge('bot_last_liq_age_seconds', 'Age of last liquidation event in seconds per symbol', ['symbol'])

        # Trade Execution Metrics
        self.bot_trades_total = Counter('bot_trades_total', 'Total trades executed', ['symbol', 'mode', 'type'])
        self.bot_trade_amount = Gauge('bot_trade_amount', 'Trade amount in USDT', ['symbol', 'type'])
        self.bot_trade_profit = Gauge('bot_trade_profit', 'PnL of individual trades in USDT', ['symbol', 'trade_type', 'signal_type'])
        self.bot_cumulative_pnl_usdt_total = Gauge('bot_cumulative_pnl_usdt_total', 'Cumulative PnL across all symbols in USDT')
        self.bot_trade_count = Counter('bot_trade_count', 'Total number of trades executed', ['symbol', 'trade_type'])

        # Position Tracking Metrics
        self.bot_open_position = Gauge('bot_open_position', 'Open position status (1=true, 0=false) per symbol', ['symbol'])
        self.bot_position_size_usdt = Gauge('bot_position_size_usdt', 'Size of open position in USDT per symbol', ['symbol'])
        self.bot_position_entry = Gauge('bot_position_entry', 'Entry price of open position per symbol', ['symbol'])
        self.bot_target_price = Gauge('bot_target_price', 'Take profit price for the open position per symbol', ['symbol'])
        self.bot_stop_price = Gauge('bot_stop_price', 'Stop loss price for the open position per symbol', ['symbol'])
        self.bot_unrealized_pnl = Gauge('bot_unrealized_pnl', 'Unrealized PnL for open positions per symbol', ['symbol'])
        self.bot_realized_pnl = Gauge('bot_realized_pnl', 'Realized PnL from closed positions per symbol', ['symbol'])
        self.bot_open_positions_count = Gauge('bot_open_positions_count', 'Number of currently open positions')
        self.bot_current_value_usdt = Gauge('bot_current_value_usdt', 'Current value of open position in USDT per symbol', ['symbol'])
        self.bot_mark_price = Gauge('bot_mark_price', 'Current mark price of the symbol', ['symbol'])
        self.bot_test_value = Gauge('bot_test_value', 'A test metric to verify Prometheus connectivity')

    def _set_metric(self, metric, labels: dict, value: float):
        """Helper function to set a metric with a monotonically increasing timestamp."""
        current_timestamp = time.time()
        if current_timestamp <= self.last_metric_timestamp:
            current_timestamp = self.last_metric_timestamp + 0.001  # Increment by 1 millisecond
        self.last_metric_timestamp = current_timestamp
        metric.labels(**labels).set(value)

    def update_rsi(self, symbol: str, value: float):
        """Update RSI metric for a symbol - call this from bot modules"""
        self._set_metric(self.bot_rsi, {'symbol': symbol}, value)

    def update_latency(self, symbol: str, latency: float):
        """Update latency metric for a symbol"""
        self._set_metric(self.bot_latency_sec, {'symbol': symbol}, latency)

    def update_events_total(self, symbol: str, count: int):
        """Update total events processed for a symbol"""
        self._set_metric(self.bot_events_total, {'symbol': symbol}, count)

    def update_last_signal_direction(self, symbol: str, direction: int):
        self._set_metric(self.bot_last_signal_direction, {'symbol': symbol}, direction)

    def update_last_signal_confidence(self, symbol: str, confidence: float):
        self._set_metric(self.bot_last_signal_confidence, {'symbol': symbol}, confidence)

    def update_last_cluster_impact_score(self, symbol: str, score: float):
        self._set_metric(self.bot_last_cluster_impact_score, {'symbol': symbol}, score)

    def update_cluster_count(self, symbol: str, count: int):
        self._set_metric(self.bot_cluster_count, {'symbol': symbol}, count)

    def update_cluster_impact_score(self, symbol: str, score: float):
        self._set_metric(self.bot_cluster_impact_score, {'symbol': symbol}, score)

    def update_top_cluster_price(self, symbol: str, price: float):
        self._set_metric(self.bot_top_cluster_price, {'symbol': symbol}, price)

    def update_top_cluster_strength(self, symbol: str, strength: float):
        self._set_metric(self.bot_top_cluster_strength, {'symbol': symbol}, strength)

    def update_cluster_volume(self, symbol: str, volume: float):
        self._set_metric(self.bot_cluster_volume, {'symbol': symbol}, volume)

    def update_active_bins(self, symbol: str, bins: int):
        self._set_metric(self.bot_active_bins, {'symbol': symbol}, bins)

    def update_sweep_detected(self, symbol: str, detected: bool):
        self._set_metric(self.bot_sweep_detected, {'symbol': symbol}, 1 if detected else 0)

    def update_support_band(self, symbol: str, price: float):
        self._set_metric(self.bot_support_band, {'symbol': symbol}, price)

    def update_resistance_band(self, symbol: str, price: float):
        self._set_metric(self.bot_resistance_band, {'symbol': symbol}, price)

    def update_imbalance_ratio(self, symbol: str, ratio: float):
        self._set_metric(self.bot_imbalance_ratio, {'symbol': symbol}, ratio)

    def update_orderbook_imbalance(self, symbol: str, imbalance: float):
        self._set_metric(self.bot_orderbook_imbalance, {'symbol': symbol}, imbalance)

    def update_trade_imbalance(self, symbol: str, imbalance: float):
        self._set_metric(self.bot_trade_imbalance, {'symbol': symbol}, imbalance)

    def update_last_liq_age(self, symbol: str, age: float):
        self._set_metric(self.bot_last_liq_age_seconds, {'symbol': symbol}, age)

    def increment_trades_total(self, symbol: str, mode: str, type: str):
        self.bot_trades_total.labels(symbol=symbol, mode=mode, type=type).inc()

    def update_trade_count(self, symbol: str, trade_type: str):
        self.bot_trade_count.labels(symbol=symbol, trade_type=trade_type).inc()

    def update_trade_amount(self, symbol: str, type: str, amount: float):
        self._set_metric(self.bot_trade_amount, {'symbol': symbol, 'type': type}, amount)

    def update_trade_profit(self, symbol: str, pnl: float, trade_type: str, signal_type: str):
        self._set_metric(self.bot_trade_profit, {'symbol': symbol, 'trade_type': trade_type, 'signal_type': signal_type}, pnl)

    def update_cumulative_pnl(self, pnl: float):
        self._set_metric(self.bot_cumulative_pnl_usdt_total, {}, pnl)

    def update_open_position(self, symbol: str, open_pos: bool):
        self._set_metric(self.bot_open_position, {'symbol': symbol}, 1 if open_pos else 0)

    def update_position_size_usdt(self, symbol: str, size: float):
        self._set_metric(self.bot_position_size_usdt, {'symbol': symbol}, size)

    def update_position_entry(self, symbol: str, entry: float):
        self._set_metric(self.bot_position_entry, {'symbol': symbol}, entry)

    def update_target_price(self, symbol: str, tp: float):
        self._set_metric(self.bot_target_price, {'symbol': symbol}, tp)

    def update_stop_price(self, symbol: str, sl: float):
        self._set_metric(self.bot_stop_price, {'symbol': symbol}, sl)

    def update_unrealized_pnl(self, symbol: str, pnl: float):
        self._set_metric(self.bot_unrealized_pnl, {'symbol': symbol}, pnl)

    def update_realized_pnl(self, symbol: str, pnl: float):
        self._set_metric(self.bot_realized_pnl, {'symbol': symbol}, pnl)

    def update_current_value_usdt(self, symbol: str, value: float):
        self._set_metric(self.bot_current_value_usdt, {'symbol': symbol}, value)

    def update_mark_price(self, symbol: str, price: float):
        self._set_metric(self.bot_mark_price, {'symbol': symbol}, price)

    def update_position_entry_price(self, symbol: str, price: float):
        self._set_metric(self.bot_position_entry, {'symbol': symbol}, price)

    def update_position_target_price(self, symbol: str, price: float):
        self._set_metric(self.bot_target_price, {'symbol': symbol}, price)

    def update_position_stop_price(self, symbol: str, price: float):
        self._set_metric(self.bot_stop_price, {'symbol': symbol}, price)

    def update_position_current_value_usdt(self, symbol: str, value: float):
        self._set_metric(self.bot_current_value_usdt, {'symbol': symbol}, value)

    def update_open_positions_count(self, count: int):
        self.bot_open_positions_count.set(count)

    def start_metrics_server(self, port: int = 8000):
        logger.info(f"Starting Prometheus metrics exporter on port {port}")
        start_http_server(port, addr='0.0.0.0')
        logger.info("Metrics server started successfully. Access /metrics at http://localhost:8000/metrics")

    async def update_resource_metrics_loop(self, symbols: List[str], status_tracker):
        """
        A loop that periodically updates resource-related metrics from the status_tracker.
        """
        while True:
            for symbol in symbols:
                s = status_tracker.status[symbol]
                
                # Update metrics from status_tracker
                self._set_metric(self.bot_mark_price, {'symbol': symbol}, s.mark_price if s.mark_price is not None else 0)
                self._set_metric(self.bot_last_liq_age_seconds, {'symbol': symbol},
                    (time.time() * 1000 - s.last_update_ms) / 1000 if s.last_update_ms else 0
                )
                self._set_metric(self.bot_events_total, {'symbol': symbol}, s.events_count)
                self._set_metric(self.bot_cluster_volume, {'symbol': symbol}, s.cluster_volume_usdt)
                self._set_metric(self.bot_active_bins, {'symbol': symbol}, s.active_bins)
                self._set_metric(self.bot_last_signal_direction, {'symbol': symbol}, 1 if "LONG" in s.last_signal_type else (-1 if "SHORT" in s.last_signal_type else 0))
                self._set_metric(self.bot_last_signal_confidence, {'symbol': symbol}, s.last_signal_confidence)
                self._set_metric(self.bot_last_cluster_impact_score, {'symbol': symbol}, s.cluster_impact_score if hasattr(s, 'cluster_impact_score') else 0)
                
            logger.debug("Updated resource metrics from status_tracker.")
            await asyncio.sleep(Config.METRICS_UPDATE_INTERVAL_S)