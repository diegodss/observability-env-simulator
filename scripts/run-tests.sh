#!/usr/bin/env bash
# Run unit tests, then the Docker integration suite against the running stack.
# The integration tests stop/start edw, technology-one-sm and one worker, and always restore them.
. "$(dirname "$0")/_common.sh"
if [ ! -x .venv/bin/pytest ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r tests/requirements.txt
fi
.venv/bin/pytest "$@"
