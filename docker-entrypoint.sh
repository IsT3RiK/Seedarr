#!/bin/bash
set -e

echo "=== Seedarr Startup ==="

# Create data directory
mkdir -p /app/backend/data

# Initialize database with all tables
echo "Initializing database..."
cd /app
python3 << 'PYTHON'
import os
os.environ.setdefault('DATABASE_URL', 'sqlite:////app/backend/data/seedarr.db')

from backend.app.database import engine, Base
from backend.app.models import *

print("Creating all tables...")
Base.metadata.create_all(bind=engine)
print("Database initialized successfully!")
PYTHON

echo "Starting Seedarr..."
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
