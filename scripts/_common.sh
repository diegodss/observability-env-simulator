# Sourced by the scenario scripts.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
INTERVAL="${SYNC_INTERVAL_SECONDS:-30}"
TARGET_URL="http://127.0.0.1:${TARGET_HOST_PORT:-8002}"
SOURCE_URL="http://127.0.0.1:${SOURCE_HOST_PORT:-8001}"

wait_ready() {  # wait_ready <url> <label>
  for _ in $(seq 1 90); do
    if curl -fsS "$1/health/ready" >/dev/null 2>&1; then echo "$2 is ready"; return 0; fi
    sleep 1
  done
  echo "$2 did not become ready in 90s" >&2; return 1
}
