#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
LOG_DIR="$(pwd)/logs"
DATA_DIR="$(pwd)/data"
BIN="$(pwd)/bin"
stop_pid() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile" || true)"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
      sleep 0.3
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}
stop_pid "$LOG_DIR/server.pid"
stop_pid "$LOG_DIR/tunnel.pid"
stop_pid "$LOG_DIR/tunnel-loop.pid"
pkill -f "$(pwd)/.venv/bin/uvicorn" 2>/dev/null || true
pkill -f "$(pwd)/bin/cloudflared tunnel --url" 2>/dev/null || true
pkill -f "$(pwd)/bin/tunnel-loop.sh" 2>/dev/null || true
pkill -f "$(pwd)/node_modules/.bin/smee" 2>/dev/null || true
pkill -f "UserKnownHostsFile=$DATA_DIR/tunnel_known_hosts" 2>/dev/null || true
echo "Stopped."
