"""Load harvest definitions from the Paper jar into Neo4j (docs/ontology.md v0.6).

    python loader/load_harvests.py --dry-run   # parse and report, no database
    python loader/load_harvests.py             # load

Harvest kinds, all from loot tables in the jar:
    mine   block loot tables (blocks/*)      -> (Harvest)-[:FROM_BLOCK]->(Block)
    kill   mob loot tables (entities/*)      -> (Harvest)-[:FROM_MOB]->(Mob)
    shear  shearing loot tables (shearing/*) -> (Harvest)-[:FROM_MOB]->(Mob), needs Shears
Every harvest has (Harvest)-[:CREATES {count, condition}]->(Item). Mining a block that needs a tool also gets
(Harvest)-[:REQUIRES {role: 'tool', orBetter: true}]->(minimum tool Item); shearing requires Shears.

Run it after `load_recipes.py --all` (the tool items come from there) and before `compute_routes.py`. Safe to
repeat: Harvest nodes are rebuilt each time.

What the jar does and does not say about tools:
- Tool TYPE for blocks comes from the block tags mineable/pickaxe, axe, shovel, hoe.
- Minimum TIER comes from incorrect_for_<tier>_tool: the first tier whose tag does not list the block.
- Whether a block drops anything WITHOUT the tool is not in the data (stone gives nothing by hand, logs do).
  Rule used: a tool is "required" for pickaxe blocks and for any block that needs better than a wooden tool;
  axe, shovel and hoe blocks only mine faster with the tool. Exceptions exist.
- Mobs need no tool to kill; a weapon is only faster. Looting and fortune bonuses are ignored everywhere.

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

from load_recipes import DEFAULT_JAR, display_name  # noqa: E402
from minecorp_agent.config import get_settings  # noqa: E402

TIERS = ["wooden", "stone", "copper", "iron", "diamond", "netherite"]   # weakest to strongest (gold mines like wood)
TOOLS = ["pickaxe", "axe", "shovel", "hoe"]                              # order of preference when a block allows several
SHEARS = "minecraft:shears"
MAX_REF_DEPTH = 4                                                        # loot tables that reference other tables


# --------------------------------------------------------------------------- read

def read_jar(jar: Path) -> tuple[dict, dict]:
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

def build(loot: dict, tags: dict) -> dict:
    """Turn the jar data into node and relationship rows."""
    cache: dict = {}
    blocks, mobs, harvests, creates, requires, items = [], [], [], [], [], {}
    stats = Counter()

    def add_drops(hid: str, drops: dict) -> None:
        for (item, cond), count in sorted(drops.items()):
            items[item] = display_name(item)
            creates.append({"harvest": hid, "item": item, "count": count, "condition": cond})

    for key in sorted(k for k in loot if k.startswith("minecraft:blocks/")):
        block_id = key.replace("blocks/", "")
        blocks.append({"id": block_id, "name": display_name(block_id)})
        d = table_drops(loot[key], classify_block, override, loot)
        if not d:
            stats["blocks with no drops (no harvest)"] += 1
            continue
        t = analyse_tool(block_id, tags, cache)
        if not t["monotonic"]:
            stats["tier list not monotonic"] += 1
        harvests.append({"id": key, "name": f"Mine {display_name(block_id)}", "kind": "mine", "tool": t["tool"],
                         "minTier": t["minTier"], "toolRequired": t["toolRequired"], "block": block_id, "mob": None})
        add_drops(key, d)
        if t["toolRequired"]:
            tool_item = f"minecraft:{t['minTier']}_{t['tool']}"
            items[tool_item] = display_name(tool_item)
            requires.append({"harvest": key, "item": tool_item, "orBetter": True})
        stats[f"mine tool={t['tool']}"] += 1

    seen_mobs = {}
    for prefix, kind, verb, classify_fn in (("entities", "kill", "Kill", classify_mob), ("shearing", "shear", "Shear", classify_mob)):
        for key in sorted(k for k in loot if k.startswith(f"minecraft:{prefix}/") and k.count("/") == 1):
            mob_id = "minecraft:" + key.split("/", 1)[1]
            d = table_drops(loot[key], classify_fn, union, loot)
            if not d:
                stats[f"{prefix} with no drops (no harvest)"] += 1
                continue
            seen_mobs[mob_id] = display_name(mob_id)
            harvests.append({"id": key, "name": f"{verb} {display_name(mob_id)}", "kind": kind, "tool": "shears" if kind == "shear" else None,
                             "minTier": None, "toolRequired": kind == "shear", "block": None, "mob": mob_id})
            add_drops(key, d)
            if kind == "shear":
                items[SHEARS] = display_name(SHEARS)
                requires.append({"harvest": key, "item": SHEARS, "orBetter": False})
            stats[f"{kind} harvests"] += 1
    mobs = [{"id": k, "name": v} for k, v in sorted(seen_mobs.items())]
    return {"blocks": blocks, "mobs": mobs, "harvests": harvests, "creates": creates, "requires": requires,
            "items": [{"id": k, "name": v} for k, v in sorted(items.items())], "stats": stats}


def write(g: dict, uri: str, user: str, password: str, database: str) -> set:
    """Write the rows to Neo4j; return the item ids that did not already exist (for the report)."""
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as s:
            s.run("CREATE CONSTRAINT block_id IF NOT EXISTS FOR (n:Block) REQUIRE n.id IS UNIQUE")
            s.run("CREATE CONSTRAINT mob_id IF NOT EXISTS FOR (n:Mob) REQUIRE n.id IS UNIQUE")
            s.run("CREATE CONSTRAINT harvest_id IF NOT EXISTS FOR (n:Harvest) REQUIRE n.id IS UNIQUE")
            existing = {r["id"] for r in s.run("MATCH (i:Item) RETURN i.id AS id")}
            s.run("MATCH (h:Harvest) DETACH DELETE h")  # regenerate
            s.run("UNWIND $rows AS r MERGE (i:Item {id: r.id}) SET i.name = coalesce(i.name, r.name)", rows=g["items"])
            s.run("UNWIND $rows AS r MERGE (b:Block {id: r.id}) SET b.name = r.name", rows=g["blocks"])
            s.run("UNWIND $rows AS r MERGE (m:Mob {id: r.id}) SET m.name = r.name", rows=g["mobs"])
            s.run("""UNWIND $rows AS r MERGE (h:Harvest {id: r.id})
                     SET h.name = r.name, h.kind = r.kind, h.tool = r.tool, h.minTier = r.minTier,
                         h.toolRequired = r.toolRequired""", rows=g["harvests"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.block IS NOT NULL
                     MATCH (h:Harvest {id: r.id}), (b:Block {id: r.block}) MERGE (h)-[:FROM_BLOCK]->(b)""", rows=g["harvests"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.mob IS NOT NULL
                     MATCH (h:Harvest {id: r.id}), (m:Mob {id: r.mob}) MERGE (h)-[:FROM_MOB]->(m)""", rows=g["harvests"])
            s.run("""UNWIND $rows AS r MATCH (h:Harvest {id: r.harvest}) MATCH (i:Item {id: r.item})
                     MERGE (h)-[c:CREATES {condition: r.condition}]->(i) SET c.count = r.count""", rows=g["creates"])
            s.run("""UNWIND $rows AS r MATCH (h:Harvest {id: r.harvest}) MATCH (i:Item {id: r.item})
                     MERGE (h)-[q:REQUIRES {role: 'tool', slot: ''}]->(i) SET q.count = 1, q.orBetter = r.orBetter""",
                  rows=g["requires"])
    return {i["id"] for i in g["items"]} - existing


def main() -> int:
    """Parse the jar, report, and (unless --dry-run) load into Neo4j."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    ap.add_argument("--dry-run", action="store_true", help="parse and report; do not touch Neo4j")
    args = ap.parse_args()
    if not args.jar.exists():
        sys.exit(f"Jar not found: {args.jar}")

    loot, tags = read_jar(args.jar)
    g = build(loot, tags)
    print(f"{len(loot)} loot tables (blocks, entities, shearing), {len(tags)} block tags")
    print(f"  Block {len(g['blocks'])}  Mob {len(g['mobs'])}  Harvest {len(g['harvests'])}  CREATES {len(g['creates'])}  "
          f"tool REQUIRES {len(g['requires'])}  items referenced {len(g['items'])}")
    print("  conditions:", dict(Counter(c["condition"] for c in g["creates"])))
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
