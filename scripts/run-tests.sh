#!/usr/bin/env bash
# Run the MineCorp tests: the Python tests (pytest) and the plugin's JUnit tests (Maven).
#
#   ./scripts/run-tests.sh                  # fast: no database, no server
#   ./scripts/run-tests.sh --integration    # also the tests that build the graph in a throwaway database (Neo4j Enterprise, the jar)
#   ./scripts/run-tests.sh --e2e            # also the end-to-end plugin test (a real Paper server and a stub agent)
#   ./scripts/run-tests.sh --all            # everything
#   ./scripts/run-tests.sh --python-only    # skip the plugin's Maven tests
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
find_python

integration=0; e2e=0; python_only=0
for arg in "$@"; do
    case "$arg" in
        --integration) integration=1 ;;
        --e2e) e2e=1 ;;
        --all) integration=1; e2e=1 ;;
        --python-only) python_only=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ $integration = 1 ] && [ $e2e = 1 ]; then markers=""
elif [ $integration = 1 ]; then markers="not e2e"
elif [ $e2e = 1 ]; then markers="not integration"
else markers="not integration and not e2e"
fi

cd "$ROOT"
failed=0
echo; echo "== Python tests (pytest, markers: '$markers') =="
"$PY" -m pip install -q -r requirements-dev.txt >/dev/null 2>&1
"$PY" -m pytest -m "$markers"; python_status=$?
[ $python_status -eq 0 ] || failed=1

java_status="skipped"
if [ $python_only = 0 ]; then
    echo; echo "== Plugin tests (JUnit via Maven) =="
    need mvn "Maven (mvn) not found on PATH."
    need javac "A JDK 25 (javac) is required on PATH. Run scripts/install-java.sh."
    (cd "$ROOT/plugin" && mvn -B test); java_status=$?
    [ "$java_status" = 0 ] || failed=1
fi

echo; echo "== Summary =="
printf '%-20s %s\n' "Python" "$([ $python_status -eq 0 ] && echo PASS || echo FAIL)"
[ $python_only = 1 ] || printf '%-20s %s\n' "Java (plugin)" "$([ "$java_status" = 0 ] && echo PASS || echo FAIL)"
exit $failed
