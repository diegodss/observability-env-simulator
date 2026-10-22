#!/usr/bin/env bash
# Baseline: build and start everything, then wait for the first sync.
. "$(dirname "$0")/_common.sh"
docker compose up --build -d
wait_ready "$SIS_URL" sis
wait_ready "$LMS_URL" lms
echo "Waiting 10s for the first sync runs..."; sleep 10
python3 scripts/status.py
