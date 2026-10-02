# MineCorp ontology

Living document. Each version records what changed and why. Status: **v0.6 BUILT and loaded (2026-10-02): recipes, harvests, organization and the Iron tool line process.**

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

## Graph model (v0.6, as built)

Every node label and relationship that is built, and nothing else: properties are in the tables below ("Node labels", "Nodes", "Relationships") so this stays readable. Round brackets are node labels. The organization and process nodes are authored in the YAML in `model/`; recipes, harvests, blocks and items come from the Paper server jar.

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
                          |   |    Task also has NEXT and HANDOFF_TO (to another Task),
                          |   |    and ESCALATES_TO and CONFIRMED_BY (to a Role)
          CRAFTED         |   |     HARVESTED
         +----------------+   +----------------+
         v                                     v
     (Recipe) --USES_STATION--> (Item)      (Harvest) --FROM_BLOCK--> (Block)
         |                                     |
         | CREATES                             | CREATES
         | REQUIRES                            | REQUIRES  (the tool, "or better")
         v                                     v
       (Item) <--------------------------------+
         ^
         | INCLUDES
         |
     (ItemClass)      an Item with an extra label: a category, which may include other categories
     (Station)        an Item with an extra label: where work is done (crafting table, furnace...)

```

**Reading rules**

- All `REQUIRES` edges of one recipe are needed together (AND). An `ItemClass` target means any ONE member (OR), except the items in `excludes`.
- A `Harvest` `REQUIRES` the minimum tool; that tool or any better one works (`orBetter`).
- A `Task` points at one definition: a `Recipe`, a `Harvest`, or neither (a manual task).
- Candidates (not yet confirmed) carry `source`, `extractedAt`, `confidence`; confirmed tasks have `CONFIRMED_BY`.

Loaded today (2026-10-02): `Item` 1,623 (of which `ItemClass` 234), `Recipe` 1,585, `Item:Station` 7, `Block` 1,113, `Harvest` 1,099, `OrgUnit` 6, `Role` 9, `Player` 1, `Process` 1, `Activity` 4, `Task` 7. Stations are `Item:Station` nodes, so a station has its own recipe. Craftable items also carry computed route properties (see "Route properties").

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
| `CRAFTED` | `Task` -> `Recipe` | | The vanilla recipe a task is carried out with. |
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

**Built:** the harvest part (`loader/load_harvests.py`) and the organization and process part (`loader/load_model.py`, reading `model/*.yaml`). Implements step 1 of [knowledge-layer.md](knowledge-layer.md) section 9. Everything above this heading describes what is loaded today (v0.5). Names marked provisional follow the Neo4j series chapters, which were only partly readable (chapter 1, operating structure, was not).

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
| `Harvest` | `id` (the loot table, e.g. `minecraft:blocks/iron_ore`) | `name` ("Mine Iron Ore"), `kind` (`mine`), `tool` (`pickaxe`, `axe`, `shovel`, `hoe` or none), `minTier` (`wooden`, `stone`, `copper`, `iron`, `diamond`, `netherite` or none) | One per block loot table. Derived: a harvest is to a block what a recipe is to a station. |

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
| `Activity` | `id` | `name`, `seq` | A stage of a process. |
| `Task` (built) | `id` | `name`, `description`, `status` (`candidate`, `confirmed`), `type` (`harvest`, `recipe`, `manual`), `automatable` (bool); for candidates also `source`, `extractedAt`, `confidence` | The delegatable unit. `manual` covers inventory work with no vanilla definition. |

### Relationships

| Relationship | From -> To | Properties | Meaning |
|---|---|---|---|
| `FROM_BLOCK` | `Harvest` -> `Block` | | What is mined |
| `CREATES` | `Harvest` -> `Item` | `count` (typical), `condition` (`default` or `silk_touch`) | What it yields; same relationship as `Recipe -[:CREATES]->` so routes can follow both |
| `REQUIRES` | `Harvest` -> `Item` | `role: 'tool'`, `orBetter: true` | The minimum tool item (for example Stone Pickaxe); any higher tier also works |
| `PART_OF` | `OrgUnit` -> `OrgUnit` | | Org structure |
| `IN_UNIT` | `Role` -> `OrgUnit` | | Where a role sits |
| `FILLED_BY` | `Role` -> `Player` | `from`, `to` (null while current) | Who holds a role, with dates. The one player may fill many roles at once. |
| `OWNS` | `Role` -> `Process` | | One owner per process |
| `HAS_ACTIVITY` | `Process` -> `Activity` | | |
| `HAS_TASK` | `Activity` -> `Task` | | |
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
Activity 1 Collect       T1 Mine iron ore    (Miner)      HARVESTED    minecraft:blocks/iron_ore     needs: Stone Pickaxe or better
                         T2 Mine oak logs    (Miner)      HARVESTED    minecraft:blocks/oak_log
Activity 2 Refine        T3 Smelt raw iron   (Smelter)    CRAFTED      iron_ingot_from_smelting_raw_iron
                         T4 Craft planks     (Crafter)    CRAFTED      oak_planks
                         T5 Craft sticks     (Crafter)    CRAFTED      stick
Activity 3 Fabricate     T6 Craft iron pickaxe (Crafter)  CRAFTED      iron_pickaxe
Activity 4 Store         T7 Stock the tools chest (Storekeeper)  type manual, no vanilla definition

NEXT: T1 -> T3 (raw iron collected, default)     T2 -> T4     T4 -> T5
      T3 -> T6     T5 -> T6     T6 -> T7 (HANDOFF_TO: Crafter to Storekeeper)
```

What the model lets the agent answer that it cannot now: who owns the line; the default path end to end; what comes after smelting; which tasks could be automated; **how to obtain a base material** (iron ore is mined, with a stone pickaxe or better); and gaps such as "T1 needs a stone pickaxe and nothing earlier in this process makes one".

### Single player

Every role is filled by the one player, so nothing here needs two people. `HANDOFF_TO` between roles is a change of hat (recorded as `COMPLETED.role`). No policy depends on telling players apart.

### How the data gets in

- **Vanilla:** a new `loader/load_harvests.py` reads block loot tables and block tags from the jar and creates `Block`, `Harvest`, `CREATES` and the minimum-tool `REQUIRES` edges (deterministic, regenerated like recipes). Derivation of the minimum tier: the lowest tier in the order wooden, stone, copper, iron, diamond, netherite that is not listed in `incorrect_for_<tier>_tool` for the block; the loader checks that every tier above it also works. Gold is ignored as a requirement because it mines like wood.
- **Authored:** the organization and processes are YAML files in a new `model/` folder (`org.yaml`, `processes/*.yaml`), version-controlled and human-edited. A new `loader/load_model.py` validates every `CRAFTED` and `HARVESTED` id against the graph and then merges the nodes. A bad reference fails the load.
- **Proposals:** later, an LLM job can draft process YAML from `what_do_i_need` routes as `status: candidate` with source and confidence. The Steward confirms by editing the file (loader writes `CONFIRMED_BY`).
- **Route calculation:** `what_do_i_need` can later report, for each base material, the harvest that gets it and the tool it needs.

### Harvest: built (blocks only, 2026-10-02)

`python loader/load_harvests.py` (run after `load_recipes.py --all`, before `compute_routes.py`; safe to repeat):

| | Count |
|---|---|
| `Block` | 1,113 (every block with a loot table) |
| `Harvest` | 1,099 (14 blocks drop nothing and get none) |
| Harvest `CREATES` edges | 1,229 (981 default, 143 silk touch, 59 block-state, 34 chance, 12 shears) |
| Tool `REQUIRES` edges | 502 (all pickaxe blocks) |
| New `Item` nodes | 52 (drops no recipe uses, such as saplings); `Item` total 1,623 |

Tool type: pickaxe 502, axe 246, shovel 34, hoe 29, none 288. Minimum tier among harvests that have a tool type: wooden 703, stone 91, iron 12, diamond 5 (the tier counts include blocks with no tool type, which have no tier). No block had a non-monotonic tier list, so "or better" is sound.

Spot checks against the game: iron ore needs a Stone Pickaxe, diamond ore an Iron Pickaxe, obsidian and ancient debris a Diamond Pickaxe, stone a Wooden Pickaxe; logs and dirt need no tool.

**Limits to know about**
- The jar does not say whether a block drops anything *without* the right tool (stone gives nothing by hand; logs do). Rule used: `toolRequired` is true for pickaxe blocks and for any block needing better than wooden. Axe, shovel and hoe blocks only mine faster with the tool. A few pickaxe blocks do drop by hand; those would need a correction from the wiki (as a candidate).
- Drops: `count` is the typical number from `set_count` (1 when unspecified); fortune and looting bonuses are ignored. `condition` is one of `default`, `silk_touch`, `shears`, `state` (block state, for example fully grown crops) or `chance` (random extras such as saplings and apples).
- Wool has no tool type in the data (shears are fastest, no tag says so), so wool blocks show no tool.
- Mob drops, shearing sheep and crops are not loaded yet.

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

The route picks one recipe per item: simplest first, skipping recipes that only reverse a compaction (ingot from nuggets), and skipping recipes whose input is only available with silk touch (iron ore item). It stops at a **mined resource**: an item that a *different* block drops, with no good recipe (raw iron from iron ore, diamond from diamond ore, glowstone dust). Items that a block of the same name drops (logs, obsidian) are ordinary base materials. For display, each base material lists the blocks it can be mined from with the tool each needs (`mineFrom`: Obsidian needs a Diamond Pickaxe or better; an Ender Chest also drops it). The jar cannot tell a natural block from a placed one, so hints such as `Tripwire` for string appear; they are true but not always sensible. Route properties were recomputed with these rules: the hardest items are now Lime Glazed Terracotta (8 steps), then Lime Banner and Lime Stained Glass Pane (7), and the netherite tools (6).

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

## Mapping to the Neo4j knowledge-layer process model (earlier hypothesis, superseded by v0.6 above)

| Knowledge-layer concept | MineCorp candidate |
|---|---|
| BusinessProcess | A MineCorp product line, e.g. "Iron tools" (to be authored; not in vanilla data) |
| BusinessActivity | A stage, e.g. "Smelt iron", "Craft tools" |
| BusinessTask | A `Task` that `CRAFTED` a `Recipe` (atomic, delegatable, has inputs/outputs) |
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
