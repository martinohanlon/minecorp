"""Load Action nodes from the Paper jar into Neo4j (docs/ontology.md v0.7).

    python loader/load_actions.py --dry-run   # parse and report, no database
    python loader/load_actions.py             # load

An Action is a node with a verb label. There are two kinds, both generated from the jar:

Gathering actions are the definitions (from loot tables and block tags):
    Mine   one per block with drops   (Action)-[:REQUIRES {role:'target'}]->(Block)
    Kill   one per mob with drops     (Action)-[:REQUIRES {role:'target'}]->(Mob)
    Shear  one per shearable mob      (Action)-[:REQUIRES {role:'target'}]->(Mob)
    Each has (Action)-[:CREATES {count, condition}]->(Item) and, when a tool is needed,
    (Action)-[:REQUIRES {role:'tool', orBetter}]->(Item). A tool is an ordinary Item.

Transforming actions are thin, one per verb and item (the Recipe nodes hold the ingredients and the station):
    Craft  crafting recipes           Smelt  smelting, blasting, smoking, campfire cooking
    Smith  smithing recipes           Cut    stonecutting recipes
    Each has (Action)-[:CREATES]->(Item); the Recipe nodes that make the same item meet it there.

Run it after `load_recipes.py --all` (the items and recipes come from there) and before `load_model.py`.
The graph is rebuilt from source, so this loader deletes and recreates every Action (and any old Harvest).

What the jar does and does not say about tools:
- Tool TYPE for blocks comes from the block tags mineable/pickaxe, axe, shovel, hoe.
- Minimum TIER comes from incorrect_for_<tier>_tool: the first tier whose tag does not list the block.
- Whether a block drops anything WITHOUT the tool is not in the data (stone gives nothing by hand, logs do).
  Rule used: a tool is "required" for pickaxe blocks and for any block that needs better than a wooden tool;
  axe, shovel and hoe blocks only mine faster with the tool. Exceptions exist.
- Mobs need no tool to kill; a weapon is only faster. Shearing needs Shears. Looting and fortune are ignored.

Conditions on drops (CREATES.condition):
    blocks: default, silk_touch, shears, state (block state, e.g. grown crops), chance
    mobs:   default, or a '+'-joined mix of killed_by_player, chance (rare), state (some other condition)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from neo4j import GraphDatabase  # noqa: E402

from load_recipes import DEFAULT_JAR, display_name, normalise  # noqa: E402
from load_recipes import read_jar as read_recipe_jar  # noqa: E402
from minecorp_agent.config import get_settings  # noqa: E402

TIERS = ["wooden", "stone", "copper", "iron", "diamond", "netherite"]   # weakest to strongest (gold mines like wood)
TOOLS = ["pickaxe", "axe", "shovel", "hoe"]                              # order of preference when a block allows several
SHEARS = "minecraft:shears"
MAX_REF_DEPTH = 4                                                        # loot tables that reference other tables
GATHER_VERBS = ("Mine", "Kill", "Shear")
TRANSFORM_VERBS = ("Craft", "Smelt", "Smith", "Cut")
RECIPE_VERB = {                                                          # recipe type -> verb of the action that makes its item
    "smelting": "Smelt", "blasting": "Smelt", "smoking": "Smelt", "campfire_cooking": "Smelt",
    "smithing_transform": "Smith", "smithing_trim": "Smith", "stonecutting": "Cut",
}


# --------------------------------------------------------------------------- read

def read_loot(jar: Path) -> tuple[dict, dict]:
    """Return (loot tables, block tags). Loot tables are keyed like 'minecraft:blocks/stone'."""
    loot, tags = {}, {}
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            m = re.fullmatch(r"data/minecraft/loot_table/((?:blocks|entities|shearing)/.+)\.json", name)
            if m:
                loot[f"minecraft:{m.group(1)}"] = json.loads(z.read(name))
                continue
            m = re.fullmatch(r"data/minecraft/tags/block/(.+)\.json", name)
            if m:
                tags[f"minecraft:{m.group(1)}"] = json.loads(z.read(name))["values"]
    return loot, tags


def tag_members(tag_id: str, tags: dict, cache: dict) -> set:
    """All block ids under a block tag, following nested tags."""
    if tag_id in cache:
        return cache[tag_id]
    cache[tag_id] = set()  # guards against cycles
    out = set()
    for v in tags.get(tag_id, []):
        out |= tag_members(v[1:], tags, cache) if v.startswith("#") else {v}
    cache[tag_id] = out
    return out


# --------------------------------------------------------------------------- tools (blocks)

def analyse_tool(block_id: str, tags: dict, cache: dict) -> dict:
    """Tool type, minimum tier and whether a tool is treated as required for a block."""
    types = [t for t in TOOLS if block_id in tag_members(f"minecraft:mineable/{t}", tags, cache)]
    if not types:
        return {"tool": None, "minTier": None, "toolRequired": False, "monotonic": True}
    capable = [t for t in TIERS if block_id not in tag_members(f"minecraft:incorrect_for_{t}_tool", tags, cache)]
    min_tier = capable[0] if capable else None
    monotonic = bool(capable) and capable == TIERS[TIERS.index(capable[0]):]
    tool = types[0]
    required = min_tier is not None and (tool == "pickaxe" or min_tier != "wooden")
    return {"tool": tool, "minTier": min_tier, "toolRequired": required, "monotonic": monotonic}


# --------------------------------------------------------------------------- drops

def classify_block(conditions: list) -> str | None:
    """Reduce block loot conditions to one label, or None for always."""
    text = json.dumps(conditions)
    if "silk_touch" in text:
        return "silk_touch"
    if "shears" in text:
        return "shears"
    if "block_state_property" in text:
        return "state"
    if "random_chance" in text or "table_bonus" in text:
        return "chance"
    return None


def classify_mob(conditions: list) -> str | None:
    """Reduce mob loot conditions to a '+'-joined set of labels, or None for always.

    A sheep's colour and a mooshroom's variant only decide WHICH item drops, so they are not conditions.
    """
    text = json.dumps(conditions)
    flags = set()
    if "killed_by_player" in text:
        flags.add("killed_by_player")
    if "random_chance" in text:
        flags.add("chance")
    if "damage_source_properties" in text:
        flags.add("state")
    if "entity_properties" in text and not any(k in text for k in ("sheep/color", "mooshroom/variant")):
        flags.add("state")
    return "+".join(sorted(flags)) or None


def override(inner: str | None, outer: str | None) -> str | None:
    """Blocks: the innermost label wins."""
    return inner or outer


def union(inner: str | None, outer: str | None) -> str | None:
    """Mobs: labels from all enclosing levels add up."""
    flags = set((inner or "").split("+")) | set((outer or "").split("+"))
    flags.discard("")
    return "+".join(sorted(flags)) or None


def number(x, default: float = 1.0) -> float:
    """A constant or the middle of a uniform range; default if unknown."""
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        if "value" in x:
            return float(x["value"])
        if "min" in x and "max" in x:
            lo, hi = max(float(x["min"]), 0.0), max(float(x["max"]), 0.0)   # a negative roll just means none
            return (lo + hi) / 2
    return default


def entry_count(entry: dict) -> float:
    """Typical item count from a set_count function; 1 if none. Bonuses are ignored."""
    for f in entry.get("functions", []):
        if f.get("function", "").endswith("set_count"):
            return number(f.get("count"))
    return 1.0


def walk(entries: list, inherited, classify, combine, tables: dict, depth: int):
    """Yield (item id, count, condition) for item entries, descending into alternatives, groups and referenced tables."""
    for e in entries:
        cond = combine(classify(e.get("conditions", [])), inherited)
        kind = e.get("type", "").split(":")[-1]
        if kind == "item":
            yield e["name"], entry_count(e), cond or "default"
        elif kind in ("alternatives", "group", "sequence"):
            yield from walk(e.get("children", []), cond, classify, combine, tables, depth)
        elif kind == "loot_table" and depth < MAX_REF_DEPTH and isinstance(e.get("value"), str):
            ref = tables.get(e["value"])
            if ref:
                for (item, c), count in table_drops(ref, classify, combine, tables, depth + 1, cond).items():
                    yield item, count, c
        # tag, dynamic and empty entries are skipped


def table_drops(loot: dict, classify, combine, tables: dict, depth: int = 0, inherited=None) -> dict:
    """Distinct (item, condition) -> typical count for one loot table."""
    out: dict[tuple[str, str], float] = {}
    for pool in loot.get("pools", []):
        rolls = number(pool.get("rolls", 1))
        pool_cond = combine(classify(pool.get("conditions", [])), inherited)
        for item, count, cond in walk(pool.get("entries", []), pool_cond, classify, combine, tables, depth):
            out.setdefault((item, cond), count * rolls)
    return out


# --------------------------------------------------------------------------- build

def build(jar: Path) -> dict:
    """Turn the jar into node and relationship rows for every action."""
    loot, tags = read_loot(jar)
    raw_recipes, _ = read_recipe_jar(jar)
    cache: dict = {}
    blocks, mobs, actions, creates, targets, tools, items = [], {}, [], [], [], [], {}
    stats = Counter()

    def add_drops(aid: str, drops: dict) -> None:
        for (item, cond), count in sorted(drops.items()):
            items[item] = display_name(item)
            creates.append({"action": aid, "item": item, "count": count, "condition": cond})

    # gathering: mine
    for key in sorted(k for k in loot if k.startswith("minecraft:blocks/")):
        block_id = key.replace("blocks/", "")
        blocks.append({"id": block_id, "name": display_name(block_id)})
        d = table_drops(loot[key], classify_block, override, loot)
        if not d:
            stats["blocks with no drops (no action)"] += 1
            continue
        t = analyse_tool(block_id, tags, cache)
        if not t["monotonic"]:
            stats["tier list not monotonic"] += 1
        aid = "mine:" + block_id
        actions.append({"id": aid, "name": f"Mine {display_name(block_id)}", "verb": "Mine", "tool": t["tool"],
                        "minTier": t["minTier"], "toolRequired": t["toolRequired"]})
        targets.append({"action": aid, "target": block_id, "kind": "Block"})
        add_drops(aid, d)
        if t["toolRequired"]:
            tool_item = f"minecraft:{t['minTier']}_{t['tool']}"
            items[tool_item] = display_name(tool_item)
            tools.append({"action": aid, "item": tool_item, "orBetter": True})

    # gathering: kill and shear
    for prefix, verb in (("entities", "Kill"), ("shearing", "Shear")):
        for key in sorted(k for k in loot if k.startswith(f"minecraft:{prefix}/") and k.count("/") == 1):
            mob_id = "minecraft:" + key.split("/", 1)[1]
            d = table_drops(loot[key], classify_mob, union, loot)
            if not d:
                stats[f"{prefix} with no drops (no action)"] += 1
                continue
            mobs[mob_id] = display_name(mob_id)
            aid = f"{verb.lower()}:{mob_id}"
            actions.append({"id": aid, "name": f"{verb} {display_name(mob_id)}", "verb": verb,
                            "tool": "shears" if verb == "Shear" else None, "minTier": None, "toolRequired": verb == "Shear"})
            targets.append({"action": aid, "target": mob_id, "kind": "Mob"})
            add_drops(aid, d)
            if verb == "Shear":
                items[SHEARS] = display_name(SHEARS)
                tools.append({"action": aid, "item": SHEARS, "orBetter": False})

    # transforming: one thin action per verb and item
    seen = set()
    transform = []
    for rid, raw in sorted(raw_recipes.items()):
        rec = normalise(rid, raw)
        if not rec["result"]:
            continue
        item = rec["result"][0]
        verb = RECIPE_VERB.get(rec["type"], "Craft")
        if (verb, item) in seen:
            continue
        seen.add((verb, item))
        items[item] = display_name(item)
        transform.append({"id": f"{verb.lower()}:{item}", "name": f"{verb} {display_name(item)}", "verb": verb, "item": item})
    for a in actions:
        stats[f"{a['verb']} actions"] += 1
    for a in transform:
        stats[f"{a['verb']} actions"] += 1
    return {"blocks": blocks, "mobs": [{"id": k, "name": v} for k, v in sorted(mobs.items())], "actions": actions,
            "creates": creates, "targets": targets, "tools": tools, "transform": transform,
            "items": [{"id": k, "name": v} for k, v in sorted(items.items())], "stats": stats}


def write(g: dict, uri: str, user: str, password: str, database: str) -> set:
    """Write the rows to Neo4j; return the item ids that did not already exist (for the report)."""
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as s:
            s.run("DROP CONSTRAINT harvest_id IF EXISTS")
            s.run("CREATE CONSTRAINT block_id IF NOT EXISTS FOR (n:Block) REQUIRE n.id IS UNIQUE")
            s.run("CREATE CONSTRAINT mob_id IF NOT EXISTS FOR (n:Mob) REQUIRE n.id IS UNIQUE")
            s.run("CREATE CONSTRAINT action_id IF NOT EXISTS FOR (n:Action) REQUIRE n.id IS UNIQUE")
            existing = {r["id"] for r in s.run("MATCH (i:Item) RETURN i.id AS id")}
            s.run("MATCH (n) WHERE n:Harvest OR n:Action DETACH DELETE n")  # regenerate (the graph is rebuilt from source)
            s.run("UNWIND $rows AS r MERGE (i:Item {id: r.id}) SET i.name = coalesce(i.name, r.name)", rows=g["items"])
            s.run("UNWIND $rows AS r MERGE (b:Block {id: r.id}) SET b.name = r.name", rows=g["blocks"])
            s.run("UNWIND $rows AS r MERGE (m:Mob {id: r.id}) SET m.name = r.name", rows=g["mobs"])
            for verb in GATHER_VERBS:
                s.run(f"""UNWIND $rows AS r WITH r WHERE r.verb = '{verb}'
                          CREATE (a:Action:{verb} {{id: r.id, name: r.name, tool: r.tool, minTier: r.minTier,
                                                    toolRequired: r.toolRequired}})""", rows=g["actions"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.kind = 'Block'
                     MATCH (a:Action {id: r.action}), (b:Block {id: r.target})
                     CREATE (a)-[:REQUIRES {role: 'target', slot: '', count: 1}]->(b)""", rows=g["targets"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.kind = 'Mob'
                     MATCH (a:Action {id: r.action}), (m:Mob {id: r.target})
                     CREATE (a)-[:REQUIRES {role: 'target', slot: '', count: 1}]->(m)""", rows=g["targets"])
            s.run("""UNWIND $rows AS r MATCH (a:Action {id: r.action}), (i:Item {id: r.item})
                     MERGE (a)-[c:CREATES {condition: r.condition}]->(i) SET c.count = r.count""", rows=g["creates"])
            s.run("""UNWIND $rows AS r MATCH (a:Action {id: r.action}), (i:Item {id: r.item})
                     CREATE (a)-[:REQUIRES {role: 'tool', slot: '', count: 1, orBetter: r.orBetter}]->(i)""", rows=g["tools"])
            for verb in TRANSFORM_VERBS:
                s.run(f"""UNWIND $rows AS r WITH r WHERE r.verb = '{verb}'
                          CREATE (a:Action:{verb} {{id: r.id, name: r.name}})
                          WITH a, r MATCH (i:Item {{id: r.item}}) CREATE (a)-[:CREATES]->(i)""", rows=g["transform"])
    return {i["id"] for i in g["items"]} - existing


def main() -> int:
    """Parse the jar, report, and (unless --dry-run) load into Neo4j."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    ap.add_argument("--dry-run", action="store_true", help="parse and report; do not touch Neo4j")
    args = ap.parse_args()
    if not args.jar.exists():
        sys.exit(f"Jar not found: {args.jar}")

    g = build(args.jar)
    print(f"  Block {len(g['blocks'])}  Mob {len(g['mobs'])}  Action {len(g['actions']) + len(g['transform'])}  "
          f"gathering CREATES {len(g['creates'])}  tool REQUIRES {len(g['tools'])}  items referenced {len(g['items'])}")
    print("  drop conditions:", dict(Counter(c["condition"] for c in g["creates"])))
    for k, v in sorted(g["stats"].items()):
        print(f"  {k}: {v}")
    if args.dry_run:
        return 0
    s = get_settings()
    new_items = write(g, s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    print(f"loaded. {len(new_items)} items were new to the graph (drops that no recipe uses).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
