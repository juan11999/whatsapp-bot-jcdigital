#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-10000}"
exec uvicorn server:app --host 0.0.0.0 --port "$PORT"
