# MineCorp architecture

Status: **v0.2, decisions agreed (2026-10-02).** Nothing here is built except the plugin scaffold. Agreed decisions are in the Decisions table below and in [AGENTS.md](../AGENTS.md).

## Goals and constraints (already decided)

- The player talks to the agent from Minecraft. First interface is the `/agent` command.
- The plugin stays **thin** (Paper plugin, Java).
- The agent is **read-only** to start: it answers questions and gives directions, and does not act in the world.
- Knowledge lives in the graph ("lighter agents over a smarter shared substrate"). World state: a summary in the graph, specifics fetched live from the server.
- Every answer leaves a **decision trace** (what was asked, which sources were used).

## MCP: two roles, and an agent can be one tool

MCP gives a server two possible roles, and a service can take both:

- **Tool provider.** The server exposes tools (and optionally resources and prompts) to a client, which is usually an agent's LLM loop.
- **Agent host ("agent as a tool").** The server wraps a whole agent behind one tool, e.g. `ask(question, player)`. The caller sees a single call; the LLM loop, its prompts and the tools it uses are hidden inside.

These combine. The MineCorp agent can be an MCP server **northbound** (it offers `ask` to the plugin and to any other MCP client) and an MCP client **southbound** (it calls the graph and Minecraft tool servers).

```
 plugin / Claude Desktop / other agents
              |  MCP: ask(question, player)
              v
   MineCorp agent  (MCP server, hosts the LLM loop)
              |  MCP client
      +-------+--------+
      v                v
  graph tools     minecraft tools
```

Why the agent-as-a-tool shape is attractive here:

- **Reuse.** Anything that speaks MCP can ask MineCorp a question: Claude Desktop or Claude Code for development and debugging, other agents later. The plugin is one client among several.
- **Clear boundary.** The caller only knows `ask`. Prompts, model choice and tool sets can change without touching the plugin.
- **Fits the knowledge-layer idea.** A governed layer with agents consuming it, and the agent itself consumable.

Costs to weigh:

- **The plugin needs an MCP client in Java.** An MCP Java SDK exists; how much it adds to a "thin" plugin is something to check in the stub (step 1 below). The alternative is a plain HTTP endpoint on the same service that calls the same core function.
- **Long calls.** An LLM loop takes seconds. MCP over streamable HTTP can carry progress notifications, but the plugin should still show an immediate "thinking..." message and run the call off the main thread.
- **Sampling is a separate option.** MCP lets a server ask its *client* to run an LLM call (sampling). We would not use that here; the agent service has its own model access.

Terminology used below: **agent service** = the process hosting the LLM loop. Whether it is reached by plain HTTP or as an MCP server (`ask`) is decision 1.

## Proposed components

```
  Minecraft player
        |  /agent <question>
        v
+-------------------+        HTTPS/JSON         +------------------------------------+
|  Paper server     | -------------------------> |  Agent service  (Python)           |
|  MineCorp plugin  |   {player, question,       |  - HTTP endpoint for the plugin    |
|  (thin, Java)     |    session id}             |  - LLM loop, calls tools via MCP   |
|                   | <------------------------- |  - writes decision trace + memory  |
|                   |   {answer}                 +--------+---------------+-----------+
|                   |                                     | MCP           | MCP
|                   |                            +--------v------+   +----v-----------+
|                   |                            | MCP server A  |   | MCP server B   |
|                   |  read-only state API       | "graph"       |   | "minecraft"    |
|                   | <--------------------------+ (not here)    |   | live state     |
|                   |            (see below)     +--------+------+   +----+-----------+
+-------------------+                                     |               |
        ^                                          +------v------+        |
        |  events (later)                          |   Neo4j     |        |
        +---------------------------------------->|  knowledge  |<-------+ (via plugin API)
           ingest path (not MCP)                   |  layer      |
                                                   +-------------+
```

(The state-API arrow is drawn loosely; see "How server B reaches the game" below.)

