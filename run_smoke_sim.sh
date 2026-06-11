#!/bin/bash

# Define variables
CONTAINER_NAME="rsi_swing_bot_container"
LOG_FILE="bot_output.log"
MONITOR_SCRIPT="monitor.py"
SIM_DURATION_SECONDS=300 # Increased duration for better data generation

# Parse arguments
NO_DOCKER=false
for arg in "$@"; do
    if [ "$arg" == "--no-docker" ]; then
        NO_DOCKER=true
        shift # Remove --no-docker from arguments
    fi
done

echo "Starting long-run stability simulation..."

# Function to run the simulation
run_simulation() {
    echo "--- Starting Simulation ---"

    # 1. Remove old log files and temporary files
    echo "Removing old log file: bot_output.log..."
    rm -f bot_output.log
    echo "Old log file removed (if any)."
    echo "Removing dummy_file.txt (if any)..."
    rm -f dummy_file.txt
    echo "Dummy file removed (if any)."
    echo "Removing monitor_output.txt (if any)..."
    rm -f monitor_output.txt
    echo "Monitor output file removed (if any)."

    if [ "$NO_DOCKER" = true ]; then
        echo "--- Running Native Simulation (no Docker) ---"
        # Set environment variables for simulation
        export SIM_MODE=true
        export SIMULATION_RANDOM_SEED=123
        # Ensure output directory exists for native run
        mkdir -p data/sim_analysis

        echo "Executing executor_bot.py directly..."
        # Dynamically find the Python executable within the virtual environment
        PYTHON_EXECUTABLE=$(find "$(pwd)/.venv" -name "python" -type f -perm /a+x | head -n 1)
        if [ -z "$PYTHON_EXECUTABLE" ]; then
            echo "Error: Python executable not found in ./.venv/. Please ensure your virtual environment is correctly set up."
            exit 1
        fi
        
        # Execute executor_bot.py directly
        "$PYTHON_EXECUTABLE" executor_bot.py --sim

        echo "Native simulation finished."
        echo "Copying signal_records.csv and trade_results.csv from /tmp to data/sim_analysis..."
        cp /tmp/signal_records.csv data/sim_analysis/signal_records.csv || echo "signal_records.csv not found in /tmp."
        cp /tmp/trade_results.csv data/sim_analysis/trade_results.csv || echo "trade_results.csv not found in /tmp."
        echo "Signal analysis CSVs copied to data/sim_analysis."
    else
        # --- Docker Simulation Logic ---
        # 2. Aggressively stop and remove all Docker containers to ensure a clean start
        echo "Stopping and removing all Docker containers (if any)..."
        docker rm -f $(docker ps -aq) > /dev/null 2>&1 || true
        echo "All Docker containers stopped and removed."

        # 3. Stop and remove existing Docker Compose services to ensure a clean start
        echo "Stopping and removing existing Docker Compose services (if any)..."
        docker compose down --remove-orphans > /dev/null 2>&1 || true
        echo "Existing Docker Compose services stopped and removed."

        # 4. Start Docker Compose services in detached mode with SIMULATION_RANDOM_SEED
        echo "Starting Docker Compose services in detached mode..."
        # Use a fixed random seed for reproducibility during stability testing
        export SIMULATION_RANDOM_SEED=123
        export SIM_MODE=true # Explicitly set SIM_MODE for the container
        # Generate a unique cache buster value to force Docker to rebuild relevant layers
        export CACHE_BUSTER_VALUE=$(date +%s)
        docker compose build --no-cache --build-arg CACHE_BUSTER=${CACHE_BUSTER_VALUE}
        docker compose up -d --build --force-recreate

        # Give Docker Compose a moment to start the services and assign container names
        # Wait for the trading_bot container to be running
        echo "Waiting for trading_bot container to start..."
        CONTAINER_ID=$(docker compose ps -q bot)
        if [ -z "$CONTAINER_ID" ]; then
            echo "Error: trading_bot container ID not found."
            exit 1
        fi

        MAX_RETRIES=10
        RETRY_COUNT=0
        until [ "$(docker inspect -f '{{.State.Status}}' $CONTAINER_ID)" == "running" ] || [ $RETRY_COUNT -ge $MAX_RETRIES ]; do
            echo "Container not yet running. Waiting... ($((MAX_RETRIES - RETRY_COUNT)) retries left)"
            sleep 2
            RETRY_COUNT=$((RETRY_COUNT + 1))
        done

        if [ "$(docker inspect -f '{{.State.Status}}' $CONTAINER_ID)" != "running" ]; then
            echo "Error: trading_bot container failed to start within the allotted time."
            exit 1
        fi
        echo "trading_bot container is running."

        echo "--- Contents of /app/src/execution/paper_trader.py in container ---"
        docker exec $CONTAINER_ID cat /app/src/execution/paper_trader.py
        echo "------------------------------------------------------------------"

        # 5. Run the simulation
        echo "Running simulation for ${SIM_DURATION_SECONDS} seconds..."
        # The bot will run in detached mode, so we just wait for the duration
        sleep $SIM_DURATION_SECONDS

        # Capture logs directly to the terminal for inspection
        echo "--- Logs from trading_bot container ---"
        docker compose logs bot
        echo "---------------------------------------"

        # Inspect /tmp directory inside the container
        echo "--- Contents of /tmp inside trading_bot container ---"
        docker exec $CONTAINER_ID ls -l /tmp
        echo "-----------------------------------------------------"

        # Inspect the contents of the generated CSV files inside the container
        echo "--- Contents of /tmp/signal_records.csv inside trading_bot container ---"
        docker exec $CONTAINER_ID cat /tmp/signal_records.csv || echo "File not found or empty."
        echo "----------------------------------------------------------------------"

        echo "--- Contents of /tmp/trade_results.csv inside trading_bot container ---"
        docker exec $CONTAINER_ID cat /tmp/trade_results.csv || echo "File not found or empty."
        echo "---------------------------------------------------------------------"

        # Copy the generated sim_signal_analysis.csv from the container to the host
        echo "Copying signal_records.csv and trade_results.csv from container to host..."
        mkdir -p data/sim_analysis # Ensure the directory exists
        docker cp trading_bot:/tmp/signal_records.csv data/sim_analysis/signal_records.csv
        docker cp trading_bot:/tmp/trade_results.csv data/sim_analysis/trade_results.csv
        echo "Signal analysis CSVs copied to data/sim_analysis."
        echo "Stopping Docker Compose services..."
        docker compose down
    fi

    echo "Simulation finished."
}

# --- Run the simulation ---
run_simulation

echo "Long-run stability simulation script finished."