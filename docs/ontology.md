# MineCorp ontology

Living document. Each version records what changed and why. **Status (2026-10-05): two graphs and a bridge, all built from source and tested.** The domain graph (game data, the world, the company) and the lexical graph (community text from the wiki) are described first in "The model"; the sections after it are the design history and the detail behind each part. 17 tutorial pages are loaded (`model/wiki_pages.yaml`, judged in [wiki-pages.md](wiki-pages.md)).

## The model: two graphs and the bridge between them

MineCorp holds two kinds of knowledge and keeps them apart. The **domain graph** is what the game and the company *are*: authoritative, read from the Paper jar and from the YAML in `model/`, rebuilt from source. The **lexical graph** is what has been *written* about it: community text from the Minecraft Wiki, cut into chunks with embeddings. A thin **bridge** of derived, reviewable relationships joins them, so a passage can lead to the facts it is about and a fact can lead to the passages that discuss it. Round brackets are nodes, `A | B` means either label, `:Ore` and `:Station` are marker labels.

### The domain graph

It has three regions that share nodes. `Block` and `Mob` are where the game data meets the world, and `Action` is where the company meets the game.

**Game things and how they are made.**

```
(ItemClass)-[:INCLUDES]->(Item | ItemClass)           categories (planks, beds); ItemClass and Station are marker labels on Item
(Recipe)-[:REQUIRES {role, count, slot, excludes}]->(Item)       all together; an ItemClass means any one member
(Recipe)-[:CREATES {count}]->(Item)                  (Recipe)-[:USES_STATION]->(Item:Station)
(Action:Craft | Smelt | Smith | Cut)-[:CREATES]->(Item)          thin; the recipes meet them at the Item
(Action:Mine)-[:REQUIRES {role: 'target'}]->(Block)               (Action:Kill | Shear)-[:REQUIRES {role: 'target'}]->(Mob)
(Action:Mine | Kill | Shear)-[:REQUIRES {role: 'tool', orBetter}]->(Item)
(Action:Mine | Kill | Shear)-[:CREATES {count, condition}]->(Item)
(Item) carries route properties: routeSteps, routeLevels, routeBaseMaterials, routeStations (derived)
```

**The world.**

```
(MobClass)-[:INCLUDES]->(Mob | MobClass)             (BiomeClass)-[:INCLUDES]->(Biome | BiomeClass)
(Biome)-[:SPAWNS {category, weight, min, max}]->(Mob)
(Dimension {minY, height})-[:HAS_BIOME]->(BiomeClass)
(Structure)-[:GENERATES_IN]->(BiomeClass)            (Structure)-[:SPAWNS {category, boundingBox, weight, min, max}]->(Mob)
(Biome)-[:GENERATES {step}]->(PlacedFeature:Ore {count, rarity, minY, maxY, peakY})-[:PLACES]->(Feature {size})-[:CREATES {replaces}]->(Block)
(PlacedFeature)-[:IN_DIMENSION]->(Dimension)
```

The two regions join at the game's raw materials, which is how "where do I mine iron" is one path:

```
(Item raw iron)<-[:CREATES]-(Action:Mine)-[:REQUIRES]->(Block iron_ore)<-[:CREATES]-(Feature)<-[:PLACES]-(PlacedFeature)<-[:GENERATES]-(Biome)
(Item gunpowder)<-[:CREATES]-(Action:Kill)-[:REQUIRES]->(Mob creeper)<-[:SPAWNS]-(Biome)
```

**The player's processes** (v0.11). There is one role, the player, so there is no organization.

```
(Process)-[:HAS_ACTIVITY {seq}]->(Activity)-[:HAS_TASK {seq}]->(Task)-[:NEXT {condition, isDefault}]->(Task)
(Task)-[:HAS_ACTION {order, quantity}]->(Action)     (Task)-[:CONFIRMED_BY {at}]->(Player)     (Player)-[:COMPLETED {at, count}]->(Task)
```

A process is a goal a player works towards in the game (get to the Nether), made of stages (activities) and steps (tasks). It holds what the game's data files do not: the structured part of the game (recipes, mining, drops, spawns) is already in the graph and needs no process. A task points at the game's own actions when the data has them, and has none when it is a manual step, so a process is defined in terms of what the game can do and says plainly where it cannot.

### The lexical graph

```
(Wiki)-[:HAS_PAGE]->(Page {url, title, revisionId, fetchedAt})-[:HAS_SECTION]->(Section)-[:HAS_SUBSECTION]->(Section)
(Chunk {text, tokens, textHash, embedding, embeddingModel})-[:PART_OF]->(Section)        (Chunk)-[:NEXT]->(Chunk)
(Chunk)-[:LINKS_TO {count, texts}]->(Page)           (Page)-[:REDIRECTS_TO]->(Page)
index: vector `chunk_embedding` over Chunk.embedding (1,536 dimensions, cosine)
```

A section is a tree, a chunk never crosses a heading, and `NEXT` follows reading order within a page. A page that is only linked to is a stub (url and title; no `fetchedAt`, no sections); loading it later fills in the same node, and every link already pointing at it stays. A redirect is a page too: the wiki's own structure is kept as it is, not flattened. Every node carries its source URL; the text is community-written (CC BY-NC-SA).

### The bridge

```
(Page)-[:ABOUT {method, match, ambiguous, candidates, status}]->(node)
(Chunk)-[:MENTIONS {method, count, linkCount, surfaces, corroborated, ambiguous, candidates, status}]->(node)
        node = Item | Mob | Block | Biome | Structure       (classes are not named)
```

Derived by `link_wiki.py` by following paths (`Chunk-LINKS_TO->Page-ABOUT->node`, with one `REDIRECTS_TO` step only when the linked page is about nothing) and by finding names in the text. Ambiguity is kept as a relationship to every candidate, flagged. Everything on the bridge is a **candidate** until a person confirms it in `model/wiki_links.yaml`. The node a relationship points at says what it is; the relationship stores no kind.

### What each part knows, and where it comes from

- **Domain, game and world:** authoritative for the Paper 26.2 jar; no hand edits. Built by `load_recipes.py`, `load_actions.py`, `load_world.py`, then `compute_routes.py`.
- **Domain, company:** authored in `model/*.yaml`, loaded by `load_model.py`.
- **Lexical:** the 17 pages of `model/wiki_pages.yaml` (549 sections, 720 chunks), with 1,133 stub pages for the pages they link to. Built by `load_wiki.py --pages` (MediaWiki API; OpenAI embeddings, cached).
- **Bridge:** `link_wiki.py`, reviewed through `model/wiki_links.yaml`.
- **Rebuild order:** `load_recipes.py --all --wipe`, `load_actions.py`, `load_world.py`, `load_trades.py`, `load_model.py`, `compute_routes.py`, `load_wiki.py`, `link_wiki.py`.

### What it holds today (real database, 2026-10-05)

Domain: `Item` 1,628 (of which 234 `ItemClass`, 7 `Station`), `Recipe` 1,585, `Action` 2,385, `Block` 1,113, `Mob` 90, `MobClass` 44, `Biome` 66, `BiomeClass` 68, `Structure` 34, `Dimension` 3, `Feature` 32, `PlacedFeature:Ore` 40; company `Player` 1 (no process yet; `OrgUnit` and `Role` were removed in v0.11). Lexical: `Wiki` 1, `Page` 1,150 (17 loaded, 1,133 stubs), `Section` 549, `Chunk` 720, `LINKS_TO` 3,191, `REDIRECTS_TO` 423. Bridge: `ABOUT` 673, `MENTIONS` 4,743.

### Which graph answers which question

- *"How do I make a bed?" "What does a creeper drop?" "Where do I mine iron?"* are domain questions: exact, checkable, and the same every time.
- *"What should I do on my first night?"* is a lexical question: advice and explanation, written by people, to be attributed.
- *"What does the wiki say about chickens, and what do they drop?"* crosses the bridge: the passage names a mob, and the mob's own drops are facts.

### Not yet modelled

- **Domain:** chest loot, fishing and bartering (they fit `Action`, as trades now do), enchantments, advancements, the other 222 placed features (trees, vegetation, lakes), structure sets, mob and block statistics, brewing.
- **Lexical:** the 40 tutorial pages held back in [wiki-pages.md](wiki-pages.md). A full-text index on `Chunk.text`. A quality or edition note on a page (the wiki marks pages as stubs, needing cleanup, or Bedrock-only).
- **Bridge:** links to `Recipe`, and an LLM extractor for implied mentions, to be measured against the deterministic baseline.

## Source evidence (Paper 26.2, vanilla)

Recipes and tags ship inside `server/versions/26.2/paper-26.2.jar` under `data/minecraft/recipe/*.json` (1,585 recipes) and `data/minecraft/tags/item/*.json` (224 item tags). The loader in `loader/` reads them directly from the jar.

| Recipe `type` | Count | Key fields |
|---|---|---|
| crafting_shaped | 733 | `pattern`, `key`, `result{id,count}`, `category`, `group` |
| crafting_shapeless | 323 | `ingredients[]`, `result` |
| stonecutting | 319 | `ingredient`, `result` |
| smelting / blasting / smoking / campfire_cooking | 73 / 25 / 9 / 9 | `ingredient`, `experience`, `cookingtime`, `result` |
| crafting_transmute | 33 | `input`, `material`, `result` |
| smithing_trim / smithing_transform | 18 / 12 | `template`, `base`, `addition`, (`pattern` for trim; `result` for transform) |
| crafting_special_* / crafting_dye / crafting_decorated_pot / crafting_imbue | ~35 | mostly no explicit ingredients (behaviour is in code) |

Observations that drive the model:
1. An ingredient reference is one of three forms: an item id (`minecraft:stick`), a **tag** (`#minecraft:planks`, which can nest other tags), or an inline **list of alternatives** (e.g. any bed).
2. Several recipes make the same result (e.g. iron ingot: smelting, blasting, from nuggets, from block, from ores). `group` ties them together.
3. The processing **station** is implied by `type`, not stored (crafting table, furnace, blast furnace, smoker, campfire, stonecutter, smithing table).
4. "Blocks" and "items" share ids in recipes, so one `Item` label covers both.
5. Minecraft has **no default or representative item** for a tag: a tag is an unordered `values` list of item ids and other tags. Any representative ("plain bed", "oak planks") would be MineCorp curated data, not vanilla.
6. Six tag ids collide with item ids (`anvil`, `dirt`, `mud`, `sand`, `stone_bricks`, `terracotta`), so categories and items cannot share an `id` namespace as written in the jar. Minecraft itself writes tag references as `#minecraft:anvil`; v0.4 keeps that notation.
7. Special recipes (banner duplicate, firework, map extending...) have no declarative inputs. They are modelled as `Recipe` nodes with `special: true` and no ingredient edges, to be described from the wiki later.

## Domain graph detail: game data and the company (v0.7 diagram)

**The `OrgUnit`, `Role`, `FILLED_BY`, `OWNS`, `PERFORMS`, `HANDOFF_TO` and `ESCALATES_TO` parts of this diagram were removed in v0.11 (see below); `CONFIRMED_BY` now points at a `Player`.** The game-data and company parts of the domain graph as built at v0.7 (the world and the lexical graph came later; see "The model" above). Every node label and relationship of those parts: properties are in the tables below ("Node labels", "Nodes", "Relationships") so this stays readable. Round brackets are node labels. The organization and process nodes are authored in the YAML in `model/`; everything else is generated from the Paper server jar.

