# Use an official Python runtime as a parent image
FROM python:3.10-slim-bullseye

# Install tini to handle signals and zombie processes
RUN apt-get update && apt-get install -y tini && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Create a non-root user and switch to it
RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

# Copy requirements.txt and install dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Declare a build argument for cache busting
ARG CACHE_BUSTER

# Copy application source code
# Use --chown to ensure the appuser owns these files
RUN echo "CACHE_BUSTER: ${CACHE_BUSTER}"
RUN find . -type d -name "__pycache__" -exec rm -rf {} + && find . -type f -name "*.pyc" -delete
COPY . /app

# Set ownership after copying all files
USER root
RUN chown -R appuser:appuser /app
USER appuser

# Ensure the entrypoint script is executable
RUN chmod +x /app/src/print_config_and_run.sh

# Remove Python bytecode to ensure latest code is run and reduce image size
RUN find . -type d -name "__pycache__" -exec rm -rf {} + && find . -type f -name "*.pyc" -delete

# Set PYTHONPATH to include /app so Python can find modules in src
ENV PYTHONPATH=/app

# Use tini as the entrypoint for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Synchronize time before running the bot (this ARG is for cache busting for other layers if needed)
ARG CACHE_BUSTER_VALUE

# Explicitly remove Python bytecode to ensure latest code is run at runtime (redundant after pip install, but as a safeguard)
RUN find /app -type d -name "__pycache__" -exec rm -rf {} +; find /app -type f -name "*.pyc" -delete

# Default command to run the application
CMD ["python", "/app/executor_bot.py"]