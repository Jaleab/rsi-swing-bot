import logging # Import the logging module
from typing import Dict, Tuple, Optional, List
import pandas as pd
import numpy as np

from .config import Config
from .rsi_calc import calculate_rsi, get_rsi_signal
from .cluster_aggregator import ClusterAggregator # Assuming ClusterAggregator is available

class SignalGenerator:
    def __init__(self, config: Config, cluster_aggregator: ClusterAggregator):
        self.config = config
        self.cluster_aggregator = cluster_aggregator
        # Define thresholds for confidence levels
        self.CONFIDENCE_HIGH_THRESHOLD = 0.75 # As per blueprint (implied from table)
        self.CONFIDENCE_MEDIUM_THRESHOLD = 0.5 # As per blueprint (implied from table)

    def _calculate_cluster_impact_score(
        self,
        symbol: str,
        current_price: float,
        cluster_snapshot: Dict
    ) -> Tuple[float, float]:
        """
        Calculates the cluster impact score based on normalized cluster strength and proximity.
        Returns (cluster_impact_score, proximity_score).
        """
        if not cluster_snapshot or not cluster_snapshot["clusters"]:
            return 0.0, 0.0

        # Find the closest cluster to the current price
        min_dist = float('inf')
        closest_cluster = None
        for cluster in cluster_snapshot["clusters"]:
            dist = abs(cluster["centroid_price"] - current_price)
            if dist < min_dist:
                min_dist = dist
                closest_cluster = cluster
        
        if closest_cluster:
            normalized_strength, percentile = self.cluster_aggregator.get_bin_strength_at(
                symbol, closest_cluster["centroid_price"]
            )
            
            # Proximity score: inverse relationship with distance, capped.
            # A small distance means higher proximity.
            # Max proximity score when price is exactly at centroid, decreasing as price moves away.
            # Normalize min_dist by current_price to make it relative.
            relative_min_dist = min_dist / current_price if current_price else 0.001
            proximity_score = max(0.0, 1.0 - (relative_min_dist * 100)) # Heuristic, adjust as needed

            # Cluster impact score: combine normalized strength and proximity
            # Stronger clusters closer to price have higher impact
            cluster_impact_score = (normalized_strength * 0.7) + (proximity_score * 0.3) # Weights can be tuned
            cluster_impact_score = min(1.0, cluster_impact_score) # Cap at 1.0

            return cluster_impact_score, proximity_score
        
        return 0.0, 0.0

    def _calculate_cluster_dominance_score(
        self,
        symbol: str,
        closest_cluster_price: float,
        cluster_snapshot: Dict,
        n_top_clusters: int = 5
    ) -> float:
        """
        Calculates a score indicating if the closest cluster is among the top N strongest clusters.
        Returns a score between 0.0 and 1.0.
        """
        if not cluster_snapshot or not cluster_snapshot["clusters"]:
            return 0.0

        top_clusters = self.cluster_aggregator.get_top_n_clusters(symbol, n_top_clusters)
        
        # Check if the closest cluster's price is near any of the top clusters' centroid prices
        for top_c in top_clusters:
            # Allow for a small tolerance when comparing prices due to binning approximation
            if abs(top_c["centroid_price"] - closest_cluster_price) < self.config.BIN_PCT * closest_cluster_price * 2: # Tolerance of 2 bins
                # If it's a top cluster, assign a high dominance score.
                # The score can be scaled by its rank or volume if needed.
                return 1.0
        return 0.0

    def decide(
        self,
        symbol: str,
        current_price: float,
        ohlcv_df: pd.DataFrame,
        cluster_snapshot: Dict,
        is_liquidation_data_available: bool,
        is_sweep: bool = False,
        actual_sweep_volume: float = 0.0
    ) -> Dict:
        logging.debug(f"[{symbol}] SignalGenerator.decide method entered. Current price: {current_price}, OHLCV last close: {ohlcv_df['close'].iloc[-1] if not ohlcv_df.empty else 'N/A'}")
        """
        Fuses RSI, cluster, and sweep data to generate a trading signal and confidence score.
        """
        # --- Initialize Signal Data ---
        signal_type = "NEUTRAL"
        confidence_score = 0.0
        decision_reasons = [] # Use a more descriptive name for internal reasons

        # --- 1. RSI Score Calculation ---
        rsi_value = calculate_rsi(ohlcv_df, column='close', length=Config.RSI_LENGTH).iloc[-1]
        rsi_signal, rsi_score = get_rsi_signal(
            rsi_value, Config.RSI_OVERSOLD, Config.RSI_OVERBOUGHT
        )
        logging.debug(f"[{symbol}] RSI Value: {rsi_value:.2f}, Signal: {rsi_signal}, Score: {rsi_score:.2f}")
        decision_reasons.append(f"RSI Value: {rsi_value:.2f}, Signal: {rsi_signal}, Score: {rsi_score:.2f}")
        
        # --- 2. Cluster Impact, Proximity, and Dominance Scores ---
        cluster_impact_score, proximity_score = 0.0, 0.0
        cluster_dominance_score = 0.0
        if is_liquidation_data_available:
            cluster_impact_score, proximity_score = self._calculate_cluster_impact_score(
                symbol, current_price, cluster_snapshot
            )
            logging.debug(f"[{symbol}] Cluster Impact Score: {cluster_impact_score:.2f}, Proximity Score: {proximity_score:.2f}")
            decision_reasons.append(f"Cluster Impact Score: {cluster_impact_score:.2f}, Proximity Score: {proximity_score:.2f}")
            
            min_dist = float('inf')
            closest_cluster_price = None
            if cluster_snapshot and cluster_snapshot["clusters"]:
                for cluster in cluster_snapshot["clusters"]:
                    dist = abs(cluster["centroid_price"] - current_price)
                    if dist < min_dist:
                        min_dist = dist
                        closest_cluster_price = cluster["centroid_price"]
            
            if closest_cluster_price is not None:
                cluster_dominance_score = self._calculate_cluster_dominance_score(
                    symbol, closest_cluster_price, cluster_snapshot
                )
                logging.debug(f"[{symbol}] Cluster Dominance Score: {cluster_dominance_score:.2f}")
                decision_reasons.append(f"Cluster Dominance Score: {cluster_dominance_score:.2f}")

        # --- 3. Sweep Score Calculation ---
        sweep_score = 0.0
        if is_liquidation_data_available and is_sweep:
            sweep_score = 1.0
            logging.debug(f"[{symbol}] Sweep Detected: {actual_sweep_volume:.2f} USDT")
            decision_reasons.append(f"Sweep Detected: {actual_sweep_volume:.2f} USDT")
        
        # --- Calculate Composite Confidence Score ---
        weighted_rsi_score = Config.W_RSI * rsi_score
        weighted_cluster_impact_score = Config.W_CLUSTER * cluster_impact_score
        weighted_sweep_score = Config.W_SWEEP * sweep_score
        weighted_proximity_score = Config.W_PROX * proximity_score
        weighted_cluster_dominance_score = Config.W_DOMINANCE * cluster_dominance_score

        confidence_score = (
            weighted_rsi_score +
            weighted_cluster_impact_score +
            weighted_sweep_score +
            weighted_proximity_score +
            weighted_cluster_dominance_score
        )
        confidence_score = min(1.0, confidence_score) # Cap at 1.0
        logging.debug(f"[{symbol}] Weighted Scores: RSI={weighted_rsi_score:.2f}, Cluster Impact={weighted_cluster_impact_score:.2f}, Sweep={weighted_sweep_score:.2f}, Proximity={weighted_proximity_score:.2f}, Dominance={weighted_cluster_dominance_score:.2f}")
        logging.debug(f"[{symbol}] Composite Confidence Score: {confidence_score:.2f}")
        decision_reasons.append(f"Weighted Scores: RSI={weighted_rsi_score:.2f}, Cluster Impact={weighted_cluster_impact_score:.2f}, Sweep={weighted_sweep_score:.2f}, Proximity={weighted_proximity_score:.2f}, Dominance={weighted_cluster_dominance_score:.2f}")
        decision_reasons.append(f"Composite Confidence Score: {confidence_score:.2f}")

        # --- Determine Final Signal Based on Hybrid Logic ---
        # Initialize signal_type based on RSI, then modify with sweeps and clusters
        if rsi_signal == "LONG":
            if is_sweep and actual_sweep_volume >= self.config.MIN_SWEEP_VOLUME_USDT: # Bullish sweep detected
                if cluster_dominance_score >= self.CONFIDENCE_HIGH_THRESHOLD:
                    signal_type = "STRONG_LONG" # RSI + Aligned Sweep + High Cluster Dominance
                    decision_reasons.append("STRONG_LONG (RSI + Sweep + Dominant Cluster)")
                elif cluster_impact_score >= self.CONFIDENCE_MEDIUM_THRESHOLD:
                    signal_type = "MEDIUM_LONG" # RSI + Aligned Sweep + Medium Cluster Impact
                    decision_reasons.append("MEDIUM_LONG (RSI + Sweep + Cluster Impact)")
                else:
                    signal_type = "MEDIUM_LONG" # RSI + Aligned Sweep (without strong cluster support)
                    decision_reasons.append("MEDIUM_LONG (RSI + Sweep)")
            elif cluster_dominance_score >= self.CONFIDENCE_HIGH_THRESHOLD and cluster_impact_score >= self.CONFIDENCE_MEDIUM_THRESHOLD:
                signal_type = "MEDIUM_LONG" # RSI + Dominant Cluster + Medium Impact
                decision_reasons.append("MEDIUM_LONG (RSI + Dominant Cluster)")
            elif rsi_score > 0: # RSI alone
                signal_type = "LOW_CONFIDENCE_LONG"
                decision_reasons.append("LOW_CONFIDENCE_LONG (RSI Only)")
            else:
                signal_type = "NEUTRAL"
                decision_reasons.append("NEUTRAL (RSI but no supporting factors)")
        elif rsi_signal == "SHORT":
            if is_sweep and actual_sweep_volume <= -self.config.MIN_SWEEP_VOLUME_USDT: # Bearish sweep detected
                if cluster_dominance_score >= self.CONFIDENCE_HIGH_THRESHOLD:
                    signal_type = "STRONG_SHORT" # RSI + Aligned Sweep + High Cluster Dominance
                    decision_reasons.append("STRONG_SHORT (RSI + Sweep + Dominant Cluster)")
                elif cluster_impact_score >= self.CONFIDENCE_MEDIUM_THRESHOLD:
                    signal_type = "MEDIUM_SHORT" # RSI + Aligned Sweep + Medium Cluster Impact
                    decision_reasons.append("MEDIUM_SHORT (RSI + Sweep + Cluster Impact)")
                else:
                    signal_type = "MEDIUM_SHORT" # RSI + Aligned Sweep (without strong cluster support)
                    decision_reasons.append("MEDIUM_SHORT (RSI + Sweep)")
            elif cluster_dominance_score >= self.CONFIDENCE_HIGH_THRESHOLD and cluster_impact_score >= self.CONFIDENCE_MEDIUM_THRESHOLD:
                signal_type = "MEDIUM_SHORT" # RSI + Dominant Cluster + Medium Impact
                decision_reasons.append("MEDIUM_SHORT (RSI + Dominant Cluster)")
            elif rsi_score > 0: # RSI alone
                signal_type = "LOW_CONFIDENCE_SHORT"
                decision_reasons.append("LOW_CONFIDENCE_SHORT (RSI Only)")
            else:
                signal_type = "NEUTRAL"
                decision_reasons.append("NEUTRAL (RSI but no supporting factors)")
        else: # RSI is NEUTRAL
            signal_type = "NEUTRAL"
            decision_reasons.append("NEUTRAL (RSI Neutral)")

        # Handle conflicting signals (sweep opposite to RSI direction)
        if rsi_signal == "LONG" and is_sweep and actual_sweep_volume <= -self.config.MIN_SWEEP_VOLUME_USDT: # Bearish sweep opposite Long RSI
            signal_type = "NEUTRAL"
            decision_reasons.append("SIGNAL_CANCELLED (Bearish Sweep opposite Long RSI)")
        elif rsi_signal == "SHORT" and is_sweep and actual_sweep_volume >= self.config.MIN_SWEEP_VOLUME_USDT: # Bullish sweep opposite Short RSI
            signal_type = "NEUTRAL"
            decision_reasons.append("SIGNAL_CANCELLED (Bullish Sweep opposite Short RSI)")

        # --- Log the comprehensive decision reasons ---
        logging.debug(f"[{symbol}] Signal Decision Process: {' | '.join(decision_reasons)}")
        if signal_type != "NEUTRAL":
            logging.info(f"[{symbol}] Final Signal: {signal_type}, Confidence: {confidence_score:.2f}, Reasons: {' | '.join(decision_reasons)}")

        return {
            "signal_type": signal_type,
            "confidence_score": confidence_score,
            "rsi_value": rsi_value,
            "rsi_signal": rsi_signal,
            "rsi_score": rsi_score, # Added rsi_score to the returned dictionary
            "cluster_impact_score": cluster_impact_score,
            "proximity_score": proximity_score,
            "cluster_dominance_score": cluster_dominance_score,
            "is_sweep": is_sweep,
            "sweep_volume_usdt": actual_sweep_volume,
            "reason": " | ".join(decision_reasons) if decision_reasons else "No strong conditions"
        }

    def check_exit_signal(
        self,
        symbol: str,
        current_price: float,
        position_type: str,
        entry_price: float,
        target_price: Optional[float],
        stop_price: Optional[float]
    ) -> Optional[str]:
        """
        Checks if an exit signal is generated based on current price, target, and stop loss.
        """
        if position_type == "LONG":
            # Check Stop Loss for LONG position
            if stop_price is not None and current_price <= stop_price:
                return "STOP_LOSS"
            # Check Take Profit for LONG position
            if target_price is not None and current_price >= target_price:
                return "TAKE_PROFIT"
        elif position_type == "SHORT":
            # Check Stop Loss for SHORT position
            if stop_price is not None and current_price >= stop_price:
                return "STOP_LOSS"
            # Check Take Profit for SHORT position
            if target_price is not None and current_price <= target_price:
                return "TAKE_PROFIT"
        
        return None


