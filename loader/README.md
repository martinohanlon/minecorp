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

Other options: `--seed <item id>` (repeatable), `--max-depth N`, `--out subset.json`, `--no-samples`.

The loader is idempotent (it uses `MERGE`); `--wipe` deletes everything first.

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
MATCH (p:Player)-[c:COMPLETED]->(t:Task)-[:USED_RECIPE]->(r:Recipe) RETURN p.name, t.name, r.name, c.at;
```

## Why Python

The neo4j driver is first-class, `zipfile` and `json` in the standard library read the jar directly, and the same language fits the later agent service (Neo4j Agent Memory and the major agent frameworks are Python-first). The plugin that talks to the server stays Java (Paper API).

## Draft status

- Full graph loaded into Neo4j 2026.09.0 (2026-10-02) following ontology v0.4: 1,571 Item (234 of them ItemClass), 1,585 Recipe, 7 Station, plus 3 sample Tasks and a test Player.
- Subset logic follows only recipes that *produce* an item. It does not yet include uses of an item (the forward direction).
- Special recipes (banner duplicate, fireworks...) have no inputs and are flagged `special`.
