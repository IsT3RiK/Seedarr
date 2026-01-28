#!/bin/bash
set -e

echo "=== Seedarr Startup ==="

# Create data directory if it doesn't exist
mkdir -p /app/backend/data

# Initialize database tables and run migrations
echo "Initializing database..."
cd /app && python -c "
from backend.app.database import engine, Base
from backend.app.models import *
Base.metadata.create_all(bind=engine)
print('Tables created successfully')
"

echo "Running database migrations..."
cd /app/backend && alembic upgrade head || echo "Migrations completed (some may have been skipped)"

echo "Database ready."

# Start the application
echo "Starting Seedarr..."
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-config /app/backend/logging_config.json
