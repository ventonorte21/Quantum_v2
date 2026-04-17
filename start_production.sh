#!/bin/bash
set -e

echo "=== Quantum Trading [Production] starting ==="

# ── 1. Database ────────────────────────────────────────────────────────────────
if [ -n "$MONGO_URL_ATLAS" ]; then
  echo "[1/5] Using MongoDB Atlas (persistent cloud storage)"
  export MONGO_URL="$MONGO_URL_ATLAS"
else
  echo "[1/5] MONGO_URL_ATLAS not set — falling back to local MongoDB"
  mkdir -p /home/runner/mongodb-data
  if ! python3 -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=500).admin.command('ping')" 2>/dev/null; then
    mongod \
      --dbpath /home/runner/mongodb-data \
      --port 27017 \
      --bind_ip 127.0.0.1 \
      --fork \
      --logpath /tmp/mongod.log
    echo "  Local MongoDB started"
  else
    echo "  Local MongoDB already running"
  fi
  export MONGO_URL="mongodb://localhost:27017"

  # Wait for local MongoDB
  for i in {1..20}; do
    if python3 -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=500).admin.command('ping')" 2>/dev/null; then
      echo "  Local MongoDB ready"
      break
    fi
    echo "  Waiting for MongoDB ($i/20)..."
    sleep 1
  done
fi

# ── 2. Verify database connectivity ───────────────────────────────────────────
echo "[2/5] Verifying database connection..."
python3 -c "
import os, sys
from pymongo import MongoClient
url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
try:
    MongoClient(url, serverSelectionTimeoutMS=8000).admin.command('ping')
    print('  Database connection OK')
except Exception as e:
    print(f'  ERROR: Cannot connect to database: {e}', file=sys.stderr)
    sys.exit(1)
"

# ── 3. Seed config collections (only if empty — never overwrites existing data) ─
echo "[3/5] Seeding config collections..."
python3 /home/runner/workspace/scripts/seed_mongodb.py

# ── 4. Verify frontend build exists (built during deploy build phase) ──────────
if [ ! -f "/home/runner/workspace/frontend/build/index.html" ]; then
  echo "[4/5] WARNING: No frontend build found — building now (may delay startup)..."
  cd /home/runner/workspace/frontend
  yarn install --frozen-lockfile --non-interactive 2>&1 | tail -5
  yarn build 2>&1 | tail -10
  cd /home/runner/workspace
else
  echo "[4/5] Frontend build ready"
fi

# ── 5. Start backend (serves API + static frontend on 0.0.0.0:5000) ───────────
echo "[5/5] Starting backend on 0.0.0.0:5000 (MONGO_URL=${MONGO_URL%%@*}@...)"
cd /home/runner/workspace/backend
exec python -m uvicorn server:app \
  --host 0.0.0.0 \
  --port 5000 \
  --workers 1
