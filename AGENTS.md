# AGENTS.md

Long-term context for AI agents working on MineCorp. Keep this file current; put raw, human-owned thinking in [NOTES.md](NOTES.md).

## Your role

- Help with **investigation and design** (human-in-the-loop). The human owns decisions.
- Default to research, options with a recommendation, and written design artefacts. Do not build large implementations unless asked.
- Surface open questions and trade-offs; record agreed decisions below.
- Cite sources for factual claims about Neo4j, Minecraft, or minecraft.wiki.

## Project summary

MineCorp is a fictitious Minecraft company that turns raw materials into higher-value products.

Objectives:
1. Demonstrate how Neo4j, context graphs, GraphRAG, Neo4j Agent Memory and an [Enterprise] Knowledge Layer can store complex processes and current world state.
2. Create an agent that helps employees (players) navigate the world and answer questions about MineCorp.
3. Present the project as a "fun" demonstration of how complex systems and domains can be supported by Neo4j and knowledge layers.

Deliverables:
1. Knowledge Layer describing world/server state, crafting/process mechanisms, and advice drawn from minecraft.wiki articles.
2. An agent that answers questions about MineCorp.

## Open questions (from NOTES.md)

- Architecture of the knowledge layer; build components.
- How to show domain, lexical, entity and agent-memory graphs.
- Agent tools required; confirm graph + Minecraft API (read-only vs. actions in the world).
- Plugin <-> agent service protocol (HTTP/WebSocket), and what the plugin exposes (chat, events, state queries). Server flavour (Paper vs Spigot vs vanilla + other) TBC when the server is built.
- Which world state is summarised in the graph vs fetched live; how is the summary kept fresh?

## Decisions log

Record: date, decision, rationale. "Leaning" = direction, not yet confirmed.

