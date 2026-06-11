FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data && chmod 777 /app/data

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "executor_bot.py"]
