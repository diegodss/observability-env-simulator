#!/usr/bin/env bash
# Reset: DESTROYS all POC data (both databases) and starts fresh with the seed.
# Usage: scripts/reset.sh --yes
. "$(dirname "$0")/_common.sh"
if [ "${1:-}" != "--yes" ]; then
  echo "This deletes all Technology One (SM) and EDW data volumes. Re-run with --yes to confirm." >&2; exit 2
fi
docker compose down -v --remove-orphans
exec scripts/up.sh