- 2026-10-01 Tone: presented as a fun demo of complex domains supported by Neo4j and knowledge layers.
- 2026-10-01 Game scope: vanilla Minecraft only (no mods). Process model = vanilla crafting, smelting, trading etc.
- 2026-10-01 Interface: Minecraft chat is the first priority; other interfaces later.
- 2026-10-01 Integration: thin server plugin (Paper/Spigot-style) forwarding chat/events to an external agent service; chosen for better later integration options.
- 2026-10-01 Server: Java edition; the user will build and host it.
- 2026-10-01 Agent is read-only to start (answers, directions; no world actions).
- 2026-10-01 Graph model agreed: domain, lexical, entity, agent memory (built in that order).
- 2026-10-01 Domain graph design is PAUSED until the server is up. The server holds crafting recipes as JSON; use that as the recipe source rather than the wiki.
- 2026-10-02 Architecture decisions 1-7 agreed (see docs/architecture.md): own-process agent over plain HTTP; purpose-built graph tools; local; OpenAI model with no framework; shared-secret auth; live-state access path revisited later.
- 2026-10-02 Knowledge layer (docs/knowledge-layer.md v0.2): the KL is the graph only; tools, policy and agent are the access layer outside it. Recipes belong to the process ontology (things = domain, transformations = process). Business processes in Minecraft: collecting resources (mining/harvesting), crafting, inventory management. Operating model is fine but every solution must work with a single player. Order of work agreed (org and process first). Neo4j moved to Enterprise Edition (2026.09.0-enterprise; licence accepted by the user in .env; done 2026-10-02). No per-node provenance on vanilla data (candidates only). Owner/steward roles are recorded as data and the single player fills all of them. Recipe (vanilla definition) and Task (MineCorp unit of work) stay separate.
- 2026-10-04 Agent wiki tools: `search_wiki` (vector search over wiki chunks, passages with page and section) and `read_section` (a whole section, optional neighbours), read-only, using the Cypher `SEARCH` clause (the `db.index.vector.queryNodes` procedure is deprecated in 2026.09). The wiki is community text: results carry a source note, the agent says "according to the wiki", and the game-data tools stay authoritative for recipes, ingredients and where items come from. 27 eval questions pass (8 about the wiki).
- 2026-10-04 Ontology v0.8 (loader built and tested; the Beginner's guide page is loaded): lexical graph of the Minecraft wiki in the same database (`Wiki`, `Page`, `Section` tree, `Chunk` with embedding); chunks are 300 tokens with 50 overlap within a section (a small section is one chunk); `text-embedding-3-small`; a local `shelve` cache of `{hash: embedding}` so a rebuild costs nothing; MediaWiki API; provenance kept. First test page: Tutorial:Beginner's guide. See docs/ontology.md.
- 2026-10-02 Ontology v0.6 decisions: mining is a `Harvest` node parallel to `Recipe`; tool requirement is the minimum tool item with `orBetter`; first load is blocks only (done); org units and roles fine for now.
- 2026-10-02 Principle: the graph database can be rebuilt from source if required (Paper jar + model/ YAML); loaders may delete and recreate what they own, the rebuild order is the rule. Not rebuildable: COMPLETED history, events, agent memory (none yet).
- 2026-10-02 Ontology v0.7 BUILT (docs/ontology.md): `Action` node with a verb secondary label (Craft, Smelt, Smith, Cut, Mine, Kill, Shear); a Task performs ordered Actions (`HAS_ACTION {order, quantity}`), several allowed, one role per task; `Harvest` removed (Mine/Kill/Shear actions carry target Block/Mob, optional tool and drops via REQUIRES/CREATES); Craft/Smelt/Smith/Cut actions are thin, generated one per verb and item, and meet their Recipe nodes at the Item; no recipe pin; tools stay plain Items; manual verbs (Store, Move) left out.
- 2026-10-02 Plugin interface: `/agent` command first (e.g. `/agent <question>`); reading normal chat messages comes later, if at all.
- 2026-10-01 Leaning: world state kept as a summary in the graph, specifics fetched from the server on demand (TBC).
- 2026-10-01 Leaning: agent uses both the knowledge graph and the Minecraft API (TBC).

## Design principles from the Neo4j posts (build should reflect these)

From "The knowledge layer for enterprise AI":
- KL = shared, governed substrate between data and agents. "Lighter agents over a smarter shared substrate": keep knowledge in the graph, not in agent prompts/code.
- Three components: **ontology** (technical, domain, business-process, policy, organisation layers), **enterprise data** (instantiates the model; queried in place or materialised), **memory** (every run leaves a decision trace).
- Request flow: interpret intent against the model -> pick authoritative source -> generate translated query -> enforce policy/access -> capture decision trace.
- Build hybrid: bottom-up LLM-assisted discovery, top-down governance. LLMs propose mappings, humans ratify.
- Federated ownership; the layer improves each time it is used.

From "The knowledge layer for enterprise: Processes":
- Process schema: BusinessProcess -> BusinessActivity -> BusinessTask (three levels; tasks are the delegatable unit). Role (not Person) performs tasks.
- Relationships: PERFORMS, OWNS (one owner per process), NEXT, HANDOFF_TO, ESCALATES_TO.
- Routing is data: NEXT carries condition, outcome, isDefault.
- Extracted processes are *candidates* with source, extraction date, confidence; humans confirm before they are canonical.

Implications for MineCorp (proposed, not yet agreed):
- "Data stays external": recipes from the server JSON and live state from the server are the authoritative sources; the graph maps to them and holds summaries (matches the leaning on world state).
- MineCorp processes (e.g. ore -> ingot -> tool) map to Process/Activity/Task; players/NPC roles map to Role. Vanilla recipes are the underlying task inputs/outputs.
- Wiki-derived advice and any LLM-extracted links are *candidates* with source/confidence until the human approves.
- Every agent answer should write a decision trace (what was asked, which sources/queries used) to memory.
- Policy/organisation layers have a light-weight analogue (e.g. which player may see which data); decide later if needed.

## Ontology

Graph data model and ontology: [docs/ontology.md](docs/ontology.md) (versioned; update it as the model changes and log changes there). v0.7 is BUILT and loaded: 2,385 `Action` nodes generated from the jar (`load_actions.py`; Mine 1,099, Craft 949, Cut 197, Kill 63, Smelt 61, Smith 12, Shear 4) and the organization and process model authored as YAML in `model/` (`load_model.py`; 6 units, 9 roles all filled by the one player, Iron tool line with 6 tasks). **The graph can be rebuilt from source at any time** (about 30 seconds): `load_recipes.py --all --wipe`, `load_actions.py`, `load_model.py`, `compute_routes.py`. `Station` is a marker label on `Item`. Route rules: stop at mined resources, avoid silk-touch-only inputs, show `getFrom` (mine, kill or shear, with the tool needed). Not built: fishing, bartering, hive harvests, chests, manual verbs (Store, Move), and where-to-find data (ore heights, biome spawns).

Loader (Python) and local Neo4j (docker-compose.yml): see [loader/README.md](loader/README.md). Full graph loaded with ontology v0.4 on 2026-10-02 for review.

## Knowledge layer

[docs/knowledge-layer.md](docs/knowledge-layer.md) (v0.3, reviewed; decisions recorded). The enterprise knowledge layer = ontology (technical, domain, data products, business process, policy, organization) + enterprise data + memory + a serving layer, built hybrid (LLM proposes, humans confirm). Series structure from the Neo4j posts: ch1 operating structure, ch2 processes, ch3 domain ontology, ch4 data products.

## Architecture

[docs/architecture.md](docs/architecture.md) (v0.2, decisions agreed). Summary: thin Java plugin -> plain HTTP -> Python agent service (own process, hand-written tool-calling loop, OpenAI model, no framework) -> purpose-built read-only graph tools (`get_recipe`, ...) over Neo4j. Live-Minecraft tools and event ingest come later; MCP is deferred (the core stays transport-neutral so `ask` can be exposed as MCP later). Runs locally; plugin-to-service auth is a shared secret.

## Agent service

[agent/](agent/): Python FastAPI service with a hand-written OpenAI tool-calling loop (`agent.py`) and seven read-only graph tools (`tools.py`): `find_item`, `get_recipes`, `what_do_i_need`, `get_uses`, `class_members`, `rank_recipes`, `how_to_get` (blocks to mine, mobs to kill or shear). Default model `gpt-5.4-mini` (`OPENAI_MODEL`). Each question writes a decision trace to `agent/logs/traces.jsonl` (git-ignored; writing traces to the graph comes later). Run with [scripts/run-agent.ps1](scripts/run-agent.ps1); details in [agent/README.md](agent/README.md). `what_do_i_need` returns an ordered `steps` list plus a `gather` list; `get_recipes` and `what_do_i_need` include an `overview` (craftingSteps, chainLength, baseMaterials, stations). Quality check: `cd agent; python -m evals.run_evals -v` (19 questions). `rank_recipes` reads route properties stored on `Item` by `python loader/compute_routes.py` (run it after every graph load; ~10 s). Hardest by that metric: glazed terracotta (8 steps) then the netherite tools (7 steps). OpenAI key, `AGENT_SHARED_SECRET` live in `.env`.

### Backlog / notes for later

- Complexity ranking is built (ontology v0.5). Open: is "most things to make" the right meaning of hard? Rarity/danger of materials is not in the data. Also: answers sometimes exceed the 6-line style limit.
- Item name lookup: use a Neo4j **full-text index** (user request, 2026-10-02) instead of the current `CONTAINS` word match in `tools.search_items`.
- Category names such as "Stone Crafting Materials" are unfriendly; the prompt asks the model to give examples. Consider curated display names.
- `what_do_i_need` picks a main route by a simple rule (non-reversible, simplest recipe first); alternatives are listed, but it does not know what is easiest to obtain in the world.

## Plugin

[plugin/](plugin/) is the thin Paper plugin (Maven, Java 25, `paper-api 26.2.build.129-stable`, kept in step with the server build). `/agent <question>` POSTs to the agent service (stub reply for now) asynchronously with a shared secret. Build and install with [scripts/build-plugin.ps1](scripts/build-plugin.ps1) (copies the jar to `server/plugins`). How to build and use it: [plugin/README.md](plugin/README.md). Not yet tested on a running server.

## Tests

[tests/README.md](tests/README.md). Run `./scripts/test.ps1` (fast set) or `./scripts/test.ps1 -All` (about a minute). 630 tests as of 2026-10-04: 125 loader unit tests, 112 wiki loader unit tests, 37 against the real Paper jar, 125 agent unit tests (fake model, no tokens), 124 agent tool tests 34 loader integration tests that rebuild the graph in a throwaway Neo4j database (`minecorptest`, created and dropped by the test; the real database is never touched) and 24 wiki graph tests (`minecorpwikitest`), 37 plugin JUnit tests, 12 end-to-end tests with a real Paper server on a random port in `.test-server/` plus a stub agent. The agent also has evals (`agent/evals`, call the model, cost tokens) for tool choice and answer quality. Each component README lists its own tests. The plugin's rules live in `AskGuard`, `Replies` and `AgentClient` so they can be tested without Minecraft. Pytest markers: `jar`, `integration`, `e2e`; the fast run excludes `integration` and `e2e`.

## Server

Scripts are in scripts/; the server itself runs in server/ (git-ignored via .gitignore).


- [scripts/start.ps1](scripts/start.ps1): downloads Paper (latest STABLE, pinned in scripts/paper-version.txt), verifies SHA-256, requires `-AcceptEula`, starts the server. Pinned to Paper 26.2 (stable, build 129 on 2026-10-01); requires Java 25+. Java 25 (Temurin) is installed on the dev machine. The script checks the Java version against Paper's declared minimum.
- [scripts/install-java.ps1](scripts/install-java.ps1): installs Temurin JDK 25 via winget (has been run).
- Proposed Neo4j-post implications (decision traces, candidate vs canonical, data stays external) were accepted by the user on 2026-10-01.

## Sources

- https://neo4j.com/blog/agentic-ai/enterprise-knowledge-layer/
- https://neo4j.com/blog/genai/the-knowledge-layer-for-enterprise-processes/
