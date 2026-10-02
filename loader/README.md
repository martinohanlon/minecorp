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

The graph can be rebuilt from source at any time. Order for a full rebuild (about 30 seconds): `load_recipes.py --all --wipe`, `load_actions.py`, `load_model.py`, then `compute_routes.py`.

The recipe loader is idempotent (it uses `MERGE`); `--wipe` deletes everything first.

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
| `test_graph_integration.py` (34, marker `integration`) | Neo4j Enterprise and the jar | the whole rebuild in order into the `minecorptest` database, which is dropped afterwards; shape, counts, the model, and the rebuild contract |

The database fixtures (`built`, `db`) live in `tests/conftest.py` and `tests/graphdb.py`, and are shared with the agent's graph tests, so the graph is built once per run.