```
                          PART_OF
                       +-----------+
                       v           |
                   (OrgUnit)-------+
                       ^
                       | IN_UNIT
                       |
 (Player) <--FILLED_BY-- (Role) --OWNS--> (Process) --HAS_ACTIVITY--> (Activity)
    |                      |                                              |
    |                      | PERFORMS                                     | HAS_TASK
    | COMPLETED            v                                              |
    +------------------> (Task) <-----------------------------------------+
                          |      Task also has NEXT and HANDOFF_TO (to another Task),
                          |      and ESCALATES_TO and CONFIRMED_BY (to a Role)
                          | HAS_ACTION
                          v
 (Block) <--REQUIRES-- (Action) --REQUIRES (tool)--> (Item)
 (Mob)   <--REQUIRES--    |
                          | CREATES
                          v
                        (Item) <--CREATES-- (Recipe) --REQUIRES--> (Item | ItemClass)
                          ^                     |
                          | INCLUDES            +--USES_STATION--> (Item)
                     (ItemClass)

 (Action) has a verb label. Craft, Smelt, Smith and Cut are thin (only the item they make; the Recipe nodes
 hold the ingredients and the station). Mine, Kill and Shear are the definitions (target, tool and drops).
 Item can also carry the marker labels ItemClass (a category) and Station (where work is done).
```

**Reading rules**

- All `REQUIRES` edges of one recipe are needed together (AND). An `ItemClass` target means any ONE member (OR), except the items in `excludes`.
- A gathering action `REQUIRES` the minimum tool; that tool or any better one works (`orBetter`). Shears are exact (`orBetter: false`). Killing needs no tool.
- A transforming action and the recipes that make its item meet at the `Item`; there is no edge between them.
- A `Task` performs zero or more ordered actions (`HAS_ACTION {order, quantity}`). No actions means a manual task.
- Candidates (not yet confirmed) carry `source`, `extractedAt`, `confidence`; confirmed tasks have `CONFIRMED_BY`.

Loaded today (2026-10-02, rebuilt from source in 30 seconds): `Item` 1,628 (of which `ItemClass` 234 and `Station` 7), `Recipe` 1,585, `Action` 2,385 (`Mine` 1,099, `Craft` 949, `Cut` 197, `Kill` 63, `Smelt` 61, `Smith` 12, `Shear` 4), `Block` 1,113, `Mob` 63, `OrgUnit` 6, `Role` 9, `Player` 1, `Process` 1, `Activity` 4, `Task` 6. Craftable items also carry computed route properties (see "Route properties").

## v0.5 model (v0.4 plus route properties)

### Design idea

Recipes require **Items**, always. Some of those Items are **categories** ("Bed", "Planks", "Iron Tool Materials") that include more specific Items. A category is an `Item` with an extra marker label `:ItemClass`, so recipe edges always have one shape, `(Recipe)-[:REQUIRES]->(Item)`, and `INCLUDES` leads from a category to its members. Categories overlap (an item can be in several, categories can contain categories), so this is a graph, not a tree; there is no `VARIANT_OF` hierarchy.

### Node labels

| Label | Key property | Other properties | Notes |
|---|---|---|---|
| `Item` | `id` (e.g. `minecraft:iron_ingot`) | `name`; for craftable concrete items also the **route properties** below | Covers blocks too. A *concrete* Item exists in the game. |
| `Item:ItemClass` | `id` (see below) | `name`, `inline` (bool), `size` (number of concrete items covered, expanding nested classes) | A *category* of Items, for example "Bed". Does not exist in the game. Replaces the old `ItemTag` label. |
| `Item:Station` | `id` (e.g. `minecraft:furnace`) | `name` | An `Item` with an extra label (like `ItemClass`): the crafting table you craft is the same node that recipes use as their station. Which station a recipe uses is derived from its `type`; see the mapping below. |
| `Recipe` | `id` (file name) | `name` (readable, derived from the id), `type`, `category`, `group`, `experience`, `cookingTime`, `special`, `pattern` (rows as a list of strings), `key` (JSON map of symbol -> ingredient id) | A vanilla transformation definition, one per JSON file; regenerated from the jar. The grid is kept so the agent can describe it. |

Category `id`: Minecraft tags keep the jar's notation with a leading `#`, e.g. `#minecraft:planks` (avoids the six id collisions with items). Anonymous alternative lists keep `inline:<hash of members>`.

ItemClass `name` (rule, in order):
1. If the class covers two or more concrete items, use the trailing word(s) shared by **all** of them: the 15 beds in `dye_brown_bed` give "Bed", `#minecraft:trapdoors` gives "Trapdoor", `#minecraft:iron_ores` gives "Iron Ore".
2. Otherwise, for a real tag, use the tag name in Title Case ("Acacia Logs", "Bee Food").
3. Otherwise, for an inline list, "Any of: A, B, C".

Tested on the 300 categories in the full graph: 160 get a shared word (rule 1), 140 fall back (134 to a tag name, 6 inline lists to "Any of..."). Rule 1 is not always the nicest word (`#minecraft:leg_armor` becomes "Leggings"), but it is consistent and automatic; a curated override is possible later.

When rule 1 would give two real tags the same name, both use their full tag name instead (`#minecraft:slabs` is "Slabs" and `#minecraft:wooden_slabs` is "Wooden Slabs", not two "Slab"; this affected 18 names, such as button, door, dye and the armor slots). The only remaining duplicate names are two pairs of anonymous lists ("Sandstone" and "Red Sandstone"), which differ by members; the `id` is always unique.

#### Anonymous lists become the real tag plus an exclusion

A recipe can list its alternatives inline instead of naming a tag. The 16 `dye_<colour>_bed` recipes each list the 15 other colours of bed. Rather than 16 near-identical "Bed" classes, the loader points the recipe at the real tag (`#minecraft:beds`, 16 beds) and records what is left out on the edge:

```
(Recipe: Dye Brown Bed) -REQUIRES {count:1, excludes:["minecraft:brown_bed"]}-> (ItemClass: Bed)
```

Rule: an anonymous list that is contained in a real tag with at most one extra item is replaced by that tag (smallest such tag), with the extra item in `excludes`. Of the 76 anonymous lists in the data, 66 map this way (16 each for beds, carpets, harnesses and "sulfur cube archetype light", plus two that equal an existing tag exactly). 10 stay as `inline:<hash>` classes (sandstone and quartz slab/stair inputs, nugget smelting inputs).

#### Route properties (computed, v0.5)

