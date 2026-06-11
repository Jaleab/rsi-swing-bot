# Phase 2.2 Verification Checklist: Guard Integrity & Observability

This checklist outlines the mandatory verification steps for Phase 2.2. All items must pass before proceeding to the next phase.

## 1. Guard Behavior Verification

*   [ ] **Max Total Open Positions Guard**:
    *   **Scenario**: Attempt to open more positions than `Config.MAX_TOTAL_OPEN_POSITIONS`.
    *   **Expected**: New trades are blocked. `GUARD_BLOCK` log with `guard=MAX_TOTAL_OPEN_POSITIONS` is emitted. `SimpleMonitor` `Trades Blocked` count increases.
*   [ ] **Max Positions Per Symbol Guard**:
    *   **Scenario**: Attempt to open more positions for a single symbol than `Config.MAX_POSITIONS_PER_SYMBOL`.
    *   **Expected**: New trades for that symbol are blocked. `GUARD_BLOCK` log with `guard=MAX_POSITIONS_PER_SYMBOL` is emitted. `SimpleMonitor` `Trades Blocked` count increases.
*   [ ] **Trade Cooldown Guard**:
    *   **Scenario**: Open a position, then immediately attempt to open another for the same symbol within `Config.TRADE_COOLDOWN_SECONDS`.
    *   **Expected**: The second trade is blocked. `GUARD_BLOCK` log with `guard=TRADE_COOLDOWN` is emitted. `SimpleMonitor` `Trades Blocked` count increases.
*   [ ] **Signal Cooldown Guard**:
    *   **Scenario**: A signal is generated, then an identical signal is generated again within `Config.SIGNAL_COOLDOWN_SECONDS`.
    *   **Expected**: The duplicate signal is ignored (does not trigger a new trade attempt). `GUARD_BLOCK` log with `guard=SIGNAL_COOLDOWN` is emitted. `SimpleMonitor` `Trades Blocked` count increases.
*   [ ] **Safe Mode Guard**:
    *   **Scenario**: Manually activate `Config.ENABLE_SAFE_MODE = True` or trigger it via errors. Then attempt to open a position.
    *   **Expected**: All new trade attempts are blocked. `GUARD_BLOCK` log with `guard=SAFE_MODE` is emitted. `SimpleMonitor` `SAFE_MODE` status is `ON` and `Trades Blocked` count increases.
*   [ ] **GuardResult Always Populated**:
    *   **Scenario**: Trigger various guard conditions.
    *   **Expected**: Every blocked trade decision is encapsulated in a `GuardResult` object, and its properties (`allowed`, `reason`, `guard_name`, `details`) are correctly populated.
*   [ ] **No Trade After Guard=DENY**:
    *   **Scenario**: Trigger any guard condition that results in `GuardResult.allowed = False`.
    *   **Expected**: No actual exchange order is placed (in SIM_MODE, no `Position` object should be created/updated).

## 2. Observability Verification

*   [ ] **Structured Guard Logs**:
    *   **Scenario**: Trigger various guard conditions.
    *   **Expected**: All guard block events are logged in the specified structured format: `GUARD_BLOCK | symbol={symbol} | guard={guard_name} | reason={reason} | details={details if details else 'N/A'}`.
*   [ ] **SimpleMonitor Shows Accurate Counts**:
    *   **Scenario**: Trigger various guard conditions.
    *   **Expected**: `SimpleMonitor` output (`monitor_output.txt`) accurately reflects:
        *   `Guards Triggered` (counts by guard name).
        *   `Trades Blocked` (total count of blocked trade attempts).
        *   `Last Guard Reason` (displays the reason for the most recent guard block).
*   [ ] **No "N/A" or Empty Guard States**:
    *   **Scenario**: Run the bot for a period without triggering any guards.
    *   **Expected**: `SimpleMonitor` displays meaningful default values (e.g., "None", 0, "N/A") for guard metrics, not empty or erroneous strings.

## 3. Execution Integrity Verification

*   [ ] **No Position Opens Without Passing Guards**:
    *   **Scenario**: Attempt to open a position when any guard condition should block it.
    *   **Expected**: The `PositionManager.open_position` method correctly respects `GuardResult` and does not proceed to place an order.
*   [ ] **No Position Opens Twice for Same Signal (if `MAX_POSITIONS_PER_SYMBOL = 1`)**:
    *   **Scenario**: Generate a signal, open a position, then immediately generate an identical signal.
    *   **Expected**: The second signal should be blocked by `MAX_POSITIONS_PER_SYMBOL` guard.
*   [ ] **No Silent Failures**:
    *   **Scenario**: Introduce an unexpected condition (e.g., attempt to pass invalid data to `open_position`).
    *   **Expected**: The system either logs an error and enters safe mode, or asserts gracefully, rather than failing silently or with unexpected behavior.
*   [ ] **StatusTracker Reflects Reality**:
    *   **Scenario**: Trigger various guard conditions and observe `status_tracker` state.
    *   **Expected**: `status_tracker.status[symbol].guard_metrics` accurately reflects the triggered guards and blocked trades.

## 4. Runtime Stability (Minimum)

*   [ ] **Run SIM_MODE ≥ 2 Hours**:
    *   **Scenario**: Let the bot run in `SIM_MODE` with event generation for at least 2 hours.
    *   **Expected**:
        *   No crashes or unexpected terminations.
        *   No significant memory growth (monitor system memory usage).
        *   Monitor thread remains alive and updates `monitor_output.txt`.
        *   Market loop does not stall (event processing continues).
