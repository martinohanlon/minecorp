# MineCorp agent service

Receives questions from the Minecraft plugin over HTTP and answers them. **Stub stage:** it returns a canned reply; the LLM loop and the graph tools come next (see [docs/architecture.md](../docs/architecture.md)).

## Run

From the project root (needs the `.venv`; `python -m venv .venv` if it does not exist):

```powershell
./scripts/start-agent.ps1
```

It installs the requirements, reads settings from `.env` and listens on `http://127.0.0.1:8080`. Stop it with Ctrl+C.

Settings (environment or `.env`; see `.env.example`):

| Name | Default | Notes |
|---|---|---|
| `AGENT_SHARED_SECRET` | none, required | 16+ characters. The plugin sends it in the `X-MineCorp-Key` header. Must match the plugin config. |
| `AGENT_HOST` | `127.0.0.1` | Keep on localhost. |
| `AGENT_PORT` | `8080` | |
| `OPENAI_API_KEY` | | Not used yet (LLM loop to come). |

## API

| | |
|---|---|
| `GET /health` | `{"status":"ok"}`; no secret needed |
| `POST /ask` | Header `X-MineCorp-Key: <secret>` (401 without it). JSON body: `playerUuid`, `playerName`, `sessionId`, `question` (1-500 characters). Reply: `{"requestId": "...", "answer": "...", "sources": [{"kind": "game data" | "wiki" | "plan", "label": "...", "url": "...", "graph": ["(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Ender Pearl})"]}]}` (`url` only for a wiki section, `graph` the paths the tool walked, shown under the source in the book; see [docs/explain-book.md](../docs/explain-book.md)). `POST /explain` takes `{"playerUuid": "..."}` and returns the book of that player's last five answers with their sources (`{title, author, pages: [{lines: [{text, url?, goto?}]}]}`; no pages when nothing has been asked); the service keeps those in memory. `POST /clear` takes `{"playerUuid": "..."}` and starts a new chat for that player (a new conversation: the book and the context are empty, what came before stays in agent memory; the plugin calls it when a player joins and for `/agent-clear`), replying `{"cleared": true}`. The `sessionId` sent with a question is ignored; the service owns the conversation (see [docs/agent-memory.md](../docs/agent-memory.md)). Invalid bodies return 422. |

Try it:

```powershell
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/ask -H "Content-Type: application/json" -H "X-MineCorp-Key: <secret>" `
  -d '{"playerUuid":"u1","playerName":"Steve","sessionId":"u1","question":"hello"}'
```

## Try it without Minecraft

`scripts/chat.py` stands in for the plugin, so the agent can be tried from a terminal:

```powershell
./scripts/chat-with-agent.ps1                                   # a chat (the agent service must be running: ./scripts/start-agent.ps1)
./scripts/chat-with-agent.ps1 how do I make a bed?              # ask once and exit
./scripts/chat-with-agent.ps1 -Direct -Trace                    # no service: run the agent in this process, and show what it did
```

It sends what the plugin sends (the same `POST /ask` with the shared secret from `.env`, the player's name and a uuid made from it) and shows what the plugin shows: "MineCorp agent: thinking...", the answer as gold lines, and "The agent is not available right now. Try again later." when it fails. It applies the plugin's rules (a question is required, at most 500 characters, a leading `/agent ` is accepted). Because it is a developer's tool it adds, in grey and marked `(stub)`, what a player never sees: why a request failed (for example `HTTP 401`), how long it took, whether the answer breaks the chat style (more than 6 lines, a line over 100 characters, markdown), and with `--trace` the agent's tool calls, timings and tokens, read from `agent/logs/traces.jsonl`. Starting the chat is joining the game, so it starts a new chat; `/clear` (or `/agent-clear`) starts another. In the chat, `/explain` asks the service for the book the plugin's `/agent-explain` will open (the last five questions, answers and sources, kept by the service; `--explain` does it after one question), `/trace` turns the developer trace on and off, `/name Alex` changes the player, `/help` and `/quit` do what they say. `--url` (or `AGENT_URL`) points at a service elsewhere.

## Memory

Optional: `AGENT_MEMORY=neo4j` keeps each player's questions and answers, and a trace of the tools each run used, in Neo4j with neo4j-agent-memory, so the explain book survives a restart. Off by default (`local`). Settings, what is stored and the design: [../docs/agent-memory.md](../docs/agent-memory.md).

**Follow-up questions** work with or without it: the player's last `AGENT_CONVERSATION_TURNS` exchanges (3) of the current conversation (it ends after `AGENT_CONVERSATION_IDLE_MINUTES` quiet, 15) are sent to the model with the new question, so "and how many of those?" has something to refer to.

## Layout

```
agent/
  requirements.txt
  minecorp_agent/
    config.py   settings and .env loading
    core.py     answer(question) -> text; independent of the transport (HTTP now, MCP possible later)
    app.py      FastAPI app: /health, /ask, shared-secret check
    __main__.py python -m minecorp_agent