For every craftable concrete item, `loader/compute_routes.py` works out the **main route** from base materials to the item (the same calculation the agent's `what_do_i_need` tool uses: preferred recipe per item, skipping reversible loops such as ingot to nuggets and back, simplest recipe first) and stores a summary on the `Item`:

| Property | Meaning |
|---|---|
| `routeSteps` | Number of crafting, smelting, smithing... steps on the main route (each distinct item made counts once, even if made in bulk) |
| `routeLevels` | Longest chain of made items from a base material to this item |
| `routeBaseMaterials` | Number of distinct base materials (including categories) the route needs |
| `routeStations` | Stations used, e.g. `["Crafting Table", "Furnace"]` |
| `routeTruncated` | `true` if the route hit the depth limit (6) and is not fully expanded; excluded from rankings |

These are **derived data**, not vanilla facts: they depend on the route-choice rule, so a different rule gives different numbers. They are recomputed after every load (`--wipe` removes them). The agent stays read-only; this is a separate write job. The `rank_recipes` tool sorts on them to answer "give me a hard recipe", and `get_recipes` and `what_do_i_need` return the same summary as `overview`.

### Relationships

| Relationship | From -> To | Properties | Meaning |
|---|---|---|---|
| `INCLUDES` | `Item:ItemClass` -> `Item` | | Category membership. A member may itself be a `:ItemClass` (nesting allowed). |
| `CREATES` | `Recipe` -> `Item` | `count` | The result. |
| `REQUIRES` | `Recipe` -> `Item` (concrete or `:ItemClass`) | `role` (`ingredient`, `template`, `base`, `addition`, `material`, `input`), `count`, `slot` (shaped key char), `excludes` (item ids of the class that may not be used, usually empty), `excludesNames` (same, readable) | Inputs. `count` is how many are needed (shapeless repeats and shaped key usage summed). |
| `USES_STATION` | `Recipe` -> `Item:Station` | | Where it is made. |
| `HAS_ACTION` | `Task` -> `Action` | `order`, `quantity` | (v0.7; replaced `CRAFTED`, the old Task-to-Recipe link.) The actions a task performs. |
| `COMPLETED` | `Player` -> `Task` | `at` (datetime), `count` | A player has completed a task (history of work). |

**How to read REQUIRES (important for queries and the agent):** all `REQUIRES` edges of a recipe are needed together (AND). When the target is an `:ItemClass`, exactly one member of that class is used (OR), except any item in `excludes`. Example: `dye_brown_bed` = 1 Brown Dye (concrete) AND 1 Bed (class of 15) = a brown bed from brown dye plus any bed except a brown one (`excludes: [brown_bed]`).

Station mapping: `crafting_*` -> crafting table (some also 2x2 inventory grid, TBC from the pattern size), `smelting` -> furnace, `blasting` -> blast furnace, `smoking` -> smoker, `campfire_cooking` -> campfire, `stonecutting` -> stonecutter, `smithing_*` -> smithing table.

### Example

(The full model is the "Graph model (v0.6, as built)" section above.)

Example (`dye_brown_bed`):

```
(Recipe: Dye Brown Bed) -REQUIRES {count:1}----------------------------> (Item: Brown Dye)
                        -REQUIRES {count:1, excludes:[brown_bed]}------> (Item:ItemClass: Bed, size 16)
                                                                           -INCLUDES-> (Item: White Bed)
                                                                           -INCLUDES-> (Item: Orange Bed)
                                                                           ... 16 beds
                        -CREATES -----------------------------------> (Item: Brown Bed)
```

REQUIRES `role` values: `ingredient`, `template`, `base`, `addition`, `material`, `input`.

### Test model (loader subset, measured on v0.3.1)

`loader/load_recipes.py` builds a small subset by walking **backwards** from seed items (default `minecraft:iron_pickaxe`) to the recipes and materials needed to make them, `--max-depth 3` by default. Run against Paper 26.2 this gives:

| | Count |
|---|---|
| Recipes | 28 |
| Items | 83 |
| Item tags (incl. 1 inline) | 15 |
| Stations | 3 (crafting table, furnace, blast furnace) |
| Sample Tasks | 3, plus a test Player who has completed "Smelt raw iron" |

Depth 3 reaches the pickaxe, sticks, iron ingot (from ores, raw iron, nuggets, block) and planks for all wood types. Sample questions to test in Neo4j are in `loader/README.md`.

### Full graph (Paper 26.2, v0.4)

`python loader/load_recipes.py --all --wipe` loads everything (a few seconds):

| Nodes | Count | Relationships | Count |
|---|---|---|---|
| Item (all) | 1,571 | CREATES | 1,566 |
| of which ItemClass | 234 (224 real tags + 10 anonymous lists) | REQUIRES | 2,315 |
| of which concrete | 1,337 | INCLUDES | 1,534 |
| Recipe | 1,585 | USES_STATION | 1,585 |
| Station | 7 | | |

Notes from the full load: 19 recipes have no `CREATES` edge (the 18 smithing trims modify existing armor rather than produce an item, plus `repair_item`, a special recipe that repairs rather than produces); 31 are `special` (no declared inputs); 243 recipes require at least one `ItemClass`; 42 items were neither produced nor in any tag (raw-ish: ores, mob drops, natural blocks, measured on v0.3.1). These feed the raw-material and process design.

### Derived (query, not stored)

- **Supply chain:** `(:Item)<-[:CREATES]-(:Recipe)-[:REQUIRES]->(:Item)-[:INCLUDES*0..]->(:Item)` walks from a product back to raw materials (`*0..` covers both concrete and class targets).
- **Raw material:** an `Item` with no `CREATES` edge pointing at it (or only reversible recipes such as nuggets <-> ingots; handle explicitly).
- **Alternatives:** `Recipe`s sharing a `CREATES` target, or with the same `group` property (group stays a property, see decisions below).

Example question this should answer: *"What do I need to make an iron pickaxe from scratch?"*

```cypher
MATCH (:Item {id:'minecraft:iron_pickaxe'})<-[:CREATES]-(r:Recipe)-[c:REQUIRES]->(i)
OPTIONAL MATCH (i:ItemClass)-[:INCLUDES*]->(leaf:Item) WHERE NOT leaf:ItemClass
RETURN r.id, c.role, c.count, i.name, i:ItemClass AS isCategory, collect(DISTINCT leaf.name) AS options
```

## v0.6: organization, process and harvest model

> **The organization described here (`OrgUnit`, `Role`, the six units, nine roles, owners, hand-offs and escalations) and the Iron tool line were removed in v0.11.** They came from the knowledge-layer posts and did not fit one player in a game. This section is kept as the record.

> **Partly superseded by v0.7 (below).** `Harvest`, `FROM_BLOCK`, `FROM_MOB`, `CRAFTED` and `HARVESTED` no longer exist: the same facts (target, tool, drops) now sit on gathering `Action` nodes, and tasks link to actions with `HAS_ACTION`. The tables and counts in this section are kept as the record of how the data was derived.

**Built:** the harvest part (`loader/load_actions.py`, formerly `load_harvests.py`) and the organization and process part (`loader/load_model.py`, reading `model/*.yaml`). Implements step 1 of [knowledge-layer.md](knowledge-layer.md) section 9. Everything above this heading describes what is loaded today (v0.5). Names marked provisional follow the Neo4j series chapters, which were only partly readable (chapter 1, operating structure, was not).

### Classification (decided)

Things are **domain**; ways of changing things are **process**. So:

| Ontology part | Node labels |
|---|---|
| Domain | `Item`, `ItemClass`, `Station` (both marker labels on `Item`), and new `Block` |
| Process, vanilla (reference data, regenerated from the jar) | `Recipe` (built), new `Harvest` |
| Process, MineCorp (authored and curated) | `Process`, `Activity`, `Task` (built, extended), `Role`-to-task links |
| Organization (authored) | `OrgUnit`, `Role`, `Player` (built) |

This only changes how `Recipe` is described; its labels and properties are unchanged.

### New data from the jar (collecting resources)

| Data | Count | Gives |
|---|---|---|
| Block loot tables `loot_table/blocks/*.json` | 1,113 | What breaking a block drops |
| Block tags `mineable/pickaxe, axe, shovel, hoe` | 4 | Which tool type works on a block |
| Block tags `incorrect_for_<tier>_tool` (wooden, stone, copper, gold, iron, diamond, netherite) and `needs_stone/iron/diamond_tool` | 10 | Which tool tiers cannot mine a block, so the minimum tier is derivable |
| Mob loot tables `loot_table/entities/*.json` | 109 | Drops from mobs (shearing, farming): **later** |

### Nodes

Domain addition:

| Label | Key | Properties | Notes |
|---|---|---|---|
| `Block` | `id` (e.g. `minecraft:iron_ore`) | `name` | Blocks that have a loot table. Same id string as the matching `Item` but a different label. |

Vanilla process addition (parallel to `Recipe`):

| Label | Key | Properties | Notes |
|---|---|---|---|
| `Block` | `id` (e.g. `minecraft:iron_ore`) | `name` | Blocks that have a loot table. Same id string as the matching `Item` but a different label. |
| `Mob` | `id` (e.g. `minecraft:spider`) | `name` | A mob that drops something when killed or sheared. |
| `Harvest` | `id` (the loot table, e.g. `minecraft:blocks/iron_ore`, `minecraft:entities/spider`, `minecraft:shearing/sheep`) | `name` ("Mine Iron Ore", "Kill Spider", "Shear Sheep"), `kind` (`mine`, `kill`, `shear`), `tool` (`pickaxe`, `axe`, `shovel`, `hoe`, `shears` or none), `minTier` (`wooden`, `stone`, `copper`, `iron`, `diamond`, `netherite` or none), `toolRequired` | One per loot table. A harvest is to a block or mob what a recipe is to a station. |

MineCorp organization (authored):

| Label | Key | Properties | Notes |
|---|---|---|---|
| `OrgUnit` | `id` | `name`, `description` | The company is the root unit. |
| `Role` | `id` | `name`, `description`, `kind` (`operational` or `governance`) | Tasks are performed by roles, not people. |
| `Player` (built) | `uuid` | `name` | The person. Keeps its name (the series uses `Person`). |

MineCorp process (authored):

| Label | Key | Properties | Notes |
|---|---|---|---|
| `Process` | `id` | `name`, `description`, `family` (`collect`, `craft`, `inventory`), `criticality`, `sla` | One owner each. |
| `Activity` | `id` | `name` | A stage of a process. Its place in the process is `seq` on `HAS_ACTIVITY` (v0.11). |
| `Task` (built) | `id` | `name`, `description`, `status` (`candidate`, `confirmed`), `type` (`harvest`, `recipe`, `manual`), `automatable` (bool); for candidates also `source`, `extractedAt`, `confidence` | The delegatable unit. `manual` covers inventory work with no vanilla definition. |

### Relationships

| Relationship | From -> To | Properties | Meaning |
|---|---|---|---|
| `FROM_BLOCK` | `Harvest` -> `Block` | | What is mined |
| `FROM_MOB` | `Harvest` -> `Mob` | | What is killed or sheared |
| `CREATES` | `Harvest` -> `Item` | `count` (typical), `condition` (blocks: `default`, `silk_touch`, `shears`, `state`, `chance`; mobs: `default` or a `+`-joined mix of `killed_by_player`, `chance`, `state`) | What it yields; same relationship as `Recipe -[:CREATES]->` so routes can follow both |
| `REQUIRES` | `Harvest` -> `Item` | `role: 'tool'`, `orBetter` | The minimum tool item (for example Stone Pickaxe; `orBetter: true`, any higher tier also works) or Shears for shearing (`orBetter: false`). Killing a mob needs no tool. |
| `PART_OF` | `OrgUnit` -> `OrgUnit` | | Org structure |
| `IN_UNIT` | `Role` -> `OrgUnit` | | Where a role sits |
| `FILLED_BY` | `Role` -> `Player` | `from`, `to` (null while current) | Who holds a role, with dates. The one player may fill many roles at once. |
| `OWNS` | `Role` -> `Process` | | One owner per process |
| `HAS_ACTIVITY` | `Process` -> `Activity` | `seq` (v0.11: the stage's place, from 1) | |
| `HAS_TASK` | `Activity` -> `Task` | `seq` (v0.11: the step's place in its activity, from 1) | |
| `PERFORMS` | `Role` -> `Task` | | Who does the task |
| `NEXT` | `Task` -> `Task` | `condition`, `outcome`, `isDefault` | Routing is data |
| `HANDOFF_TO` | `Task` -> `Task` | | The next task belongs to a different role: a change of hat (may be the same person) |
| `ESCALATES_TO` | `Task` -> `Role` | | Where the normal path needs help |
| `CRAFTED` (built) | `Task` -> `Recipe` | | The vanilla recipe behind a crafting task |
| `HARVESTED` | `Task` -> `Harvest` | | The vanilla harvest behind a mining task |
| `COMPLETED` (built) | `Player` -> `Task` | `at`, `count`, `role` (the hat worn) | History of work |
| `CONFIRMED_BY` | `Task` -> `Role` | `at` | Which governance role confirmed a candidate |

Edge names for the org structure are provisional and may change when series chapter 1 is read.

### Graph model

See "Graph model (v0.6, as built)" at the top of this document.

### MineCorp operating model (data to author)

Org units and roles (the one player fills all roles, from the same date, `to` null):

| Unit | Roles | Process family |
|---|---|---|
| Extraction | Miner | collect |
| Refining | Smelter | craft |
| Fabrication | Crafter, Smith | craft |
| Logistics | Storekeeper | inventory |
| Governance | Process Owner, Domain Owner, Platform Owner, Steward (`kind: governance`) | n/a |

Governance roles: Process Owner `OWNS` the processes. Domain Owner, Platform Owner and Steward exist as roles from the start; what they own (data products, the graph platform) is attached when the technical ontology exists. The Steward is the role in `CONFIRMED_BY`.

Worked example, process "Iron tool line":

```
Activity 1 Collect       T1 Mine iron ore      (Miner)        Mine iron ore x3     needs: Stone Pickaxe or better
                         T2 Mine oak logs      (Miner)        Mine oak log x2
Activity 2 Refine        T3 Smelt raw iron     (Smelter)      Smelt iron ingot x3
                         T4 Make sticks        (Crafter)      1 Craft oak planks x2, 2 Craft stick x4
Activity 3 Fabricate     T5 Craft iron pickaxe (Crafter)      Craft iron pickaxe
Activity 4 Store         T6 Stock the tools chest (Storekeeper)  no actions: manual

NEXT: T1 -> T3 (raw iron collected, default)   T2 -> T4   T3 -> T5   T4 -> T5   T5 -> T6 (HANDOFF_TO: Crafter to Storekeeper)
```

What the model lets the agent answer that it cannot now: who owns the line; the default path end to end; what comes after smelting; which tasks could be automated; **how to obtain a base material** (iron ore is mined, with a stone pickaxe or better); and gaps such as "T1 needs a stone pickaxe and nothing earlier in this process makes one".

### Single player

Every role is filled by the one player, so nothing here needs two people. `HANDOFF_TO` between roles is a change of hat (recorded as `COMPLETED.role`). No policy depends on telling players apart.

### How the data gets in

- **Vanilla:** `loader/load_actions.py` (formerly `load_harvests.py`) reads block loot tables and block tags from the jar and creates `Block`, `Harvest`, `CREATES` and the minimum-tool `REQUIRES` edges (deterministic, regenerated like recipes). Derivation of the minimum tier: the lowest tier in the order wooden, stone, copper, iron, diamond, netherite that is not listed in `incorrect_for_<tier>_tool` for the block; the loader checks that every tier above it also works. Gold is ignored as a requirement because it mines like wood.
- **Authored:** the organization and processes are YAML files in a new `model/` folder (`org.yaml`, `processes/*.yaml`), version-controlled and human-edited. A new `loader/load_model.py` validates every `CRAFTED` and `HARVESTED` id against the graph and then merges the nodes. A bad reference fails the load.
- **Proposals:** later, an LLM job can draft process YAML from `what_do_i_need` routes as `status: candidate` with source and confidence. The Steward confirms by editing the file (loader writes `CONFIRMED_BY`).
- **Route calculation:** `what_do_i_need` can later report, for each base material, the harvest that gets it and the tool it needs.

### Harvest: built (blocks only, 2026-10-02)

`python loader/load_actions.py` (formerly `load_harvests.py`; run after `load_recipes.py --all`, before `compute_routes.py`; safe to repeat):

| | Count |
|---|---|
| `Block` | 1,113 (every block with a loot table) |
| `Harvest` | 1,166: 1,099 mine (14 blocks drop nothing and get none), 63 kill, 4 shear |
| `Mob` | 63 (30 of the 93 mob loot tables drop nothing, such as allays and armor stands) |
| Harvest `CREATES` edges | 1,229 (981 default, 143 silk touch, 59 block-state, 34 chance, 12 shears) |
| Tool `REQUIRES` edges | 502 (all pickaxe blocks) |
| New `Item` nodes | 52 (drops no recipe uses, such as saplings); `Item` total 1,623 |

Tool type: pickaxe 502, axe 246, shovel 34, hoe 29, none 288. Minimum tier among harvests that have a tool type: wooden 703, stone 91, iron 12, diamond 5 (the tier counts include blocks with no tool type, which have no tier). No block had a non-monotonic tier list, so "or better" is sound.

Spot checks against the game: iron ore needs a Stone Pickaxe, diamond ore an Iron Pickaxe, obsidian and ancient debris a Diamond Pickaxe, stone a Wooden Pickaxe; logs and dirt need no tool.

**Limits to know about**
- The jar does not say whether a block drops anything *without* the right tool (stone gives nothing by hand; logs do). Rule used: `toolRequired` is true for pickaxe blocks and for any block needing better than wooden. Axe, shovel and hoe blocks only mine faster with the tool. A few pickaxe blocks do drop by hand; those would need a correction from the wiki (as a candidate).
- Drops: `count` is the typical number from `set_count` (1 when unspecified); fortune and looting bonuses are ignored. `condition` is one of `default`, `silk_touch`, `shears`, `state` (block state, for example fully grown crops) or `chance` (random extras such as saplings and apples).
- Wool has no tool type in the data (shears are fastest, no tag says so), so wool blocks show no tool.
- Mob drops and shearing are loaded (2026-10-02). Looting, fortune and enchantments are ignored. A sheep's wool colour and a mooshroom's variant only decide which item drops, so they are not conditions. A negative random range (spider eye, -1 to 1) counts as none, so its typical count is 0.5. Crops are in the block loot with a `state` condition.
- Fishing, bartering, hive and berry harvests, carving, brushing and chests are not loaded. See "Other harvest sources" below.

### Organization and process: built (2026-10-02)

`python loader/load_model.py` reads [model/org.yaml](../model/org.yaml) and [model/processes/](../model/processes/), validates every reference (roles, units, task ids in `NEXT`, and that each `recipe` and `harvest` id exists in the graph; a bad one fails the load and writes nothing), then rebuilds `OrgUnit`, `Role`, `Process` and `Activity` and merges `Task` by id. `HANDOFF_TO` is added automatically wherever the next task belongs to a different role. Re-running gives the same graph. Early test tasks and the `TestPlayer` from the first loader are removed.

| Loaded | Count |
|---|---|
| `OrgUnit` | 6 (MineCorp, Extraction, Refining, Fabrication, Logistics, Governance) |
| `Role` | 9 (5 operational, 4 governance), every one `FILLED_BY` the one player from 2026-10-02 |
| `Process` / `Activity` / `Task` | 1 (Iron tool line) / 4 / 7 |
| `NEXT` / `HANDOFF_TO` / `ESCALATES_TO` / `CONFIRMED_BY` | 6 / 4 / 1 / 7 (confirmed by the Steward role) |

Queries it answers today (checked): who owns the Iron tool line and who currently fills that role (Process Owner, the player, since 2026-10-02); the default path from mining to storing (Mine iron ore, Smelt raw iron, Craft iron pickaxe, Stock the tools chest); what comes after smelting and under what condition; which tasks are automatable (4); where responsibility changes hands (4 handoffs); and what a mining task yields and needs (Mine iron ore: Raw Iron, with a Stone Pickaxe or better).

Authoring the example exposed a real error: mining an iron ore block drops **raw iron**, not the iron ore item (that needs silk touch), so the first draft's "smelt iron ore" did not match its own mining task. The same flaw was in the agent's routes. Both are fixed (see "Route rules" below).

### Route rules (agent `what_do_i_need` and the stored route properties)

The route picks one recipe per item: simplest first, skipping recipes that only reverse a compaction (ingot from nuggets), and skipping recipes whose input is only available with silk touch (iron ore item). It stops at a **gathered resource**: an item that a *different* block drops, or that a mob drops when killed or sheared (not a rare or conditional drop), with no good recipe (raw iron from iron ore, diamond from diamond ore, glowstone dust, string from spiders, gunpowder from creepers). Items that a block of the same name drops (logs, obsidian) are ordinary base materials. For display, each base material lists ways to get it (`getFrom`: mine, kill or shear, what from, and the tool needed; for example Obsidian needs a Diamond Pickaxe or better, and an Ender Chest also drops it). Mobs come first. The jar cannot tell a natural block from a placed one, and sources are listed alphabetically, so odd ones appear (a cat or a strider for string alongside spiders); they are true but not always sensible. The `how_to_get` agent tool lists every way, with conditions such as "only when a player kills it". Route properties were recomputed with these rules: the hardest items are now Lime Glazed Terracotta (8 steps), then Lime Banner and Lime Stained Glass Pane (7), and the netherite tools (6).

### Other harvest sources (mobs and shearing built 2026-10-02; the rest surveyed, not built)

Mobs killed and shearing are loaded (see above). The jar holds more, all as loot tables in the same JSON format, so they would fit the existing `Harvest` model:

| Source | Loot tables | What it is | Needs |
|---|---|---|---|
| Mobs killed (**built**) | `entities/` (93 mobs; 63 drop something) | Drops when a mob dies; some depend on how (killed by a player, rare chance drops) | No tool (a weapon is faster; looting ignored) |
| Shearing (**built**) | `shearing/` (4 mobs: sheep, mooshroom, snow golem, bogged; the per-colour tables are folded in) | Wool and similar without killing the mob | Shears |
| Plants and hives | `harvest/` (beehive, cave vine, sweet berry bush); crops such as wheat are already in `blocks/` (block-state condition) | Honey, glow berries, sweet berries | Sometimes shears or a bottle |
| Gameplay | `gameplay/` (fishing: fish, junk, treasure; piglin bartering; chicken lay; sniffer digging; armadillo shed; turtle grow; cat gift; panda sneeze; hero of the village gifts) | Drops from an action, not a block or a death | Fishing rod, gold ingots, etc. |
| Carving, brushing | `carve/pumpkin`, `brush/armadillo`, `archaeology/` (7) | Carved pumpkin, scutes, suspicious-sand finds | Shears or brush |
| Chests | `chests/` (59) | Loot found in generated structures; not a repeatable harvest | none |
| Other | `charged_creeper` (mob heads), `spawners`, `pots`, `dispensers`, `equipment` | Special cases | n/a |

Why it mattered: a route stops at "base materials", and before mobs were loaded only items that blocks drop could say how to get them. Now string comes from spiders, cave spiders, cats and striders, gunpowder from creepers, ghasts and witches, blaze rods from blazes (only when a player kills one), rabbit hide from rabbits, and wool by shearing a sheep. The agent shows this as `getFrom` in `what_do_i_need` and through the `how_to_get` tool.

Where to find things is also in the jar: ore placement (`worldgen/placed_feature/ore_*`, for example iron generates between y -24 and 56, ten veins per chunk) and mob spawn lists per biome (`worldgen/biome/*.json`, for example plains spawns sheep, pigs, chickens and cows). Neither is modelled; they would add `Biome` and placement data.

Mob design (built, then reshaped by v0.7): a domain node `Mob`; `Kill` and `Shear` actions that `REQUIRE {role: 'target'}` the mob; the same `CREATES {count, condition}`; Shears as the tool for shearing.

### Questions for review

Decided (2026-10-02): **1** `Harvest` is its own node, **2** the tool requirement is the minimum tool item with `orBetter`, **3** first load is blocks only, **4** org units and roles are fine for now, **5** author the model as YAML (done), **6** keep the label `Player`, **7** no further work on silk touch or variable drops (the default drop plus a silk-touch drop is recorded), **8** `Station` is a marker label on `Item` (built).

| # | Question | Recommendation |
|---|---|---|
| 1 | Model mining as its own node type (`Harvest`, parallel to `Recipe`) or as a plain `Block -[:DROPS]-> Item`? | `Harvest` node. It matches the decision that transformations are process, can be the target of a `Task`, and shares `CREATES` with recipes so routes follow both |
| 2 | Tool requirement as the minimum tool item with `orBetter`, or as a computed category of every allowed tool? | Minimum item with `orBetter`. It reads naturally ("Stone Pickaxe or better") and gives a graph path to the tool's own recipe |
| 3 | Scope of the first harvest load | Blocks only (1,113 loot tables, which covers mining and wool blocks). Shearing, mobs and crops later, from the entity loot tables |
| 4 | Are the org units and roles above the ones you want? | Agree or amend before they are authored |
| 5 | Author the organization and processes as YAML in the repo and load them, or write Cypher or ask the agent to create them? | YAML plus loader: reviewable in git, validated against real recipe ids, repeatable |
| 6 | Keep the node label `Player`, or rename to `Person` as in the series? | Keep `Player` |
| 7 | Silk touch and variable drops (fortune) in harvests | Record the default drop plus a `silk_touch` drop where the table has one; `count` is the typical number; fortune and loot bonuses are ignored for now |
| 8 | Should `Station` link to something? | **Decided: `Station` is a marker label on `Item`** (one node, two labels), so a station has its own recipe and `USES_STATION` points at a real item. Built. |

## v0.7: actions (built 2026-10-02)

> Actions and `HAS_ACTION` are unchanged. The rule "one role per task", `HANDOFF_TO` and the Iron tool line example below were removed in v0.11.

Replaces `Harvest`, `FROM_BLOCK`, `FROM_MOB`, `CRAFTED` and `HARVESTED`. The earlier v0.6 sections above still describe what the data holds (drops, tools, counts); in the graph those facts now sit on `Action` nodes.

### Why

A `Task` used to point straight at one `Recipe` or one `Harvest` and had a `type` of recipe, harvest or manual. That was too simple: a task could not do more than one thing, it had to pin an exact recipe (the first draft of the Iron tool line pinned the wrong one), and manual work had no place in the model.

### The model

A **Task performs one or more ordered Actions** (`HAS_ACTION`). An **Action** is a node with a secondary label for its verb. Actions are shared nodes generated from the Paper jar; a task points at the ones it performs.

| Kind | Verb labels | What the action holds | Where the requirements live | Built |
|---|---|---|---|---|
| **Gathering** | `Mine`, `Kill`, `Shear` | The action **is** the definition: its target (`Block` or `Mob`), the tool, and what it drops | On the action itself | 1,099 + 63 + 4 |
| **Transforming** | `Craft`, `Smelt`, `Smith`, `Cut` | Only the verb and the item it makes | On the `Recipe` nodes that create the same item (ingredients, station) | 949 + 61 + 12 + 197 |

A transforming action and its recipes meet at the `Item`; there is no edge between them, so "Smelt Iron Ingot" can be done by any smelting-type recipe (raw iron, iron ore, deepslate iron ore, blasting). One transforming action is generated per verb and item, and the verb follows the recipe type:

| Verb | Recipe types |
|---|---|
| `Craft` | crafting (shaped, shapeless, transmute, special and dye) |
| `Smelt` | smelting, blasting, smoking, campfire cooking |
| `Smith` | smithing |
| `Cut` | stonecutting |

The verbs follow the roles in the organization (a Smelter smelts, a Smith smiths). More verbs can be added later (Fish, Barter, Store, Move...) without changing the rest of the model.

### Nodes and properties

| Label | Key | Properties | Notes |
|---|---|---|---|
| `Action` + verb label | `id`: `<verb>:<id>` (`mine:minecraft:iron_ore`, `kill:minecraft:spider`, `shear:minecraft:sheep`, `craft:minecraft:iron_pickaxe`, `smelt:minecraft:iron_ingot`) | `name` ("Mine Iron Ore", "Craft Iron Pickaxe"); gathering actions also `tool` (type: `pickaxe`, `axe`, `shovel`, `hoe`, `shears` or none), `minTier` (`wooden`, `stone`, `copper`, `iron`, `diamond`, `netherite` or none), `toolRequired` | Generated from the jar; regenerated on every rebuild. |

`Block`, `Mob`, `Recipe` and `Item` (with `ItemClass` and `Station`) are unchanged. Tools are ordinary `Item`s (a stone pickaxe has its own recipe); there is no `Tool` node. `Task` keeps `id`, `name`, `description`, `status`, `automatable` and the candidate fields; its `type` property is gone (a task with no actions is manual).

### Relationships

| Relationship | From -> To | Properties | Meaning |
|---|---|---|---|
| `HAS_ACTION` | `Task` -> `Action` | `order` (1, 2, 3...), `quantity` (items to make; default 1) | The task's steps, in order. Quantity is per task, so it sits on the link, because the action is shared. |
| `CREATES` | `Action` -> `Item` | gathering: `count` (typical), `condition` (blocks: `default`, `silk_touch`, `shears`, `state`, `chance`; mobs: `default` or a `+`-joined mix of `killed_by_player`, `chance`, `state`) | What the action yields. For a transforming action it is just the item it makes. |
| `REQUIRES` | `Action` -> `Block` or `Mob` | `role: 'target'` | What is mined, killed or sheared. |
| `REQUIRES` | `Action` -> `Item` | `role: 'tool'`, `orBetter` | The minimum tool (Stone Pickaxe, `orBetter: true`) or Shears (`orBetter: false`). Optional. |

`REQUIRES` has several roles; the `role` property tells them apart: `ingredient` and the other recipe roles (on `Recipe`), `tool` and `target` (on gathering actions).

### Rules

- **One role per task.** All of a task's actions are done by the same role. A change of role is a `NEXT` plus `HANDOFF_TO` between tasks. Within a task the order is `HAS_ACTION.order`; between tasks it is `NEXT`.
- **Actions are checked by id.** `load_model.py` fails the load if a task names an action that is not in the graph, or a quantity that is not a whole number of 1 or more.
- **Manual work** is a task with no actions. Actions for manual verbs (Store, Move) are not modelled yet.
- **No recipe pin.** An earlier draft let a task pin one recipe (`using`). It was dropped: the route picks a method by item, and the chain between tasks is checked through the items.

### Example: the Iron tool line

From [model/processes/iron-tool-line.yaml](../model/processes/iron-tool-line.yaml). A task names the actions it performs by id:

```yaml
- id: iron-tool-line/mine-iron-ore
  role: miner
  actions:
    - {do: "mine:minecraft:iron_ore", quantity: 3}      # drops raw iron; needs a stone pickaxe or better

- id: iron-tool-line/smelt-raw-iron
  role: smelter
  actions:
    - {do: "smelt:minecraft:iron_ingot", quantity: 3}

- id: iron-tool-line/make-sticks                         # one task, two ordered actions, one role
  role: crafter
  actions:
    - {do: "craft:minecraft:oak_planks", quantity: 2}
    - {do: "craft:minecraft:stick", quantity: 4}

- id: iron-tool-line/stock-tools-chest                   # manual: no actions
  role: storekeeper
```

Loaded: 6 tasks, 6 `HAS_ACTION` links, 5 `NEXT`, 4 handoffs. Checked in the graph: "Mine Iron Ore" requires the Iron Ore block and a Stone Pickaxe or better and creates Raw Iron (default) and Iron Ore (silk touch); "Kill Spider" requires the Spider mob and creates String and Spider Eye; "Smelt Iron Ingot" meets the smelting recipes at the Iron Ingot item.

### What it enables

- A task can be authored without choosing a recipe; the route picks it (the agent already does this by item).
- A task can carry several steps.
- One query for "what produces this item": `(a:Action)-[:CREATES]->(i)` finds mining, killing, shearing, crafting, smelting, smithing and cutting alike.
- Gap checks across a process: the tool a gathering action needs is an item; if no earlier action creates it (or a better one), the process has a gap. `load_model.py` prints these as notes.

### What changed in the code

| Area | Change |
|---|---|
| `loader/load_actions.py` (was `load_harvests.py`) | Builds the gathering actions from loot tables and block tags, and one thin transforming action per verb and item from the recipes. Deletes and recreates every `Action` (and any old `Harvest`). |
| `loader/load_model.py`, `model/processes/*.yaml` | Tasks list `actions:`; `HAS_ACTION {order, quantity}` is written; `Task.type` is removed; old task-to-recipe/harvest links are cleared. |
| `agent/minecorp_agent/tools.py` | `mined_from`, `sources_of`, `silk_only` and `how_to_get` read `Action` nodes (verb labels and `REQUIRES {role:'target'}`); answers are unchanged. |
| Rebuild | The graph is rebuilt from source whenever required (Paper jar plus `model/`). Order: `load_recipes.py --all --wipe`, `load_actions.py`, `load_model.py`, `compute_routes.py` (30 seconds). Not rebuildable from source: `COMPLETED` history, future event data and agent memory (none yet). |

### Decisions (2026-10-02)

| Decision |
|---|
| `Action` is a node with a secondary label for its verb (Craft, Smelt, Smith, Cut, Mine, Kill, Shear). |
| `Harvest` is gone: the Mine, Kill and Shear actions carry its target, tool and drops. |
| A craft-type action crafts an item and the `Recipe` creates the item; they meet at the `Item`. |
| Actions are shared, generated nodes; the task's link carries `order` and `quantity`. |
| The link is `HAS_ACTION` (not `PERFORM_ACTION`, which is easy to confuse with `PERFORMS`). |
| No recipe pin (`using`). |
| The Smelt, Smith and Cut actions are generated now, for a more complete graph of how Minecraft works. |
| A task may have several ordered actions; one role per task. |
| Manual verbs (Store, Move) are left out for now. |
| Tools are ordinary Items; no separate `Tool` node. |
| The graph can be rebuilt from source if required. |

## v0.8: lexical graph of the Minecraft wiki (designed and loader built 2026-10-04)

An experiment: turn wiki pages into a lexical graph (pages, sections, overlapping chunks, embeddings) in the same database, so the agent can later combine text retrieval with the domain graph (GraphRAG). First test page: https://minecraft.wiki/w/Tutorial:Beginner%27s_guide. The wiki text is CC BY-NC-SA, so every node keeps its source URL for attribution. Wiki content is **candidate** knowledge, not authoritative like the jar data.

### The model

```
(Wiki {url, name})
  -[:HAS_PAGE]-> (Page {url, title, revisionId, fetchedAt})
      -[:HAS_SECTION]-> (Section {url, title, level, order})        top-level sections only
            -[:HAS_SUBSECTION]-> (Section)                          the tree, as deep as the wiki goes

(Chunk {id, index, text, tokens, textHash, embedding, embeddingModel})
      -[:PART_OF]-> (Section)                                       the section the text came from
      -[:NEXT]-> (Chunk)                                            reading order across the whole page
```

### Nodes and properties

| Label | Properties | Notes |
|---|---|---|
| `Wiki` | `url`, `name` | unique `url` |
| `Page` | `url`, `title`, `revisionId`, `fetchedAt` | unique `url`; title decoded (`%27` is an apostrophe) |
| `Section` | `url`, `title`, `level`, `order` | unique `url` (page URL plus `#anchor`); text before the first heading is a section titled with the page title, `order` 0 |
| `Chunk` | `id`, `index`, `text`, `tokens`, `textHash`, `embedding`, `embeddingModel` | unique `id` (section url plus position); `index` is the position on the page; `text` is the raw chunk |

### Rules

- **One kind of chunk:** fixed size, overlapping, **300 tokens with a 50-token overlap** (`tiktoken`, `cl100k_base`); both are command-line options.
- A chunk is built **within one section** and never crosses a heading, so `PART_OF` is exact; overlap exists only between chunks of the same section.
- A section of 300 tokens or fewer is **one chunk**, no overlap.
- Cuts fall at sentence boundaries, falling back to word boundaries for a very long sentence.
- `NEXT` follows reading order across section boundaries, so the chain runs through the whole page.
- **Embedded text** is the heading path plus the chunk text (for example "Beginner's guide > Shelter > ..."); the heading path does not count towards the 300 tokens and is not stored in `text`.
- Embedding model `text-embedding-3-small` (1536 dimensions, cosine). One vector index on `Chunk.embedding`.
- Constraints: unique `Wiki.url`, `Page.url`, `Section.url`, `Chunk.id`.
- **Embedding cache:** a local `shelve` store (git-ignored, `loader/cache/`), keyed by model, dimensions and the sha256 of the exact embedded text, holding the vector as float32 bytes. A rebuild (wipe, reload) therefore makes no OpenAI calls for unchanged text.
- **Provenance:** `revisionId` and `fetchedAt` on `Page`; `textHash`, `embeddingModel` and `tokens` on `Chunk`.
- Pages come from the MediaWiki API (`action=parse`) with a polite User-Agent, not by scraping.
- Same database as the rest of the graph. The loader's `--wipe` removes only `Wiki`, `Page`, `Section` and `Chunk` nodes.

### Agent tools (built 2026-10-04)

`search_wiki(question, limit)` embeds the question with the model the chunks carry (`embeddingModel`) and runs the vector index with the Cypher `SEARCH` clause (the older `db.index.vector.queryNodes` procedure is deprecated in Neo4j 2026.09). Hits in the same section are merged into one passage (the overlap between chunks is not repeated); each result has the page, the section path, `sectionUrl` and a score. `read_section(section, before, after)` returns a whole section by URL or unique title, its subsections, and optionally the chunks before and after it in reading order (`NEXT`, never across pages). The wiki is candidate (community) knowledge: results carry a source note and the agent attributes answers to the wiki; recipes, ingredients and drops stay with the game-data tools.

### Not in v0.8 (v0.9 covers the first two)

- `(Chunk)-[:MENTIONS]->(Item|Block|Mob)` and links from guide sections to `Task` and `Activity`: the link that makes it GraphRAG; added once retrieval works.
- A full-text index on `Chunk.text` (for hybrid search).
- Judging the chunk size: compare with about 10 questions whose answer section is known (is it in the top 3?); change the size or overlap if it fragments too much.

## v0.9: linking the wiki to the game graph (built 2026-10-04; written to the real database 2026-10-05)

The wiki chunks (v0.8) say useful things about items, mobs and blocks, but nothing connected the text to the game data. v0.9 keeps the wiki's own structure (its links and its redirects) as graph, and derives two relationships from it by following paths. Everything derived is a **candidate** (governance): a person reviews it.

```
(Chunk)-[:LINKS_TO {count, texts}]->(Page)-[:REDIRECTS_TO]->(Page)       the wiki, as found
(Page)-[:ABOUT {method, match, ambiguous, candidates, status}]->(node)    a page is about the nodes its title names
(Chunk)-[:MENTIONS {method, count, linkCount, surfaces, corroborated,
                    ambiguous, candidates, status}]->(node)               a chunk is about the nodes it points at
        node = (:Item) | (:Mob) | (:Block that is not also an Item) | (:Biome) | (:Structure)
```

- **One `Page` per URL.** A page that is only linked to is a stub `Page` (`url`, `title`; no `fetchedAt`, no sections). Loading it later fills in the same node, and the links to it stay. A redirect is a page too: **`(:Page {title: 'Black Bed'})-[:REDIRECTS_TO]->(:Page {title: 'Bed'})`**. A link goes to the page *as written*, never silently to where it lands, because a redirect can hide what was meant: "Black Bed" is the item Black Bed, not the category Bed. Links to files, categories, special pages, red links, external sites and the page itself are dropped; a link in the overlap of two chunks belongs to both; links never change chunk text or hashes.
- **`ABOUT`**: a page is about every node whose name is its title (case-insensitive, plural and singular forms allowed; an exact name beats a plural). The node says what it is: its labels (`Item`, `ItemClass`, `Station`, `Mob`, `Block`) are the context, so nothing about the kind is copied onto the relationship. A block that is also an item (the torch) is not counted twice.
- **`MENTIONS`** from a link follows a path: `(Chunk)-[:LINKS_TO]->(Page)-[:ABOUT]->(node)`, and when the linked page is about nothing the path may take one `REDIRECTS_TO` step first, so the nearest page that is about something wins ("Black Bed" decides before "Bed"; "Wood", which is about nothing, falls through to "Logs"). The editor said what the words mean, so shorthand works: "iron" linking to Iron Ingot is `linkCount: 1`.
- **`MENTIONS`** from a name is found by scanning the chunk text (the one job the graph cannot do): longest name first, whole words, any case, plural forms but never singular ones (so the verb "leave" is not the category Leaves). The scan hands plain strings to the graph, which says which nodes carry each name.
- **Ambiguity stays in the graph.** When a title or a name can mean several nodes of the same quality (the page "Chicken" is the mob and the item; "dirt" is the item and the category of dirt blocks) there is a relationship to **each** of them, with `ambiguous: true` and `candidates: n`. Whatever reads the graph next, with the chunk as context, decides.
- **A link is evidence, and the absence of one says nothing**: editors link the first mention only (on the test page torch is mentioned 11 times and linked 3, chest 17 and 2, zombie 8 and 1). `corroborated` is true when the same wiki page links the node somewhere; name-only mentions that are not corroborated are the review list. Count distinct nodes per section, not sums: chunks overlap.
- **Origin.** The job's relationships carry `origin: 'link_wiki'`; that is all it replaces. A relationship a person adds by hand (another origin) is kept, and an `ABOUT` made by hand is followed like any other.
- **Human review file** `model/wiki_links.yaml`: `stoplist` (generic words the name scan ignores), `aliases` (other wording for a node: a string is an item id, `{mob: id}` and `{block: id}` name the others, a list names several) and `pages` (fixes to what a page is about; a fix is `status: confirmed`; `null` says the page is about nothing in the game). Neither list affects link evidence.

### The job

`loader/link_wiki.py` is a write job like `compute_routes.py`; no OpenAI; deterministic. The graph work is Cypher: titles to names, the link-and-redirect paths, merging the two kinds of evidence, corroboration. It runs in **one transaction** (old links are never removed without the new ones being written). `--dry-run` runs everything, reads the report from the result, and **rolls back**. The report lists the ambiguous pages, the linked pages that are about nothing in the game (a free list of concepts the ontology could add), the name-only mentions the page never links (where generic words show up), and the nodes found only through links. Rebuild order: the game graph, then `load_wiki.py`, then `link_wiki.py` (`load_recipes.py --wipe` removes the wiki nodes too).

### Measured on the one test page (Tutorial:Beginner's guide), dry run

The page links 302 times to 225 other pages, 63 of them redirects. 112 pages are about something in the game (118 `ABOUT` relationships; 103 exact, 15 plural or singular). 6 pages are ambiguous and linked to every candidate: Arrow, Chicken, Chickens, Compass, Sand and Terracotta (the item and its category, and for Chicken the item and the mob). 89 linked pages are about nothing in the game: Java Edition, Bedrock Edition, Crafting, Hay bale, Biomes, Controls, Hunger, Village... The chunks have 263 `MENTIONS` over 135 nodes (115 items, 15 mobs, 5 blocks): 29 from links only, 118 from names only, 116 from both; 40 are ambiguous. 24 nodes are found only through the wiki's links (Black Bed ... Yellow Bed, Iron Ingot, Copper Ingot, Wooden Sword, Wooden Shovel, Wooden Hoe, Boats, Dyes, Cactus, Carrot). 36 nodes are mentioned by name but never linked on the page: some real (leather, pumpkin, obsidian, apple), some generic words (axe, hoe, book, copper, wall). Precision has not been checked by hand; the stoplist, aliases and fixes are empty until you review the report.

### Not in v0.9

- A `wiki_about(item)` agent tool (the passages that mention a given node), the other direction of what `search_wiki` now does.
- Linking to `Recipe`, and an LLM extractor (implied mentions, relationship types such as recommends or warns about), to be measured against this deterministic baseline.
- Modelling the concept pages the wiki links (Biome, Hunger, Village, Controls).

## v0.10: the world: mobs, biomes, structures and ore (built and loaded 2026-10-05)

Where things are. Everything here is read from the Paper jar by `loader/load_world.py` (so it can be rebuilt from source), and it connects to what is already there: the `Mob` and `Block` nodes, and through them the `Mine` and `Kill` actions that give items.

```
(MobClass)-[:INCLUDES]->(Mob | MobClass)            entity-type tags: Undead, Illager, Can Equip Saddle...
(Biome)-[:SPAWNS {category, weight, min, max}]->(Mob)
(BiomeClass)-[:INCLUDES]->(Biome | BiomeClass)      biome tags: Is Forest, Has Structure Village Plains...
(Dimension {minY, height})-[:HAS_BIOME]->(BiomeClass)                       overworld, the nether, the end
(Structure {type, step, suppressedSpawns})-[:GENERATES_IN]->(BiomeClass)
(Structure)-[:SPAWNS {category, boundingBox, weight, min, max}]->(Mob)

(Biome)-[:GENERATES {step}]->(PlacedFeature:Ore)-[:PLACES]->(Feature)-[:CREATES {replaces}]->(Block)
(PlacedFeature)-[:IN_DIMENSION]->(Dimension)
```

- **Mobs are all the creatures**, not only those that drop something. The jar has 93 top-level entity loot tables (the 16 nested ones are sheep colours); every spawner uses one of them. Without `armor_stand`, `player` and `mannequin` that is **90 mobs** (`load_actions` made 63, the ones with drops). A mob that drops nothing is just a `Mob` with no `Kill` action: the villager is one, so "does it drop anything" is answered by the graph, not by absence of a node.
- **Classes are separate labels** (`MobClass`, `BiomeClass`), unlike item categories, which are `Item` nodes with a marker label because recipes require them in place of an item. Nothing requires a class in place of a mob or a biome, and keeping them apart means a class is never mistaken for a creature or a place. They use `INCLUDES` and the `#` ids like item categories, and nest like tags do (Undead includes Zombies includes the zombie). A class is kept only when it reaches at least one mob or biome (44 of 48 entity tags; tags about boats and arrows are dropped; 68 biome tags). To find a structure's biomes, walk `GENERATES_IN` then `INCLUDES*`; nothing is expanded or copied.
- **Dimensions** come from the dimension types (the overworld caves variant is left out) and the `is_overworld`, `is_nether` and `is_end` biome tags. No biome is in two dimensions; `the_void` is in none.
- **Structures** (34) generate in the biome class their data names. A spawn override that lists mobs becomes `SPAWNS` (a fortress spawns blazes and wither skeletons); one that lists none means that category does not spawn there, kept as `suppressedSpawns` (an ancient city suppresses all eight).
- **Ore is two shared things.** A `Feature` is what is made: a vein of a size that turns one kind of stone into one kind of ore (`CREATES {replaces}` names the block or tag it replaces). A `PlacedFeature` is where and how often: veins per chunk (`countMin`, `countMax`), `rarityChance` (1 in n chunks), a height range, and the biomes that list it. They are separate because both are shared: 8 of the 32 ore features have two placements (`ore_iron` is placed high and in the middle; redstone, gold and quartz also have two), and 35 of the 40 placements are listed by more than one biome (the upper iron by 55). The name `Feature` in Minecraft is the generator type in code, so the configured one is called `Feature` here only because that is how the data reads it. Only ore placements are loaded (40 of 262, using 32 features); trees, vegetation, lakes and geodes would use the same shape. The marker label `Ore` follows the `ItemClass` and `Station` pattern.
- **Heights are real block heights.** The data gives each end as an anchor: absolute, above the bottom of the world, or below the top (the top block is `minY + height - 1`). They are resolved with the dimension the placement is generated in (found through the biomes that list it) and clamped to the world: `minY`, `maxY`, and `peakY` where a trapezoid is commonest (its middle, before clamping). The raw anchors are kept as text. The numbers agree with the figures commonly quoted for these ores (from memory, not re-checked against the wiki here), which is a sanity check on the arithmetic, not a proof: iron peaks at y=232 (upper) and 16 (middle), coal 96, copper 48, lapis 0, gold -16, emerald 232, and every diamond placement lies between -64 and 16.
- **The path answers "where do I mine iron".** `Item raw iron <-CREATES- Mine -REQUIRES-> Block iron_ore <-CREATES- Feature <-PLACES- PlacedFeature <-GENERATES- Biome` gives the three placements (y 80 to 319 peaking at 232, 90 veins per chunk; y -24 to 56, peaking at 16; y -64 to 72) in 55 biomes. Ancient debris is only in the nether, emeralds only in 10 biomes.
- **Wiki and the world.** The link job names `Biome` and `Structure` nodes too (classes are not named), so the wiki pages Desert, Plains and Igloo become `ABOUT` those nodes and text mentioning them becomes `MENTIONS`. `model/wiki_links.yaml` accepts `{biome: id}` and `{structure: id}`.

### Rebuild order

`load_recipes.py --all --wipe`, `load_actions.py`, **`load_world.py`**, `load_trades.py` (v0.12), `load_model.py`, `compute_routes.py`, then `load_wiki.py` and `link_wiki.py`. The world loader needs the `Block` and `Mob` nodes the actions loader makes; it deletes and recreates its own nodes and merges the mobs, and `load_actions.py` leaves them alone.

### Measured on the 26.2 jar

90 mobs, 44 mob classes, 66 biomes, 68 biome classes, 34 structures, 3 dimensions, 795 biome spawns and 9 structure spawns, 34 structure-to-biome links, 32 ore features, 40 ore placements and 1,433 links from biomes to them.

### Not in v0.10

- The other 222 placed features (trees, vegetation, lakes, geodes), structure sets (spacing and frequency), carvers, spawn costs.
- Chest loot, fishing and bartering (these fit the `Action` model), enchantments and advancements. Villager trades are built in v0.12.
- Mob and block statistics (health, damage, hardness) and brewing, which are in game code, not the data files.
- Agent tools that read the world (parked while the tools are reviewed). `search_wiki` already lists Biome and Structure mentions.

## v0.12: trading (built 2026-10-08)

Villager and wandering trader trades from the Paper jar, which holds them as data (`villager_trade`, `trade_set` and the tags that list a set). `loader/load_trades.py`; rebuild order: after `load_world.py`, before `load_model.py`.

```
(Action:Trade {maxUses, reputationDiscount, xp, variablePrice, enchanted, enchantLevels, villagerTypes})
    -[:REQUIRES {role: 'price', count}]->(Item)        what the villager wants
    -[:REQUIRES {role: 'additional', count}]->(Item)   a second item some trades also want
    -[:CREATES {count}]->(Item)                        what you get
    -[:LOCATES {decoration, radius}]->(Structure)      an exploration map and the structure it leads to
(Profession)-[:OFFERS {level}]->(TradeSet {amount})-[:INCLUDES]->(Trade)
(Mob wandering_trader)-[:OFFERS {pool}]->(TradeSet {amount})-[:INCLUDES]->(Trade)
(Mob villager)-[:HAS_PROFESSION]->(Profession)
```

- **A trade is an `Action`** with the verb label `Trade`, like `Mine`, `Kill` and `Shear`: what it needs and what it gives are the same relationships (`REQUIRES`, `CREATES`), so a task can perform it (`trade:minecraft:farmer/1/wheat_emerald`) and the "how do I get X" path can reach it. The gathering queries name their verbs (`Mine`, `Kill`, `Shear`), so trades do not appear in them until the tools are taught to.
- **A trade set is the pool a villager draws from at one level.** The game picks `amount` trades from the set at random; eight sets ask for one more than they hold, and the villager then offers what there is. The same trade can be in several sets (the armorer, toolsmith and weaponsmith share three). `OFFERS {level}` puts the level (1 to 5) on the relationship; the wandering trader has pools (`buying`, `common`, `uncommon`) instead of levels.
- **A price that varies has no count.** A librarian's enchanted book costs by the enchantment, so its price has `variablePrice` and its `REQUIRES` has no `count`. A trade for an enchanted item says `enchanted` (and `enchantLevels`, such as "5 to 19", for armour and tools); there is no enchantment model yet.
- **Some trades are only offered by some villagers.** A map to a swamp hut is only sold by jungle, snow and taiga villagers: `villagerTypes`, empty when every villager offers it.
- **A map leads somewhere.** The cartographer's ten exploration maps `LOCATES` the structure of their destination tag (woodland mansion, ocean monument, trial chambers, the villages, the jungle temple, the swamp hut), so the trading and world graphs meet at the structure.
- **Not loaded:** the optional trade rebalance datapack that ships inside the jar; the profession's workstation and the villager's biome type (the game's code, not its data).

Measured on the 26.2 jar: 388 trades (every one is in a set), 68 trade sets, 13 professions, 320 items traded, 394 set-to-trade links, 4 trades with a variable price, 21 for an enchanted item, 27 limited to some village types, 10 maps. Every trade involves emeralds (87 give them, the rest cost them), and no trade gives a diamond. The Trading tutorial of the wiki is in the lexical graph from v0.12.

## v0.11: the player's processes (built 2026-10-07)

### Why

The company model of v0.6 and v0.7 copied the knowledge-layer posts: five organization units, nine roles (four of them governance roles), owners, hand-offs and escalations. None of it fits Minecraft played by one person. The operational roles (Miner, Smelter, Crafter, Smith) were the action verbs renamed, the one process (the Iron tool line) repeated a route the agent already works out from the game data, and with one player filling every role a hand-off or an escalation could never mean anything. The manifesto is a guide, not a mapping to be matched exactly.

### The model

- **One role: the player.** `OrgUnit`, `Role`, `PART_OF`, `IN_UNIT`, `FILLED_BY`, `OWNS`, `PERFORMS`, `HANDOFF_TO` and `ESCALATES_TO` are gone, with the task properties `automatable` and `role` and the process properties `family`, `criticality`, `sla` and `owner`. Minecraft can be played together but mostly is not; roles can come back if that changes.
- **A process is a goal a player works towards.** `Process` is the goal ("Get to the Nether"), `Activity` a stage ("Get obsidian", "Build the frame") and `Task` a step. The labels and the shape (`HAS_ACTIVITY`, `HAS_TASK`, `NEXT`) are unchanged, so the mapping to the manifesto's process schema still reads.
- **Known and unknown.** The *known* processes are the structured game: recipes, mining, drops and spawns, already derived from the jar and followed by `what_do_i_need` and `where_to_find`. They are not authored. A process holds the *unknown*: what a player has to do that no data file says (shelter, mob farms, getting to the Nether, defeating the dragon, getting emeralds).
- **Order is on the relationships.** `HAS_ACTIVITY {seq}` numbers a process's stages and `HAS_TASK {seq}` its steps within a stage (from 1, in the order of the YAML file), as `HAS_ACTION {order}` does for a task's actions. The nodes carry no order, so an activity or task reads the same wherever it is used.
- **A task is structured or manual.** It names the game actions it performs (`HAS_ACTION`) when the data has them, and has none when it is a manual step (build the frame). A route's alternatives are data: `NEXT` carries a `condition` and `isDefault`.
- **A process can build on others.** `builds_on: [nether-portal]` in a process file becomes `(Process)-[:BUILDS_ON]->(Process)`: the dragon is a larger goal that needs the Nether, so it points at that process instead of repeating its tasks. The loader checks that the names exist, that a process does not build on itself and that none is listed twice.
- **Everything is a candidate until a player confirms it.** A process file says `status: candidate` with `source` (the wiki section the steps come from), `extracted_at` and `confidence`, or `status: confirmed` with `confirmed_by` (a player) and `confirmed_at`. A confirmed task has `(Task)-[:CONFIRMED_BY {at}]->(Player)`.

### What was built

`loader/load_model.py` reads `model/players.yaml` and `model/processes/*.yaml` (see [model/processes/README.md](../model/processes/README.md) for the file format); with no process file it loads the player only. A load removes `OrgUnit` and `Role` nodes and their constraints from a database that still has them, and keeps tasks that hold `COMPLETED` history. The authored organization and the Iron tool line were deleted. The tests load a sample process from `tests/loader/fixtures/` because the real model has none yet.

### Next

The five processes, the shelter, emeralds, the Nether portal, the ender dragon and a simple mob farm (`model/processes/`), are drafted as candidates, and tasks of three of them perform real `Trade` actions. The dragon `BUILDS_ON` the Nether portal (v0.11 and later: `builds_on: [nether-portal]` in the process file). That completes the owner's list. (several need wiki pages that are not loaded yet). After that: links from a task to the things it involves (items, mobs, biomes, structures) and to the wiki sections that explain it, a "builds on" link between processes (the dragon builds on the Nether), and an agent tool that reads a process.

## Mapping to the Neo4j knowledge-layer process model (earlier hypothesis, superseded by v0.6 above)

| Knowledge-layer concept | MineCorp candidate |
|---|---|
| BusinessProcess | A MineCorp product line, e.g. "Iron tools" (to be authored; not in vanilla data) |
| BusinessActivity | A stage, e.g. "Smelt iron", "Craft tools" |
| BusinessTask | A `Task` that performs `Action`s (atomic, delegatable, has inputs/outputs) |
| Role | A MineCorp job, e.g. Smelter; stations are tools of a Role, not Roles themselves |
| `NEXT` (routing as data) | Derived from output of one task being input of the next, plus conditions (fuel, location) |

Decided (v0.2): `Task` is separate from `Recipe`. That keeps vanilla data (authoritative, regenerated from the jar) apart from MineCorp's curated process model (candidate -> confirmed). Still to model: `Activity`, `Process`, `Role`, `NEXT`.

## Governance (from the blog posts)

- Vanilla recipe/tag data is **authoritative**: regenerated from the server jar per version, never edited by hand. A `source` property for provenance is deferred (removed from the model in v0.3 as not needed yet).
- MineCorp processes, roles and any wiki- or LLM-derived links are **candidates** (`status`, later `source`, `extractedAt`, `confidence`) until you confirm them.

## Decisions

| Date | Decision |
|---|---|
| 2026-10-02 | `Task` is a separate node from `Recipe`; `(Player)-[:COMPLETED]->(Task)`. |
| 2026-10-02 | `group` stays a property of `Recipe`, not a node. |
| 2026-10-02 | Store the shaped grid (`pattern`, `key`) on `Recipe` for the agent to show. |
| 2026-10-02 | Item metadata beyond recipes (stack size, drops, etc.) is out of scope for now. |
| 2026-10-02 | Minecraft versioning: ignore for now; use a separate graph if a new version is ever needed. |
| 2026-10-02 | Label for categories is `ItemClass` (user choice). Names use the common word shared by the members where possible (e.g. "Bed"). Relationships renamed: `CONSUMES` -> `REQUIRES`, `PRODUCES` -> `CREATES` (user choice, v0.4). |
| 2026-10-08 | v0.12 (built): villager trades from the jar's data. A trade is an `Action` with the verb label `Trade` (price in `REQUIRES`, result in `CREATES`); a profession offers trade sets by level and a set includes the trades the game draws from. Maps `LOCATES` structures. The Trading wiki page joined the lexical graph. |
| 2026-10-07 | v0.11 (built): one role (the player), no organization. A process is a goal a player works towards (get to the Nether), holding the unknown part of the game; the known part (recipes, mining, drops, spawns) is derived and not authored. The Iron tool line was removed. Order of the first processes: shelter, emeralds, Nether, dragon, mob farm. |
| 2026-10-05 | v0.10 (built): the world from the jar. Every creature is a `Mob` (90), a mob that drops nothing has no `Kill` action. Tag classes are separate labels (`MobClass`, `BiomeClass`) with `INCLUDES`, not marker labels. Ore is a shared `Feature` and a shared `PlacedFeature:Ore`, heights resolved to block heights and clamped to the world. Only ore placements for now. Link targets widened to Biome and Structure. |
| 2026-10-04 | v0.9 (built): one `Page` per URL with stubs for pages only linked to; redirects are pages with `REDIRECTS_TO`; `(Chunk)-[:LINKS_TO]->(Page)`; `(Page)-[:ABOUT]->(node)` and `(Chunk)-[:MENTIONS]->(node)` for Item, Mob and Block, derived by following paths, as candidates; an ambiguous title or name is linked to every candidate with `ambiguous` and `candidates` (the reader decides with context); nothing about the node kind is stored on the relationship; links are positive evidence only; human review file `model/wiki_links.yaml`. |
| 2026-10-04 | v0.8 (loader built): wiki lexical graph `Wiki` > `Page` > `Section` tree, with `Chunk` `PART_OF` a section and linked by `NEXT`; one chunk kind, 300 tokens with 50 overlap within a section (small section = one chunk); `text-embedding-3-small`; local `shelve` cache of `{hash: embedding}` for rebuilds; provenance stored; MediaWiki API; same database. |
| 2026-10-02 | v0.7: `Action` node with a verb label replaces `Harvest` and the direct Task-to-Recipe link; `HAS_ACTION {order, quantity}`; thin transforming actions generated for every verb and item; no recipe pin; built. |
| 2026-10-02 | The graph can be rebuilt from source if required (Paper jar plus `model/`); the rebuild order is the rule. |
| 2026-10-02 | v0.6: mining modelled as a `Harvest` node parallel to `Recipe`; tool requirement is the minimum tool item with `orBetter`; first load blocks only; org units and roles accepted for now. Built for harvests. |
| 2026-10-02 | v0.5: route summary stored as properties on `Item` (`routeSteps`, `routeLevels`, `routeBaseMaterials`, `routeStations`, `routeTruncated`), computed by a separate write job; used to rank recipes by difficulty. |
| 2026-10-02 | v0.4: one `Item` label for everything a recipe requires; categories are `Item:ItemClass` (replacing `ItemTag`), linked by `INCLUDES`. |
| 2026-10-02 | v0.4: category ids keep the `#` prefix (`#minecraft:planks`); categories get readable names. |
| 2026-10-02 | v0.4: no vanilla default item per category; a representative, if wanted, is curated MineCorp data. |
| 2026-10-02 | v0.4: all REQUIRES edges are AND; an `ItemClass` target means any one member (OR) except `excludes`. |
| 2026-10-02 | v0.4: anonymous alternative lists that are a real tag minus one item point at the real tag, with the item in `excludes` on the edge (option 3 for the "Bed" problem). |

## Open questions

1. Block tags (`tags/block`) are not modelled; item tags (224) are. Checked: tag `values` are plain strings only (no `{id, required}` objects) in 26.2, so the loader can be simple.
2. How are `Task`s authored: by hand, or proposed by an LLM from recipe chains and confirmed by you (candidate -> confirmed)?
3. `Player` here overlaps with the agent-memory layer. Does the Player node get created by the plugin, or by the memory layer? Revisit when we reach memory.
4. Should MineCorp curate a representative item per category (e.g. "Oak Planks" for "Planks") for the agent to suggest by default?
5. What does completing a `Task` mean in practice: self-reported in chat, or detected by the plugin from game events (e.g. an item crafted)?

## Change log

- **v0.8 agent tools** (2026-10-04): `search_wiki` and `read_section` added to the agent (9 tools); 59 unit and 30 graph tests; 8 new eval questions, 27/27 passing. First real searches on the loaded Beginner's guide: "how do I skip the night" finds Skipping the night (0.84) and "what should I do on my first night" finds The first night (scores for unrelated questions are still around 0.7, so the score is not a relevance cutoff).
- **v0.12 built** (2026-10-08): villager and wandering trader trades (`Trade` actions, `TradeSet`, `Profession`; `loader/load_trades.py`), and the Trading tutorial in the lexical graph. See "v0.12".
- **v0.11 built** (2026-10-07): one role, the player. `OrgUnit`, `Role` and their relationships removed; a process is a goal a player works towards, made of stages and steps, holding what the game data does not; `model/org.yaml` became `model/players.yaml`; the Iron tool line was deleted; `CONFIRMED_BY` points at a `Player`. See "v0.11".
- **v0.10 built** (2026-10-05): `loader/load_world.py`: `Mob` completed to 90, `MobClass`, `Biome`, `BiomeClass`, `Structure`, `Dimension`, `Feature`, `PlacedFeature:Ore`, `SPAWNS`, `GENERATES_IN`, `HAS_BIOME`, `GENERATES`, `PLACES`, `IN_DIMENSION`; ore heights resolved to block heights. The link job now also names Biome and Structure. 75 unit, 52 real-jar and 28 graph tests; every fault injected into the loader was caught. Found while building: Neo4j does not allow constraints and data in one transaction (constraints first); the shared tag walk is order-dependent on cyclic tags (none in the data); `the_void` is in no dimension.
- **v0.9 agent use** (2026-10-05): `search_wiki` hits and `read_section` list the nodes a passage is about (`mentions`: name, kind from the node's labels, id, `linked`, `ambiguous`), read by following `MENTIONS`; ambiguous words list every candidate so the model chooses from the passage; the model follows a mention to `get_recipes` and the other game-data tools. 83 unit and 40 graph tests for the wiki tools; all 29 eval questions pass (the wiki-then-recipe question chains search_wiki, read_section and get_recipes).
- **v0.9 built** (2026-10-04): `LINKS_TO`, `REDIRECTS_TO`, stub pages, `ABOUT`, `MENTIONS`, built graph-native (the job is Cypher over paths; Python only scans text); ambiguity kept as a relationship to every candidate; targets Item, Mob and Block; `load_wiki.py` keeps links and redirects as pages and relationships; `loader/link_wiki.py` and `model/wiki_links.yaml` (stoplist, aliases, page fixes). 59 unit and 89 graph tests for the job, plus link tests for the loader. Bugs found while building: a text-less icon link shifted every later link out of place (fixed, and a test pins that links never change chunk text); redirects first collapsed every coloured bed into the category Bed (now redirects are pages); the first job write was lazy and not committed on return (now one transaction); wordings on a mention were in a different order on each run (now sorted). (`link_wiki.py` was then run for real on 2026-10-05.)
- **v0.8 loader built** (2026-10-04): `loader/load_wiki.py` with 112 unit and 24 graph tests; the test page gives 34 sections and 43 chunks (median 230 tokens, max 300) for about $0.0002 of embedding. Noted while building: recipe pictures (`mcui`, `invslot`) and zero-width characters are removed; an intro with no text is not a node; `NEXT` links chunks within one page; `load_recipes.py --wipe` also removes the wiki nodes, so the wiki is reloaded last (its embeddings come from the cache).
- **v0.8 DESIGNED** (2026-10-04): lexical graph of the Minecraft wiki (`Wiki`, `Page`, `Section` tree, `Chunk` with embedding, `PART_OF`, `NEXT`); chunking, embedding, cache and provenance rules decided. First test page: Tutorial:Beginner's guide. Not built.
- **v0.7** (2026-10-02): built. `Action` node with a verb label (Craft, Smelt, Smith, Cut, Mine, Kill, Shear) between `Task` and the vanilla `Recipe` nodes; `Harvest`, `FROM_BLOCK`, `FROM_MOB`, `CRAFTED` and `HARVESTED` removed (Mine, Kill and Shear actions carry target, tool and drops); transforming actions are thin and meet their `Recipe` nodes at the `Item`; `HAS_ACTION {order, quantity}`; several ordered actions per task with one role; no recipe pin; 2,385 actions generated; full rebuild from source in 30 seconds. `load_harvests.py` renamed `load_actions.py`.
- **v0.6.4** (2026-10-02): mob harvests built: `Mob` (63), `Harvest` kinds `kill` (63) and `shear` (4), `FROM_MOB`; loader handles loot tables that reference other tables (sheep colours). Agent: `getFrom` in routes, new `how_to_get` tool, route stop rule extended to mob sources. 1,166 harvests in total; `Item` 1,628.
- **v0.6.3** (2026-10-02): `Station` is now a marker label on `Item` (like `ItemClass`) instead of a separate node; `USES_STATION` points at the item (decision 8). Node labels table repaired: `Recipe` and `Item:Station` rows restored, and a garbled paragraph about duplicate class names corrected.
- **v0.6.2** (2026-10-02): relationships renamed `USED_RECIPE` -> `CRAFTED` and `USED_HARVEST` -> `HARVESTED` (graph reloaded; older change log entries keep the names used at the time); node labels in the diagram now use round brackets.
- **v0.6.1** (2026-10-02): graph model diagram replaced by one current picture of the whole built model (organization, MineCorp process, vanilla definitions, domain, computed route properties, reading rules); the two older partial diagrams removed.
- **v0.6 built** (2026-10-02): organization and process loaded from YAML (6 units, 9 roles, 1 process, 7 tasks); route rules revised after the iron ore finding (mined resources, silk-touch penalty, `mineFrom` hints); route properties recomputed; sample tasks removed from the recipe loader.
- **v0.6 harvest built** (2026-10-02): `Block` (1,113), `Harvest` (1,099), harvest `CREATES` (1,229), tool `REQUIRES` (502) loaded by `loader/load_harvests.py`; 52 new `Item` nodes. Organization and process parts remain proposed.
- **v0.6 PROPOSED** (2026-10-02): organization (`OrgUnit`, `Role`, `FILLED_BY`), process (`Process`, `Activity`, `NEXT`, `PERFORMS`, `OWNS`, handoffs, `CONFIRMED_BY`), vanilla `Harvest` and `Block` from loot tables and block tags; `Recipe` classified as process. Not built.
- **v0.5** (2026-10-02): route properties on craftable `Item`s, computed by `loader/compute_routes.py` (988 items in about 10 s; 1 truncated at depth 6: Waxed Chiseled Copper); `rank_recipes` agent tool. Hardest by the metric: Lime and Purple Glazed Terracotta (8 steps), then the Netherite tools (7 steps).
- **v0.4** (2026-10-02): built and loaded. Anonymous alternative lists map to real tags with `excludes` (66 of 76); only 10 inline classes remain. Full graph: 1,571 Item (234 ItemClass), 1,585 Recipe, 7 Station.
- **v0.4 PROPOSED, rev 2** (2026-10-02): label is `ItemClass` (marker label on `Item`); name rule uses the common word of the members (160 of 300 existing categories qualify; others fall back); `CONSUMES` -> `REQUIRES`, `PRODUCES` -> `CREATES`. The measured v0.3.1 sections keep the old names because they describe the graph as loaded.
- **v0.4 PROPOSED** (2026-10-02): `ItemTag` replaced by `Item:Class` (categories are Items with a marker label); `#` prefix on category ids to avoid six id collisions; readable category names; AND/OR reading of `CONSUMES` documented; no vanilla default item per category (noted). Not yet built.
- **v0.3.1** (2026-10-02): full graph loaded (`--all`); counts recorded above.
- **v0.3** (2026-10-02): `Recipe.name` added; `source` property removed from all nodes (not needed yet); relationships renamed `COMPLETES` -> `COMPLETED` and `USES_RECIPE` -> `USED_RECIPE`.
- **v0.2.1** (2026-10-02): added ASCII model and test-model description; loader draft and Neo4j docker config added.
- **v0.2** (2026-10-02): added `Task` (separate from `Recipe`, via `USED_RECIPE`), `Player` and `COMPLETED`; grid stored on `Recipe`; `group` stays a property; versioning and item metadata deferred. Verified tag entries are plain strings.
- **v0.1** (2026-10-02): initial draft from inspection of Paper 26.2 recipe and tag JSON.
