#!/usr/bin/env bash
# Primary outage: stop only the LMS and wait two sync intervals.
. "$(dirname "$0")/_common.sh"
docker compose stop lms
WAIT=$(( INTERVAL * 2 + 5 ))
echo "LMS stopped. Waiting ${WAIT}s (two sync intervals) for all three jobs to fail..."
sleep "$WAIT"
python3 scripts/status.py
echo
echo "Latest failed-run log per worker:"
for s in sync-students sync-staff sync-courses; do
  docker compose logs --no-log-prefix "$s" | grep '"event": "run_completed"' | grep '"outcome": "failure"' | tail -1 || true
done
