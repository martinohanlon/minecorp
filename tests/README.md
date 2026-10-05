# Tests

One command runs them: `./scripts/test.ps1` (fast set) or `./scripts/test.ps1 -All` (everything, about a minute).

| Layer | Where | Tool | What it needs | Time | Count |
|---|---|---|---|---|---|
| **Loader unit tests** | `tests/loader/test_load_recipes.py`, `test_load_actions.py`, `test_load_model.py` | pytest | nothing (synthetic data; one test builds a tiny jar in a temp folder) | under 1 s | 125 |
| **Wiki loader unit tests** | `tests/loader/test_load_wiki.py` | pytest | nothing (word-count tokenizer, fake embedder, mock HTTP) | about 1 s | 193 |
| **Link job unit tests** | `tests/loader/test_link_wiki.py` | pytest | nothing | under 1 s | 59 |
| **Real jar tests** | `tests/loader/test_real_jar.py` (marker `jar`) | pytest | `server/versions/26.2/paper-26.2.jar` (skipped if absent) | under 1 s | 37 |
| **Agent unit tests** | `tests/agent/test_tools_unit.py`, `test_wiki_unit.py`, `test_agent_loop.py`, `test_config.py`, `test_app.py` | pytest | nothing (fake model, fake tools, scripted fake graph, FastAPI test client) | about 2 s | 212 |
| **Agent tool tests** | `tests/agent/test_tools_graph.py` (marker `integration`) | pytest | Neo4j running with Enterprise Edition, plus the jar | about 25 s | 124 |
| **Agent wiki tool tests** | `tests/agent/test_wiki_graph.py` (marker `integration`) | pytest | Neo4j running with Enterprise Edition (no jar, no OpenAI) | about 6 s | 40 |
| **Graph integration** | `tests/loader/test_graph_integration.py` (marker `integration`) | pytest | Neo4j running with Enterprise Edition, plus the jar | about 20 s | 34 |
| **Wiki graph tests** | `tests/loader/test_wiki_integration.py` (marker `integration`) | pytest | Neo4j running with Enterprise Edition (no jar, no OpenAI) | about 10 s | 37 |
| **Link job graph tests** | `tests/loader/test_link_wiki_integration.py` (marker `integration`) | pytest | Neo4j running with Enterprise Edition (no jar, no OpenAI) | about 20 s | 89 |
| **Plugin unit tests** | `plugin/src/test/java/com/minecorp/plugin/` | JUnit 6 via Maven | JDK 25, Maven | about 5 s | 37 |
| **Plugin end to end** | `tests/plugin/test_plugin_e2e.py` (marker `e2e`) | pytest | JDK 25, Maven, `server/` set up once | about 25 s | 12 |

Counts as of 2026-10-05: 999 tests (962 Python, 37 Java). The fast set is 626 Python tests plus the Java ones.

## Running

```powershell
./scripts/test.ps1                 # loader and agent unit tests, real jar tests, plugin JUnit tests
./scripts/test.ps1 -Integration    # also rebuild the graph in a throwaway database and check it
./scripts/test.ps1 -E2E            # also start a real Paper server with the plugin
./scripts/test.ps1 -All
./scripts/test.ps1 -PythonOnly     # skip Maven
```

Or by hand: `.venv\Scripts\python -m pytest` (fast set; `pytest.ini` leaves out `integration` and `e2e`), `pytest -m integration`, `pytest -m e2e`, `pytest -m ""` for all Python tests, and `mvn -B test` in `plugin/`. Install the test tools with `pip install -r requirements-dev.txt`.

## What each layer protects

**Loader unit tests** check the rules in the loaders against small made-up data: recipe parsing (shaped counts, shapeless repeats, stations, special recipes), how an alternative list becomes a real tag plus an exclusion, category naming and its collision rule, tool tiers and the "required" rule, loot conditions, rolls and negative ranges, loot tables that point at other tables (sheep colours), the action rows that come out of a jar, and every validation error in the YAML model (with the real `model/` files checked too).

**Wiki loader tests** cover the lexical graph loader (`load_wiki.py`) with no network, no OpenAI and no database. A mock transport stands in for the wiki API and a fake embedder for OpenAI, and a word-count tokenizer makes chunk sizes easy to work out by hand. They check how page HTML becomes a section tree (headings and anchors, paths, noise such as edit links, references and recipe pictures left out, lists and tables as lines), the chunk rules (never longer than the size, cut at sentence boundaries, overlap of whole sentences and then words, never across a heading, one chunk for a small section), ids and hashes, and the embedding cache: a second run or a rebuild makes no calls, only new text is sent, and a failure part-way keeps what was already paid for. **Wiki graph tests** write a page into their own throwaway database (`minecorpwikitest`) and check the tree, `PART_OF`, a single `NEXT` chain, constraints, the vector index and a real vector search, reloading a page, a second page, and that `--wipe` leaves the game graph alone.

**Link job tests** cover `link_wiki.py`, which links wiki pages and chunks to the game graph. Most of the job is Cypher, so most of its tests are graph tests: a small game graph (with `minecraft:chicken` as both an item and a mob, and the torch as both an item and a block) and wiki pages loaded with the real loader are put in their own throwaway database, the job is run, and the assertions are about the graph. They check what a page title is about (exact beating plural, a category and an item of one name, an ambiguous title linked to every candidate with its flag, fixes from the config), how a link becomes a mention by following `LINKS_TO`, `ABOUT` and at most one `REDIRECTS_TO` (the page as written beating the page it redirects to, a redirect followed only when the linked page is about nothing), names in the text (longest first, whole words, never singular forms, stoplist and aliases), the two kinds of evidence merged with their counts and wordings, corroboration by the same page, and the run itself: a dry run that rolls back, one transaction that keeps the old links when it fails, links made by hand surviving, a bad config writing nothing, the wiki and game graphs left unchanged. The unit tests cover only what is Python: the written forms of names, the scan of chunk text, the config file, and the report text.

