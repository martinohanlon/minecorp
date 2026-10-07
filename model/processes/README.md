# Processes

One YAML file per process. A process is a **goal a player works towards in the game**: the things a player does to
progress or survive that the game's own data files do not describe (build a shelter, get emeralds, get to the Nether,
defeat the dragon, make a mob farm). The structured part of the game (recipes, mining, drops, spawns) is already in the
graph and needs no process: `what_do_i_need` works it out.

A process has **activities** (stages, in order) and each has **tasks** (steps). A task either names the game
**actions** it performs, when the game data has them (`mine:minecraft:obsidian`, `craft:minecraft:flint_and_steel`), or
has none and is a **manual** step (build the frame). `next` says what follows what, with a condition, so an alternative
route is data (`default: true` marks the usual one).

```yaml
process:
  id: nether-portal
  name: Get to the Nether
  description: One or two sentences.

# Everything here is a candidate until a player confirms it.
status: candidate             # or: confirmed (then confirmed_by: <player name>, confirmed_at: <date>)
source: https://minecraft.wiki/w/Tutorial:Nether_portals    # where the steps come from (candidates)
extracted_at: "2026-10-07"
confidence: 0.7

activities:
  - id: gather
    name: Gather
    seq: 1
    tasks:
      - id: nether-portal/mine-obsidian      # prefixed with the process id
        name: Mine obsidian
        description: Mine obsidian with a diamond pickaxe.
        actions:
          - {do: "mine:minecraft:obsidian", quantity: 10}
      - id: nether-portal/build-frame
        name: Build the frame        # no actions: a manual step

next:
  - {from: nether-portal/mine-obsidian, to: nether-portal/build-frame, condition: obsidian collected, default: true}
```

`python loader/load_model.py --dry-run` checks every action id against the graph and every reference, and lists every
problem at once. See [docs/ontology.md](../../docs/ontology.md) (v0.11).
