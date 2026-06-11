@echo off
echo Stopping and removing existing Docker containers...
docker-compose down -v

echo Rebuilding and starting Docker containers in detached mode...
docker-compose build --no-cache
docker-compose up -d
set SAVE_DATA=true

echo Streaming logs for rsi_swing_bot_container (Press Ctrl+C to stop streaming logs, containers will continue to run in background)...
docker logs -f rsi_swing_bot_container