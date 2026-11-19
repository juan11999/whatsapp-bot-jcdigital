#!/usr/bin/env bash
# Start (or restart) the webhook server and a private HTTPS tunnel.
# Default tunnel: serveo.net (SSH reverse tunnel via the box egress proxy).
# smee.io is opt-in only (USE_SMEE=1) and must not be used for real WhatsApp
# webhooks: channels are publicly viewable and replay payloads.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
VENV="$ROOT/.venv"
BIN="$ROOT/bin"
LOG_DIR="$ROOT/logs"
DATA_DIR="$ROOT/data"
mkdir -p "$LOG_DIR" "$DATA_DIR" "$BIN"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -U pip
  "$VENV/bin/pip" install -r "$ROOT/requirements.txt"
fi

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

stop_pid() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile" || true)"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      # If this is the tunnel loop, also kill its child ssh
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
      sleep 0.4
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

stop_pid "$LOG_DIR/server.pid"
stop_pid "$LOG_DIR/tunnel.pid"
stop_pid "$LOG_DIR/tunnel-loop.pid"
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
pkill -f "$ROOT/.venv/bin/uvicorn" 2>/dev/null || true
pkill -f "$BIN/cloudflared tunnel --url" 2>/dev/null || true
pkill -f "$BIN/tunnel-loop.sh" 2>/dev/null || true
pkill -f "$ROOT/node_modules/.bin/smee" 2>/dev/null || true
pkill -f "ProxyCommand=$BIN/proxy-connect.py" 2>/dev/null || true
pkill -f "UserKnownHostsFile=$DATA_DIR/tunnel_known_hosts" 2>/dev/null || true
sleep 0.3

export PYTHONUNBUFFERED=1
nohup "$VENV/bin/uvicorn" server:app --host "$HOST" --port "$PORT" --app-dir "$ROOT" \
  >>"$LOG_DIR/server.log" 2>&1 &
echo $! > "$LOG_DIR/server.pid"

ok=0
for i in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
    ok=1
    break
  fi
  sleep 0.25
done
if [ "$ok" != 1 ]; then
  echo "Server failed to start. Last log lines:" >&2
  tail -n 40 "$LOG_DIR/server.log" >&2
  exit 1
fi

if [ "${USE_SMEE:-}" = "1" ]; then
  echo "WARNING: USE_SMEE=1 is for local experiments only. Do not point Gupshup at smee.io" >&2
  echo "— payloads (including WhatsApp history) are publicly viewable and replayed." >&2
  if [ ! -x "$ROOT/node_modules/.bin/smee" ]; then
    npm install --silent smee-client@3
  fi
  SMEE_URL="${SMEE_URL:-}"
  if [ -z "$SMEE_URL" ]; then
    SMEE_URL="$(curl -sS --max-time 20 -o /dev/null -w '%{redirect_url}' https://smee.io/new)"
  fi
  nohup "$ROOT/node_modules/.bin/smee" --url "$SMEE_URL" --target "http://127.0.0.1:${PORT}/webhook" \
    >>"$LOG_DIR/tunnel.log" 2>&1 &
  echo $! > "$LOG_DIR/tunnel.pid"
  echo "$SMEE_URL" > "$ROOT/PUBLIC_URL.txt"
  echo "Webhook (SMEE, not for production): $SMEE_URL"
  exit 0
fi

# Default: private Serveo tunnel with auto-restart
nohup "$BIN/tunnel-loop.sh" >>"$LOG_DIR/tunnel-loop.out" 2>&1 &
echo $! > "$LOG_DIR/tunnel-loop.pid"

url=""
for i in $(seq 1 50); do
  if [ -s "$ROOT/PUBLIC_URL.txt" ]; then
    url="$(tr -d '[:space:]' < "$ROOT/PUBLIC_URL.txt")"
    if [ -n "$url" ]; then
      break
    fi
  fi
  sleep 0.4
done
if [ -z "$url" ]; then
  echo "Tunnel loop started but no URL yet. See $LOG_DIR/tunnel.log and $LOG_DIR/tunnel.ssh.log" >&2
  tail -n 40 "$LOG_DIR/tunnel.log" >&2 || true
  exit 1
fi

echo "Server:  http://127.0.0.1:${PORT}  (pid $(cat "$LOG_DIR/server.pid"))"
echo "Webhook: $url"
echo "Provider: serveo.net (SSH reverse tunnel). URL changes if the SSH session restarts."
echo "Current URL also in $ROOT/PUBLIC_URL.txt"
