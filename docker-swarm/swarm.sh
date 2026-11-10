#!/usr/bin/env bash
# Starts and stops the two Swarm stacks: simulator and observability (platform).
#
#   ./swarm.sh          interactive menu (1 = start, 2 = stop)
#   ./swarm.sh start    or ./swarm.sh 1
#   ./swarm.sh stop     or ./swarm.sh 2
#
# Meant to live in observability-env-simulator/docker-swarm/.
# If the platform repo is somewhere else:  PLATFORM_DIR=/path ./swarm.sh
#
# Order (it matters, so the platform does not raise alerts or create tickets for nothing):
#   start: simulator -> wait until ready -> observability
#   stop:  observability -> simulator
#
# Does not touch Traefik, Jellyfin, networks or volumes (data is kept).

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIMULATOR_DIR="${SIMULATOR_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PLATFORM_DIR="${PLATFORM_DIR:-$SIMULATOR_DIR/../observability-platform}"

SIMULATOR_STACK="simulator"
PLATFORM_STACK="observability"
SIMULATOR_FILE="$SIMULATOR_DIR/docker-swarm/simulator-stack.yml"
PLATFORM_FILE="$PLATFORM_DIR/docker-swarm/observability-stack.yml"

READY_TIMEOUT="${READY_TIMEOUT:-180}"    # seconds to wait for a stack to become ready
SETTLE_SECONDS="${SETTLE_SECONDS:-35}"   # grace period after the simulator is ready (workers' first sync)

if [[ -t 1 ]]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BOLD=""; RESET=""
fi

info() { echo "${BOLD}==>${RESET} $*"; }
ok()   { echo "${GREEN}✔${RESET} $*"; }
warn() { echo "${YELLOW}!${RESET} $*"; }
die()  { echo "${RED}✘ $*${RESET}" >&2; exit 1; }

ask_yes_no() {  # ask_yes_no "question" [default: y|n]
  local prompt="$1" default="${2:-y}" hint reply
  [[ "$default" == "y" ]] && hint="[Y/n]" || hint="[y/N]"
  read -r -p "$prompt $hint " reply || true
  reply="${reply:-$default}"
  [[ "$reply" =~ ^[yY]$ ]]
}

