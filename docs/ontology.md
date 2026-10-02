# MineCorp ontology

Living document. Each version records what changed and why. Status: **v0.5, built and loaded into Neo4j (2026-10-02).** The full graph is loaded with the v0.4 model. The test-model subset section below was measured on v0.3.1 and has not been rebuilt.

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

## v0.5 model (v0.4 plus route properties)

### Design idea

Recipes require **Items**, always. Some of those Items are **categories** ("Bed", "Planks", "Iron Tool Materials") that include more specific Items. A category is an `Item` with an extra marker label `:ItemClass`, so recipe edges always have one shape, `(Recipe)-[:REQUIRES]->(Item)`, and `INCLUDES` leads from a category to its members. Categories overlap (an item can be in several, categories can contain categories), so this is a graph, not a tree; there is no `VARIANT_OF` hierarchy.

### Node labels

| Label | Key property | Other properties | Notes |
|---|---|---|---|
| `Item` | `id` (e.g. `minecraft:iron_ingot`) | `name`; for craftable concrete items also the **route properties** below | Covers blocks too. A *concrete* Item exists in the game. |
| `Item:ItemClass` | `id` (see below) | `name`, `inline` (bool), `size` (number of concrete items covered, expanding nested classes) | A *category* of Items, for example "Bed". Does not exist in the game. Replaces the old `ItemTag` label. |

Category `id`: Minecraft tags keep the jar's notation with a leading `#`, e.g. `#minecraft:planks` (avoids the six id collisions with items). Anonymous alternative lists keep `inline:<hash of members>`.

ItemClass `name` (rule, in order):
1. If the class covers two or more concrete items, use the trailing word(s) shared by **all** of them: the 15 beds in `dye_brown_bed` give "Bed", `#minecraft:trapdoors` gives "Trapdoor", `#minecraft:iron_ores` gives "Iron Ore".
2. Otherwise, for a real tag, use the tag name in Title Case ("Acacia Logs", "Bee Food").
3. Otherwise, for an inline list, "Any of: A, B, C".

Tested on the 300 categories in the full graph: 160 get a shared word (rule 1), 140 fall back (134 to a tag name, 6 inline lists to "Any of..."). Rule 1 is not always the nicest word (`#minecraft:leg_armor` becomes "Leggings"), but it is consistent and automatic; a curated override is possible later.

When rule 1 would give two real tags the same name, both use their full tag name instead ( is "Slabs" and  is "Wooden Slabs", not two "Slab"; this affected 18 names, such as button, door, dye and the armor slots). The only remaining duplicate names are two pairs of anonymous lists ("Sandstone" and "Red Sandstone"), which differ by members; the uid=197618(martin.ohanlon.neo4j) gid=197121 groups=197121 is always unique.

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
| `USES_STATION` | `Recipe` -> `Station` | | Where it is made. |
| `USED_RECIPE` | `Task` -> `Recipe` | | The vanilla recipe a task is carried out with. |
| `COMPLETED` | `Player` -> `Task` | `at` (datetime), `count` | A player has completed a task (history of work). |

**How to read REQUIRES (important for queries and the agent):** all `REQUIRES` edges of a recipe are needed together (AND). When the target is an `:ItemClass`, exactly one member of that class is used (OR), except any item in `excludes`. Example: `dye_brown_bed` = 1 Brown Dye (concrete) AND 1 Bed (class of 15) = a brown bed from brown dye plus any bed except a brown one (`excludes: [brown_bed]`).

Station mapping: `crafting_*` -> crafting table (some also 2x2 inventory grid, TBC from the pattern size), `smelting` -> furnace, `blasting` -> blast furnace, `smoking` -> smoker, `campfire_cooking` -> campfire, `stonecutting` -> stonecutter, `smithing_*` -> smithing table.

### Graph model (ASCII)

```
                       (:Player)
                       uuid, name
                           |
                           | COMPLETED {at, count}
                           v
                        (:Task)                       curated by MineCorp (candidate -> confirmed)
                        id, name, description, status
                           |
                           | USED_RECIPE
                           v
   (:Station) <------ (:Recipe) -----------------------> (:Item)
   id, name            id, name, type,        CREATES    id, name
     USES_STATION      category, group,       {count}
                       experience, cookingTime,
                       special, pattern, key
                           |
                           | REQUIRES {role, count, slot}      AND across a recipe's edges
                           v
                        (:Item)        concrete, e.g. Brown Dye
                        (:Item:ItemClass)  category, e.g. Bed (OR: one member)
                           |
                           | INCLUDES
                           v
                        (:Item) concrete members, or (:Item:ItemClass) nested categories

Vanilla, regenerated from the jar:  Item (incl. Item:ItemClass), Recipe, Station
MineCorp, curated:                  Task, Player  (later: Process, Activity, Role, NEXT)
```

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

## Mapping to the Neo4j knowledge-layer process model (hypothesis, to test)

| Knowledge-layer concept | MineCorp candidate |
|---|---|
| BusinessProcess | A MineCorp product line, e.g. "Iron tools" (to be authored; not in vanilla data) |
| BusinessActivity | A stage, e.g. "Smelt iron", "Craft tools" |
| BusinessTask | A `Task` that `USED_RECIPE` a `Recipe` (atomic, delegatable, has inputs/outputs) |
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

- **v0.5** (2026-10-02): route properties on craftable `Item`s, computed by `loader/compute_routes.py` (988 items in about 10 s; 1 truncated at depth 6: Waxed Chiseled Copper); `rank_recipes` agent tool. Hardest by the metric: Lime and Purple Glazed Terracotta (8 steps), then the Netherite tools (7 steps).
- **v0.4** (2026-10-02): built and loaded. Anonymous alternative lists map to real tags with `excludes` (66 of 76); only 10 inline classes remain. Full graph: 1,571 Item (234 ItemClass), 1,585 Recipe, 7 Station.
- **v0.4 PROPOSED, rev 2** (2026-10-02): label is `ItemClass` (marker label on `Item`); name rule uses the common word of the members (160 of 300 existing categories qualify; others fall back); `CONSUMES` -> `REQUIRES`, `PRODUCES` -> `CREATES`. The measured v0.3.1 sections keep the old names because they describe the graph as loaded.
- **v0.4 PROPOSED** (2026-10-02): `ItemTag` replaced by `Item:Class` (categories are Items with a marker label); `#` prefix on category ids to avoid six id collisions; readable category names; AND/OR reading of `CONSUMES` documented; no vanilla default item per category (noted). Not yet built.
- **v0.3.1** (2026-10-02): full graph loaded (`--all`); counts recorded above.
- **v0.3** (2026-10-02): `Recipe.name` added; `source` property removed from all nodes (not needed yet); relationships renamed `COMPLETES` -> `COMPLETED` and `USES_RECIPE` -> `USED_RECIPE`.
- **v0.2.1** (2026-10-02): added ASCII model and test-model description; loader draft and Neo4j docker config added.
- **v0.2** (2026-10-02): added `Task` (separate from `Recipe`, via `USED_RECIPE`), `Player` and `COMPLETED`; grid stored on `Recipe`; `group` stays a property; versioning and item metadata deferred. Verified tag entries are plain strings.
- **v0.1** (2026-10-02): initial draft from inspection of Paper 26.2 recipe and tag JSON.
