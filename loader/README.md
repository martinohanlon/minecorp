# Recipe loader (draft)

Loads Minecraft recipes (a subset by default, or everything with `--all`) from the Paper server jar into Neo4j, following [docs/ontology.md](../docs/ontology.md).

## Run

```powershell
# 1. Neo4j (needs Docker)
copy .env.example .env        # then set NEO4J_PASSWORD
docker compose up -d

# 2. Loader
python -m venv .venv
.venv\Scripts\pip install -r loader\requirements.txt

# parse + report only (no database needed)
python loader\load_recipes.py --dry-run

# load (reads NEO4J_* from the environment; docker compose reads .env, Python does not)
$env:NEO4J_PASSWORD = "<same as .env>"
.venv\Scripts\python loader\load_recipes.py --wipe
```

Load everything (all recipes and item tags) with `--all --wipe`.

**Then load the actions** (the gathering actions Mine, Kill and Shear from block, mob and shearing loot tables, with their targets, tools and drops; and one thin Craft, Smelt, Smith or Cut action per item from the recipes; about 5 seconds). Run it after `load_recipes.py --all` and before `load_model.py`:

```powershell
.venv\Scripts\python loader\load_actions.py            # --dry-run to preview
```

**Then load the MineCorp model** (organization and processes, authored as YAML in [model/](../model/); about 2 seconds). It checks every recipe and harvest id against the graph and fails with a list of problems without writing anything; `--dry-run` only validates:

```powershell
.venv\Scripts\python loader\load_model.py
```

Edit `model/org.yaml` or `model/processes/*.yaml` and re-run; the YAML is the source of truth and the run is repeatable. `COMPLETED` history and players are left alone.

**After every full load, compute the route properties** (steps, chain length and base materials per craftable item; about 10 seconds). The agent's `rank_recipes` tool needs them, and a `--wipe` removes them:

```powershell
# reads NEO4J_* from .env itself (through the agent settings)
.venv\Scripts\python loader\compute_routes.py
```

Use `--dry-run` to see the numbers without writing.

Other options: `--seed <item id>` (repeatable), `--max-depth N`, `--out subset.json`.

The graph can be rebuilt from source at any time. Order for a full rebuild (about 30 seconds): `load_recipes.py --all --wipe`, `load_actions.py`, `load_model.py`, then `compute_routes.py`. Note that `load_recipes.py --wipe` removes **every** node, including the wiki graph below; reload it last with `load_wiki.py` (free when the embedding cache is intact), then run `link_wiki.py`.

## Wiki loader: a lexical graph of minecraft.wiki (v0.8)

`load_wiki.py` turns a wiki page into pages, a section tree and overlapping chunks with embeddings, in the same database. Design and rules: [docs/ontology.md](../docs/ontology.md) (v0.8).

```powershell
.venv\Scripts\pip install -r loader\requirements.txt

# fetch, split, chunk and report; no OpenAI, no database (shows the tree, chunk sizes, cost, cache hits)
.venv\Scripts\python loader\load_wiki.py --dry-run

# embed (cache first) and load; needs OPENAI_API_KEY and the NEO4J_* settings in .env
.venv\Scripts\python loader\load_wiki.py
.venv\Scripts\python loader\load_wiki.py --page "Tutorial:Beginner's guide" --size 300 --overlap 50 --wipe
```

| Option | Default | Meaning |
|---|---|---|
| `--page` | `Tutorial:Beginner's guide` | a page title or a minecraft.wiki URL |
| `--size`, `--overlap` | 300, 50 | tokens per chunk and tokens repeated from the previous chunk (tiktoken `cl100k_base`) |
| `--model` | `text-embedding-3-small` | OpenAI embedding model (1536 dimensions, cosine) |
| `--cache` | `loader/cache/embeddings` | the local embedding cache (a Python `shelve` file, git-ignored) |
| `--dry-run` | off | report only |
| `--wipe` | off | first remove every `Wiki`, `Page`, `Section` and `Chunk` node (nothing else) |

Loading a page again replaces that page's sections and chunks; other pages and the game graph are untouched. A chunk never crosses a heading, and a section that fits in `--size` tokens is one chunk. The embedded text is the heading path plus the chunk. Because vectors are cached by model and the hash of the exact embedded text, a rebuild makes no OpenAI calls for unchanged text, and changing `--size` only pays for the chunks that changed. Edit links, references, navigation boxes and recipe pictures are left out of the text. Fetching uses the MediaWiki API with a User-Agent that names this project (wiki text is CC BY-NC-SA).

