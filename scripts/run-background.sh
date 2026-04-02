#!/usr/bin/env bash
# RouterCut 백그라운드. CIFS 마운트에 root 필요 시 ROUTERCUT_MOUNT_USE_SUDO=1 + sudoers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROUTERCUT_MOUNT_USE_SUDO="${ROUTERCUT_MOUNT_USE_SUDO:-1}"
export ROUTERCUT_DEBUG="${ROUTERCUT_DEBUG:-}"
export PORT="${PORT:-15777}"
LOG="${ROUTERCUT_LOG:-${TMPDIR:-/tmp}/routercut.log}"
PIDFILE="${ROUTERCUT_PIDFILE:-${TMPDIR:-/tmp}/routercut.pid}"
export PYTHONUNBUFFERED=1
nohup python3 "$ROOT/app.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
printf 'routercut pid=%s log=%s port=%s\n' "$(cat "$PIDFILE")" "$LOG" "${PORT:-15777}"
