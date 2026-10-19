# Multi-stage build for smaller final image
FROM python:3.14-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtualenv, so they do not depend on
# which user's home directory Python looks in
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu


# Final stage
FROM python:3.14-slim-bookworm

# Add labels for metadata
LABEL org.opencontainers.image.title="Wyoming Silero TTS"
LABEL org.opencontainers.image.description="Wyoming protocol server for Silero Text-to-Speech"
LABEL org.opencontainers.image.source="https://github.com/staastas/wyoming-silero"
LABEL org.opencontainers.image.licenses="MIT"

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the application
RUN useradd -m -u 1000 -s /bin/bash silero

# Set working directory
WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Copy application code
COPY wyoming_silero/ wyoming_silero/

COPY docker_run.sh .
RUN chmod +x docker_run.sh

# Create directory for model cache
RUN mkdir -p /app/silero/model && \
    chown -R silero:silero /app

# The entrypoint starts as root only to fix the ownership of a bind-mounted
# model directory, then drops to the silero user.

# Health check: the server must answer a Wyoming Describe request. The long
# start period covers the first run, when the model is being downloaded.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python3 -m wyoming_silero.healthcheck || exit 1

# Expose Wyoming protocol port
EXPOSE 10200

# Set entrypoint
ENTRYPOINT ["bash", "docker_run.sh"]
