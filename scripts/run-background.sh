#!/usr/bin/env bash
# RouterCut를 백그라운드(nohup)로 띄웁니다. 마운트에 root가 필요하면 sudo NOPASSWD를 설정한 뒤
# ROUTERCUT_MOUNT_USE_SUDO=1 을 켜세요(아래 기본값).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROUTERCUT_MOUNT_USE_SUDO="${ROUTERCUT_MOUNT_USE_SUDO:-1}"
export ROUTERCUT_DEBUG="${ROUTERCUT_DEBUG:-}"
LOG="${ROUTERCUT_LOG:-${TMPDIR:-/tmp}/routercut.log}"
PIDFILE="${ROUTERCUT_PIDFILE:-${TMPDIR:-/tmp}/routercut.pid}"
export PYTHONUNBUFFERED=1
nohup python3 "$ROOT/app.py" >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
printf 'routercut pid=%s log=%s (ROUTERCUT_MOUNT_USE_SUDO=%s)\n' "$(cat "$PIDFILE")" "$LOG" "$ROUTERCUT_MOUNT_USE_SUDO"
