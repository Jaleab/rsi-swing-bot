# RSI Swing Bot — Quantitative Review

**Date:** 2026-06-10  
**Reviewer:** Quant Trader Perspective  
**Verdict:** ❌ **Do not deploy capital.** The codebase has too many critical bugs and unvalidated assumptions. Continue development only after resolving all Critical findings below.

---

## Executive Summary

The bot attempts a novel **liquidation-cluster + RSI hybrid strategy** with an event-driven async architecture, Prometheus/Grafana monitoring, and multiple operating modes (live, simulation, backtest). The architecture and monitoring surface are well-structured for a prototype. However, the actual trading logic contains **severe implementation errors**, the backtest engine is broken and produces no useful results, signal weights are miscalibrated, and several key decision paths are either dead code or will never trigger as intended. The gap between what the code *looks like* it does and what it *actually* does is wide enough that running this with real money would be gambling.

---

## Overall Scorecard

| Dimension | Score (1–10) | Notes |
|---|---|---|
| Architecture & Code Structure | 7 | Good separation of concerns, async event loop, clean module layout |
| Strategy Rationale | 6 | Liquidation clusters as support/resistance is plausible but unproven |
| Signal Generation Logic | 3 | Weights overflow 1.0, bearish sweeps dead code, no regime filter |
| Risk Management | 5 | Guardrails exist but SL/TP values unvalidated, no drawdown circuit-breaker |
| Backtesting Validity | 1 | Engine is functionally broken; zero valid backtest results exist |
| Code Correctness | 2 | Duplicate methods, broken attribute references, dead code paths |
| Configuration Discipline | 4 | Duplicate symbol lists, nonstandard RSI thresholds, inconsistent naming |
| Deployment Readiness | 6 | Docker, systemd, Grafana dashboards are well-prepared |

**Weighted Composite: 3.8 / 10**

---

## Critical Findings

### C1: Signal Weights Sum to 1.10

**File:** `src/config.py:47–51`  
**File:** `src/signal_generator.py:147–160`

```python
W_RSI      = 0.50
W_CLUSTER  = 0.30
W_SWEEP    = 0.15
W_PROX     = 0.05
W_DOMINANCE = 0.10  # Total = 1.10
```

The composite confidence score is the sum of five terms that scale to **1.10** before capping at 1.0. This means once you have an RSI signal (0.50) and any cluster impact (0.30), you're already at 0.80 — the sweep, proximity, and dominance scores are effectively meaningless because they bump the score into the cap. The weight system isn't "wrong" so much as **deceptive** — it creates the illusion of a 5-factor model when only RSI + cluster matter.

**Fix:** Normalize weights to sum to 1.0, or better, eliminate the linear combination and use a logistic / ensemble model.

---

### C2: Backtest Engine Is Broken

**File:** `src/backtest/backtest_engine.py`

Multiple fatal errors:

1. **`trading_signal['signal'] == 1` will never be true.**  
   `SignalGenerator.decide()` returns string keys like `"STRONG_LONG"`, `"LOW_CONFIDENCE_LONG"`, `"NEUTRAL"`. The backtest engine checks an integer `1` or `-1` that is never in the dictionary (line 131). No trades will ever be taken.

2. **`cluster_aggregator.get_snapshot(current_price)` is called with `await`**, but `get_snapshot()` is a synchronous method — this will raise `TypeError` (line 109).

3. **Duplicate method definitions** — `_log_signal` and `_save_signal_log_to_csv` are defined twice identically (lines 196–253).

4. **`Config.SYMBOL` (singular) referenced** (line 48) but `Config.SYMBOLS` (plural) is the actual attribute.

5. **`position_manager.update_position()` receives a raw `dict`** (line 139), but the method expects a `Position` object (`src/position_manager.py:184-188`).

**Verdict:** This backtest engine has **never produced a valid trade**. Any results in `results.csv` are from the simpler `grid_search.py` / `backtest_rsi.py` path that uses a reduced strategy.

---

