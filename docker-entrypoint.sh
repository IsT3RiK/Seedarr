#!/bin/sh
set -e

echo "=== Seedarr Startup ==="

# Create data directory
mkdir -p /app/backend/data

# Initialize database with all tables
echo "Initializing database..."
cd /app/backend
PYTHONPATH=/app/backend python3 -c "
from app.database import engine
from app.models import Base, TMDBCache, Tags, FileEntry, Settings, Tracker, Categories, C411Category, ProcessingQueue, BBCodeTemplate, NamingTemplate, NFOTemplate
print('Creating all tables...')
Base.metadata.create_all(bind=engine)
print('Database initialized successfully!')
"

echo "Starting Seedarr..."
cd /app/backend
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
