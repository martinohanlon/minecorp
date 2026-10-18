# MineCorp plugin

A thin [Paper](https://papermc.io) plugin that connects Minecraft to the MineCorp agent service. `/agent <question>` sends the question to the agent service over HTTP and shows the reply. The agent is a **stub** for now and returns a canned answer.

| | |
|---|---|
| Server | Paper 26.2 (pinned in [scripts/paper-version.txt](../scripts/paper-version.txt)) |
| Java | 25 |
| Build | Maven |
| Paper API | `io.papermc.paper:paper-api:26.2.build.129-stable` (set by `paper.api.version` in [pom.xml](pom.xml)) |

## Prerequisites

- A JDK 25 on `PATH` (`javac -version`). Install one with `./scripts/install-java.ps1`.
- Maven 3.9+ on `PATH` (`mvn -version`). Install it with `./scripts/install-maven.ps1`. Maven uses `JAVA_HOME` if it is set, otherwise the `java` on `PATH`; `mvn -version` should report Java 25.
- The server set up as described in the project root: `./scripts/start-server.ps1 -AcceptEula` downloads Paper into `server/` on first run.

## Build

From the project root, the one-step way:

```powershell
./scripts/build-plugin.ps1
```

This runs the Maven build and copies `MineCorp-0.1.0.jar` into `server/plugins/`.

Or by hand:

```powershell
cd plugin
mvn package
# output: plugin/target/MineCorp-0.1.0.jar
copy target\MineCorp-0.1.0.jar ..\server\plugins\
```

The first build downloads the Paper API and its dependencies from `repo.papermc.io`, so it needs network access.

## Configuration

The plugin reads `server/plugins/MineCorp/config.yml`:

| Key | Default | Meaning |
|---|---|---|
| `agent-url` | `http://127.0.0.1:8080` | Where the agent service listens |
| `shared-secret` | empty | Must equal `AGENT_SHARED_SECRET` in the project `.env`. While empty, `/agent` tells the player the agent is not configured. |
| `timeout-seconds` | `30` | How long to wait for an answer |

`./scripts/build-plugin.ps1` creates this file the first time, with the secret copied from `.env`. It never overwrites an existing file (delete it to regenerate). The file lives under `server/`, which is git-ignored.

## Run and test (end to end)

You need three things running: Neo4j is **not** required for the stub.

1. **Agent service** (leave it running):

   ```powershell
   ./scripts/start-agent.ps1
   ```

2. **Build and install the plugin.** Stop the Minecraft server first (a running server keeps the old jar open), then:

   ```powershell
   ./scripts/build-plugin.ps1
   ```

3. **Start the Minecraft server:**

   ```powershell
   ./scripts/start-server.ps1 -AcceptEula
   ```

   (`-AcceptEula` is only needed until `server/eula.txt` says `eula=true`.) The startup log should show `[MineCorp] MineCorp plugin enabled (agent: http://127.0.0.1:8080)`.
4. **Ask a question**, from either place:
   - **Server console:** `agent how do I make an iron pickaxe?` (no slash)
   - **In game:** connect a Minecraft Java client of the same version (26.2) to `localhost`, then `/agent how do I make an iron pickaxe?`

   Expected: a grey "MineCorp agent: thinking..." line, then a gold reply starting "Hello <name>. You asked: ...".
5. Stop the server with `stop` in the console, and the agent with Ctrl+C.

Behaviours to expect:

| Situation | What the player sees |
|---|---|
| `/agent` with no question | `Usage: /agent <question>` |
| Question over 500 characters | A "too long" message |
| A second question while one is still being answered | "Still thinking about your last question..." |
| Agent service not running or times out | "The agent is not available right now. Try again later." (details in the server log) |
| `shared-secret` empty | "The agent is not configured yet. Ask an admin." |
| `/agent-explain` before any question | "Nothing to show yet. Ask something with /agent <question> first." |
| `/agent-explain` from the server console | "Only a player can open the notes book." |
| `/agent-clear` from the server console | "Only a player has a chat to clear." |

The permission `minecorp.agent` controls who may use the command (everyone by default).

### `/agent-clear`

Starts a new chat: the agent opens a new conversation for the player, so what they asked before is no longer used to understand their follow-up questions or shown in their book (it stays in agent memory as a conversation of its own). The plugin only posts the player's uuid to `POST /clear` and says "Chat cleared. Your next question starts a new chat." A player who **joins** starts a new chat the same way (`PlayerJoinEvent`; quiet if the agent is down). Console: "Only a player has a chat to clear." Permission `minecorp.clear` (everyone by default).

### `/agent-explain`

Opens a book with the player's last five questions, answers and the sources behind them (for a graph source, the paths the tool walked). The plugin only posts the player's uuid to `POST /explain` and opens what comes back (`Audience#openBook`: the book is opened, not given as an item); the agent service keeps the history and lays out the pages ([docs/explain-book.md](../docs/explain-book.md)). A line with an `http(s)` address opens it (`open_url`, blue and underlined; the game asks before opening) and a contents line jumps to its page (`change_page`). Any other kind of address from the service is dropped. Permission `minecorp.explain` (everyone by default).

After rebuilding, **restart the server**. Avoid `/reload`, which Paper discourages for plugins.

## Layout

```
plugin/
  pom.xml                                      Maven build, Paper API dependency
  src/main/resources/plugin.yml                Plugin descriptor: name, main class, api-version, command, permission
  src/main/resources/config.yml                Default config (copied to the server on first start)
  src/main/java/com/minecorp/plugin/
    MineCorpPlugin.java                        Entry point, the /agent, /agent-explain and /agent-clear commands and the join listener; connects the pieces below to Bukkit
    AskGuard.java                              The rules for accepting a question (usage, length, configured, one at a time)
    Replies.java                               Splits an answer into chat lines
    AgentClient.java                           HTTP client for the agent service (POST /ask, POST /explain, POST /clear)
    ExplainBook.java                           The book from /explain as plain data; keeps only http(s) links and jumps to real pages
    BookRender.java                            Turns that into an Adventure book with the click events
  src/test/java/com/minecorp/plugin/           JUnit tests (see ../tests/README.md)
```

`plugin.yml` takes its version from `pom.xml` (`${project.version}`), so change the version in one place.

## Changing the Minecraft version

When the server is moved to a new Paper version, update all of these together:

1. [scripts/paper-version.txt](../scripts/paper-version.txt), or run `./scripts/start-server.ps1 -Update`.
2. `paper.api.version` in [pom.xml](pom.xml). List the available builds at <https://repo.papermc.io/repository/maven-public/io/papermc/paper/paper-api/>.
3. `api-version` in [src/main/resources/plugin.yml](src/main/resources/plugin.yml) (the major.minor, e.g. `'26.2'`).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `release version 25 not supported` or `invalid target release` during the build | Maven is using an older JDK. Point `JAVA_HOME` at JDK 25. |
| Build cannot resolve `paper-api` | No network, or `paper.api.version` is not a published build. |
| Server log shows the plugin was not loaded or has an invalid `plugin.yml` | Check `api-version` and the `main` class name in `plugin.yml` against the log message. |
| `Unknown command` for `/agent` | The plugin did not load. Check the startup log, and that the jar is in `server/plugins/`. |
| `/agent` says the agent is not available | The agent service is not running, the URL or port is wrong, or the secret does not match (the agent logs a 401). See the server log. |
| Plugin loads but the version warning appears | `api-version` is older or newer than the server. Align it with the server version. |

## What's next

The agent service gets an LLM loop and graph tools; the plugin does not change. See [docs/architecture.md](../docs/architecture.md) and [agent/README.md](../agent/README.md).

## Tests

```powershell
cd plugin
mvn -B test                      # 54 JUnit tests, about 5 seconds
```

| Test class (in `plugin/src/test/java/com/minecorp/plugin/`) | What it checks |
|---|---|
| `AskGuardTest` | empty question, 500-character limit (exactly 500 allowed), not configured, one question at a time per sender, the order the rules are checked in, and 16 simultaneous attempts letting only one through |
| `AgentClientTest` | against a local HTTP server: method, path, secret header, JSON body and escaping, the answer, trailing slashes, and every failure (non-200, not JSON, no answer, answer not a string, empty body, timeout, nothing listening) |
| `ExplainBookTest` | reading the book: title and author defaults, line order, only `http`/`https` links kept (not `javascript:`, `file:`...), jumps only to pages that exist, every malformed shape an IO error |
| `BookRenderTest` | a link line opens its address, a jump line changes page, a plain line has no click, page and book layout |
| `RepliesTest` | how an answer splits into chat lines |
| `PluginDescriptorTest` | `plugin.yml` and `config.yml` as packaged: main class exists, version filled in, `api-version` matches Paper, the `/agent` command and permission, no secret shipped |

The end-to-end test (`tests/plugin/test_plugin_e2e.py`) runs the built plugin in a real Paper server with a stub agent.

Or from the project root: `./scripts/run-tests.ps1`; add `-E2E` to also start a real Paper server with this plugin and a stub agent (about 25 seconds, on a random port, so your own server can keep running). Details in [../tests/README.md](../tests/README.md).
