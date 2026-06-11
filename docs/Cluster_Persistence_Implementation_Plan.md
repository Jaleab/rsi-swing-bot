# Cluster Aggregator State Persistence Implementation Plan

## Objective

To enable the `ClusterAggregator` to persist its internal state (aggregated liquidation `bins` and `events_deque`) across bot restarts, allowing it to retain its "memory" of liquidation clusters without starting from scratch.

## Persistence Strategy

1.  **Serialization Format**: JSON (for readability and ease of parsing).
2.  **Storage Location**: `Config.DATA_DIR` (e.g., `/app/data/`).
3.  **Trigger for Saving**:
    *   Graceful shutdown of the bot.
    *   Periodic saving during bot operation.
4.  **Trigger for Loading**:
    *   Bot startup.

## Detailed Implementation Steps

### Phase 1: Update `src/config.py`

Add new configuration parameters to control persistence behavior.

```python
# --- Cluster Persistence ---
PERSIST_CLUSTERS: bool = True # Enable/disable cluster state persistence
CLUSTER_STATE_FILE: str = "cluster_state.json" # Filename for saving cluster state
CLUSTER_PERSISTENCE_INTERVAL_S: int = 300 # How often to save cluster state in seconds (e.g., 5 minutes)
```

### Phase 2: Modify `src/cluster_aggregator.py`

Implement methods to save and load the aggregator's state.

1.  **Add `save_state()` method**:
    *   This asynchronous method will serialize `self.symbol_data` (which includes `bins` and `events_deque` for all symbols) to a JSON file in `Config.DATA_DIR`.
    *   `events_deque` needs to be converted to a list for JSON serialization.
    *   Handle file I/O errors and log appropriately.

2.  **Add `load_state()` method**:
    *   This asynchronous method will attempt to load `self.symbol_data` from the JSON file at startup.
    *   Convert lists back to `deque` objects after deserialization.
    *   Handle `FileNotFoundError` (for first run) and `JSONDecodeError`.
    *   Upon successful load, ensure old events are pruned using `_expire_old_events` for each symbol to align with current `SLIDING_WINDOW_S` and `CLUSTER_MAX_AGE_S` configurations, as these might have changed since the last save.

### Phase 3: Modify `executor_bot.py`

Integrate the save/load functionality and manage periodic saving.

1.  **Loading State at Startup**:
    *   After initializing `cluster_aggregator`, call `await cluster_aggregator.load_state()` before starting the main `while True` loop.
    *   If `Config.PERSIST_CLUSTERS` is `True`.

2.  **Periodic Saving**:
    *   Implement an `asyncio.Task` that periodically calls `await cluster_aggregator.save_state()` based on `Config.CLUSTER_PERSISTENCE_INTERVAL_S`. This task should be added to `background_tasks`.

3.  **Graceful Shutdown Saving**:
    *   Implement a signal handler (e.g., for `SIGTERM` in a Unix-like environment, or a more robust shutdown mechanism for Docker) to ensure `await cluster_aggregator.save_state()` is called before the bot exits. This is critical to save the latest state.

### Phase 4: Update Documentation

Update `RSI_Swing_Bot_Living_Document.md` to reflect the new cluster persistence capabilities.

1.  **Add a new section**: "Cluster Aggregator State Persistence".
2.  **Explain**:
    *   Why persistence is important (maintaining market memory across restarts).
    *   How it works (serialization to JSON, periodic saving, loading at startup).
    *   Mention the new configuration parameters in `src/config.py`.
    *   Highlight that `events_deque` and `bins` are now saved and reloaded.

## Testing Considerations

*   **Initial Run**: Verify that the bot starts without a `cluster_state.json` file, creates one, and builds its state.
*   **Restart Test**: Restart the bot and verify that `cluster_state.json` is loaded and the `ClusterAggregator` resumes with previous data (check logs for `last_event_timestamp` values after load).
*   **Periodic Save Test**: Confirm that the `cluster_state.json` file is updated periodically.
*   **Graceful Shutdown Test**: Ensure the state is saved when the bot is stopped gracefully (e.g., `Ctrl+C` in the terminal).
*   **Data Integrity**: Verify that the loaded data correctly reconstructs the clusters (e.g., `get_snapshot` returns expected values). Also, verify that `get_snapshot()` after load produces the same cluster strengths as before shutdown.
*   **Error Handling**: Test scenarios where the `cluster_state.json` file is corrupted or missing.

## Optional Enhancements

*   **Alternative Serialization**: For very large datasets or improved performance, consider replacing JSON serialization with alternatives like Python's `pickle` module (for native Python objects) or a lightweight embedded database like SQLite. JSON is human-readable but can be slower for extensive `events_deque` data.