# Example usage (for testing)
async def main():
    class MockConfig:
        RSI_LENGTH = 18
        RSI_OVERSOLD = 34
        RSI_OVERBOUGHT = 77
        W_RSI = 0.50
        W_CLUSTER = 0.30
        W_SWEEP = 0.15
        W_PROX = 0.05
        BIN_MODE = "percent"
        BIN_PCT = 0.002
        BIN_ABS = 0.5
        SLIDING_WINDOW_S = 300
        SWEEP_THRESHOLD_FACTOR = 2.0
        MIN_SWEEP_VOLUME_USDT = 50000.0 # Increased for more realistic simulation
        W_DOMINANCE = 0.1 # New weight for cluster dominance

    class MockClusterAggregator:
        def __init__(self):
            pass
        
        def get_bin_strength_at(self, symbol, price):
            # Mock implementation
            if symbol == "SOL/USDT" and price > 20.0 and price < 20.5:
                return 1.5, 90.0 # Strong cluster, high percentile
            return 0.2, 30.0

        def is_sweep_detected(self, symbol, current_price):
            # Mock implementation
            if symbol == "SOL/USDT" and current_price > 20.0 and current_price < 20.5:
                return True, 60000.0 # Sweep detected (bullish)
            if symbol == "SOL/USDT" and current_price > 29.5 and current_price < 30.5:
                return True, -70000.0 # Sweep detected (bearish, using negative for direction)
            return False, 0.0
        
        def get_top_n_clusters(self, symbol: str, n: int) -> List[Dict]:
            # Mock implementation
            if symbol == "SOL/USDT":
                return [
                    {"centroid_price": 20.1, "volume": 1000.0},
                    {"centroid_price": 29.9, "volume": 1200.0},
                    {"centroid_price": 19.5, "volume": 500.0},
                ][:n]
            return []


    # Mock OHLCV DataFrame
    data = {
        'close': [10, 12, 15, 13, 11, 14, 16, 18, 17, 19, 20, 22, 21, 23, 25, 24, 26, 28, 27, 29, 30]
    }
    ohlcv_df = pd.DataFrame(data)
    # Ensure enough data for RSI calculation
    ohlcv_df = pd.concat([ohlcv_df] * 5, ignore_index=True)
    ohlcv_df['close'] = ohlcv_df['close'].astype(float) # Explicitly cast to float


    mock_config = MockConfig()
    mock_cluster_aggregator = MockClusterAggregator()
    signal_generator = SignalGenerator(mock_config, mock_cluster_aggregator)

    # Scenario 1: Strong LONG signal (RSI oversold, bullish sweep, dominant cluster)
    print("--- Scenario 1: Strong LONG ---")
    mock_cluster_snapshot_long = {
        "clusters": [
            {"centroid_price": 20.1, "volume": 1000.0},
            {"centroid_price": 19.5, "volume": 500.0},
        ]
    }
    ohlcv_df.loc[ohlcv_df.index[-1], 'close'] = 0.1 # Make RSI strongly oversold (e.g., < 34)
    signal = signal_generator.decide(
        symbol="SOL/USDT",
        current_price=20.1,
        ohlcv_df=ohlcv_df.copy(),
        cluster_snapshot=mock_cluster_snapshot_long,
        is_liquidation_data_available=True,
        is_sweep=True,
        actual_sweep_volume=60000.0 # Simulating a bullish sweep
    )
    print(signal)

    # Scenario 2: Strong SHORT signal (RSI overbought, bearish sweep, dominant cluster)
    print("\n--- Scenario 2: Strong SHORT ---")
    mock_cluster_snapshot_short = {
        "clusters": [
            {"centroid_price": 29.9, "volume": 1200.0},
            {"centroid_price": 30.5, "volume": 600.0},
        ]
    }
    ohlcv_df.loc[ohlcv_df.index[-1], 'close'] = 90.0 # Make RSI strongly overbought (e.g., > 77)
    signal = signal_generator.decide(
        symbol="SOL/USDT",
        current_price=29.9,
        ohlcv_df=ohlcv_df.copy(),
        cluster_snapshot=mock_cluster_snapshot_short,
        is_liquidation_data_available=True,
        is_sweep=True,
        actual_sweep_volume=-70000.0 # Simulating a bearish sweep
    )
    print(signal)

    # Scenario 3: Neutral (RSI not signaling)
    print("\n--- Scenario 3: Neutral ---")
    ohlcv_df.loc[ohlcv_df.index[-1], 'close'] = 25.0 # Make RSI neutral
    signal = signal_generator.decide(
        symbol="SOL/USDT",
        current_price=25.0,
        ohlcv_df=ohlcv_df.copy(),
        cluster_snapshot=mock_cluster_snapshot_long,
        is_liquidation_data_available=True
    )
    print(signal)

    # Scenario 4: RSI only fallback (LONG)
    print("\n--- Scenario 4: RSI Only Fallback (LONG) ---")
    ohlcv_df.loc[ohlcv_df.index[-1], 'close'] = 0.1 # Make RSI strongly oversold
    signal = signal_generator.decide(
        symbol="SOL/USDT",
        current_price=20.0,
        ohlcv_df=ohlcv_df.copy(),
        cluster_snapshot={}, # No cluster data
        is_liquidation_data_available=False
    )
    print(signal)

    # Scenario 5: RSI LONG but opposite BEARISH sweep (should be neutral/cancelled)
    print("\n--- Scenario 5: RSI LONG but opposite BEARISH sweep ---")
    ohlcv_df.loc[ohlcv_df.index[-1], 'close'] = 0.1 # Make RSI strongly oversold
    signal = signal_generator.decide(
        symbol="SOL/USDT",
        current_price=30.0, # Price where bearish sweep is detected
        ohlcv_df=ohlcv_df.copy(),
        cluster_snapshot=mock_cluster_snapshot_long,
        is_liquidation_data_available=True,
        is_sweep=True,
        actual_sweep_volume=-70000.0 # Simulating a bearish sweep
    )
    print(signal)

    # Scenario 6: RSI SHORT but opposite BULLISH sweep (should be neutral/cancelled)
    print("\n--- Scenario 6: RSI SHORT but opposite BULLISH sweep ---")
    ohlcv_df.loc[ohlcv_df.index[-1], 'close'] = 90.0 # Make RSI strongly overbought
    signal = signal_generator.decide(
        symbol="SOL/USDT",
        current_price=20.1, # Price where bullish sweep is detected (aligned with mock)
        ohlcv_df=ohlcv_df.copy(),
        cluster_snapshot=mock_cluster_snapshot_short,
        is_liquidation_data_available=True,
        is_sweep=True,
        actual_sweep_volume=60000.0 # Simulating a bullish sweep
    )
    print(signal)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())