#!/bin/bash
# Atlas detection — same logic as start_production.sh
if [ -n "$MONGO_URL_ATLAS" ]; then
  echo "[DB] Using Atlas (MONGO_URL_ATLAS)"
  export MONGO_URL="$MONGO_URL_ATLAS"
else
  echo "[DB] MONGO_URL_ATLAS not set — using local MongoDB"
  export MONGO_URL="mongodb://localhost:27017"
  # Wait for local MongoDB to be available
  for i in {1..15}; do
    if python3 -c "from pymongo import MongoClient; MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=500).admin.command('ping')" 2>/dev/null; then
      echo "MongoDB ready"
      break
    fi
    echo "Waiting for MongoDB ($i/15)..."
    sleep 1
  done
fi

# Start FastAPI backend
cd /home/runner/workspace/backend
exec python -m uvicorn server:app --host localhost --port 8000