| Component | Language | Responsibility | Talks to |
|---|---|---|---|
| **Plugin** | Java (Paper) | `/agent` command; send question + player id; show the reply; later: emit events, answer state queries | Agent service |
| **Agent service** | Python | LLM loop, system prompt, session handling, decision traces; offers `ask` (plain HTTP and/or as an MCP tool); calls tools via MCP | Plugin, MCP A, MCP B, Neo4j (memory) |
| **MCP server A: graph** | Python | Read-only tools over the knowledge graph | Neo4j |
| **MCP server B: minecraft** | Python | Read-only tools for live server state | Plugin (or server) |
| **Ingest service** (later) | Python | Receives events, updates the graph | Neo4j |
| **Neo4j** | | Knowledge layer: domain, lexical, entity, agent-memory graphs | |

Python for everything outside the plugin: it matches the loader and the main agent and memory libraries.

### MCP server A: graph tools

Purpose-built tools, not raw Cypher. They carry the knowledge-layer logic, so the agent stays light and answers stay consistent.

Examples (names provisional): `get_recipe(item)`, `what_do_i_need(item)` (supply chain to raw materials), `ways_to_make(item)`, `class_members(class)`, `where_is_it_made(item)`, `player_history(player)`.

- Each tool states the AND/OR rule in its output (recipe edges are AND; an `ItemClass` means any one member except `excludes`), so the model cannot misread the graph.
- Read-only by construction: the Neo4j user for this server has read permission only.
- Worth checking whether Neo4j's own MCP server covers a generic "run a read query" tool for exploration. I would keep that separate from the product tools above.

### MCP server B: live Minecraft state

Read-only tools: `online_players`, `player_location(player)`, `player_inventory(player)`, `nearby_blocks`, `block_at`, `list_chests_near(player)` and so on. Deliberately separate from A because:

- different trust boundary: it touches the live game, and a future write or action tool belongs behind its own permission;
- different failure mode: the game server can be down while the graph is fine;
- different deployment: it needs a path into the Paper server.

It can start as a second tool group inside the same process as A if that is quicker, as long as the code is split so it can move out.

### How server B reaches the game

The plugin is the only thing with in-process access to world state. Options:

| Option | How | Pros | Cons |
|---|---|---|---|
| **1. Plugin exposes a small read-only API** (localhost) | B calls it | Simple. Full state access. | Plugin is a server as well as a client. Inbound port on the game host. |
| **2. Plugin keeps an outbound connection** (WebSocket) to the agent side; B sends requests down it | | No inbound port. Same channel can carry events. | More moving parts. Needs reconnect handling. |
| 3. RCON | B sends console commands | No plugin work | Text-only output, limited, awkward to parse. |
| 4. Bot player (e.g. mineflayer) | A fake player joins | No plugin work | Limited view, breaks the "thin plugin" plan. |

Recommendation: **start with option 1 (localhost HTTP API, read-only, behind a shared key)** when server B is built, and move to option 2 if the services end up on a different host. Both are small and the plugin stays thin: it only reads Bukkit state on the main thread and returns JSON.

## The ask path (query)

1. Player types `/agent how do I make an iron pickaxe?`.
2. The plugin replies at once ("thinking...") and hands the work to an async task. It must **not** block the main server thread.
3. The plugin POSTs `{playerUuid, playerName, question, sessionId}` to the agent service.
4. The agent runs its tool loop: graph tools (A), live-state tools (B) if needed.
5. The agent writes a decision trace (question, tools called, sources) to the graph, then returns the answer.
6. The plugin sends the answer to the player, split into short lines that read well in chat.

Things to design in: a timeout and a friendly failure message; a per-player rate limit; a `minecorp.agent` permission; answers short enough for chat (the agent prompt should know it is writing for Minecraft chat).

## Future question: capturing events into the graph

**Yes, it adds a second path, but it does not change the shape above.** Today every arrow starts at the player. Events start at the game, run the other way, and are not agent-initiated, so they do not go through MCP.

```
 Paper events --> plugin (filter + batch) --HTTPS--> Ingest service --> Neo4j
```

What the plugin does: listen for a **whitelist** of Bukkit events (craft, furnace output, join/quit, perhaps block placement and container changes), then filter, aggregate and batch them on an async queue and send the batch out. It does not interpret anything.

What the ingest service does: validate, map events to the ontology and update the graph.

