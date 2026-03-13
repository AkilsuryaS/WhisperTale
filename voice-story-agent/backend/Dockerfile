# ─────────────────────────────────────────────────────────────
# Voice Story Agent — Backend
# Multi-stage build: keeps the runtime image lean.
#
# Build:  docker build -t voice-story-agent-backend .
# Run:    docker run -p 8080:8080 --env-file .env voice-story-agent-backend
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

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
