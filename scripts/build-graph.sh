#!/usr/bin/env bash
# Build the whole MineCorp graph from source (the jar, model/ and the wiki page list): recipes, actions, world, trades,
# model, routes, then the wiki and its links. WIPES the database named in .env first.
#
#   ./scripts/build-graph.sh                # asks, then rebuilds everything
#   ./scripts/build-graph.sh --list         # show the steps and change nothing
#   ./scripts/build-graph.sh --no-wiki      # the game graph only (no OpenAI, no network)
#   ./scripts/build-graph.sh --from model   # carry on from a step after a failure
#   ./scripts/build-graph.sh --yes          # do not ask
#
# All arguments go to scripts/build_graph.py, which explains them (--help).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
find_python
exec "$PY" "$ROOT/scripts/build_graph.py" "$@"
