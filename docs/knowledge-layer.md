# MineCorp enterprise knowledge layer

Status: **v0.3, open items A-C decided (2026-10-02).** Describes the target design and where the project is today. Nothing here changes existing code. Agreed decisions are in section 8 and in [AGENTS.md](../AGENTS.md).

Sources: [The knowledge layer for enterprise AI](https://neo4j.com/blog/agentic-ai/enterprise-knowledge-layer/) (the manifesto) and [The knowledge layer for enterprise: Processes](https://neo4j.com/blog/genai/the-knowledge-layer-for-enterprise-processes/) (series chapter 2). The series has four chapters: 1 operating structure, 2 processes, 3 domain ontology (not yet published when read), 4 data products (not yet published). Where this document uses the posts' words it quotes them; the MineCorp content is our proposal.

## 1. What it is

The knowledge layer (KL) is "a shared, governed substrate where an enterprise's knowledge lives": a connected, formally defined map of how the business runs, which sits between data assets and the agents and applications that use them. It is **executable**, not documentation. The posts contrast it with:

| Not this | Because |
|---|---|
| A BI semantic layer | It holds atoms (metrics) but lacks authoritative sources, lineage, policy enforced at request time, and mappings to real systems |
| A context layer | It enriches what the model sees but does not govern what the enterprise means |
| A data catalog | A catalog is definitional; the KL is executable |

Principle: **"lighter agents over a smarter shared substrate."** The agent still does the execution; the KL does the grounding.

**For MineCorp**, the "enterprise" is the fictional company MineCorp, which turns raw materials into higher-value products. Its employees are the players. The KL describes: the Minecraft domain (the things), how MineCorp works (processes, roles, teams), the systems and data behind it (server, wiki, graph), the rules it runs by (policy), and what it has learned from use (memory). It is also the demonstration that a complex domain with live state can be run this way.

**Boundary (decided): the KL is the graph.** That is the ontology, the enterprise data and the memory, all in Neo4j. Everything that reads or serves it (the graph tools, policy checks, trace capture, the agent) is the *access layer* and sits outside the KL. The manifesto folds serving into the KL; we keep it separate so the graph stands on its own and any consumer can use it.

## 2. High-level design

```
 SOURCES                       BUILD AND GOVERNANCE               KNOWLEDGE LAYER = THE GRAPH (Neo4j)        ACCESS LAYER (outside)   CONSUMERS
 -----------                   --------------------               -----------------------------------        ----------------------   ---------
 Paper jar: recipes, tags,  -> loaders (deterministic) ---------> ONTOLOGY                                    - interpret intent       MineCorp assistant
   loot tables, block tags                                          technical   domain   data products  <---   - select sources           (in Minecraft chat)
 minecraft.wiki articles ----> ingestion + LLM extraction -------->  business process   policy   org            - translate to queries   other agents (later)
 Minecraft server state -----> plugin events + state API --------->                                            - enforce policy         developers (later,
 People (the player/owner) --> review and confirmation (HITL) ---->  ENTERPRISE DATA                            - capture the trace         via MCP)
                                                                      reference   master   transactional
                               candidate -> confirmed                 + unstructured (wiki chunks)
                                                                    MEMORY
                                                                      decision traces   agent memory  <------  every run writes back
```

- **Authoritative data is never edited by hand.** Vanilla data is regenerated from the jar; the wiki is ingested as text; live state is read from the server.
- **The LLM proposes, humans ratify.** Anything not deterministic (extracted processes, wiki-to-entity links, suggested tasks) enters as a *candidate* with source, date and confidence, and becomes canonical only when its owner confirms it.
- **Every consumer goes through the access layer**, which is where policy is enforced and traces are captured.

## 3. Components

### 3.1 Ontology (six parts, as in the manifesto)

A modelling rule decided in review: **things are domain; ways of changing things are process.** An item is a thing. A recipe is a way of turning things into other things, so it is process. This moves `Recipe` (and what hangs off it) out of the domain ontology and into the process ontology. The graph itself does not change; this is how the model is classified and described.

| Part | Holds | MineCorp content (proposal) | Status |
|---|---|---|---|
| **Technical** | Systems, data sources, data assets | `System` (Paper server, plugin, agent service, Neo4j); `DataSource` (server jar, minecraft.wiki, server state API); `DataAsset` (recipe JSON, tag JSON, loot table JSON, wiki article); attributes: how accessed, how fresh, who owns it, authoritative or not | Not started |
| **Domain (business)** | The things: key entities and relationships | `Item`, `ItemClass` (categories of items), `Station` (the blocks where work is done); next: `Block` (what can be mined), `Mob`, `Biome`, `Structure`, `Dimension`; MineCorp-level: `Facility`, `Product` | `Item`, `ItemClass`, `Station` built |
| **Data product definitions** | The link from technical assets to domain and process concepts | "Recipe catalog" (jar -> Recipe), "Loot catalog" (loot tables and block tags -> mining and harvesting), "World state summary" (server state -> Facility/Station counts), "Player activity" (events -> Task completions) | Not started (series chapter 4) |
| **Business process** | Tasks, activities, decision points, flows | Vanilla **transformation definitions**: `Recipe` (inputs -> outputs at a station), later `Harvest` (block or mob -> drops, with the tool needed). MineCorp layer: `Process -> Activity -> Task`, `NEXT {condition, outcome, isDefault}`, `HANDOFF_TO`, `ESCALATES_TO`, `automatable`; `Task -[:USED_RECIPE]-> Recipe` | Recipe built; Task, Player, `COMPLETED` built (sample data only) |
| **Policy** | Rules for access and use | `Policy` nodes applying to `Role`, `Tool`, `DataProduct`; e.g. the agent is read-only, candidates need the owner's confirmation | Informal (permission node, read-only mode) |
| **Organization** | Units, roles, ownership, responsibilities | `OrgUnit -> Role`, `Person` (the player) `FILLED_BY` a role with dates; one owner per process | Not started (series chapter 1) |

Series conventions worth keeping: tasks are performed by **roles, not people**, and roles resolve to people through dated `FILLED_BY` edges, so who does a job can change without changing the process, and history stays auditable.

Open modelling point (section 8): vanilla `Recipe` is a *definition* of a transformation; a MineCorp `Task` is a *unit of assigned work* that uses one. They stay separate nodes, both in the process ontology.

### 3.2 What business processes look like in Minecraft

Three process families, from review:

| Family | What it is | Vanilla data behind it |
|---|---|---|
| **Collecting resources** | Mining and harvesting: breaking blocks (ore, stone, logs, wool blocks), shearing sheep, harvesting crops | Block loot tables (1,113 in the jar: which block drops which item); block tags `mineable/pickaxe`, `mineable/axe`, `needs_stone_tool`, `needs_iron_tool`, `needs_diamond_tool` (which tool is needed); mob loot tables (109) for shearing and farming |
| **Crafting** | Turning materials into products: crafting, smelting, smithing, cutting | Recipes (1,585) and tags; already in the graph |
| **Inventory management** | Storing, sorting and moving goods; keeping stock for a production line | No vanilla definition. MineCorp-authored tasks and rules, evidenced by live state (chests, inventories) |

The first two families are largely *derivable* from vanilla data; the third is where MineCorp's own operating model carries the content.

### 3.3 Enterprise data

| Kind | MineCorp content | How it gets in |
|---|---|---|
| **Reference** (controlled values) | Item and tag ids, recipe types, station list, vanilla recipes, loot tables, block tags | Loaded from the server jar; materialised in the graph; regenerated per version |
| **Master** (core entities) | Items, the player, facilities, stations in the world | Items materialised; player and facilities from the plugin |
| **Transactional** (events) | Crafts, smelts, blocks mined, task completions | Plugin events, aggregated, through an ingest path |
| **Live state** | Inventories, positions, chests, nearby blocks | **Queried in place** from the server (plugin state API), summarised in the graph |
| **Unstructured** | minecraft.wiki articles | Chunked and linked to entities (lexical and entity graphs) |

The manifesto's rule applies: each can be queried in place, materialised or virtualised, and the choice is part of the data product definition.

### 3.4 Memory

"Every agent interaction with the KL leaves a decision trace."

- **Decision traces:** question, player, tools called, the graph facts read, answer, outcome. Today they go to `agent/logs/traces.jsonl`; the target is `Trace` nodes linked to the `Item` and `Recipe` nodes they read, so any answer can be explained and queried ("which answers relied on this recipe?").
- **Agent memory:** conversations, per-player context and preferences, entities learned (Neo4j Agent Memory; research deferred by decision).
- **Improvement loop:** traces show where the layer is thin or wrong (a failed search, an ambiguous name, a question with no tool). These become proposed changes that an owner reviews. The eval set in `agent/evals` is the first form of this.

### 3.5 Access layer (outside the KL)

From the manifesto, on each request the serving side interprets intent against the model, assembles context, selects authoritative sources, generates queries, routes tools, enforces policy and access, explains and captures the trace, and updates memory.

| Manifesto step | Today | Target |
|---|---|---|
| Interpret intent | The LLM chooses tools | Same; ontology terms in tool descriptions |
| Select sources | Fixed: graph tools only | Choose between graph, wiki, live server by data product |
| Generate queries / route tools | Hand-written Cypher inside tools | Same; tools remain the interface to the graph |
| Enforce policy | Shared secret, `minecorp.agent` permission, read-only database mode | Policy nodes in the graph, checked on every tool call |
| Capture the trace | JSON lines | Written to the graph |
| Update memory | None | Agent memory |

### 3.6 Build and governance

- **Hybrid build:** "bottom-up discovery, where LLMs accelerate acquisition, meets top-down governance, where the organization defines what's authoritative."
- **Candidate versus canonical:** extracted output is "a draft input, not a fact". It carries `source`, `extractedAt`, `confidence` and a status. "Extraction gives you candidates and governance decides what's true." Vanilla data is authoritative by construction and needs none of this.
- **Ownership (federated):** domain owners own meaning, data products and mappings; the platform owns the substrate (reliable, queryable, versioned); governance sets cross-domain rules. See the single-player constraint below for how this works with one person.
- **Versioned:** the ontology and its changes are versioned (here: `docs/ontology.md` with a change log).

## 4. Design constraint: one player

In practice there is **one player** (the project owner). The operating model may define a full organization, but every solution must work with a single person:

- One person can fill every role at once (`FILLED_BY` allows many roles per person); no process may *require* two different people.
- A `HANDOFF_TO` between roles is valid when both roles are filled by the same person. It marks a change of hat, not of person.
- The owner is also the domain owner, the process owner and the steward who confirms candidates. The roles are still named in the data so the pattern is visible in the demonstration.
- No policy may depend on telling players apart (for example "see only your own inventory"). Per-player policies are written, but with one player they have nothing to deny.
- The agent can be modelled as a performer of `automatable` tasks later, which adds a second *actor* without adding a second player.

## 5. The four graphs and where they live

| Graph (from NOTES.md) | Lives in the KL as | Built from |
|---|---|---|
| **Domain** | Domain, process, organization and policy ontology plus reference and master data | Jar loader, authored process and org model |
| **Lexical** | Unstructured data: wiki articles as chunks | Wiki ingestion |
| **Entity** | Links from chunks to domain entities (mentions, advice about an Item/Station/Process) | LLM extraction -> candidates -> confirmed |
| **Agent memory** | Memory: traces, conversations, learned context | Agent runs |

## 6. A MineCorp operating model (proposal)

Illustrative; to be agreed before it becomes data. It must work with one player (section 4).

- **Org units:** Extraction (collecting resources), Refining (smelting, cooking), Fabrication (crafting and smithing), Logistics (inventory management). Each unit maps to one or two of the process families in section 3.2.
- **Roles:** Miner, Smelter, Crafter, Smith, Storekeeper, Process Owner. The player fills any of them for periods (`FILLED_BY {from, to}`), usually all at once.
- **Process example:** "Iron tool line" (criticality, target throughput as SLA).
  - *Collect:* mine iron ore (stone pickaxe or better), gather logs.
  - *Refine:* smelt iron ore into iron ingots; craft planks and sticks.
  - *Fabricate:* craft the iron tools.
  - *Store:* keep ingots and tools stocked in the right chest.
- **Routing as data:** `NEXT {condition, outcome, isDefault}` so an agent can answer "what comes after smelting?" and "which path is the default?" Tasks reference vanilla recipes or harvests for their inputs and outputs, with `automatable` set for what the agent could eventually do.
- **Policies (examples):** the agent never acts in the world (read-only); candidate processes need the process owner's confirmation; live inventory and location are visible only to their owner.

## 7. Where the project is today

| Component | State |
|---|---|
| Domain: items, categories, stations | Built and loaded (1,571 Item, 7 Station) |
| Process: recipes (vanilla transformation definitions) | Built and loaded (1,585 Recipe); route properties for ranking (ontology v0.5) |
| Access layer: tool-calling agent, six read-only graph tools, shared secret | Built |
| Decision traces | Written to a file, not the graph |
| Process: MineCorp Process/Activity/Task/Role | `Task`, `Player`, `COMPLETED` only; sample data |
| Harvest definitions (mining, shearing) from loot tables and block tags | Not started |
| Technical, policy, organization ontology; data products | Not started |
| Lexical and entity graphs (wiki) | Not started |
| Agent memory | Not started (research deferred) |
| Live state and events | Not started |
| Candidate/confirm workflow and provenance | Not started |

## 8. Decisions

| # | Question | Decision (2026-10-02) |
|---|---|---|
| 1 | Is the KL a named component, or just the graph plus the agent's tools? | **Just the graph.** The tools, policy checks and agent are the access layer outside it (section 1). |
| 2 | Order of work | **Agreed:** organization and process first, then technical ontology and data products, traces into the graph, policy nodes, wiki, then live state and memory (section 9). |
| 3 | Is the proposed operating model right? | **Yes, with the constraint that it must work with a single player** (section 4). |
| 4 | Neo4j edition | **Enterprise Edition. Done:** `neo4j:2026.09.0-enterprise`, licence accepted by you in `.env`; data volume kept and checked (1,571 Item, 1,585 Recipe, 988 route summaries). Enables graph types (schema enforcement) and Cypher 25. |
| 5 | Where do recipes belong? | **Process ontology, not domain** (things are domain, transformations are process). |
| 6 | What are the business processes in Minecraft? | **Collecting resources (mining and harvesting), crafting, inventory management** (section 3.2). |

Decided after the second review:

| # | Question | Decision (2026-10-02) |
|---|---|---|
| 7 | **A: provenance on vanilla data.** | **Leave as is:** no `source` property on vanilla nodes. When the technical ontology is built, lineage is recorded once, from the data to its asset (for example `Recipe -[:SOURCED_FROM]-> DataAsset`), and per-node `source`, `extractedAt` and `confidence` are used only on candidates (wiki or LLM output). |
| 8 | **B: owner and steward roles** (domain owner, process owner, platform owner, steward). | **Yes, record them as data.** The one player fills all of them ("I am both"). The demonstration can then show who confirmed what. |
| 9 | **C: Recipe versus Task.** | **Keep separate.** A recipe is vanilla's definition of a transformation; a task is MineCorp's unit of assigned work, with a role and a place in a process. One recipe can back many tasks. |

## 9. Suggested order of work

1. **Organization and process** (series chapters 1 and 2): the operating model; `Process/Activity/Task/Role/OrgUnit`, `NEXT` routing, `FILLED_BY`; tasks for mining, crafting and storing. Include **harvest definitions** from loot tables and block tags so "collecting resources" is data and not only prose.
2. **Technical ontology and data product definitions:** small, and makes lineage visible ("this answer came from the Recipe catalog, sourced from the Paper 26.2 jar").
3. **Traces into the graph** with links to what was read; first improvement loop.
4. **Policy nodes** checked by the access layer.
5. **Wiki: lexical and entity graphs** with the candidate/confirm workflow (first real use of LLM extraction).
6. **Live state and events**, then **agent memory** (inventory management needs live state).

## Next actions

- Build step 1 of section 9: the organization and process model (org units, roles with the player filling all of them, owner roles, the three process families), including harvest definitions from loot tables and block tags. Update `docs/ontology.md` first (Recipe classified as process; new node types), then the loader.
- With Enterprise Edition available, decide whether to use **graph types** to enforce the schema (the process post's `01-create-graph-type.cypher`) or stay with constraints.

## Change log

- **v0.3** (2026-10-02): decisions 7-9 recorded (A leave as is, B record owner roles and the player fills all, C keep Recipe and Task separate). Neo4j moved to Enterprise Edition.
- **v0.2** (2026-10-02): revised after review. KL boundary is the graph (access layer outside); recipes classified as process; three process families (collecting resources by mining and harvesting, crafting, inventory management) with their vanilla data; single-player constraint; decisions 1-6 recorded; open items A-C explained.
- **v0.1 PROPOSAL** (2026-10-02): first draft.
