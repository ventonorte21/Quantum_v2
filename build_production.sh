#!/bin/bash
set -e

echo "=== Quantum Trading [Build] ==="

echo "[1/3] Installing frontend dependencies..."
cd /home/runner/workspace/frontend
yarn install --frozen-lockfile --non-interactive

echo "[2/3] Building frontend..."
yarn build

echo "[3/3] Pre-compiling Python bytecode (speeds up cold start)..."
cd /home/runner/workspace
python3 -m compileall -q backend/ 2>/dev/null || true

echo "=== Build complete ==="