### C3: Bearish Sweep Detection Is Dead Code

**File:** `src/cluster_aggregator.py:187-225`  
**File:** `src/signal_generator.py:188-189`

`ClusterAggregator.is_sweep_detected()` returns `(is_sweep, recent_volume)` where `recent_volume` is **always positive** (a sum of `event.qty_usdt`, which is always ≥ 0).

The SignalGenerator then checks:
```python
if is_sweep and actual_sweep_volume <= -self.config.MIN_SWEEP_VOLUME_USDT:
```
Since `actual_sweep_volume >= 0` always, this condition is **impossible**. Bearish sweep signals (`STRONG_SHORT` from sweep) will **never fire**. The bot's short logic relies entirely on RSI + cluster dominance — half the short signal hierarchy is dead.

**Fix:** The sweep detector needs to track net directional volume (long liquidations minus short liquidations), not just total volume.

---

### C4: Non-Existent Methods Referenced in executor_bot.py

**File:** `executor_bot.py:540-547`

```python
guard_results = signal_generators[symbol].check_guardrails(
    symbol, signal_type, confidence_score,
    s.mark_price, cluster_snapshot,
    ohlcv_dataframes[symbol]
)
```

`SignalGenerator` has **no `check_guardrails()` method**. This will raise `AttributeError` at runtime.

Additionally:
- `position_manager.calculate_position_size()` does not exist (line 579)
- `exchange_client.execute_order()` does not exist on `AbstractExchangeClient` (line 585)
- `Config.BYBIT_WS_URL`, `Config.BINANCE_WS_URL`, `Config.MARKET_LOOP_DELAY` are referenced but never defined

This entire trade execution path in the live `market_loop` is **unreachable due to failure**.

---

### C5: RSI Exit Strategy Missing in Live Path

**File:** `rsi_swing_strategy.pine:23-24` vs `executor_bot.py`

The TradingView Pine Script prototype exits when RSI crosses above overbought (70). This is the strategy's *primary* exit — RSI mean-reversion closing. The live Python bot **never checks RSI for exits**. It only exits via:

1. Fixed SL/TP hit (`signal_generator.check_exit_signal()`)
2. Bearish sweep while long / bullish sweep while short (cancel signal)

This means the live bot may hold positions indefinitely through regime changes as long as the stop loss isn't hit.

---

### C6: No Valid Backtest Evidence

The codebase contains no statistically meaningful backtest. Specifically:
- No walk-forward analysis
- No out-of-sample period
- No Monte Carlo simulation
- No benchmark comparison (buy-and-hold, simple RSI-only)
- No Sharpe / Sortino calculation outside the grid search stub
- No recovery from transaction costs or slippage

**A strategy of this complexity should not be deployed without at least 2–3 years of out-of-sample validation showing positive expectancy.**

---

### C7: RSI Parameters Are Non-Standard Without Justification

**File:** `src/config.py:33-35`

```python
RSI_LENGTH     = 7      # Very short (standard is 14)
RSI_OVERSOLD   = 40     # Very loose (standard is 30)
RSI_OVERBOUGHT = 60     # Very loose (standard is 70)
```

An RSI(7) on a 1-minute timeframe with 40/60 thresholds will fire **constantly**. There is no evidence (backtest, research, paper) cited for these values. The Pine Script prototype uses 14/30/70 — completely different parameters. This suggests the Python implementation and the TradingView prototype are testing **different strategies**.

---

## Medium Severity

### M1: Duplicate Initialization in `main()`

`executor_bot.py:main()` initializes `status_tracker` twice (lines 718 and 823), `position_managers` twice (lines 764 and 827). The second set of initializations shadows the first, wasting memory and creating confusion about which instance is actually used.

### M2: OHLCV Fetched on Every Event

In `market_loop()` (line 419), OHLCV is fetched from the exchange on **every single event processed** — potentially hundreds of times per second in live mode. This will:
- Hit exchange rate limits quickly
- Add 50–200ms latency per fetch
- Produce identical data 99.9% of the time between candle closes

