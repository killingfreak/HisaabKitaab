# syntax=docker/dockerfile:1
FROM python:3.12-slim

# bcrypt and asyncpg both need a C compiler to build from source on some
# platforms; build-essential covers that. Removed from the final layer
# isn't done here (single-stage, kept simple) — acceptable for a pilot
# deploy; revisit with a multi-stage build if image size becomes a concern.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so Docker's layer cache skips reinstalling
# dependencies on every code change — only rebuilds this layer when
# requirements.txt itself changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as a non-root user — a container running as root that gets
# compromised has host-level implications a non-root one doesn't.
RUN useradd --create-home appuser && chown -R appuser:appuser /app && chmod +x entrypoint.sh
USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
