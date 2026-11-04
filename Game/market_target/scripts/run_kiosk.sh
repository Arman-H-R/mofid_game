#!/usr/bin/env sh
# Market Pulse kiosk launcher (Linux). Restarts the game if it exits with an error;
# Esc pressed twice (exit code 0) stops it for real.
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
mkdir -p logs
while true; do
    "$PY" main.py "$@"
    code=$?
    [ "$code" -eq 0 ] && break
    echo "$(date '+%F %T') Market Pulse exited with code $code; restarting in 3 seconds" >> logs/kiosk_restarts.log
    sleep 3
done
