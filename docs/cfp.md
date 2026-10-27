# Conference proposal: Mining for Meaning

Call for papers text for presenting MineCorp at technology conferences. Draft v1, 2026-10-27.

| | |
|---|---|
| **Slot** | 30 minutes |
| **Audience** | developers, architects and technical leaders |
| **Format** | talk with a live demo |
| **Product naming** | say "graph database" in the abstract, not Neo4j (the product comes out in the talk) |

## Title

**Mining for Meaning: A Knowledge Layer for Agents**

## Abstract

AI agents are only as reliable as the knowledge they can reach. Put everything in the prompt and it gets expensive and stale. Give the agent free rein over your databases and it guesses, and you can't tell why it answered what it did. A knowledge layer is a better answer: a shared, governed graph between your data and your agents, so the agents stay light and the substrate does the remembering.

This talk shows what that means by building one for an unlikely domain: a vanilla Minecraft server and MineCorp, a fictional company that turns raw materials into higher-value products. Players ask the agent questions in game chat ("what do I need for a diamond pickaxe?", "where do I find diamonds?", "how do I get to the Nether?"). It answers from a graph database of recipes, mobs, biomes, villager trades, enchantments and step-by-step processes. It also searches the community wiki as text and remembers the conversation.

In 30 minutes we will cover:

- Why a knowledge layer beats prompt-stuffing, retrieval on its own, or free-form text-to-query for agents, and when it is not worth it.
- The architecture: ontology, data and memory, behind a thin client and an agent that can only call purpose-built, read-only tools.
- How to structure the graph: a domain graph from authoritative data, a lexical graph from text, a bridge between them, and a decision trace for every answer. Machine-extracted knowledge stays a candidate until a person confirms it.
- How to build and govern it: loading from the source of truth, keeping the model as a validated spec that is tested against the live graph, and evals that catch drift. We will also show what went wrong, including a similarity threshold that threw away the right answer.

You will see MineCorp in action in a live demo, including the in-game book that shows the graph path behind each answer.

You will leave with a reusable pattern for any domain built on catalogues and processes: runbooks, supply chains, support, operations.

## Short version (about 60 words, for forms with a small limit)

What makes an agent trustworthy? A knowledge layer: a governed graph between your data and your agents. We build one for a Minecraft company, covering when a graph beats prompt-stuffing, the architecture, how to structure the graph, how to build and test it, and a live demo. Take away a pattern for any process-heavy domain.

## Takeaways (for the "what will attendees learn" box)

- When a knowledge layer is the right choice for agents, and when it is not.
- The three parts of a knowledge layer (ontology, data, memory) and where the agent's tools sit relative to it.
- A practical graph structure: domain, lexical and bridge graphs, with candidate versus confirmed knowledge.
- A build and testing approach: source-of-truth loaders, a spec checked against the graph, and evals for the agent.

## Where the content comes from in this repo

- The knowledge layer framing: the two Neo4j posts listed in [AGENTS.md](../AGENTS.md) under Sources.
- The graph structure: [ontology.md](ontology.md) and [model/ontology.yaml](../model/ontology.yaml) (the spec that a test checks against the built graph).
- The architecture: [architecture.md](architecture.md) and [agent-tools.md](agent-tools.md).
- Memory and decision traces: [agent-memory.md](agent-memory.md).
- The in-game book of sources: [explain-book.md](explain-book.md).
- "What went wrong" stories: the similarity threshold (the "Wiki fallback" section of [agent-tools.md](agent-tools.md)) and the category that could not be crafted (the 2026-10-22 "Wool fixes" entry in AGENTS.md).
