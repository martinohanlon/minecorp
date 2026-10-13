#!/usr/bin/env bash
# Try the MineCorp agent from a terminal, without Minecraft (a stand-in for the plugin).
#
#   ./scripts/chat-with-agent.sh                         # a chat; the agent service must be running (./scripts/start-agent.sh)
#   ./scripts/chat-with-agent.sh how do I make a bed?    # ask once
#   ./scripts/chat-with-agent.sh --trace --direct        # run the agent in this process, and show what it did
#   ./scripts/chat-with-agent.sh --help                  # all options (--name, --explain, --url ...)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
find_python
export PYTHONIOENCODING=utf-8
exec "$PY" "$ROOT/scripts/chat.py" "$@"
