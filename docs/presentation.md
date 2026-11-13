# Presentation outline: Mining for Meaning

Slide overview for the talk in [cfp.md](cfp.md). Draft v1, 2026-11-12. This is the outline to turn into slides: each slide has its message, what is on it and the visual. No speaker notes yet.

| | |
|---|---|
| **Talk** | Mining for Meaning: A Knowledge Layer for Agents |
| **Strategy** | build ONE complete master deck (all 24 slides); each talk is cut from it by audience and available time (see "Cutting the deck") |
| **Slot** | the CFP says 30 minutes with a live demo; other lengths are cut from the master deck |
| **Audience** | multiple; the structure and demo are fixed and the examples are adapted (see the end) |
| **Product naming** | Neo4j is named from the architecture slide on |
| **Game naming** | the game is **not named until the reveal slide (21)**; see "The naming rule" below |

## The four takeaways

Every slide serves one of these. The closing slide repeats them.

1. **When** a knowledge layer is the right choice for agents, and when it is not.
2. **The three parts** (ontology, data, memory) and where the agent's tools sit relative to them.
3. **A graph structure** that works: domain, lexical and bridge, with candidate versus confirmed knowledge.
4. **How to build and test it**: source-of-truth loaders, a spec checked against the graph, evals for the agent.

## The naming rule

The slides show the game's items and art (a diamond sword, ore, a crafting table) but never name the game, and never use game words in text before the reveal. Those who know it will recognise it; everyone else reads a company, its products and its processes, and draws their own parallels. Say nothing false: MineCorp is "a fictional company", and the abstract (which does name the game) stays as it is.

| Instead of | Write |
|---|---|
| players | workers, employees |
| the game, the server, Minecraft client | the system, the client |
| mobs, creatures | sources, suppliers (or "creatures" under a picture) |
| crafting, smelting | making, processing (the item names stay) |
| the Nether, the Ender Dragon | the end-game goal, a multi-stage objective |
| enchantments | upgrades (the word is fine under a picture, never explained) |
| in-game book | a panel the worker opens |

Screenshots that show the game (the chat, the book, the world) wait until the demo. Before it, use cropped item art, diagrams and Neo4j views.

## Cutting the deck

The master deck is complete and is not shortened to fit a slot. For each talk, hide (do not delete) the slides that do not serve that audience or time. Keep slide numbers in the master so a cut is a list of numbers.

**Core slides, in every version:** 1, 2, 3, 4, 6, 9, 10, 13, 21, 22, 24 (hook, use case, the complexity, the three parts, the graph map, one process, the reveal, the demo, the takeaways).

**Add by audience:**

| Audience | Add to the core |
|---|---|
| Developers | 8, 15, 16, 17, 18, 19, 20 (architecture, rebuild, spec test, evals, failures, tools, sources) |
| Architects | 5, 7, 11, 12, 14, 15, 16, 23 (sources, why a graph, each graph, memory, rebuild, spec, when not) |
| Technical leaders | 5, 11, 12, 14, 20, 23 (sources, the graphs and their business parallels, evidence, when not) |

**By time:** with less time, drop add-ons from the end of the list first; with more, use them all and extend the demo (every step in slide 22). The demo steps are also cuttable: 1, 3 and 5 are the minimum.

---

## Part 1. The hook (takeaway 1)

### 1. Title
- Mining for Meaning: A Knowledge Layer for Agents
- Name, role, contact.
- **Visual:** a block of ore turning into a diamond (the image for slide 3 in miniature).

### 2. The hook
- **"Agents are only as reliable as the knowledge they can reach."**
- **Visual:** the sentence alone, large.

### 3. The use case: MineCorp
- A company that turns raw materials into finished products.
- Workers ask an agent questions: what do I need, where do I find it, what is the way to X.
- The game is not named.
- **Visual:** the image of raw materials (cobblestone, iron ore, a log) on the left, a diamond sword on the right, an arrow between them.

