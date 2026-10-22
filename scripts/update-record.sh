#!/usr/bin/env bash
# Data update: PATCH one SIS record, wait for the next sync, show it in the LMS.
# Usage: scripts/update-record.sh <students|staff|courses> <id> '<json patch>'
# Example: scripts/update-record.sh students STU-0001 '{"last_name":"Renamed"}'
. "$(dirname "$0")/_common.sh"
if [ $# -ne 3 ]; then sed -n '2,4p' "$0"; exit 2; fi
COLLECTION="$1"; ID="$2"; PATCH="$3"
echo "SIS after update:"
curl -fsS -X PATCH "$SIS_URL/api/$COLLECTION/$ID" \
  -H "Authorization: Bearer ${API_TOKEN-dev-poc-token}" -H 'Content-Type: application/json' -d "$PATCH"
echo; WAIT=$(( INTERVAL + 5 ))
echo "Waiting ${WAIT}s for the $COLLECTION sync..."; sleep "$WAIT"
echo "LMS:"; curl -fsS "$LMS_URL/api/$COLLECTION/$ID"; echo
