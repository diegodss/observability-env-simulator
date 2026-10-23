#!/usr/bin/env bash
# Data update: PATCH one Technology One (SM) record, wait for the next sync, show it in the EDW.
# Usage: scripts/update-record.sh <students|staff|courses> <id> '<json patch>'
# Example: scripts/update-record.sh students STU-0001 '{"last_name":"Renamed"}'
. "$(dirname "$0")/_common.sh"
if [ $# -ne 3 ]; then sed -n '2,4p' "$0"; exit 2; fi
COLLECTION="$1"; ID="$2"; PATCH="$3"
echo "Technology One (SM) after update:"
curl -fsS -X PATCH "$SOURCE_URL/api/$COLLECTION/$ID" \
  -H "Authorization: Bearer ${API_TOKEN-dev-poc-token}" -H 'Content-Type: application/json' -d "$PATCH"
echo; WAIT=$(( INTERVAL + 5 ))
echo "Waiting ${WAIT}s for the $COLLECTION sync..."; sleep "$WAIT"
echo "EDW:"; curl -fsS "$TARGET_URL/api/$COLLECTION/$ID"; echo