### 4. Three ways to give an agent knowledge, and where they break
- **Everything in the prompt:** expensive and stale.
- **Free rein over the databases (text-to-query):** the agent guesses, and you cannot tell why it answered what it did.
- **Retrieval on its own:** finds passages, knows nothing about how things connect.
- **A knowledge layer:** the alternative, built up over the next slides. It is not always worth it (see slide 23).
- **Visual:** three small diagrams, each with the failure written under it. The fourth is left blank on purpose and filled in later.
- Source of the comparison: the CFP checklist says the demo must back this up with a real comparison.

## Part 2. The knowledge layer: sources and the complexity (takeaway 1 and 2)

### 5. What does MineCorp know? The sources
- The system of record (in the demo it turns out to be the server's own data files): recipes, suppliers, locations, ore, trades, upgrades. Authoritative.
- The community wiki: advice, strategy, how things work. Written by people, can be wrong.
- Defined processes: a first-night shelter, getting emeralds, and the multi-stage end-game goals. Not in the data anywhere, so authored.
- The agent's own memory: what each player asked and what the agent did.
- **Visual:** four sources on the left, one graph on the right.

### 6. Reveal the complexity: nothing to a diamond sword
- Show one item and everything that leads to it: every recipe, every alternative ingredient, every station, every place to mine, kill or trade for each piece.
- **Visual (the centrepiece of this part):** the graph of all possible routes from nothing to a diamond sword, drawn from the real database in Neo4j Browser or Bloom. It should look too big to hold in your head.
- To prepare: pick the query and the layout, and decide how many routes there are (count them from the graph; do not quote a number until it is counted).
- Hardest items for contrast: glazed terracotta (8 steps), netherite tools (7 steps), from `rank_recipes`.

### 7. Why a graph
- The question is about paths and connections ("what do I need", "where do I find"), not rows.
- The same facts from several angles: an item is a recipe result, an ingredient, a drop, a trade, an ore.
- **Visual:** the diamond sword reduced to one path, `(Item)<-[CREATES]-(Recipe)-[REQUIRES]->(Item)`, so the audience sees the notation used in the demo.

## Part 3. Architecture (takeaway 2)

### 8. The shape of it
- Client -> thin plugin -> agent service -> read-only tools -> Neo4j.
- The plugin only forwards the question and opens the answer.
- The agent has a hand-written tool loop over a model, with no framework.
- **Visual:** the line diagram from the README, redrawn with neutral labels (no "Minecraft client", no "Paper server").

### 9. The three parts of a knowledge layer, and where the tools sit
- **Ontology** (the model), **data** (instances of the model), **memory** (decision traces).
- The knowledge layer is the graph. The tools, the policy and the agent are the access layer outside it.
- Read-only purpose-built tools are the access control.
- **Visual:** the graph as a box with three layers inside; the tools drawn around it as a ring.
- Source: [knowledge-layer.md](knowledge-layer.md), [architecture.md](architecture.md).

## Part 4. The graph structure (takeaway 3)

### 10. Three graphs
- **Domain graph:** known, authoritative, from the game data and authored YAML. Business parallel: the catalogue and the bill of materials.
- **Lexical graph:** inferred, from the wiki; sections and embedded chunks. Business parallel: the documentation.
- **Memory graph:** what happened, the conversations and the trace of every answer. Business parallel: the audit trail.
- **Two kinds of link between them:**
  - **The bridge** (domain to lexical): derived links from wiki passages to the items, mobs and blocks they mention.
  - **The player** (memory to domain): each memory belongs to a player who is also in the domain graph.
- Say plainly that the full join is the direction of travel, not what is built: memory has no links to the lexical graph, and linking what the agent learns (long-term entities) to game nodes is not built. Do not call the bridge a link between all three.
- A fourth graph in the agreed model (entity, extracted long-term knowledge) is not built; leave it off the slide.
- **Visual:** three clouds in three colours used on every later slide: a band of links between domain and lexical (the bridge), and a single thread from memory to domain (the player).

### 11. The domain graph: known
- Items, recipes, mining, drops, mobs, biomes, structures, ore, villager trades, enchantments.
- Loaded from the source of truth, never typed by hand.
- **Business parallel:** the product catalogue and the bill of materials.

### 12. The lexical graph and the bridge: inferred
- Wiki pages split into sections and chunks, embedded for vector search, linked to the nodes they are about.
- A link is evidence; no link says nothing. Ambiguous names link to every candidate and the model chooses from the passage.
- **Business parallel:** internal documentation, wikis and tickets, connected to the product data.

### 13. The process graph: the unknown part
- A process holds what the data does not: a goal (Process), its stages (Activity), its steps (Task), with `NEXT` carrying the alternatives.
- Steps name real actions where the data has them; the rest are manual.
- **Candidate versus confirmed:** a drafted process is a candidate (source, confidence) until a person confirms it.
- **Business parallel:** runbooks and standard operating procedures; LLM-extracted knowledge waits for human approval.
- **Visual:** the "get emeralds" process as a small flow with a branch: trade (the default: sell wheat to a farmer or sticks to a fletcher) or mine. It needs no game words. (The Nether portal process is saved for the demo.)

### 14. Memory: the decision trace (the third graph)
- Each player has conversations, messages and a trace of the tools every answer used, linked to the player.
- This is the audit trail, and the way the agent handles follow-up questions.
- **Business parallel:** who asked what, and why the system answered as it did.
- **Visual:** the `(User)-[:IS_PLAYER]->(Player)` link joining the memory graph to the domain graph. It is the only link today; say so.

## Part 5. Build and govern (takeaway 4)

### 15. Rebuild from source
- The graph can be rebuilt from the jar and the YAML in about 30 seconds, plus the wiki step.
- Loaders may wipe and recreate what they own; only history and memory are not rebuildable.
- **Visual:** the build order as a line: recipes, actions, world, enchantments, trades, model, routes, wiki, links.

### 16. The model as a checked spec
- The ontology is also written as data (33 nodes, 66 relationships) in the Neo4j Ontology Graph Specification.
- A test compares the spec with the real built graph in both directions, so the code and the model cannot drift.
- It found real gaps in the first draft.
- **Visual:** spec on the left, graph on the right, the test between them.

### 17. Evals for the agent
- 72 questions that check tool choice and answer quality, run against the real model.
- They catch drift and flakiness; some evals are known to be flaky and that is stated.
- **Visual:** a pass/fail grid, with one red cell.

### 18. What went wrong (two stories)
- **The threshold that threw away the right answer.** "What does sharpness do?" answered "I could not find it". The agent had searched the wiki, but a single score floor of 0.65 dropped passages that scored 0.644 to 0.648. Fixed with two tiers (hard floor 0.60, weak below 0.70), and the missing Enchantment page was added. Measured: covered and uncovered questions overlap in score, so the model has to make the call.
- **The category that could not be crafted.** "How do I get wool?" answered "from sheep", never crafting, because wool is a category and craftability was judged on the category node. Fixed by reporting how many members can be crafted, and how the simplest one is made.
- **Visual:** one slide each, or one slide with two columns: the question, the wrong answer, the cause, the fix.
- Wording: show the item names (Sharpness, Wool) but do not explain them; the lesson is the threshold and the category, not the game. Say "the right passage scored 0.644" and "wool is a category of 16 items".

## Part 6. The agent (takeaway 2)

### 19. The tools
- Purpose-built, read-only: `find`, `list`, `get_recipes`, `what_do_i_need`, `how_to_get`, `where_to_find`, `describe`, `members`, `get_process`, `search_wiki`, `read_section`.
- The model chooses a tool; the tool decides the Cypher. The agent never writes a query.
- Every result has a size budget, and a failure is returned as a typed error the model can act on.
- **Visual:** a question on the left, the tool calls in the middle, the graph paths on the right.

### 20. Sources, and graph paths you can see
- Each answer carries its sources, built in code from the tool results (never by the model), plus the graph pattern the tool walked.
- The worker can ask for them and gets a panel with the question, the answer and the sources.
- **Business parallel:** the answer comes with its evidence.
- **Visual:** a mock-up of the panel (text and one highlighted pattern), not a screenshot. The real book appears in the demo.

## Part 7. Reveal and demo

### 21. The reveal
- **"MineCorp is a game."** A short slide, one beat. First time the game is named.
- Why a game works as a test domain: public data, real complexity, answers you can check, nothing real at risk.
- And the point: the pattern did not change when the domain was named. Swap the names and it is your catalogue and your runbooks.
- **Visual:** the slide 3 image (raw materials to diamond sword), now with the game's name and logo under it, then straight into the demo.

### 22. Live demo (about 7 minutes)
Planned order; each step makes one point.

1. `/agent what do I need for a diamond pickaxe?` the domain graph, a recipe route.
2. `/agent where do I find diamonds?` the world graph (ore heights, biomes).
3. `/agent how do I get to the Nether?` a process, labelled as a draft, with the wiki pages it came from.
4. `/agent what does sharpness do?` the lexical graph and the bridge.
5. `/agent-explain` the book: sources and graph paths.
6. A follow-up ("and how many of those do I need?") memory and context.
7. Neo4j Browser: the same path in the graph (the pattern in the book is a query that returns it).

Fallbacks: the terminal stub (`chat-with-agent.ps1 -Trace`) if the client or server fails; screenshots on a hidden slide.
To decide: the comparison against prompt-stuffing and retrieval that the CFP promises (the same question through each).

## Part 8. Close

### 23. When it is not worth it
- Small, stable data that fits in a prompt.
- A single source, no processes, no connections between things.
- No one to own the model: a knowledge layer needs stewards.
- **Visual:** a short two-column "yes / not yet" checklist.

### 24. The four takeaways, and the pattern
- Repeat the four takeaways.
- **The pattern:** known data, inferred text, authored processes and memory in one graph; read-only tools; a checked spec; evals.
- Fits any domain built on catalogues and processes: runbooks, supply chains, support, operations.
- Links: the repository and the two Neo4j posts.

---

## Adapting to the audience

The structure and the demo stay. The parallels and the depth change.

| Audience | Emphasise | Trim |
|---|---|---|
| Developers | architecture, tools, the loader and spec test, evals, the failure stories; show more Cypher | business parallels |
| Architects | the three parts, the access layer, candidate versus confirmed, rebuild from source | tool detail |
| Technical leaders | the use case, the enterprise parallels on each graph slide, governance, slide 23 | Cypher, loader detail |

When to reveal the game (slide 21):

| Audience | Reveal |
|---|---|
| Technical leaders, mixed or business-leaning events | at slide 21, before the demo (the default) |
| Architects | at slide 21 |
| Developers, or an event where the game is part of the draw | as early as slide 3; the visuals then name the game and the naming rule is dropped |

## Open items

- Pick and test the "nothing to a diamond sword" graph view, and count the routes.
- Decide whether the demo includes the side-by-side comparison with prompt-stuffing and retrieval (the CFP promises it; nothing measures it yet).
- The CFP checklist: read-only, one confirmed process (all five are still candidates), memory linked to the player only.
- Produce the images: blocks to a diamond sword, the architecture line, the three-colour graph map.
- Agree the cut lists in "Cutting the deck" once the slides exist; a hidden-slide list per talk is all that varies.
- Check the demo's first on-screen moment: the game appears for the first time, so the reveal slide and the first chat command must follow each other without a gap.
- The CFP abstract names the game; decide whether it stays (recommended).
