# ===========================
# Stage 1: Builder
# ===========================
FROM python:3.11-slim as builder

WORKDIR /build

COPY backend/requirements.txt .

RUN pip install --no-cache-dir --no-warn-script-location -r requirements.txt

# ===========================
# Stage 2: Runtime
# ===========================
FROM python:3.11-slim

WORKDIR /app

# Install gosu for stepping down from root
RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy installed dependencies from builder stage
COPY --from=builder /usr/local /usr/local

# Copy application code
COPY backend/ ./backend/

# Copy entrypoint script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PUID=1000 \
    PGID=1000

# Create data directory
RUN mkdir -p /app/backend/data

EXPOSE 8000

# Run migrations then start app
ENTRYPOINT ["/docker-entrypoint.sh"]
