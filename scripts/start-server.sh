#!/usr/bin/env bash
# Download (if needed) and start a Paper server for MineCorp. Runs from ../server (git-ignored).
#
#   ./scripts/start-server.sh --accept-eula             # first run: writes eula=true (read https://aka.ms/MinecraftEULA first)
#   ./scripts/start-server.sh                           # the pinned version (scripts/paper-version.txt)
#   ./scripts/start-server.sh --update                  # look for the newest STABLE version and pin it
#   ./scripts/start-server.sh --version 26.2 --memory 6G
#
# Needs java and curl. The JSON from Paper's download API is read with the project's Python (or python3).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

version="latest"; memory="4G"; update=0; accept=0
while [ $# -gt 0 ]; do
    case "$1" in
        --version) version="$2"; shift ;;
        --memory) memory="$2"; shift ;;
        --update) update=1 ;;
        --accept-eula) accept=1 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

api="https://fill.papermc.io/v3/projects/paper"
need java "Java not found on PATH. Run scripts/install-java.sh (Paper needs a recent JDK)."
need curl "curl not found on PATH."
if   [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
else need python3 "Python not found."; PY=python3; fi

server="$ROOT/server"; mkdir -p "$server"; cd "$server"
pin="$ROOT/scripts/paper-version.txt"

# json <expression on d>: print a value from the JSON on stdin
json() { "$PY" -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

if [ "$version" = "latest" ] && [ -f "$pin" ] && [ $update = 0 ]; then version="$(tr -d '\r\n ' < "$pin")"; fi

if [ "$version" = "latest" ]; then
    versions="$(curl -fsSL "$api" | json "' '.join(v for g in d['versions'].values() for v in g)")"
    for v in $versions; do
        case "$v" in *-*) continue ;; esac                 # skip pre-releases and release candidates
        build="$(curl -fsSL "$api/versions/$v/builds/latest")"
        if [ "$(echo "$build" | json "d['channel']")" = "STABLE" ]; then version="$v"; break; fi
    done
    [ "$version" != "latest" ] || { echo "No STABLE Paper build found." >&2; exit 1; }
else
    build="$(curl -fsSL "$api/versions/$version/builds/latest")"
fi
echo "$version" > "$pin"

name="$(echo "$build" | json "d['downloads']['server:default']['name']")"
url="$(echo "$build" | json "d['downloads']['server:default']['url']")"
sha="$(echo "$build" | json "d['downloads']['server:default']['checksums']['sha256']")"
id="$(echo "$build" | json "d['id']")"
if [ ! -f "$server/$name" ]; then
    echo "Downloading $name ..."
    curl -fL -o "$server/$name" "$url"
    if command -v sha256sum >/dev/null 2>&1; then got="$(sha256sum "$server/$name" | cut -d' ' -f1)"; else got="$(shasum -a 256 "$server/$name" | cut -d' ' -f1)"; fi
    if [ "$got" != "$sha" ]; then rm -f "$server/$name"; echo "Checksum mismatch for $name." >&2; exit 1; fi
fi

min_java="$(curl -fsSL "$api/versions/$version" | json "d['version']['java']['version']['minimum']")"
have_java="$(java -version 2>&1 | head -n1 | sed -E 's/.*"([0-9]+).*/\1/')"
if [ -n "$have_java" ] && [ "$have_java" -lt "$min_java" ] 2>/dev/null; then
    echo "Paper $version needs Java $min_java+, found $have_java." >&2; exit 1
fi

if [ $accept = 1 ]; then echo "eula=true" > "$server/eula.txt"
elif ! grep -q "eula=true" "$server/eula.txt" 2>/dev/null; then
    echo "Minecraft EULA not accepted. Read https://aka.ms/MinecraftEULA then rerun with --accept-eula." >&2; exit 1
fi

echo "Starting Paper $version (build $id) with $memory ..."
exec java "-Xms$memory" "-Xmx$memory" -jar "$server/$name" --nogui
