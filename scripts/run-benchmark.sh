#!/usr/bin/env bash
# Run the benchmark: does a knowledge layer let a small model match a large one? (docs/benchmark.md)
# Checks the gold answers against the graph, asks before spending money, runs every question on four arms, then prints the report.
#
#   ./scripts/run-benchmark.sh                          # the full run: 720 runs, about 6 dollars; asks first
#   ./scripts/run-benchmark.sh --yes                    # do not ask
#   ./scripts/run-benchmark.sh --repeats 1 --yes        # a third of the cost: one repeat
#   ./scripts/run-benchmark.sh --repeats 1 s1 s2        # only questions whose id contains s1 or s2
#   ./scripts/run-benchmark.sh --arms bare --models gpt-6-luna --repeats 1
#   ./scripts/run-benchmark.sh --verify-only            # free: check the gold answers against the graph
#   ./scripts/run-benchmark.sh --report-only            # free: report on the newest run (--run ID for another)
#   ./scripts/run-benchmark.sh --help                   # all options (--effort, --seed, --no-shuffle, --max-cost ...)
#
# Needs the graph (the Neo4j in .env) and OPENAI_API_KEY. Results go to agent/bench/results/. The cost cap is --max-cost (default 10 dollars).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
find_python
export PYTHONIOENCODING=utf-8
exec "$PY" "$ROOT/scripts/run_benchmark.py" "$@"
