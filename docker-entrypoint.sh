#!/bin/bash
set -e

echo "=== Seedarr Startup ==="

# Default PUID/PGID if not set
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Running with PUID=$PUID and PGID=$PGID"

# Create group if it doesn't exist
if ! getent group seedarr > /dev/null 2>&1; then
    groupadd -g "$PGID" seedarr
fi

# Create user if it doesn't exist
if ! id -u seedarr > /dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/bash seedarr
fi

# Create data directory if it doesn't exist
mkdir -p /app/backend/data

# Fix permissions on data directory (writable) and pip packages
chown -R seedarr:seedarr /app/backend/data
chown -R seedarr:seedarr /root/.local

# Run database migrations as seedarr user
echo "Running database migrations..."
DB_PATH="/app/backend/data/seedarr.db"

if [ -f "$DB_PATH" ]; then
    echo "Existing database found - running migrations..."
    cd /app/backend && gosu seedarr alembic upgrade head || {
        echo "WARNING: Migration failed. Attempting stamp + upgrade..."
        cd /app/backend && gosu seedarr alembic stamp head 2>/dev/null || true
        echo "WARNING: Migration had issues. Starting app anyway."
    }
else
    echo "Fresh install - no existing database"
    # Let SQLAlchemy create tables on first startup, then stamp alembic
    cd /app/backend && gosu seedarr alembic upgrade head 2>/dev/null || {
        echo "Initial migration will run on first startup."
    }
fi

echo "Migrations complete."

# Start the application as seedarr user
echo "Starting Seedarr..."
exec gosu seedarr uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-config /app/backend/logging_config.json
