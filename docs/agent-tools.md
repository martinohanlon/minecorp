# Agent tools: review and proposal

2026-10-05. A review of the nine tools the agent has today, and a proposal for a set that covers the whole model (the domain graph, the lexical graph and the bridge; see [ontology.md](ontology.md)). Status: steps 1 to 4 of the order of work are built (3: `describe` and `where_to_find`; 4: `members`, the same argument name everywhere, the tool rules moved from the prompt into the descriptions) (the `names` full-text index, `find`, name resolution that shows every item a generic name fits; per-tool size budgets and three error types); the rest is not.

## What the evidence says

Measured on the real graph and on the 290 questions the agent has answered since 2026-10-02 (nearly all of them evals, so this shows how the model behaves, not how players ask).

**Cost on every request.** The nine tool definitions are about 1,100 tokens and the system prompt about 1,200, so roughly 2,300 tokens go out before the player's question. A question takes 3,900 tokens in the middle and 18,600 at most.

**Use.** `get_recipes` 142 calls, `find_item` 101, `search_wiki` 64, `what_do_i_need` 45, `how_to_get` 29, `class_members` 18, `get_uses` 17, `rank_recipes` 16, `read_section` 14. Zero errors. 22 questions used no tool. Median one tool call per question.

**What works.** No errors in 290 questions. The graph tools are quick (medians of 7 to 92 ms; `search_wiki` 216 ms because it embeds the question, 1.6 s at worst). The recipe tools return 0.2 to 2.5 thousand characters. The graph connection is read-only by construction, the tools are tested against the real graph, and limits are clamped the same way everywhere.

**What to change.**

1. **Looking a name up costs a round trip.** The most common sequence is `find_item` then `get_recipes` (54 of 290), yet `get_recipes`, `what_do_i_need`, `how_to_get` and `get_uses` already resolve names themselves. The model calls `find_item` first because its description says to when unsure.
2. **A generic name loops.** "How do I make a pickaxe?" took 7 to 9 calls, one `get_recipes` per kind of pickaxe.
3. **`search_wiki` is the heavy one.** Its results are 5,800 characters in the middle, and 9 of 64 calls came within 1,000 characters of the 10,000-character cap, which cuts the JSON in the middle when it is reached. The other tools stay under 2,500.
4. **Every failure looks like an outage.** Any exception becomes "the knowledge graph is not available", including a bad argument. It never happened in 290 questions, but the model would be told the wrong thing.
5. **The prompt repeats the tools.** About twenty lines of tool rules sit in the system prompt as well as in the descriptions, which will drift.
6. **Argument names differ** (`query`, `item`, `category`, `question`, `section`).
7. **Blind spots against the new model.**
   - No tool starts from a mob, biome, structure, dimension or ore, and none answers "where". The prompt even tells the model not to say where.
   - `class_members` knows only item categories, not mob or biome classes.
   - Wiki passages now list biomes, structures and mobs as mentions, and the model cannot look any of them up.
   - Nothing reaches the company (`Task`, `Process`) or a player's progress.

## Proposal

**Principles.** A tool is a path through the graph. One resolver finds a name of any kind, and every tool accepts a name directly, so looking up first is the exception. Ambiguity is returned, not resolved: a name that is an item and a mob gives both. The node's labels say what it is. Every result has a size budget. One error shape for a bad argument, another for a real outage. The facts belong to the domain tools and the advice to the lexical tools, and a result from one can hand over to the other.

**Eleven tools now.**

*Find and describe*

- **`find(query, kind?)`** replaces `find_item`. A full-text search over the names of items, categories, mobs, blocks, biomes and structures, with typo and plural tolerance. Exact name first, then relevance. Returns candidates with their kind. Needed only when another tool says a name is ambiguous or unknown.
- **`describe(name)`** is new. One short view of any thing, by kind, and every candidate if the name is ambiguous.
  - An item: what it is, whether it is craftable, how many ways to get it.
  - A mob: its drops, where it spawns, its classes.
  - A biome: climate, what spawns, which structures generate.
  - A structure: which biomes, what spawns, what is suppressed.
  - A block: drops and the tool needed.
  - It also lists the top wiki sections that mention the thing (`MENTIONS`), which makes a separate "wiki about" tool unnecessary.

*Making things* (unchanged: they work and are tested)

- **`get_recipes(item)`**: `Item <-CREATES- Recipe -REQUIRES-> Item`.
- **`what_do_i_need(item, quantity)`**: the ordered route to the item from raw materials.
- **`get_uses(item)`**: what an item goes into.
- **`rank_recipes(order, limit)`**: by the computed route size.

*Getting and finding things*

- **`how_to_get(item)`**: `Item <-CREATES- Action -REQUIRES-> Block | Mob`, with the tool needed. Today's tool, and the place later sources (chest loot, villager trades, fishing, bartering) plug in, because they are all `Action` nodes.
- **`where_to_find(name)`** is new, and it is what the prompt now forbids. It follows the world: for an item or block, `Mine -> Block <- Feature <- PlacedFeature <- Biome` gives the heights (range and peak), the veins per chunk and the dimension; for a mob, `Biome -SPAWNS-> Mob` and `Structure -SPAWNS-> Mob`; for a structure, its biomes. It compresses ("all overworld biomes" when that is true) so the answer stays small.
- **`members(class)`** replaces `class_members`. It lists what an item category, mob class or biome class includes, by walking `INCLUDES`.

*The wiki* (unchanged)

- **`search_wiki(question, limit)`** and **`read_section(section, before, after)`**. Their results get a size budget that cuts at a whole passage, never in the middle of the JSON.

**Next, not now: two for the company.** `get_process(name?)` for the process, its activities, tasks, owners and handoffs, and `my_progress()` for what the asking player has completed. Both need the player's identity from the question, which the tools do not receive today, and the company model is still one process and six tasks. They are the point of the project and deserve a design of their own.

**Cost.** Eleven definitions are about 300 tokens more than nine. Moving the tool rules out of the system prompt and into the descriptions takes out about 400. The request overhead should end up the same, with fewer calls per question.

## Order of work

1. A full-text index over names, created by the loaders, and `find` plus name resolution inside every tool (this removes the round trip and the pickaxe loop).
2. A size budget for every result and the two error shapes.
3. `describe` and `where_to_find` over the world graph, with evals for "where do I find diamonds", "what spawns in a desert", "does a villager drop anything".
4. `members` in place of `class_members`; trim the prompt.
5. Later: the enchantment, trade and advancement models arrive as more things for `describe`, more sources for `how_to_get`, and then the company tools.

## Questions for you

1. One `describe` for every kind, or a tool per kind (`get_mob`, `get_biome`, ...)? One tool keeps the request small and returns every candidate of an ambiguous name; separate tools are easier to describe to the model.
2. Are the company tools next after the world ones, or after more of the game?
3. Should `where_to_find` answer for a single place only ("the best y-level") or give the full picture and let the model choose? I propose the full picture, short.
