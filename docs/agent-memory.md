# Agent memory

The agent can keep what players ask, and how each answer was reached, in Neo4j with [neo4j-agent-memory](https://github.com/neo4j-labs/agent-memory) (Neo4j Labs; the Python SDK, version 0.7). It is **optional and off by default**: with `AGENT_MEMORY=local` the history of each player's last five answers lives in the agent process only and is lost when it restarts.

Sources: the [Python SDK page](https://neo4j.com/labs/agent-memory/sdks/python/), the [repository](https://github.com/neo4j-labs/agent-memory) and the package itself (v0.7.0, read for this design). The package is async only and needs Python 3.10+ and Neo4j 5.26+.

## Conversation context: the agent remembers what was just said

Each question used to be a conversation of one. Now the model is sent the player's recent exchanges ahead of the new question, as earlier chat turns (`agent/minecorp_agent/conversation.py`):

```
system:    SYSTEM_PROMPT (with a line saying the earlier turns are context only)
user:      Player Steve asks: how do I make an eye of ender?
assistant: - Crafting Table: 1 blaze powder + 1 ender pearl ...
user:      Player Steve asks: and how many of each for 12 of them?        <- the new question
```

- **Does not need agent memory.** The exchanges come from the in-process history, which every answer fills. Memory (`AGENT_MEMORY=neo4j`) only adds that a conversation survives a restart: when the history has nothing for a player, it is refilled from memory (the same read that rebuilds the explain book).
- **Depth:** the last `AGENT_CONVERSATION_TURNS` exchanges (default 3; 0 turns it off, 10 at most).
- **When a conversation ends:** after `AGENT_CONVERSATION_IDLE_MINUTES` of quiet (default 15). The test is on the gap between consecutive exchanges, so a long afternoon of questions each a few minutes apart is one conversation and a question the next morning is not part of it. There is no command for a fresh start; the timeout is enough (owner's decision).
- **Only text is replayed**: the questions and the answers the player saw, not the tool results or sources. A follow-up that needs facts calls the tools again, so answers stay grounded in the graph. An earlier answer's numbers may be reused ("12 of them" is arithmetic on what was said); anything new is looked up.
- **Only good answers** are part of the conversation (the same ones the explain book keeps); a run that failed or hit its turn limit is not.
- **Never costs an answer:** if the history or memory cannot be read, the question is answered with no context. A wrong setting is logged once and the defaults are used.
- **Visible:** the trace (`agent/logs/traces.jsonl`, `contextTurns`) and the memory trace (`context_turns`) say how many earlier exchanges each run was sent. Cost: roughly 300 tokens an exchange.
- **How it is tested:** unit tests for the rules (`tests/agent/test_conversation.py`), and three evals with a canned conversation (`followup-count`, `followup-other-item`, `followup-stale`; `"history"` in `agent/evals/questions.json`). Without the context the first two fail ("I need to know what 'them' refers to") and with it they pass; the third checks that an unrelated question is not bent by an old one.

## What it gives us, and what it holds

neo4j-agent-memory has three kinds of memory. MineCorp uses two now and has a switch for the third.

| Memory | Used for | Stored as | Setting |
|---|---|---|---|
| **Short-term** | each player's questions and answers; the explain book is rebuilt from it | `(:Conversation {session_id: <player uuid>})-[:HAS_MESSAGE]->(:Message {role, content, metadata})`, chained with `NEXT_MESSAGE`. The answer's **sources are in its metadata as JSON** | always, when memory is on |
| **Reasoning** | the decision trace the knowledge-layer design asks for: what was asked, which tools ran, whether it worked | `(:User)-[:HAS_TRACE]->(:ReasoningTrace {task, success, outcome})-[:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->(:ToolCall)-[:INSTANCE_OF]->(:Tool)`, and `(:ReasoningTrace)-[:INITIATED_BY]->(:Message)` | `AGENT_MEMORY_TRACES` (on) |
| **Long-term** | entities and preferences an LLM finds in what players say | entities, linked to the messages they came from | `AGENT_MEMORY_EXTRACT` (off) |

A trace is kept for **every run**, including one that failed or hit its turn limit (`success: false`); the conversation only gets exchanges that were answered well, because that is what the book shows. A trace stores the size of each tool result and the labels of its sources, not the result itself.

## Turning it on

In `.env` (all are in `.env.example`):

| Setting | Default | Meaning |
|---|---|---|
| `AGENT_MEMORY` | `local` | `local`: no neo4j-agent-memory. `neo4j`: use it. |
| `AGENT_MEMORY_DATABASE` | the game graph's own (`NEO4J_DATABASE`) | where memory is written; see below |
| `AGENT_MEMORY_TRACES` | `true` | write a reasoning trace for every run |
| `AGENT_MEMORY_EMBED` | `false` | embed messages and traces with OpenAI so memory can be searched by meaning (a fraction of a cent per answer; needs `OPENAI_API_KEY`) |
| `AGENT_MEMORY_EXTRACT` | `false` | long-term memory: an LLM extracts entities from what players say (an extra model call per answer; needs `OPENAI_API_KEY`) |

It uses the same Neo4j server, user and password as the game graph (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`). A wrong or missing setting is logged once at start-up (`agent memory is OFF because of a setting: ...`) and the agent carries on with local history; the service log says which mode it is in.

### Same database or a separate one

By default memory is written **into the same database as the game graph**. That is what lets long-term memory link to the nodes already there (an entity a player talked about to the `Item` or `Mob` of the same name, the way the wiki links work). The cost: **`build-graph` wipes the whole database, memory included**. This was decided with the owner: do not worry about clearing memory on a rebuild.

`AGENT_MEMORY_DATABASE=minecorpmemory` puts memory in a database of its own (created if missing; needs Neo4j Enterprise). It survives a rebuild but cannot be linked to the game graph. The library's labels (`Conversation`, `Message`, `ReasoningTrace`, `ReasoningStep`, `ToolCall`, `Tool`, `User`) do not clash with the game graph's; a test runs memory inside the fully built graph to prove it.

## How it works

```
answer                       the in-process History (always)     the book is built from this
  |
  +-- record() -------------> background thread, own asyncio loop --> neo4j-agent-memory --> Neo4j
                              (never waited for; failures are logged)

book needed, History empty ----> memory.recent(player) --> History is refilled, then the book is built
(after a restart)
```

- `agent/minecorp_agent/history.py` stays the always-on cache. The explain book never waits for Neo4j.
- `agent/minecorp_agent/memory.py` is the whole integration: the settings (`options_from_env`), what is stored and how it is read back, and `AgentMemory`, which runs the async library on a thread of its own (the service is synchronous) and keeps one player's writes in order.
- **Memory never gets in the way of an answer.** A write is queued and not waited for; Neo4j being down is logged and tried again a minute later; at most 100 writes may be waiting (more are dropped and logged); a read gives up after 8 seconds and shows what the cache has.
- On shutdown the service calls `close_memory()` so pending writes finish. A process that uses the core directly must call it before exiting (the chat stub does with `--direct`): it cannot be left to `atexit`, because by then Python has shut down the thread pool asyncio needs to reach the database.

### One place we go around the library

The library's `get_conversation(limit=n)` returns the **oldest** n messages (and at most 1,000), which would show a returning player their first five answers, not their last five. `memory.py` therefore reads the newest messages with one query of its own through the library's connection (`LATEST_MESSAGES`). It depends on the library's storage layout, and `tests/agent/test_memory_graph.py` fails if that changes. If a later version offers "latest n", use it.

## Not built yet

- **Matching the memory `User` node with the model's `Player` node** (owner's todo, 2026-10-16). The library creates a `(:User)` for each player, identified by the player's uuid, and the company model has `(:Player {uuid, name})`, which `CONFIRMED_BY` points at. They should be the same identity or linked on uuid, so a player's conversations, traces and confirmed processes can be followed together. Open: the properties the library puts on `User`, and players who are not in `model/players.yaml`.

- **A `recall` tool, and long-term memory in answers** (agreed to wait, 2026-10-16). Conversation context covers the current conversation. Looking further back ("what did I ask about shelters last week?") would be a read-only `recall(query)` tool that searches the player's own messages and traces by meaning (needs `AGENT_MEMORY_EMBED`); it must take the player from the request, never from an argument the model supplies. Preferences and entities from `AGENT_MEMORY_EXTRACT` would come in through the library's `get_context(...)`.
- **Linking long-term entities to the game graph** (`(:Entity)-[:SAME_AS]->(:Item)`): a job like `link_wiki.py`, once extraction has produced entities worth linking.
- **Memory of its own for the plan steps a player has done** (progress through a process, confirming a candidate): the model has `CONFIRMED_BY` ready for it.

## Tests

`tests/agent/test_memory_unit.py` (settings, what is stored and read back, the writer, Neo4j being down), `test_memory_core.py` (the switch, what the core records, rebuilding the book, shutdown) and `test_memory_graph.py` (the real library in throwaway databases, including inside the fully built game graph). None of them call OpenAI.
