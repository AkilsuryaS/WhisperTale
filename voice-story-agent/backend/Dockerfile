# ─────────────────────────────────────────────────────────────
# Voice Story Agent — Backend
# Multi-stage build: keeps the runtime image lean.
#
# Build:  docker build -t voice-story-agent-backend .
# Run:    docker run -p 8080:8080 --env-file .env voice-story-agent-backend
#
# The image exposes port 8080 and uses the PORT env-var at runtime
# so Cloud Run can inject its own port without rebuilding.
# ─────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for some Google SDK C extensions
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
USER appuser
WORKDIR /home/appuser/app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=appuser:appuser app/ ./app/

# Cloud Run listens on PORT env var (default 8080)
ENV PORT=8080
EXPOSE 8080

# Unbuffered output so logs appear in Cloud Logging immediately
ENV PYTHONUNBUFFERED=1

# Liveness probe — Cloud Run and local health-checks both use /health.
# The interval/timeout/retries below are conservative for a cold-start
# container: wait 30 s before the first check, then every 30 s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:' + __import__('os').environ.get('PORT','8080') + '/health', timeout=4); \
         sys.exit(0 if r.status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
