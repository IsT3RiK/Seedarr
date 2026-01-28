#!/bin/bash
set -e

echo "=== Seedarr Startup ==="

# Run database migrations
echo "Running database migrations..."
cd /app/backend && alembic upgrade head

echo "Migrations complete."

# Start the application
echo "Starting Seedarr..."
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-config /app/backend/logging_config.json
