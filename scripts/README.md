# Scripts

Every script has a PowerShell version (`.ps1`, Windows) and a bash version (`.sh`, Linux, macOS and Git Bash), named for what it does. Run them from the project root. The `.sh` versions share `_common.sh` and need `chmod +x` after a fresh clone on Linux (the repository keeps the executable bit).

| Script | What it does |
|---|---|
| `build-graph` | Builds the **whole** graph from source: recipes (wipes the database), actions, world, enchantments, trades, model, routes, then the wiki pages and their links. Asks before wiping. `--list` shows the steps, `--no-wiki` builds only the game graph (no OpenAI, no network), `--from <step>` carries on after a failure, `--yes` does not ask. It loads `.env` for the loaders (they do not read it themselves). The logic is in [build_graph.py](build_graph.py), so it is the same on every system. |
| `start-server` | Downloads (if needed) and starts the Paper server in `server/`. First run needs `-AcceptEula` (`--accept-eula` in bash) after you have read the Minecraft EULA. `-Update` / `--update` looks for a newer stable version. The version is pinned in [paper-version.txt](paper-version.txt). |
| `build-plugin` | Builds the plugin with Maven, copies the jar into `server/plugins` and, once, writes the plugin config with the shared secret from `.env`. Restart the server to load it. |
| `start-agent` | Starts the agent service (HTTP) with the project's `.venv`; settings come from `.env`. |
| `chat-with-agent` | A terminal stand-in for the plugin, to try the agent without Minecraft: `-Direct` / `--direct` runs the agent in this process, `-Trace` / `--trace` shows what it did, `--explain` shows the notes book. The program is [chat.py](chat.py) (`--help`). |
| `run-benchmark` | Runs the benchmark of [../docs/benchmark.md](../docs/benchmark.md): checks every gold answer against the graph (free), asks before spending money, runs all questions on four arms (small and large model, with and without the knowledge layer), then prints the report. A full run is 720 runs and cost about 6 dollars on 2026-11-18; `-MaxCost` (`--max-cost`) is the cap, default 10. `-Yes` does not ask, `-Repeats 1` is a third of the cost, `-VerifyOnly` and `-ReportOnly` are free, and the rest of the words are question ids to run. Needs the graph and `OPENAI_API_KEY`. The logic is in [run_benchmark.py](run_benchmark.py). |
| `run-tests` | The Python tests and the plugin's JUnit tests. `-Integration`, `-E2E`, `-All`, `-PythonOnly` (`--integration`, `--e2e`, `--all`, `--python-only` in bash). See [../tests/README.md](../tests/README.md). |
| `install-java` | Installs Temurin JDK 25 (winget on Windows; Homebrew or the Adoptium apt repository on macOS and Linux). |
| `install-maven` | Installs Apache Maven, which `build-plugin` and the plugin tests need. On Windows it downloads Maven from Apache (winget has no official package), checks the SHA-512, unpacks it to `%LOCALAPPDATA%\Programs` and adds it to your user PATH (no administrator rights); `-Version` picks the release. On macOS and Linux it uses Homebrew or apt, or a download into `~/.local`. Open a new terminal afterwards. |

A new machine, in order: `install-java`, `install-maven`, `start-server` (with the EULA flag, then stop it), `build-graph`, `build-plugin`, `start-agent`, then start the server again. The Neo4j database itself is `docker compose up -d` (see [../loader/README.md](../loader/README.md)).

The bash versions were written on Windows and have been syntax-checked there (`bash -n`) and run for `build-graph --list`; the rest have not been run on Linux or macOS yet.
