#!/bin/bash
# Deploy RSI Swing Bot to a cloud VPS.
# Prerequisites: Ubuntu 22.04+, Docker, Docker Compose v2, git
#
# Usage:
#   1. Copy this script and .env to your VPS
#   2. Fill in .env with your Bybit testnet API keys
#   3. Run: bash deploy.sh
#
# The bot will run 24/7 and auto-restart on crash or reboot.

set -e

echo "=== RSI Swing Bot — VPS Deployment ==="

# 1. Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    echo "Docker installed. Log out and back in if this is your first run."
fi

# 2. Clone repo if not already present
if [ ! -d "rsi-swing-bot" ]; then
    echo "Cloning repository..."
    git clone https://github.com/Jaleab/rsi-swing-bot.git
fi

cd rsi-swing-bot
git pull origin master

# 3. Check .env exists
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found."
    echo "  cp .env.example .env"
    echo "  # Edit .env with your Bybit testnet API keys"
    echo "  # Set BYBIT_TESTNET=true and SIM_MODE=false for testnet live trading"
    exit 1
fi

# 4. Build and start
echo "Building Docker image..."
docker compose build

echo "Starting bot + Prometheus + Grafana..."
docker compose up -d

# 5. Show status
echo ""
echo "=== Deployment complete ==="
echo ""
echo "Services:"
echo "  Bot:        docker compose logs -f bot"
echo "  Metrics:    http://<VPS-IP>:8000/metrics"
echo "  Prometheus: http://<VPS-IP>:9090"
echo "  Grafana:    http://<VPS-IP>:3000  (admin/admin)"
echo ""
echo "Useful commands:"
echo "  docker compose logs -f bot     # Follow bot logs"
echo "  docker compose restart bot     # Restart bot"
echo "  docker compose down            # Stop everything"
echo "  docker compose up -d           # Start everything"
echo ""
echo "The bot will auto-restart on crash or VPS reboot."
