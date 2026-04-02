#!/usr/bin/env bash
# RouterCut를 백그라운드(nohup)로 띄웁니다. SMB는 마운트 없이 445로 접속합니다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROUTERCUT_DEBUG="${ROUTERCUT_DEBUG:-}"
export PORT="${PORT:-15777}"
LOG="${ROUTERCUT_LOG:-${TMPDIR:-/tmp}/routercut.log}"
PIDFILE="${ROUTERCUT_PIDFILE:-${TMPDIR:-/tmp}/routercut.pid}"
export PYTHONUNBUFFERED=1
nohup python3 "$ROOT/app.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
printf 'routercut pid=%s log=%s port=%s\n' "$(cat "$PIDFILE")" "$LOG" "${PORT:-15777}"
