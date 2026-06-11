import subprocess
import time
import datetime
import os

BOT_CONTAINER_NAME = "rsi_swing_bot_container"
LOG_FILE = "monitor.log"

def log_message(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(f"[{timestamp}] {message}")

def container_exists(name):
    try:
        subprocess.run(
            ["docker", "inspect", name],
            capture_output=True, text=True, check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False

def is_bot_running():
    try:
        # Check if the Docker container is running
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", BOT_CONTAINER_NAME],
            capture_output=True, text=True, check=True
        )
        return "true" in result.stdout.strip().lower()
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        log_message("Docker command not found. Please ensure Docker is installed and in your PATH.")
        return False

def remove_bot_container():
    log_message(f"Attempting to remove Docker container: {BOT_CONTAINER_NAME}")
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", BOT_CONTAINER_NAME],
            capture_output=True, text=True, check=False # Do not check=True, container might not exist
        )
        if result.returncode == 0:
            log_message(f"Successfully removed Docker container: {BOT_CONTAINER_NAME}")
        else:
            log_message(f"Warning: Docker container {BOT_CONTAINER_NAME} may not have been removed. Stdout: {result.stdout.strip()}, Stderr: {result.stderr.strip()}")
        return True
    except Exception as e:
        log_message(f"Error removing Docker container {BOT_CONTAINER_NAME}: {e}")
        return False

def create_bot_container():
    log_message(f"Attempting to create Docker container: {BOT_CONTAINER_NAME}")
    try:
        # Mount the current working directory as a volume to persist logs and data
        # -v $(pwd):/app will mount the host's current directory to /app inside the container
        # This makes bot_output.log and any other data accessible on the host.
        result = subprocess.run(
            ["docker", "run", "-d", "--name", BOT_CONTAINER_NAME, "-p", "8000:8000", "-v", "/f/Desktop/rsi_swing_bot:/app", "rsi_swing_bot-bot"],
            capture_output=True, text=True, check=True
        )
        log_message(f"Successfully created Docker container: {BOT_CONTAINER_NAME}. Container ID: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        log_message(f"Error creating Docker container {BOT_CONTAINER_NAME}: {e.stderr}")
        return False
    except Exception as e:
        log_message(f"Unexpected error during container creation: {e}")
        return False

def start_bot_container():
    log_message(f"Attempting to start Docker container: {BOT_CONTAINER_NAME}")
    try:
        result = subprocess.run(
            ["docker", "start", BOT_CONTAINER_NAME],
            capture_output=True, text=True, check=True
        )
        log_message(f"Successfully started Docker container: {BOT_CONTAINER_NAME}. Stdout: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        log_message(f"Error starting Docker container {BOT_CONTAINER_NAME}: {e.stderr}")
        return False
    except Exception as e:
        log_message(f"Unexpected error during container start: {e}")
        return False

def main():
    log_message("RSI Swing Bot Monitor started.")
    while True:
        # Always ensure the container is recreated to pick up new code changes
        if container_exists(BOT_CONTAINER_NAME):
            log_message(f"Existing container '{BOT_CONTAINER_NAME}' found. Removing it...")
            remove_bot_container()
        
        log_message(f"Bot container '{BOT_CONTAINER_NAME}' does not exist or was removed. Attempting to create it...")
        if create_bot_container():
            log_message(f"Bot container '{BOT_CONTAINER_NAME}' created successfully.")
            # Verify bot_output.log existence and size on host
            bot_output_log_path = os.path.join(os.getcwd(), "bot_output.log")
            if os.path.exists(bot_output_log_path):
                log_message(f"bot_output.log exists on host. Size: {os.path.getsize(bot_output_log_path)} bytes.")
            else:
                log_message(f"WARNING: bot_output.log DOES NOT exist on host after container creation.")
        else:
            log_message(f"Failed to create bot container '{BOT_CONTAINER_NAME}'. Will retry.")
            time.sleep(300)
            continue

        if not is_bot_running():
            log_message(f"Bot container '{BOT_CONTAINER_NAME}' is not running. Attempting to restart...")
            if start_bot_container():
                log_message(f"Bot container '{BOT_CONTAINER_NAME}' restarted successfully.")
            else:
                log_message(f"Failed to restart bot container '{BOT_CONTAINER_NAME}'. Will retry.")
            
        else:
            log_message(f"Bot container '{BOT_CONTAINER_NAME}' is running.")
            
        # Fetch and log the last few lines of the bot's output, especially useful if it crashed silently
        try:
            bot_logs_result = subprocess.run(
                ["docker", "logs", BOT_CONTAINER_NAME, "--tail", "20", "--since", "5m"], # Last 20 lines from the last 5 minutes
                capture_output=True, text=True, check=False # Do not check=True here, as container might be stopped
            )
            if bot_logs_result.stdout:
                log_message(f"Bot container '{BOT_CONTAINER_NAME}' latest logs (stdout):\n{bot_logs_result.stdout}")
            if bot_logs_result.stderr:
                log_message(f"Bot container '{BOT_CONTAINER_NAME}' errors (stderr):\n{bot_logs_result.stderr}")
        except Exception as e:
            log_message(f"Error fetching bot logs: {e}")

        time.sleep(300) # Check every 5 minutes
if __name__ == "__main__":
    main()