# Sourced by the other .sh scripts (not run on its own). Sets ROOT (the project folder) and, with find_python, PY.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

find_python() {
    if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"   # Windows (Git Bash)
    elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"                  # Linux, macOS
    else echo "No .venv found. Run: python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2; exit 1
    fi
}

need() {   # need <command> <message>: stop with the message when the command is missing
    command -v "$1" >/dev/null 2>&1 || { echo "$2" >&2; exit 1; }
}
