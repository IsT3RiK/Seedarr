# ===========================
# Stage 1: Builder
# ===========================
FROM python:3.11-slim as builder

WORKDIR /build

COPY backend/requirements.txt .

RUN pip install --user --no-cache-dir --no-warn-script-location -r requirements.txt

# ===========================
# Stage 2: Runtime
# ===========================
FROM python:3.11-slim

WORKDIR /app

# Copy installed dependencies from builder stage
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY backend/ ./backend/

# Copy entrypoint script and fix Windows line endings
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH

# Create data directory
RUN mkdir -p /app/backend/data

EXPOSE 8000

# Run migrations then start app
ENTRYPOINT ["/docker-entrypoint.sh"]
