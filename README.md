# MineCorp

MineCorp is a fictitious company in the world of Minecraft that turns raw materials into higher-value products. It is also a **demonstration of how Neo4j can hold a complex domain** and let an AI agent use it: a player types a question in Minecraft chat and an agent answers from a knowledge graph of the game's own data, plus the Minecraft Wiki.

```
/agent how do I make an eye of ender?
MineCorp agent: thinking...
- Crafting Table: 1 blaze powder + 1 ender pearl
- That makes 1 eye of ender.

/agent-explain      ->  opens a book: the question, the answer and the sources behind it,
                        with the graph paths the agent walked, e.g.
                        (Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})
```

## Purpose

- Show how **Neo4j, context graphs, GraphRAG, Neo4j Agent Memory and a knowledge layer** can store complex processes and world state.
- Provide an **agent that helps players** find their way around the game: recipes, where things are, how to reach a goal such as the Nether.
- Be a **fun** way to explain what a knowledge layer is. The agent is read-only: it answers, it does not act in the world.

What is in the graph (all in one Neo4j database; see [docs/ontology.md](docs/ontology.md)):

| Graph | Built from | Holds |
|---|---|---|
| **Domain** | the Paper server jar (authoritative), and `model/*.yaml` | items, recipes, mining, drops, mobs, biomes, structures, ore, villager trades, and the company: the player and processes (shelter, emeralds, Nether, dragon, mob farm) drafted as candidates for a person to confirm |
| **Lexical** | pages of the Minecraft Wiki (community text) | sections and embedded chunks for vector search |
| **Bridge** | derived | links from wiki pages and passages to the items, mobs and blocks they are about |

## How it fits together

```
Minecraft client --chat--> Paper server + MineCorp plugin --HTTP--> agent service (Python) --Cypher--> Neo4j
   /agent <question>        (thin, Java)     shared secret         tool-calling loop over an OpenAI model
   /agent-explain                                                  read-only graph tools + wiki search
```

The plugin only forwards the question and opens the answer book. The agent service owns the logic, the history of each player's last five answers (optionally kept in Neo4j with [neo4j-agent-memory](docs/agent-memory.md), together with a trace of how each answer was reached) and the layout of the book. More: [docs/architecture.md](docs/architecture.md), [docs/knowledge-layer.md](docs/knowledge-layer.md), [docs/explain-book.md](docs/explain-book.md).

## Install

You need: **Docker** (for Neo4j), **Python 3.13** (what it is developed on), **Java 25 and Maven** (the Paper server and the plugin), an **OpenAI API key**, and a Minecraft Java Edition client of the same version as the server (26.2).

The scripts come in two flavours, `./scripts/<name>.ps1` (Windows PowerShell) and `./scripts/<name>.sh` (bash); see [scripts/README.md](scripts/README.md). The commands below use the PowerShell ones.

1. **Python environment**
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements-dev.txt
   ```
   (on Linux and macOS: `.venv/bin/pip`)
2. **Settings.** Copy `.env.example` to `.env` (it is git-ignored) and set at least `NEO4J_PASSWORD`, `NEO4J_ACCEPT_LICENSE_AGREEMENT` (Neo4j Enterprise needs you to accept its licence: `yes` or `eval`), `OPENAI_API_KEY` and `AGENT_SHARED_SECRET` (16 or more characters; the plugin sends it to the agent).
3. **Neo4j:** `docker compose up -d` (Browser on <http://localhost:7474>, Bolt on 7687; ports are configurable in `.env`, which lets you run a second development server).
4. **Java:** `./scripts/install-java.ps1` if you have no JDK 25.
5. **The Paper server:** `./scripts/start-server.ps1 -AcceptEula` downloads and starts it in `server/` (read the [Minecraft EULA](https://aka.ms/MinecraftEULA) first). Stop it with `stop` once it is up.

## Build

1. **The graph:** `./scripts/build-graph.ps1` rebuilds everything from source (about 30 seconds for the game graph, plus the wiki step): recipes, actions, world, trades, the company model, route properties, then the wiki pages and their links. It **wipes the Neo4j database in `.env`** after asking. `--list` shows the steps, `--no-wiki` skips the wiki (no OpenAI use, no network), `--from <step>` carries on after a failure. The wiki step embeds text with OpenAI; the result is cached in `loader/cache/`, so repeat builds are free.
2. **The plugin:** `./scripts/build-plugin.ps1` builds it, copies the jar into `server/plugins` and writes its config (the secret from `.env`) once.

## Use

1. Start the agent: `./scripts/start-agent.ps1` (listens on 127.0.0.1:8080).
2. Start the server: `./scripts/start-server.ps1`, and join it with a Minecraft 26.2 Java client.
3. In chat: `/agent <question>`, for example `/agent where do I find diamonds?` or `/agent how do I get to the Nether?`. Then `/agent-explain` opens a book with your last five questions, answers and sources (wiki sources are links).

Follow-up questions work ("and how many of those do I need?"): the agent is sent your last three exchanges, for as long as you keep asking within 15 minutes of each other.

**Agent memory (optional).** Set `AGENT_MEMORY=neo4j` in `.env` before starting the agent to keep each player's questions and answers, and a trace of the tools every run used, in Neo4j with neo4j-agent-memory; `/agent-explain` then works after a restart too. It is off by default. Memory is written into the game graph's database, so `build-graph` wipes it along with the graph unless you give it a database of its own. Settings and design: [docs/agent-memory.md](docs/agent-memory.md).

**Without Minecraft:** `./scripts/chat-with-agent.ps1` is a terminal stand-in for the plugin (`-Trace` shows the tools the agent called, `-Direct` runs the agent in the same process; type `/explain` for the book). Details in [agent/README.md](agent/README.md).

## Tests

`./scripts/run-tests.ps1` runs the fast set (no database, no server). `-Integration` also builds the graph in throwaway databases, `-E2E` starts a real Paper server with the plugin, `-All` does both. The agent's answer quality is checked separately with evals that call the model and cost tokens: `cd agent; python -m evals.run_evals -v`. See [tests/README.md](tests/README.md).

## Where things are

| Path | What |
|---|---|
| [agent/](agent/) | the agent service (FastAPI, tool-calling loop, graph tools, sources, history) |
| [loader/](loader/) | loaders that build the graph from the jar, `model/` and the wiki ([README](loader/README.md)) |
| [model/](model/) | authored YAML: the player, the processes, the wiki page list and link fixes |
| [plugin/](plugin/) | the Paper plugin ([README](plugin/README.md)) |
| [scripts/](scripts/) | build, run and test scripts ([README](scripts/README.md)) |
| [docs/](docs/) | ontology, architecture, knowledge layer, tool and book designs |
| [AGENTS.md](AGENTS.md) | context and the **decisions log** for people and AI agents working on the project; [NOTES.md](NOTES.md) holds the original thinking |

## Sources

- [The knowledge layer for enterprise AI](https://neo4j.com/blog/agentic-ai/enterprise-knowledge-layer/)
- [The knowledge layer for enterprise: Processes](https://neo4j.com/blog/genai/the-knowledge-layer-for-enterprise-processes/)
