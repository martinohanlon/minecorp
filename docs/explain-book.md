# The explain book: showing a player where an answer came from

2026-10-10. The player can ask how an answer was made and is given a book to read. This page is the contract between the agent service, the chat stub and the Minecraft plugin; the stub's `/explain` is the working reference for the plugin.

## What the owner decided

- The command is **`/agent-explain`**. It is not `/agent why`, because `/agent <words>` is a question and "why" would read as one.
- The agent's answers do **not** have to say where they came from ("according to the wiki" was dropped). The book is where that is shown.
- The book shows **sources only**: not the tool calls, their arguments, timings or tokens. Those stay in the developer's `/trace` in the chat stub.
- No unprompted footer in the answer.
- **The book is opened, not given**: no item, nothing to carry or drop. (The first proposal also gave a copy; the owner chose open only.)
- The book holds the **last five** questions, answers and sources. The **agent service keeps them**, per player, **in memory** for now (not the plugin): it already knows the player, it is where neo4j-agent-memory will live, and the page layout is then written once. The plugin only asks for the book and draws it. `History` (`agent/minecorp_agent/history.py`) is the one place to replace when the memory comes. (The first version kept the history in the client; the owner pointed out the service should.)

## Sources

Each tool result is turned into sources by code (`agent/minecorp_agent/sources.py`), never by the model, so a source is something a tool really returned. The `POST /ask` reply carries them:

```json
{"requestId": "abc123def456", "answer": "...", "sources": [
  {"kind": "plan", "label": "Get to the Nether (draft, not confirmed)"},
  {"kind": "wiki", "label": "Tutorial:Shelters > Nether portal room", "url": "https://minecraft.wiki/w/Tutorial:Shelters#Nether_portal_room"},
  {"kind": "game data", "label": "Recipes for Iron Pickaxe"}]}
```

| kind | what it is | label | link |
|---|---|---|---|
| `game data` | read from the server's own files, authoritative | "Recipes for Iron Pickaxe", "How to get Emerald", "Where to find Desert", "About Creeper", "What counts as Planks", "What is needed to make Torch", "What Diamond is used for", "Recipe difficulty ranking" | none |
| `wiki` | community-written text from the Minecraft Wiki | the page and section, "Tutorial:Shelters > Hole in the wall" | the section's address |
| `plan` | a plan drafted from the wiki, not yet confirmed by the player | the plan's name, with "(draft, not confirmed)" until confirmed | none; the wiki pages it came from follow as `wiki` sources |

Each source appears once, in the order the agent looked things up, at most 8. A search by name and the list of plans are not sources; a lookup that failed is not a source. An answer with no sources (a greeting, an off-topic question) has an empty list. A source's `url` is only ever an `http://` or `https://` address (the game ignores any other kind of link), and the stub and plugin drop anything else. The plugin ignores fields it does not know, so the older plugin keeps working with the new reply.

## Asking for the book

`POST /explain` (header `X-MineCorp-Key`, like `/ask`), body `{"playerUuid": "..."}`. The service answers with the book as plain data, and `pages` is empty when the player has asked nothing yet (the plugin then says so):

```json
{"title": "Agent notes", "author": "MineCorp Agent", "pages": [
  {"lines": [{"text": "Agent notes"}, {"text": ""}, {"text": "Last 1 question(s):"}, {"text": "1. How do I get to the Nether?", "goto": 2}]},
  {"lines": [{"text": "1. How do I get to the Nether?"}, {"text": ""}, {"text": "- Build and light a Nether portal..."}]},
  {"lines": [{"text": "Sources for 1:"}, {"text": ""}, {"text": "- wiki: Tutorial:Nether portals", "url": "https://minecraft.wiki/w/Tutorial:Nether_portals"}]}]}
```

A line is one line of text. `url` makes it a link to open, and `goto` makes it jump to that page (counted from 1). Only a good answer is remembered: a failure or a run that hit its turn limit is not. Asking for the book is not itself remembered. Players only ever see their own: the book is built from the uuid in the request, and the plugin is the only caller (it holds the shared secret).

## The pages

Title "Agent notes", author "MineCorp Agent". Pages are text of at most **700 characters** (a written book holds about 1,024), split between lines, or at a space inside a very long line:

1. **Contents:** `Agent notes`, a blank line, `Last N question(s):`, then `1. <question, cut to 40 characters and "..." if longer>` for each, oldest first.
2. For each exchange, oldest first:
   - the **answer pages**: `<n>. <the question in full>`, a blank line, then the answer's lines;
   - the **sources pages**: `Sources for <n>:`, a blank line, then one line per source, `- <kind>: <label>`; or `No sources: I answered without looking anything up.`

In the game the contents lines jump to their page (a `change_page` click event) and a line with a `url` is a clickable link (an `open_url` click event; the game normally asks the player before opening a link). In the stub, `/explain` prints the pages with `[jumps to page n]` after a jump and the link on its own line, since a terminal cannot click.

## Where it lives

| piece | where | status |
|---|---|---|
| sources from each tool result | `agent/minecorp_agent/sources.py`, collected on each `Step` and the `Result` | built |
| the last five per player | `agent/minecorp_agent/history.py` (`History`, `Exchange`), filled by `core.answer_with_sources` | built |
| the pages | `agent/minecorp_agent/book.py` (`build_book`), returned by `core.explain` and `POST /explain` | built |
| the stub | `scripts/chat.py`: `Chat.explain()` asks the service (or calls `core.explain` with `--direct`), `/explain` shows the pages | built |
| the book in Minecraft | the plugin: `/agent-explain` (permission `minecorp.explain`) posts the player's uuid to `/explain` and opens the book with `Audience#openBook`, drawing `url` as `open_url` and `goto` as `change_page` | next |

The developer's trace (`/trace`, `agent/logs/traces.jsonl`) also records each step's sources now.

A later benefit of the service owning the history: it could give the model the previous exchange, so a follow-up such as "and for stone?" makes sense. That is a separate change.
