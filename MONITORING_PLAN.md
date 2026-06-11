# 🧭 Goal

Implement **Prometheus + Grafana** monitoring for your trading bot to track:

* Market and signal metrics (RSI, liquidation cluster, confidence, etc.)
* Bot health (data freshness, latency, errors)
* Performance (per-pair updates/sec, memory, CPU)

---

## 🧩 Full Implementation Plan

### **Step 1 – Install Prometheus Client Library**

Add to dependencies:

```bash
pip install prometheus_client
```

If you have a `requirements.txt`:

```txt
prometheus_client>=0.20.0
```

---

### **Step 2 – Instrument Metrics in `src/status_tracker.py`**

Add metrics that mirror your table columns:

```python
from prometheus_client import Gauge

rsi_gauge = Gauge("bot_rsi", "RSI value per pair", ["symbol"])
confidence_gauge = Gauge("bot_signal_confidence", "Signal confidence per pair", ["symbol"])
signal_gauge = Gauge("bot_signal_state", "Current signal (-1 sell, 0 neutral, 1 buy)", ["symbol"])
events_rate_gauge = Gauge("bot_events_per_sec", "Events processed per second", ["symbol"])
latency_gauge = Gauge("bot_latency_sec", "Seconds since last update per symbol", ["symbol"])
```

In your update loop:

```python
def update_metrics(symbol, snapshot):
    rsi_gauge.labels(symbol).set(snapshot.rsi)
    confidence_gauge.labels(symbol).set(snapshot.confidence)
    signal_gauge.labels(symbol).set(snapshot.signal)
    events_rate_gauge.labels(symbol).set(snapshot.events_per_sec)
    latency_gauge.labels(symbol).set(snapshot.latency)
```

---

### **Step 3 – Create Prometheus Exporter (`src/metrics_exporter.py`)**

```python
from prometheus_client import start_http_server
import logging

def start_metrics_server(port: int = 8000):
    logging.info(f"Starting Prometheus metrics exporter on port {port}")
    start_http_server(port)
```

You’ll import and call this in your main bot runner.

---

### **Step 4 – Update Bot Entry (`executor_bot.py`)**

At the top of your async entrypoint:

```python
from src.metrics_exporter import start_metrics_server

if Config.ENABLE_PROMETHEUS:
    start_metrics_server(Config.PROMETHEUS_PORT)
```

Add config keys:

```python
# Monitoring
ENABLE_PROMETHEUS: bool = True
PROMETHEUS_PORT: int = 8000
```

---

### **Step 5 – Create `monitoring/prometheus/prometheus.yml`**

```yaml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: "bot_metrics"
    static_configs:
      - targets: ["bot:8000"]  # service name from docker-compose
```

---

### **Step 6 – Create `docker-compose.yml`**

```yaml
version: "3.8"

services:
  bot:
    build: .
    container_name: trading_bot
    ports:
      - "8000:8000"
    environment:
      - ENABLE_PROMETHEUS=true

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    volumes:
      - ./monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
    depends_on:
      - prometheus
```

---

### **Step 7 – Configure Grafana Dashboard**

Once all services are running (`docker compose up`):

1. Visit [http://localhost:3000](http://localhost:3000) → login: `admin` / `admin`
2. Add Data Source → **Prometheus** → URL: `http://prometheus:9090`
3. Create Dashboard:

   * **Panel 1:** `bot_rsi{symbol="BTCUSDT"}`
   * **Panel 2:** `bot_signal_confidence`
   * **Panel 3:** `bot_latency_sec`
   * **Panel 4:** `bot_events_per_sec`
4. Adjust visualization type (time-series, gauge, stat) for readability.

Later you can export the dashboard as JSON to `monitoring/grafana/bot_dashboard.json` for versioning.

---

## ⚙️ Performance & Scaling Notes

| Concern                     | Solution                                                                          |
| --------------------------- | --------------------------------------------------------------------------------- |
| Many symbols                | Use per-symbol label metrics (no dynamic metric creation in hot path).            |
| Async vs thread             | `prometheus_client` is non-blocking, safe in async.                               |
| Long runs                   | Prometheus handles roll-ups automatically; you can set `scrape_interval: 5s–10s`. |
| Container resource tracking | Add `node_exporter` later for CPU/RAM stats if needed.                            |

---

## 🧠 Future Enhancements

* Add custom metric: `bot_signal_executions_total` (Counter)
* Add alerting rules in Prometheus (e.g., stale feed > 30 s)
* Persist Grafana dashboards under `monitoring/grafana/provisioning/`
* Integrate Telegram or Discord alert for threshold breaches.