Design consequences:

- **Not a firehose.** Raw block-level events would swamp the graph. Aggregate in the plugin (counts per player per interval) and store summaries or milestones, not every event. This matches "summary in the graph, specifics from the server".
- **Closes open questions in the ontology.** Detecting `(Player)-[:COMPLETED]->(Task)` automatically (for example from a craft or smelt event matching a `Task`'s recipe) is the natural first event use.
- **Idempotent writes.** Events carry an id; the ingest uses `MERGE` so a retried batch does no harm. Delivery is at-least-once.
- **Buffering.** The plugin keeps a bounded in-memory queue and drops or coalesces events when the ingest service is down. The game must never lag or fail because the agent side is away.
- **One channel or two.** If option 2 above (outbound WebSocket) is chosen for server B, the same connection can carry events, which argues for it. This is the only place where events and the architecture interact in a way that should be decided early.
- **Ingest versus agent write access.** Keep the agent read-only. Graph updates from events come from the ingest service with its own, narrow write permission.
- **Schema.** Event handling will extend the ontology (player stats, facility or station records, task completions). Do that in `docs/ontology.md` first.

## Suggested build order

1. **End-to-end stub (next):** plugin `/agent <question>` POSTs over HTTP to a tiny agent service that returns a canned reply; plugin shows it. This proves the plugin, async handling, auth and message formatting before any LLM is involved.
2. **Agent plus MCP A:** real LLM loop with the graph tools; answer recipe and supply-chain questions.
3. **Decision traces and sessions** in the graph.
4. **MCP B plus the plugin's read-only API:** live-state questions ("what do I have, what am I missing?").
5. **Events and ingest:** task completion first.
6. **Lexical and entity graphs** (minecraft.wiki advice) as a further tool group on A.
7. **Agent memory** (Neo4j Agent Memory) for per-player context.

## Decisions

| # | Topic | Decision (2026-10-02) |
|---|---|---|
| 1 | Agent service | Its own process. Reached from the plugin by **plain HTTP**. Exposing it as an MCP server (`ask`) stays possible later because the core function is kept transport-neutral. |
| 2 | Graph tools | **Purpose-built tools** per action (`get_recipe`, `what_do_i_need`, ...), not generic Cypher. |
| 3 | First stub transport | Plain HTTP POST from the plugin (follows from 1). |
| 4 | How server B reaches the game | **Revisit later** (plugin read-only localhost API or outbound WebSocket). Not needed until live-state tools are built. |
| 5 | Where services run | Locally on the user's machine for now (Docker Compose or plain processes next to Neo4j). |
| 6 | LLM and framework | **OpenAI model, no agent framework** for the time being: a hand-written tool-calling loop. The model id is chosen at build time and kept in configuration. |
| 7 | Auth between plugin and services | Shared secret in config, services bound to localhost. |

Consequences worth noting:

- **Hand-written loop.** The agent service is a small Python program: call the model, run any tool calls against the tools, feed results back, repeat until a final answer. It needs a turn limit, per-call timeouts and a log of each step, since that log is the decision trace.
- **Tool schema.** Each graph tool is defined once (name, description, parameters, function) and that single definition is used both to describe it to the model and to run it. Keeping that definition independent of the OpenAI API format means the tools can later be served as an MCP server unchanged.
- **Secrets.** An OpenAI API key is needed in `.env` (git-ignored). It never goes in the plugin.
- **Where MCP now fits.** MCP is deferred, not dropped: graph tools (A) and the Minecraft tools (B) can be plain Python functions inside the agent service at first, and be split into MCP servers when there is a reason (reuse from Claude Desktop, a separate trust boundary for B).

## Change log

- **v0.2** (2026-10-02): decisions 1-7 agreed (plain HTTP, purpose-built tools, local, OpenAI with no framework). The "Decisions for you" table is replaced by a Decisions table.
- **v0.1.1** (2026-10-02): corrected the MCP section. An MCP server *can* host an agent as a single tool (`ask`); the agent can be an MCP server northbound and a client southbound. Decisions 1 and 3 updated.
- **v0.1 PROPOSAL** (2026-10-02): first draft.
