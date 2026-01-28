#!/bin/bash
set -e

echo "=== Seedarr Startup ==="

# Create data directory
mkdir -p /app/backend/data

# Initialize database with all tables
echo "Initializing database..."
cd /app/backend
export PYTHONPATH=/app/backend

python3 << 'PYTHON'
import sys
sys.path.insert(0, '/app/backend')

from app.database import engine, Base
from app.models import *

print("Creating all tables...")
Base.metadata.create_all(bind=engine)
print("Database initialized successfully!")
PYTHON

echo "Starting Seedarr..."
cd /app
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