```

`core.answer()` is the only place that will change as the agent grows.

## Tools

Thirteen read-only tools, defined once in `minecorp_agent/tools.py` and `minecorp_agent/wiki.py`:

| Tool | Answers |
|---|---|
| `find`, `get_recipes`, `what_do_i_need`, `how_to_get`, `get_uses`, `members`, `rank_recipes` | recipes, routes, drops and difficulty, from the game data (authoritative; built from the Paper jar) |
| `list` | everything of one kind: all the biomes (by dimension), mobs, structures, villager professions, dimensions, item categories, mob and biome classes, or items and blocks a page of 100 at a time (`contains` narrows them). A long list is summarised for chat. Classes come with the number of things in them, flat, not as a tree (they are Minecraft's tags); `members` opens one |
| `get_process` | step-by-step plans for goals the game's data does not spell out (shelter, emeralds, the Nether, the ender dragon, a simple mob farm): with no name it lists them, with a name it gives the usual route and where it branches, with a step it gives that step in full; steps that are game actions name them. Plans are candidates drafted from the wiki and say so |
| `describe`, `where_to_find` | what anything with a name is, and where it is in the world (ore heights and biomes, mob spawns, what a biome has, a structure's biomes and who sells a map to it), from the world graph; `describe` also lists the wiki sections that discuss it and what a villager profession trades at each level Enchantments (v0.13) are covered by the same tools: `describe` gives an enchantment's maximum level, what it goes on, what it cannot be combined with, where it comes from and the enchanting power each level needs, and an item's `canBeEnchantedWith`; `where_to_find` gives the ways to get one and the villagers that offer it; `list`, `find` and `members` know enchantments and their classes (treasure, curse, exclusive sets). What an enchantment does is the wiki's (`search_wiki`). |
| `how_to_get` (trades) | also lists the villagers and the wandering trader that trade an item: profession, level, price (8 at most, lowest level first, then a count of the rest) |
| `search_wiki` | advice and how-to questions (first night, shelter, controls, mob behaviour) by vector search over the wiki chunks; returns passages with page and section, and the game nodes each passage is about (`mentions`) |
| `read_section` | the whole text of a wiki section (by the `sectionUrl` of a hit, or its title), optionally with the passages before and after, and what it is about (`mentions`) |

The wiki tools need the wiki loaded (`python loader/load_wiki.py`, see [loader/README.md](../loader/README.md)). The question is embedded with the model the chunks used (`EMBEDDING_MODEL`, default `text-embedding-3-small`; a mismatch is reported, not searched). Results carry a source note and the prompt tells the model to say "according to the wiki"; recipes, ingredients and where an item comes from still come from the game-data tools, and the wiki is only a fallback for those (for example what a mob drops). If the wiki is not loaded, `search_wiki` says so and the other tools carry on.

**Mentions.** Each passage lists the game items, mobs and blocks it is about, read by following `(Chunk)-[:MENTIONS]->(node)` (written by `loader/link_wiki.py`): `{name, kind, id}`, plus `linked: true` when the wiki itself links to it and `ambiguous: true` when the same wording can name several nodes. The kind comes from the node's own labels (Item, Category, Station, Mob, Block); an ambiguous word lists every candidate (the item Chicken and the mob Chicken), and the model chooses from the passage. At most 8 per passage, linked ones first. The prompt tells the model to use a mention to look up game facts with the game-data tools, so a wiki passage can lead to `get_recipes` or `what_do_i_need`. Without the link job nothing changes: passages simply have no `mentions`.

## Use with the plugin

See [plugin/README.md](../plugin/README.md): build the plugin, start this service, start the Minecraft server, then type `/agent <question>`.

## Tests

Two kinds, and they answer different questions.

**Tests** (`tests/agent/`, no model, no tokens) check that the code around the model behaves:

| File | Needs | What it checks |
|---|---|---|
| `test_tools_unit.py` | nothing | the tool registry and the specs sent to the model are valid; how recipes, requirements and routes are written as text; how conditions are put into words |
| `test_agent_loop.py` | nothing | the tool-calling loop against a scripted fake model: tool calls and results, a tool that raises, over-long results cut at the cap, the turn limit (last turn forbids tools, fallback answer), model failures, trace lines written |
| `test_wiki_unit.py` | nothing (scripted fake graph) | `search_wiki` and `read_section`: merging and stitching of chunks, ordering, limits, error messages, neighbours, and the mentions (kinds from labels, linked and ambiguous flags, every candidate listed, merged per passage, capped) |
| `test_config.py` | nothing | `.env` reading (never overrides real variables) and the settings: secret of 16+ characters, safe defaults (localhost), overrides and types |
| `test_app.py` | nothing | `/health`, the secret check (exact match only, done before the body is read), field limits, one request id per call, start-up problems turned into a friendly message |
| `test_tools_graph.py` (marker `integration`) | Neo4j Enterprise and the Paper jar | every tool against the real graph, rebuilt from source in a throwaway database: name resolution and ambiguity, recipe shapes, categories and exclusions, limit clamping, `how_to_get` wording and ordering, and `what_do_i_need` on 47 items to prove every step only uses things gathered or made before it |
| `test_wiki_graph.py` (marker `integration`) | Neo4j Enterprise (no jar, no OpenAI) | the wiki tools against two pages loaded into a throwaway database with fake embeddings: the real vector query, section paths, subsections, neighbours, a missing index, and the mentions written by the real link job (an ambiguous chicken is an item and a mob) |

**Evals** (`agent/evals`, calls the model, costs tokens) check that the model chooses the right tools and gives a good answer. Run them after changing the prompt or a tool description, not on every change:

```powershell
cd agent
python -m evals.run_evals
```

Run the tests from the project root with `./scripts/run-tests.ps1` (add `-Integration` for the graph tests). More in [../tests/README.md](../tests/README.md).
