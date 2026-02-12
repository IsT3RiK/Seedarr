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

# Smart database migration
echo "Running database migrations..."
cd /app/backend

# Detect DB state and fix alembic tracking before running migrations
gosu seedarr python3 -c "
import sqlite3, os, sys, subprocess

db_path = os.environ.get('DATABASE_URL', 'sqlite:////app/backend/data/seedarr.db')
db_file = db_path.replace('sqlite:///', '').replace('sqlite://', '')
if not db_file.startswith('/'):
    db_file = '/app/backend/data/seedarr.db'

if not os.path.exists(db_file):
    print('Fresh install - no existing database')
    sys.exit(0)

conn = sqlite3.connect(db_file)
cur = conn.cursor()

# Get existing tables
cur.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
tables = [r[0] for r in cur.fetchall()]

if 'settings' not in tables:
    print('Empty database - fresh install')
    conn.close()
    sys.exit(0)

# Get settings columns to detect actual DB state
cur.execute('PRAGMA table_info(settings)')
columns = [col[1] for col in cur.fetchall()]

has_qbit_content = 'qbittorrent_content_path' in columns
has_wizard = 'wizard_completed' in columns
has_extra_config = 'extra_config' in columns

# Determine the real migration level based on schema
if has_qbit_content:
    real_rev = '025_add_qbittorrent_content_path'
elif has_wizard:
    real_rev = '024_migrate_adapter_types'
elif has_extra_config:
    real_rev = '022_add_naming_and_nfo_templates'
else:
    real_rev = None

# Check what alembic thinks the version is
current_rev = None
if 'alembic_version' in tables:
    cur.execute('SELECT version_num FROM alembic_version')
    row = cur.fetchone()
    if row:
        current_rev = row[0]

conn.close()

print(f'DB schema level: {real_rev or \"unknown\"}')
print(f'Alembic tracked at: {current_rev or \"not tracked\"}')

# Fix mismatches
if current_rev == real_rev:
    print('Alembic tracking matches DB state - OK')
    sys.exit(0)

if current_rev and current_rev != real_rev and real_rev:
    # Alembic is wrong (e.g. stamped at head but columns missing)
    print(f'Mismatch detected! Correcting: {current_rev} -> {real_rev}')
    r = subprocess.run(['alembic', 'stamp', '--purge', real_rev], capture_output=True, text=True)
    if r.returncode != 0:
        print(f'Stamp correction failed: {r.stderr}')
        sys.exit(1)
    print(f'Corrected to {real_rev}')
elif not current_rev and real_rev:
    # Old DB without alembic tracking
    print(f'Untracked DB - stamping at {real_rev}')
    r = subprocess.run(['alembic', 'stamp', real_rev], capture_output=True, text=True)
    if r.returncode != 0:
        print(f'Stamp failed: {r.stderr}')
        sys.exit(1)
    print(f'Stamped at {real_rev}')
else:
    print('Could not determine DB state - will try upgrade from scratch')
"

# Now run the actual upgrade (applies any remaining migrations)
if gosu seedarr alembic upgrade head; then
    echo "Migrations complete."
else
    echo "WARNING: Migration had issues. Starting app anyway."
fi

# Start the application (stay in /app/backend for 'app.xxx' imports)
echo "Starting Seedarr..."
exec gosu seedarr uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-config /app/backend/logging_config.json
