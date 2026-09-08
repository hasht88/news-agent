#!/usr/bin/env bash
set -e

PORT=${1:-8000}
echo "Starting News Agent Settings Server on http://127.0.0.1:${PORT}..."
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --reload