Try it once loaded (vector search from a question's embedding, then walk to the section and the next chunk):

```cypher
MATCH (c:Chunk)-[:PART_OF]->(s:Section)<-[:HAS_SECTION|HAS_SUBSECTION*]-(:Page)
RETURN s.title, c.index, c.tokens, left(c.text, 80) ORDER BY c.index
```

The recipe loader is idempotent (it uses `MERGE`); `--wipe` deletes everything first.

## Linking the wiki to the game graph (v0.9)

`load_wiki.py` also keeps the wiki's own structure as graph: `(Chunk)-[:LINKS_TO {count, texts}]->(Page)` for each link, and `(Page)-[:REDIRECTS_TO]->(Page)` for a page that redirects (the Black Bed page redirects to Bed). A link goes to the page *as written*. A page that is only linked to is a stub `Page` (url and title; no `fetchedAt`, no sections); loading it later fills in the same node. The chunk text and the embedding hashes are not affected by links.

`link_wiki.py` then connects the wiki to the game graph. The work is Cypher in one transaction; Python only scans the text for names. It needs no OpenAI and replaces its own earlier links, so run it as often as you like (after `load_wiki.py` and the game-graph loaders):

```powershell
.venv\Scripts\python loader\link_wiki.py --dry-run     # does everything, prints the review report, rolls back
.venv\Scripts\python loader\link_wiki.py               # keeps the links
```

```
(Page)-[:ABOUT]->(node)        a page is about the nodes whose name is its title ("Torch" -> the item Torch)
(Chunk)-[:MENTIONS]->(node)    the chunk is about the node, by a link or by a name in the text (or both)
       node = Item (a category and a station are Items too) | Mob | Block that is not also an Item
```

A link counts by following a path: `(Chunk)-[:LINKS_TO]->(Page)-[:ABOUT]->(node)`, and if the linked page is about nothing, one `REDIRECTS_TO` step first. So a link to Black Bed means the item Black Bed (not the category Bed), and a link to Wood (a redirect to Logs) means the category. Where a title or a name can mean several nodes (the page Chicken is the mob and the item; dirt is the item and the category) there is a relationship to **each** of them, with `ambiguous: true` and `candidates: n`, and whatever reads the graph next decides from the context. The node a relationship points at says what it is, so no kind is stored on the relationship. `linkCount` is how many wiki links point at the node, `count` how often its name appears, `surfaces` the wordings, `corroborated` whether the same page links it anywhere; `status` is `candidate`, or `confirmed` for a fix from the config. A link is evidence; no link says nothing (editors link the first mention only), so nothing is dropped for lacking one.

The report lists what to review: ambiguous pages, linked pages that are about nothing in the game (Biome, Hunger...), and name-only mentions the page never links, which is where generic words show up. Put fixes in `model/wiki_links.yaml` (it explains `stoplist`, `aliases` and `pages`) and run again. The job's relationships carry `origin: 'link_wiki'`; relationships you add by hand with another origin are kept.

## Sample queries (Neo4j Browser at http://localhost:7474)

```cypher
// A browsable slice: iron pickaxe and what it needs
MATCH (:Item {id:'minecraft:iron_pickaxe'})<-[:CREATES]-(r:Recipe)-[c:REQUIRES]->(i) RETURN r, c, i;

// What does an iron pickaxe need? (OR for a class: pick any member)
MATCH (:Item {id:'minecraft:iron_pickaxe'})<-[:CREATES]-(r:Recipe)-[c:REQUIRES]->(i)
RETURN r.name, r.pattern, c.role, c.slot, c.count, i.name, i:ItemClass AS isClass, i.size;

// Brown bed: brown dye AND any bed except a brown one
MATCH (r:Recipe {id:'minecraft:dye_brown_bed'})-[c:REQUIRES]->(i)
RETURN i.name, i:ItemClass AS isClass, c.count, c.excludesNames;

// Every way to make an iron ingot, and where
MATCH (:Item {id:'minecraft:iron_ingot'})<-[:CREATES]-(r:Recipe)-[:USES_STATION]->(s)
RETURN r.name, r.type, s.name;

// The members of a class
MATCH (:ItemClass {id:'#minecraft:iron_tool_materials'})-[:INCLUDES*]->(i:Item) WHERE NOT i:ItemClass
RETURN i.name;

// All classes and how many items they cover
MATCH (c:ItemClass) RETURN c.name, c.id, c.size ORDER BY c.size DESC LIMIT 25;

// Tasks a player has completed
MATCH (t:Task)-[h:HAS_ACTION]->(a:Action) RETURN t.name, h.order, a.name, h.quantity ORDER BY t.name, h.order;

// What a mining action needs and yields
MATCH (a:Mine {id:'mine:minecraft:iron_ore'})-[:REQUIRES]->(x) MATCH (a)-[c:CREATES]->(i) RETURN a.name, labels(x)[0] AS needs, x.name, collect(i.name + ' [' + c.condition + ']') AS yields;

// Every way to get an item: actions and recipes meet at the item
MATCH (x)-[:CREATES]->(:Item {id:'minecraft:iron_ingot'}) RETURN labels(x) AS kind, x.name;

// Tasks a player has completed
MATCH (p:Player)-[c:COMPLETED]->(t:Task) RETURN p.name, t.name, c.at;
```

## Why Python

The neo4j driver is first-class, `zipfile` and `json` in the standard library read the jar directly, and the same language fits the later agent service (Neo4j Agent Memory and the major agent frameworks are Python-first). The plugin that talks to the server stays Java (Paper API).

## Draft status

- Full graph loaded into Neo4j 2026.09.0 (2026-10-02) following ontology v0.4: 1,571 Item (234 of them ItemClass), 1,585 Recipe, 7 Station, plus 3 sample Tasks and a test Player.
- Subset logic follows only recipes that *produce* an item. It does not yet include uses of an item (the forward direction).
- Special recipes (banner duplicate, fireworks...) have no inputs and are flagged `special`.

## Tests

`./scripts/test.ps1` runs the loader tests (125 unit tests, 37 against the real jar), and `-Integration` also rebuilds the whole graph in a throwaway database and checks it. See [../tests/README.md](../tests/README.md).

| File (in `tests/loader/`) | Needs | What it checks |
|---|---|---|
| `test_load_recipes.py` (43) | nothing | recipe parsing: shaped counts, shapeless repeats, stations, special recipes, alternative lists becoming a tag plus an exclusion, category naming and collisions |
| `test_load_actions.py` (40) | nothing (builds a tiny jar in a temp folder) | tool tiers and the "required" rule, loot conditions, rolls and negative ranges, loot tables that point at other tables, the action rows that come out |
| `test_load_model.py` (42) | nothing | every validation error in the YAML model, plus the real `model/` files |
| `test_real_jar.py` (37, marker `jar`) | the Paper jar | rules that hold for any version, and known vanilla facts that act as canaries when the server version changes |
| `test_load_wiki.py` (193) | nothing (word-count tokenizer, fake embedder, mock HTTP) | the wiki loader: titles and URLs, the API request and errors, HTML to a section tree (noise left out, lists, tables, odd characters), tree and paths, chunk size and overlap rules, ids and hashes, the embedding cache (rebuild is free, failure keeps paid-for vectors), the page links (positions, redirects as pages, the text unchanged by links), the report and the command line (a dry run touches nothing) |
| `test_link_wiki.py` (59) | nothing | the link job's Python: the written forms of names, the scan of chunk text (longest name first, whole words, stoplist, aliases), the review config, the report text |
| `test_wiki_integration.py` (37, marker `integration`) | Neo4j Enterprise (no jar, no OpenAI) | the wiki graph written into its own throwaway database `minecorpwikitest`: tree, chunks, `PART_OF`, one `NEXT` chain, constraints, the vector index and a vector search, reloading a page, a second page, and `--wipe` leaving the game graph alone |
| `test_link_wiki_integration.py` (89, marker `integration`) | Neo4j Enterprise (no jar, no OpenAI) | the link job on a small game graph and loaded wiki pages in `minecorplinktest`: what a title is about, ambiguity linked to every candidate, links followed through redirects, names in text, the two kinds of evidence merged, dry run rolled back, one transaction, hand-made links kept |
| `test_graph_integration.py` (34, marker `integration`) | Neo4j Enterprise and the jar | the whole rebuild in order into the `minecorptest` database, which is dropped afterwards; shape, counts, the model, and the rebuild contract |

The database fixtures (`built`, `db`) live in `tests/conftest.py` and `tests/graphdb.py`, and are shared with the agent's graph tests, so the graph is built once per run.
