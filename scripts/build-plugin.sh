#!/usr/bin/env bash
# Build the MineCorp plugin with Maven, copy the jar into server/plugins, and create the plugin config (with the shared
# secret from .env) if it does not exist yet. Restart the server to load a new jar. Avoid /reload.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
need mvn "Maven (mvn) not found on PATH."
need javac "A JDK 25 (javac) is required on PATH. Run scripts/install-java.sh."

(cd "$ROOT/plugin" && mvn -B -q package)

plugins="$ROOT/server/plugins"
mkdir -p "$plugins"
for jar in "$ROOT"/plugin/target/MineCorp-*.jar; do
    cp -f "$jar" "$plugins/"
    echo "Copied $(basename "$jar") to $plugins"
done

# Plugin config: written once from .env so the secret matches the agent service.
config="$plugins/MineCorp/config.yml"
if [ ! -f "$config" ]; then
    secret=""; port="8080"
    if [ -f "$ROOT/.env" ]; then
        value() { grep -E "^[[:space:]]*$1[[:space:]]*=" "$ROOT/.env" | tail -n1 | cut -d= -f2- | tr -d '\r' | sed -E "s/^[[:space:]]*//; s/[[:space:]]*$//; s/^(['\"])(.*)\1\$/\2/"; }
        secret="$(value AGENT_SHARED_SECRET || true)"
        p="$(value AGENT_PORT || true)"; [ -n "$p" ] && port="$p"
    fi
    [ -n "$secret" ] || echo "WARNING: AGENT_SHARED_SECRET not found in .env; set shared-secret in $config yourself." >&2
    mkdir -p "$plugins/MineCorp"
    printf 'agent-url: "http://127.0.0.1:%s"\nshared-secret: "%s"\ntimeout-seconds: 30\n' "$port" "$secret" > "$config"
    echo "Created $config"
else
    echo "Kept existing $config (delete it to regenerate from .env)"
fi
