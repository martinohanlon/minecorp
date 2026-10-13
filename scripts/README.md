# Scripts

Every script has a PowerShell version (`.ps1`, Windows) and a bash version (`.sh`, Linux, macOS and Git Bash), named for what it does. Run them from the project root. The `.sh` versions share `_common.sh` and need `chmod +x` after a fresh clone on Linux (the repository keeps the executable bit).

| Script | What it does |
|---|---|
| `build-graph` | Builds the **whole** graph from source: recipes (wipes the database), actions, world, trades, model, routes, then the wiki pages and their links. Asks before wiping. `--list` shows the steps, `--no-wiki` builds only the game graph (no OpenAI, no network), `--from <step>` carries on after a failure, `--yes` does not ask. It loads `.env` for the loaders (they do not read it themselves). The logic is in [build_graph.py](build_graph.py), so it is the same on every system. |
| `start-server` | Downloads (if needed) and starts the Paper server in `server/`. First run needs `-AcceptEula` (`--accept-eula` in bash) after you have read the Minecraft EULA. `-Update` / `--update` looks for a newer stable version. The version is pinned in [paper-version.txt](paper-version.txt). |
| `build-plugin` | Builds the plugin with Maven, copies the jar into `server/plugins` and, once, writes the plugin config with the shared secret from `.env`. Restart the server to load it. |
| `start-agent` | Starts the agent service (HTTP) with the project's `.venv`; settings come from `.env`. |
| `chat-with-agent` | A terminal stand-in for the plugin, to try the agent without Minecraft: `-Direct` / `--direct` runs the agent in this process, `-Trace` / `--trace` shows what it did, `--explain` shows the notes book. The program is [chat.py](chat.py) (`--help`). |
| `run-tests` | The Python tests and the plugin's JUnit tests. `-Integration`, `-E2E`, `-All`, `-PythonOnly` (`--integration`, `--e2e`, `--all`, `--python-only` in bash). See [../tests/README.md](../tests/README.md). |
| `install-java` | Installs Temurin JDK 25 (winget on Windows; Homebrew or the Adoptium apt repository on macOS and Linux). |

A new machine, in order: `install-java`, `start-server` (with the EULA flag, then stop it), `build-graph`, `build-plugin`, `start-agent`, then start the server again. The Neo4j database itself is `docker compose up -d` (see [../loader/README.md](../loader/README.md)).

The bash versions were written on Windows and have been syntax-checked there (`bash -n`) and run for `build-graph --list`; the rest have not been run on Linux or macOS yet.
