# Acceptance Criteria for RSI Swing Bot Testing

This document outlines the acceptance criteria for each phase of testing the RSI Swing Bot, ensuring its robustness, correctness, and operational stability.

---

## PHASE 1 — Internal Code Testing (Unit + Integration)

**Goal:** Confirm logic correctness without running the bot.

### Pass Criteria:
*   ✔ All tests pass.
*   ✔ Coverage ≥ 80% for logic modules (e.g., SignalGenerator, ClusterAggregator, PositionManager).

---

## PHASE 2 — Dry-Run Simulation Test (Local, No APIs)

**Goal:** Validate bot behavior using synthetic data streams in a simulated environment.

### Pass Criteria:
*   ✔ Bot runs continuously for 2 hours without `monitor.py` intervention.
*   ✔ Logs show healthy activity, including signal generation and absence of critical errors.
*   ✔ No crash, no WebSocket loop stuck, no runaway mark-price ("infinite drift").
*   ✔ Signals are generated with variability in confidence, sweep volume, and cluster impact.
*   ✔ ABSENCE of tracebacks, unhandled exceptions, or process exit messages in logs.

---

## PHASE 3 — Metrics + Monitoring Test (Prometheus + Grafana)

**Goal:** Validate the bot's observability and monitoring setup.

### Pass Criteria:
*   ✔ Prometheus target for the bot is "UP" at `http://localhost:9090/targets`.
*   ✔ Metrics (e.g., `bot_signals_total{type="LONG"}`) are accessible via `docker exec -it rsi_swing_bot_container curl localhost:8000/metrics` and show changing values over time.
*   ✔ Grafana dashboards (at `http://localhost:3000`) are updating and correctly display:
    *   Signal rate
    *   Sweep count
    *   Cluster sizes
    *   Profitability (if simulated)
    *   System health (latency, lag, exceptions)

---

## PHASE 4 — Testnet Execution Test (Real API but Small Risk)

**Goal:** Confirm bot functionality with real exchange APIs in a low-risk testnet environment.

### Pass Criteria:
*   ✔ Zero unhandled exceptions.
*   ✔ All orders executed (market buy, market sell) are confirmed.
*   ✔ Position synchronization is accurate.
*   ✔ Mark price consistency is maintained.
*   ✔ No liquidations occur (given very low risk settings).
*   ✔ Bot runs for 48 hours without crashing.
*   ✔ Orders reconcile 100%.
*   ✔ No stuck WebSockets.

---

## PHASE 5 — Pre-Production Canary Test (Small Real Capital)

**Goal:** Validate bot performance and safety with minimal real capital in a production-like environment.

### Pass Criteria:
*   ✔ No operational anomalies detected.
*   ✔ No `monitor.py` restarts.
*   ✔ No unexpected exposure or unexplained orders.
*   ✔ PnL behavior is as expected.
*   ✔ Signal-to-trade coherence is maintained.
*   ✔ Stable Prometheus heartbeat.

---

## PHASE 6 — Full Production Deployment

**Goal:** Final deployment to full production after all previous phases pass.

### Requirements:
*   `monitor.py` active and fully functional.
*   Alerts configured for critical events.
*   Daily max loss (risk kill switch) implemented and active.
*   Versioned Docker tags used for deployments.
*   Rollback script ready for immediate use.