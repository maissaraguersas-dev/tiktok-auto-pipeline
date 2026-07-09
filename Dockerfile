# ═══════════════════════════════════════════════════════════════════════════════
# TikTok Auto Pipeline - Docker Image
# ═══════════════════════════════════════════════════════════════════════════════

# ── Builder Stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Runtime Stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL maintainer="TikTok Auto Pipeline"
LABEL description="Automated AI TikTok content pipeline"
LABEL version="1.0.0"

WORKDIR /app

# Install runtime dependencies
# ffmpeg: Video processing
# libpq5: PostgreSQL client
# chromium + dependencies: Browser automation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq5 \
    chromium \
    chromium-driver \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libvulkan1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security
RUN groupadd -r pipeline && useradd -r -g pipeline -s /bin/false pipeline

# Create necessary directories
RUN mkdir -p /app/storage/raw /app/storage/processed /app/logs && \
    chown -R pipeline:pipeline /app

# Copy application code
COPY --chown=pipeline:pipeline . .

# Switch to non-root user
USER pipeline

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin/chromium
ENV DISPLAY=:99

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command
ENTRYPOINT ["python", "main.py"]
CMD ["--loop", "--interval", "3600"]
