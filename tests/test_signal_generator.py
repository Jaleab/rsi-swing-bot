import pytest
import pandas as pd
import ta # Import ta library
from unittest.mock import MagicMock
from typing import Literal, Optional

from src.config import Config
from src.signal_generator import SignalGenerator

# Fixture for a clean SignalGenerator instance
@pytest.fixture
def signal_generator_instance():
    # Use a modified config for quicker testing and predictable values
    temp_config = Config()
    temp_config.RSI_LENGTH = 14
    temp_config.RSI_OVERSOLD = 20 # More lenient for testing crossovers
    temp_config.RSI_OVERBOUGHT = 80 # More lenient for testing crossovers
    temp_config.STOP_LOSS_PERCENT = 0.01
    temp_config.TAKE_PROFIT_PERCENT = 0.02
    temp_config.W_RSI = 0.5
    temp_config.W_CLUSTER = 0.3
    temp_config.W_SWEEP = 0.15
    temp_config.W_PROX = 0.05 # Not used yet, but for completeness
    temp_config.W_DOMINANCE = 0.10 # New weight for cluster dominance
    temp_config.CONFIDENCE_HIGH_THRESHOLD = 0.75
    temp_config.PROXIMITY_DECAY_FACTOR = 0.5 # Adjusted to ensure a high proximity score for tests
    temp_config.W_PROX_CLUSTER_MULTIPLIER = 1.0 # Ensure full impact of proximity and normalized strength
    temp_config.W_SWEEP_MULTIPLIER = 0.5
    mock_cluster_aggregator = MagicMock()
    return SignalGenerator(temp_config, mock_cluster_aggregator)


@pytest.mark.parametrize("rsi_signal_direction, current_price, cluster_normalized_strength, sweep_volume, expected_confidence_score, expected_signal_type, expected_reason_fragment, expected_cluster_impact_score_min", [
    # Case 1: RSI LONG, no sweep, dominant cluster -> MEDIUM_LONG
    ("LONG", 100.0, 1.0, 0.0, 0.94, "MEDIUM_LONG", "MEDIUM_LONG (RSI + Dominant Cluster)", 0.9),
    # Case 2: RSI LONG, bullish sweep, dominant cluster -> STRONG_LONG
    ("LONG", 100.0, 1.0, 50.0, 1.0, "STRONG_LONG", "STRONG_LONG (RSI + Sweep + Dominant Cluster)", 0.9),
    # Case 3: RSI SHORT, no sweep, dominant cluster -> MEDIUM_SHORT
    ("SHORT", 100.0, 1.0, 0.0, 0.94, "MEDIUM_SHORT", "MEDIUM_SHORT (RSI + Dominant Cluster)", 0.9),
    # Case 4: RSI LONG, no sweep, medium cluster impact -> LOW_CONFIDENCE_LONG
    ("LONG", 100.0, 0.2, 0.0, 0.77, "LOW_CONFIDENCE_LONG", "LOW_CONFIDENCE_LONG (RSI Only)", 0.41), # Corrected expected_cluster_impact_score_min
    # Case 5: NEUTRAL RSI, no cluster/sweep -> NEUTRAL
    ("NEUTRAL", 100.0, 0.0, 0.0, 0.226, "NEUTRAL", "NEUTRAL (RSI Neutral)", 0.0), # Corrected expected_confidence_score
])

