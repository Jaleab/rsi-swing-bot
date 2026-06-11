#!/bin/bash

# Print the value of ENABLE_MANUAL_SWEEP_INJECTION from Config to stdout
python -c 'from src.config import Config; print(f"ENABLE_MANUAL_SWEEP_INJECTION_FROM_CONFIG: {Config.ENABLE_MANUAL_SWEEP_INJECTION}")'

# Execute the main bot script, piping its output to bot_output.log
python -B -m executor_bot | tee -a /app/bot_output.log