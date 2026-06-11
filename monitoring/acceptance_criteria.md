# Acceptance Criteria for RSI Swing Bot Testing

This document outlines the pass/fail thresholds and criteria for each phase of the RSI Swing Bot's testing lifecycle. Meeting these criteria is essential for progressing to the next phase.

---

## Phase 1: Unit & Integration Testing (Developer-focused)

**Objective:** Ensure individual components and their immediate interactions function correctly and robustly.

*   **All unit tests:** Pass 100%.
*   **Code coverage:** >= 80% for core modules (signal generation, aggregator, executor).
*   **No flakey tests:** Re-run 5x in CI without failures.

---

## Phase 2: Dry Run & Functional Testing (Simulated Environment)

**Objective:** Validate end-to-end bot behavior in a live-like simulated environment without financial risk, focusing on operational flow and initial performance.

*   **Bot uptime:** Runs for >= 6 hours without crashing (no container exit or uncaught exceptions).
*   **Signal generation rate:** Within expected bounds (e.g., 1–12 signals/day per symbol – tune per symbol).
*   **Simulated PnL drift:** Acceptable (no > X% drawdown vs baseline – pick X small, e.g., 0.1%).
*   **Prometheus metrics:** All configured metrics are successfully scraped and showing time-series data for every symbol (no missing labels).

---

## Phase 3: Live Testnet Trading (Staging Environment)

**Objective:** Validate end-to-end bot behavior in a real-world, risk-free trading environment, connecting to actual exchange APIs.

*   **Order execution & reconciliation:** Orders placed, filled, and reconciled with `PositionManager` 100% of the time for market orders.
*   **API errors:** No unhandled API errors for 48 hours.
*   **Risk limits:** Max daily loss threshold triggers safe-mode when tested.

---

## Phase 4: Production Deployment (Live Environment)

**Objective:** Deploy the bot to a live production environment with real capital, focusing on continuous monitoring and rapid response.

*   **Canary window (1–7 days):** Small capital deployment, zero critical alerts. If >1 critical alert/day, initiate immediate rollback.