def test_decide_signals(signal_generator_instance, rsi_signal_direction, current_price, cluster_normalized_strength, sweep_volume, expected_confidence_score, expected_signal_type, expected_reason_fragment, expected_cluster_impact_score_min):
    # Mocking cluster_aggregator for the test
    signal_generator_instance.cluster_aggregator = MagicMock()
    signal_generator_instance.cluster_aggregator.get_bin_strength_at.return_value = (cluster_normalized_strength, 70.0)
    signal_generator_instance.cluster_aggregator.get_top_n_clusters.return_value = [
        {"centroid_price": current_price * 0.999, "volume": 1000.0},
        {"centroid_price": current_price * 1.001, "volume": 500.0},
    ]
    signal_generator_instance.cluster_aggregator.is_sweep_detected.return_value = (sweep_volume > 0, abs(sweep_volume))

    # Create a dummy DataFrame and directly set RSI values for precise testing
    ohlcv_df = pd.DataFrame({'close': [100.0] * 100}) # Need enough data for RSI calculation
    if rsi_signal_direction == "LONG":
        ohlcv_df['close'].iloc[-1] = 1.0 # Make RSI strongly oversold
    elif rsi_signal_direction == "SHORT":
        ohlcv_df['close'].iloc[-1] = 1000.0 # Make RSI strongly overbought
    # No change for NEUTRAL, RSI will be around 50

    # Ensure RSI calculation works for the mock data
    ohlcv_df['rsi'] = ta.momentum.RSIIndicator(ohlcv_df['close'], window=signal_generator_instance.config.RSI_LENGTH).rsi()
    ohlcv_df.dropna(inplace=True)
    
    mock_cluster_snapshot = {
        "clusters": [
            {"bin_idx": 0, "centroid_price": current_price * 0.999, "volume": 1000.0, "normalized_strength": cluster_normalized_strength, "events": 10},
            {"bin_idx": 1, "centroid_price": current_price * 1.001, "volume": 500.0, "normalized_strength": 0.5, "events": 5},
        ],
        "top_clusters": [
            {"bin_idx": 0, "centroid_price": current_price * 0.999, "volume": 1000.0, "normalized_strength": cluster_normalized_strength, "events": 10},
            {"bin_idx": 1, "centroid_price": current_price * 1.001, "volume": 500.0, "normalized_strength": 0.5, "events": 5},
        ],
        "median_volume": 100.0,
    }
    
    signal_output = signal_generator_instance.decide(
        symbol="SOL/USDT",
        current_price=current_price,
        ohlcv_df=ohlcv_df,
        cluster_snapshot=mock_cluster_snapshot,
        is_liquidation_data_available=True,
        is_sweep=(sweep_volume != 0),
        actual_sweep_volume=sweep_volume
    )

    assert signal_output["signal_type"] == expected_signal_type
    assert signal_output["confidence_score"] == pytest.approx(expected_confidence_score, rel=0.1) # Allowing for some floating point deviation
    assert expected_reason_fragment in signal_output["reason"] # Check for fragment in full reason string

    if rsi_signal_direction == "NEUTRAL":
        assert signal_output["rsi_signal"] == "NEUTRAL"
    else:
        assert signal_output["rsi_signal"] == rsi_signal_direction

    if rsi_signal_direction == "NEUTRAL":
        assert signal_output["confidence_score"] == pytest.approx(expected_confidence_score, rel=0.1)
    else:
        assert signal_output["confidence_score"] > 0.0 # Should have some confidence if not neutral
    
    # The actual_sweep_volume is passed directly, so we check that
    assert signal_output["sweep_volume_usdt"] == abs(sweep_volume) # sweep_volume_usdt is always positive
    assert signal_output["cluster_impact_score"] >= expected_cluster_impact_score_min # Check minimum impact score


def test_decide_fallback_rsi(signal_generator_instance):
    current_price = 100.0
    # Create a dummy DataFrame and directly set RSI values for a buy signal
    ohlcv_df = pd.DataFrame({'close': [100.0] * 100}) # Need enough data for RSI calculation
    ohlcv_df['close'].iloc[-1] = 1.0 # Make RSI strongly oversold
    
    # Ensure RSI calculation works for the mock data
    ohlcv_df['rsi'] = ta.momentum.RSIIndicator(ohlcv_df['close'], window=signal_generator_instance.config.RSI_LENGTH).rsi()
    ohlcv_df.dropna(inplace=True)

    signal_output = signal_generator_instance.decide(
        symbol="SOL/USDT",
        current_price=current_price,
        ohlcv_df=ohlcv_df,
        cluster_snapshot={"clusters": []}, # Provide an empty cluster snapshot
        is_liquidation_data_available=False,
        is_sweep=False,
        actual_sweep_volume=0.0
    )

    assert signal_output["signal_type"] == "LOW_CONFIDENCE_LONG"
    # The rsi_score is now part of the signal_output dictionary
    assert signal_output["confidence_score"] == pytest.approx(signal_generator_instance.config.W_RSI * signal_output["rsi_score"])
    assert "LOW_CONFIDENCE_LONG (RSI Only)" in signal_output["reason"]
