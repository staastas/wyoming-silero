# Multi-stage build for smaller final image
FROM python:3.14-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu


# Final stage
FROM python:3.14-slim-bookworm

# Add labels for metadata
LABEL org.opencontainers.image.title="Wyoming Silero TTS"
LABEL org.opencontainers.image.description="Wyoming protocol server for Silero Text-to-Speech"
LABEL org.opencontainers.image.source="https://github.com/yourusername/wyoming-silero"
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
COPY --from=builder /root/.local /home/silero/.local
ENV PATH=/home/silero/.local/bin:$PATH

# Copy application code
COPY wyoming_silero/ wyoming_silero/


COPY docker_run.sh .
RUN chmod +x docker_run.sh

# Create directory for model cache
RUN mkdir -p /app/silero/model && \
    chown -R silero:silero /app

# Switch to non-root user
USER silero

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sys; from wyoming_silero import __version__; sys.exit(0)" || exit 1

# Expose Wyoming protocol port
EXPOSE 10200

# Set entrypoint
ENTRYPOINT ["bash", "docker_run.sh"]