load_env_file() {
  local f="$1"
  if [[ -f "$f" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$f"
    set +a
    ok "Variables loaded from $f"
  fi
}

check_swarm() {
  command -v docker >/dev/null || die "docker not found."
  local state
  state="$(docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || true)"
  [[ "$state" == "active" ]] || die "Swarm is not active. Run: docker swarm init"
  [[ "$(docker info --format '{{.Swarm.ControlAvailable}}')" == "true" ]] \
    || die "This node is not a Swarm manager."
}

ensure_network() {
  local name="$1"
  if docker network inspect "$name" >/dev/null 2>&1; then
    local driver
    driver="$(docker network inspect "$name" --format '{{.Driver}}')"
    [[ "$driver" == "overlay" ]] \
      || die "Network '$name' exists but is '$driver', not overlay. Remove it: docker network rm $name"
  else
    docker network create -d overlay --attachable "$name" >/dev/null
    ok "Overlay network '$name' created"
  fi
}

check_node_label() {
  if [[ -n "$(docker node ls --filter "node.label=obs=true" -q 2>/dev/null)" ]]; then
    return 0
  fi

  warn "No node with label obs=true. Services will stay in 'pending'."

  local nodes=() node chosen=""
  while IFS= read -r node; do
    nodes+=("$node")
  done < <(docker node ls --format '{{.Hostname}}')

  if (( ${#nodes[@]} == 0 )); then
    die "No nodes found."
  elif (( ${#nodes[@]} == 1 )); then
    # Only one node: offer to label it right away
    if ask_yes_no "Add label obs=true to node '${nodes[0]}'?" y; then
      chosen="${nodes[0]}"
    fi
  else
    # Several nodes: pick one by number
    echo "Available nodes:"
    PS3="Choose a node number: "
    select chosen in "${nodes[@]}" "Skip"; do
      [[ -n "$chosen" ]] && break
    done
    [[ "$chosen" == "Skip" ]] && chosen=""
  fi

  if [[ -n "$chosen" ]]; then
    docker node update --label-add obs=true "$chosen" >/dev/null
    ok "Node '$chosen' labeled with obs=true"
  else
    warn "Label it later with: docker node update --label-add obs=true <NODE_NAME>"
    ask_yes_no "Continue anyway?" n || exit 1
  fi
}

stack_ready() {  # every service in the stack has current replicas == desired (and > 0)
  local out line cur des
  out="$(docker stack services "$1" --format '{{.Replicas}}' 2>/dev/null)" || return 1
  [[ -n "$out" ]] || return 1
  while read -r line; do
    cur="${line%%/*}"
    des="${line##*/}"; des="${des%% *}"
    [[ "$cur" == "$des" && "$des" != "0" ]] || return 1
  done <<< "$out"
}

wait_ready() {
  local stack="$1" waited=0
  info "Waiting for '$stack' to become ready (up to ${READY_TIMEOUT}s)..."
  until stack_ready "$stack"; do
    if (( waited >= READY_TIMEOUT )); then
      echo
      docker stack services "$stack" || true
      die "Timed out waiting for '$stack'. Check: docker stack ps $stack --no-trunc"
    fi
    printf '.'
    sleep 3
    waited=$((waited + 3))
  done
  (( waited > 0 )) && echo
  ok "'$stack' is ready"
}

wait_removed() {
  local stack="$1" waited=0
  while [[ -n "$(docker stack services "$stack" -q 2>/dev/null)" ]]; do
    (( waited >= 60 )) && { warn "'$stack' is still being removed."; return 0; }
    sleep 2
    waited=$((waited + 2))
  done
  ok "'$stack' removed"
}

build_simulator_images() {
  info "Building simulator images..."
  ( cd "$SIMULATOR_DIR"
    docker build -t observability-env-simulator/technology-one-sm:local ./app-technology-one-sm
    docker build -t observability-env-simulator/edw:local ./app-edw
    docker build -t observability-env-simulator/appevents-worker:local ./integrations
  )
  ok "Images built"
}

do_start() {
  check_swarm
  [[ -f "$SIMULATOR_FILE" ]] || die "File not found: $SIMULATOR_FILE"
  [[ -f "$PLATFORM_FILE" ]]  || die "File not found: $PLATFORM_FILE (use PLATFORM_DIR=...)"

  load_env_file "$SIMULATOR_DIR/.env"
  load_env_file "$PLATFORM_DIR/.env"

  if [[ -z "${JIRA_API_TOKEN:-}" ]]; then
    read -r -s -p "JIRA_API_TOKEN (required for Keep to create tickets): " JIRA_API_TOKEN
    echo
    [[ -n "$JIRA_API_TOKEN" ]] || die "JIRA_API_TOKEN is empty."
    export JIRA_API_TOKEN
  fi

  ensure_network observability-sim
  ensure_network traefik-public
  check_node_label

  if ask_yes_no "Rebuild the simulator images?" y; then
    build_simulator_images
  fi

  # 1) Simulator first
  info "Deploying the simulator ($SIMULATOR_STACK)..."
  ( cd "$SIMULATOR_DIR/docker-swarm" && docker stack deploy -c simulator-stack.yml "$SIMULATOR_STACK" )
  wait_ready "$SIMULATOR_STACK"

  info "Waiting ${SETTLE_SECONDS}s for the workers to finish their first sync..."
  sleep "$SETTLE_SECONDS"

  # 2) Platform afterwards
  info "Deploying the platform ($PLATFORM_STACK)..."
  ( cd "$PLATFORM_DIR/docker-swarm" && docker stack deploy -c observability-stack.yml "$PLATFORM_STACK" )
  wait_ready "$PLATFORM_STACK"

  echo
  ok "Everything is up."
  docker stack services "$SIMULATOR_STACK"
  docker stack services "$PLATFORM_STACK"
  echo
  echo "Keep:         http://keep.swarm.test"
  echo "Grafana:      http://grafana.swarm.test"
  echo "Prometheus:   http://prometheus.swarm.test"
  echo "Alertmanager: http://alertmanager.swarm.test"
}

do_stop() {
  check_swarm
  warn "This takes down the '$PLATFORM_STACK' and '$SIMULATOR_STACK' stacks."
  warn "Volumes (databases, Grafana, Prometheus) are kept. Traefik and Jellyfin are not affected."
  ask_yes_no "Confirm?" n || { echo "Cancelled."; exit 0; }

  # Platform first, so it does not raise alerts while the simulator goes down
  info "Removing $PLATFORM_STACK..."
  docker stack rm "$PLATFORM_STACK" || warn "Stack '$PLATFORM_STACK' not found."
  wait_removed "$PLATFORM_STACK"

  info "Removing $SIMULATOR_STACK..."
  docker stack rm "$SIMULATOR_STACK" || warn "Stack '$SIMULATOR_STACK' not found."
  wait_removed "$SIMULATOR_STACK"

  echo
  ok "Stacks taken down."
}

choice="${1:-}"
if [[ -z "$choice" ]]; then
  echo "${BOLD}Swarm stacks: simulator + observability${RESET}"
  echo "  1) Start"
  echo "  2) Stop"
  echo "  0) Exit"
  read -r -p "Option: " choice
fi

case "$choice" in
  1|start) do_start ;;
  2|stop)  do_stop ;;
  0|"")    echo "Bye." ;;
  *)       die "Invalid option: $choice (use 1/start, 2/stop or 0)" ;;
esac