### M3: No Position Size Precision / Lot Size Adherence

`position_manager.determine_position_size()` (line 101) returns unrounded `usdt_to_invest / current_price`. Cryptocurrency exchanges enforce specific step sizes (e.g., 0.01 SOL). This will cause order rejections in live mode. The `apply_precision()` utility exists in `executor_bot.py` but is never called.

### M4: Liquidation Data Has No Directional Attribution

`ClusterAggregator._ingest_liquidation_event()` bins all liquidations by price only, ignoring whether the liquidation was a long or short position being force-closed. A cluster of *long* liquidations (traders forced to sell) is structurally different from a cluster of *short* liquidations (traders forced to buy), but the aggregator treats them identically. This undermines the entire cluster analysis premise.

### M5: No Exchange WebSocket Message Loss Handling

WebSocket connections can drop messages silently. The Bybit liquidation feed has no sequence number tracking or periodic REST snapshot reconciliation. In production, missing even a few large liquidation events would corrupt the cluster state.

### M6: Config Has Two Different Symbol Lists

- `Config.SYMBOLS` — loaded from `BYBIT_SYMBOLS` env var, defaults to `SOL/USDT`
- `Config.DEFAULT_SYMBOLS_RSI_SWING_BOT` — hardcoded to `["BTC/USDT", "ETH/USDT"]`

Different parts of the code reference different lists. `executor_bot.py` uses `DEFAULT_SYMBOLS_RSI_SWING_BOT` for exchange client initialization (line 761) but `SYMBOLS` for the loop (line 349). This means WebSocket subscriptions might not match the symbols being traded.

---

## Low Severity

### L1: Pine Script vs Python — Different Strategies

The Pine Script is long-only with RSI crossover entries and RSI-based exits. The Python bot is long/short with threshold-based entries and no RSI exits. These are different trading systems sharing a name.

### L2: No Market Regime Detection

The strategy has no awareness of trending vs. ranging markets. RSI mean-reversion strategies underperform in strong trends. An ADX or volatility filter would significantly reduce false signals.

### L3: `BYBIT_SYMBOLS` env var parsed with `;` delimiter (line 8 of config.py), but the doc in `RUNBOOK.md` shows comma-separated or space-separated examples, creating a documentation gap.

### L4: Catch-all exception handlers throughout the codebase (`except Exception as e`) mask genuine bugs and make debugging difficult.

---

## What Would Be Required to Proceed

| # | Requirement | Effort Estimate |
|---|---|---|
| 1 | Fix backtest engine — align data structures, fix async/sync mismatches | 1–2 days |
| 2 | Run and validate a proper backtest (in-sample + out-of-sample, walk-forward) | 3–5 days |
| 3 | Fix signal weight math and dead sweep logic | 0.5 day |
| 4 | Fix missing method references in executor_bot.py | 1 day |
| 5 | Add RSI exit logic to live bot (or remove from Pine Script) | 0.5 day |
| 6 | Add directional liquidation attribution to cluster aggregator | 1–2 days |
| 7 | Implement position size precision/lot size handling | 0.5 day |
| 8 | Add transaction cost + slippage models to backtest | 0.5 day |
| 9 | Add regime filter (ADX, volatility) | 1 day |
| 10 | WebSocket sequence number tracking + REST reconciliation | 1 day |
| 11 | Run simulation for >1000 trades, evaluate statistical significance | 2–3 days |
| 12 | Deploy to testnet for minimum 2 weeks before mainnet | — |

**Total estimated effort before live deployment: 3–5 weeks of focused development + 2 weeks minimum testnet observation.**

---

## Recommendation

The **architecture** is sound and the monitoring/observability stack is well ahead of most hobbyist bots. However, the **strategy logic is critically broken** in multiple places and there is **zero validated backtest evidence** that the approach has positive expectancy.

**Continue development**, but with the understanding that this is still a **prototype**, not a production-ready trading system. Fix the critical bugs first, validate the strategy on historical data, and only then proceed to testnet.
