# Detailed Plan: Refining Signal Generation for RSI Swing Bot

This document outlines the detailed plan for integrating sweep detection and enhanced cluster strength information from the `ClusterAggregator` into the `SignalGenerator` to produce more actionable trading signals for the RSI Swing Bot.

### **Goal: Integrate sweep detection and enhanced cluster strength into `SignalGenerator` for improved trading signals.**

**Phase 1: Adjust `SignalGenerator` for Direct Sweep Integration**

1.  **Modify `SignalGenerator.decide()` method signature:**
    *   Remove the `sweep_volume` parameter. The `SignalGenerator` will now directly query the `ClusterAggregator` for sweep information.
2.  **Integrate direct sweep detection call:**
    *   Inside `SignalGenerator.decide()`, call `self.cluster_aggregator.is_sweep_detected(symbol, current_price)` to obtain the `is_sweep` boolean and `actual_sweep_volume`.

**Phase 2: Enhance Cluster Information Integration**

1.  **Update `_calculate_cluster_impact_score()` or introduce a new method to capture "cluster dominance" and "cluster context":**
    *   Currently, `_calculate_cluster_impact_score()` focuses on the closest cluster. We need to expand this to incorporate insights from `cluster_aggregator.get_top_n_clusters()`.
    *   **New Metric: Cluster Dominance Score**: This score will evaluate if the closest cluster is also one of the top N strongest clusters (e.g., top 3 or 5). A cluster that is both close and dominant should contribute more significantly to the confidence.
    *   **New Metric: Cluster Context Score**: This score will consider the overall distribution of top clusters. For example, if multiple strong clusters are aligned in a particular direction (e.g., strong buy-side clusters below current price for a long signal), it adds conviction. (Note: `snapshot.bands`, `volume imbalance strength`, `volatility from recent sweeps` are noted as future enhancements requiring `ClusterAggregator` changes; for now, we focus on `get_top_n_clusters`).

**Phase 3: Implement Hybrid Signal Logic and Refine Confidence Scoring**

1.  **Adjust `confidence_score` calculation:**
    *   Update the weighting structure to account for the newly integrated sweep information and the enhanced cluster dominance/context scores.
    *   The `W_SWEEP` and `W_CLUSTER` weights in `Config` will become even more critical here.
2.  **Implement Hybrid Signal Determination Logic:**
    *   **Primary Filter (RSI):** The RSI signal (`rsi_signal`) will still be the initial directional indicator.
    *   **Sweep Alignment/Cancellation:**
        *   If `is_sweep` is detected:
            *   If the sweep direction aligns with the `rsi_signal` (e.g., a strong bullish sweep during an RSI oversold condition), it will significantly boost the `confidence_score` and contribute to a "STRONG" signal.
            *   If the sweep direction is *opposite* to the `rsi_signal` (e.g., a bearish sweep during an RSI oversold condition), it should *cancel* the `rsi_signal` entirely or downgrade it to "NEUTRAL", regardless of other factors.
    *   **Cluster Reinforcement:**
        *   The new Cluster Dominance Score and Cluster Context Score will further scale the confidence. A high Cluster Dominance Score in alignment with the RSI and sweep will contribute to a "VERY STRONG" signal (Tier 1 trade).
    *   **Tiered Signal Generation (reflecting the hybrid approach):**
        *   **"STRONG_LONG" / "STRONG_SHORT"**: Requires aligned RSI, an aligned sweep, and potentially high cluster dominance.
        *   **"MEDIUM_LONG" / "MEDIUM_SHORT"**: RSI + significant cluster impact/dominance, but without a strong aligned sweep, or with a weaker sweep.
        *   **"LOW_CONFIDENCE_LONG" / "LOW_CONFIDENCE_SHORT"**: Based primarily on RSI and some cluster presence, but no sweep or a non-aligned sweep.
        *   **"NEUTRAL"**: If RSI is neutral, or if a sweep contradicts the RSI signal.
3.  **Update `reason` field:**
    *   Ensure the `reason` string clearly communicates the factors contributing to the final signal, including sweep detection and enhanced cluster insights.

### **Mermaid Diagram: Updated Signal Generation Flow**

```mermaid
graph TD
    A[SignalGenerator.decide(symbol, current_price, ohlcv_df, cluster_snapshot, is_liquidation_data_available)] --> B{Calculate RSI Value & Signal};
    B --> C{Call ClusterAggregator.is_sweep_detected(symbol, current_price)};
    C --> D{Calculate Cluster Impact, Dominance & Context Scores};
    D --> E{Combine RSI, Sweep, Cluster Scores};
    E --> F{Apply Hybrid Signal Logic};
    F{Apply Hybrid Signal Logic} -- Sweep opposite RSI --> G[Signal: NEUTRAL];
    F{Apply Hybrid Signal Logic} -- RSI + Aligned Sweep + High Cluster --> H[Signal: STRONG_LONG/SHORT];
    F{Apply Hybrid Signal Logic} -- RSI + Aligned Sweep --> I[Signal: HIGH_CONFIDENCE_LONG/SHORT];
    F{Apply Hybrid Signal Logic} -- RSI + Cluster Only --> J[Signal: MEDIUM_CONFIDENCE_LONG/SHORT];
    F{Apply Hybrid Signal Logic} -- RSI Only --> K[Signal: LOW_CONFIDENCE_LONG/SHORT];
    G, H, I, J, K --> L[Return Signal Dictionary];