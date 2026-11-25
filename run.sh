#!/usr/bin/env bash
# STATION DESK — one command to open the desk
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/backend:$PYTHONPATH"
PORT="${PORT:-8787}"
echo "◈ STATION DESK  →  http://localhost:$PORT"
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --app-dir backend "$@"
