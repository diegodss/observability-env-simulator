#!/usr/bin/env bash
# Recovery: start the EDW again; workers recover on their own schedule.
. "$(dirname "$0")/_common.sh"
docker compose start edw
wait_ready "$TARGET_URL" edw
WAIT=$(( INTERVAL + 5 ))
echo "Waiting ${WAIT}s (one sync interval) for automatic recovery..."
sleep "$WAIT"
python3 scripts/status.py
