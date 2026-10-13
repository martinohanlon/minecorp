#!/usr/bin/env bash
# Start the MineCorp agent service (uses the project's .venv; settings come from .env).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
find_python

"$PY" -m pip install -q -r "$ROOT/agent/requirements.txt"
cd "$ROOT/agent"
exec "$PY" -m minecorp_agent