**Real jar tests** check what must hold for any Minecraft version (every recipe parses, every reference resolves, action ids are unique, tier lists are consistent) and a set of known vanilla facts that act as canaries when the server version changes: iron ore needs a stone pickaxe, diamond ore an iron pickaxe, obsidian a diamond pickaxe, spiders drop string, blazes drop rods only when a player kills them, sheep can be sheared for wool. If one fails after an upgrade, check the game first.

**Graph integration tests** rebuild everything from source in the documented order (`load_recipes.py --all --wipe`, `load_actions.py`, `load_model.py`, `compute_routes.py`) in a database called `minecorptest`, which is created at the start and dropped at the end. Your real database is never touched. They check the shape of the result (counts match what was built, no `Harvest` left, stations are items, gathering actions have one target, constraints exist), facts about specific nodes, the authored model in the graph (the one player fills every role, task actions are numbered 1..n, handoffs exist exactly where the role changes, the default path), and the rebuild contract: loading the model twice gives the same graph, and reloading the actions drops the task links until the model is loaded again.

**Agent unit tests** run without a model or a database. The loop is driven by a scripted fake model, so they check what the code does with each kind of reply: tool calls and results, a tool that raises, results cut at the size cap, the turn limit (the last turn forbids tools and a fallback answer is used), model failures, and the trace written for each request. They also cover the tool registry and the specs sent to the model, how recipes and routes are written as text, `.env` and settings handling, and the HTTP layer (the secret is checked before the body, exact match only, field limits, one request id per call).

**Agent wiki tool tests** (`search_wiki` and `read_section`). The unit tests use a scripted stand-in for the graph and check what the tools do with the rows: passages from one section are merged and the overlap between chunks is not repeated (stitching the loader's chunks gives the section text back exactly), best hit first, limits clamped, the embedding-model mismatch and the "wiki not loaded" messages, ambiguous section titles, and neighbours fetched only when asked. The graph tests load two pages into a throwaway database with fake embeddings and run the real queries (the `SEARCH` vector clause, the walk to the section path, the tree, `NEXT` neighbours that never cross pages). The mentions on each passage are checked both ways: the unit tests with scripted rows (kind from the node's labels, linked and ambiguous flags, every candidate listed, merged per passage, capped), and the graph tests with the real link job run over the loaded pages. Whether real OpenAI embeddings rank well, and whether the model picks these tools and follows a mention to the game-data tools, is covered by the wiki questions in the evals.

**Agent tool tests** call every tool directly against the real graph in the same throwaway database, with no model involved. They check name resolution and ambiguity, recipe shapes (grid, categories, exclusions, items nobody crafts), limit clamping, the wording and ordering of `how_to_get`, and `what_do_i_need`: the exact iron pickaxe route, quantities multiplied through, raw iron rather than the silk-touch-only ore, no loops through nuggets and blocks, and, for 47 different items, that every step only uses things gathered or made earlier and the answer stays small. They also check the stored route numbers against a fresh calculation. Whether the *model* picks the right tool is not tested here; that is what the evals are for.

**Plugin unit tests** cover the pieces that hold the rules, without a Minecraft server:
- `AskGuard`: empty question, 500-character limit (exactly 500 is allowed), not configured, one question at a time per sender, the order the rules are checked in, and a concurrency test (only one of 16 simultaneous attempts is accepted).
- `AgentClient`: against a local HTTP server, the request (method, path, secret header, JSON body, escaping), the answer, trailing slashes, every failure (non-200, not JSON, no answer, answer not a string, empty body, timeout, nothing listening).
- `Replies`: how an answer splits into chat lines.
- `PluginDescriptorTest`: `plugin.yml` and `config.yml` as packaged (main class exists, version filled in, `api-version` matches the Paper API, the `/agent` command and its permission, no secret shipped).

**Plugin end to end** copies the server files from `server/` into `.test-server/` (first run only, git-ignored), builds the plugin, starts Paper on a free port with a stub agent service, and types commands on the console. It checks both what the player sees and what the agent received: usage message, a question and its answer, the request body and secret header, multi-line answers, the length limit, an agent error, a reply that is not JSON, a second question while one is running, a timeout followed by a new question, an unreachable agent (and that the server stays responsive), and a clean shutdown. Your real server can keep running.

## What is not covered

- Whether the model chooses the right tool and phrases a good answer. That is the job of the evals (`cd agent; python -m evals.run_evals`, which calls the model and costs tokens); the get-iron question is known to be flaky because the model sometimes asks which kind of iron.
- The plugin is tested from the console. A real player's chat and the way a Player (not the console) is identified are unit-covered through `AskGuard` and not run in a live game.
- `compute_routes.py` is covered through the integration tests, not on its own.

## Checking that the tests can fail

When the tests were written, the loaders and the plugin were broken on purpose in a copy (28 deliberate faults, for example "shaped recipes ignore the pattern", "the length limit is exclusive", "the secret header has the wrong name"), and every one made a test fail. The agent tests were not checked this way; writing them did find one real bug (the agent validated the body before checking the secret, now fixed). After a big change it is worth repeating the exercise for the code you touched.

## Adding tests

- A new loader rule: add a unit test with small made-up data next to the existing ones, and a fact to `test_real_jar.py` if it should hold for the real jar.
- A new or changed agent tool: add a case to `tests/agent/test_tools_graph.py` (and to `test_tools_unit.py` if it formats text), and an eval question if the model must choose it.
- A new graph shape: add an assertion to `test_graph_integration.py`.
- Tests that need the database or a server must carry the `integration` or `e2e` marker, so the fast run stays fast.
