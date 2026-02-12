#!/bin/sh
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
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/sh seedarr
fi

# Create data directory if it doesn't exist
mkdir -p /app/backend/data

# Fix permissions on data only (user config persists across updates)
chown -R seedarr:seedarr /app/backend/data

# Run database migrations
echo "Running database migrations..."
cd /app/backend

if gosu seedarr alembic upgrade head 2>/dev/null; then
    echo "Migrations complete."
else
    echo "Migration upgrade failed, stamping current state..."
    gosu seedarr alembic stamp head
    echo "Database stamped at current version."
fi

# Start the application (must run from /app for 'backend.app.main' import)
cd /app
echo "Starting Seedarr..."
exec gosu seedarr uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --log-config /app/backend/logging_config.json
