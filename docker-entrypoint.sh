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

# Resolve database path from DATABASE_URL (default: /app/backend/data/seedarr.db)
DATABASE_URL="${DATABASE_URL:-sqlite:////app/backend/data/seedarr.db}"
DB_FILE="${DATABASE_URL#sqlite:///}"
DB_DIR="$(dirname "$DB_FILE")"

# Ensure database directory exists with correct ownership
echo "Database directory: $DB_DIR"
mkdir -p "$DB_DIR"
chown -R seedarr:seedarr "$DB_DIR"

# Run database migrations as seedarr user
echo "Running database migrations..."

if [ -f "$DB_FILE" ]; then
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
cd /app/backend
exec gosu seedarr uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-config /app/backend/logging_config.json
