"""Load block harvest definitions (mining) from the Paper jar into Neo4j (docs/ontology.md v0.6).

    python loader/load_harvests.py --dry-run   # parse and report, no database
    python loader/load_harvests.py             # load

Creates, from block loot tables and block tags:
    (:Block), (:Harvest {tool, minTier, toolRequired}), (Harvest)-[:FROM_BLOCK]->(Block),
    (Harvest)-[:CREATES {count, condition}]->(Item), and for blocks that need a tool
    (Harvest)-[:REQUIRES {role: 'tool', orBetter: true}]->(minimum tool Item).

Run it after `load_recipes.py --all` (the tool items come from there) and before
`compute_routes.py`. Safe to repeat: Harvest nodes are rebuilt each time.

What the jar does and does not say about tools:
- Tool TYPE comes from the block tags mineable/pickaxe, axe, shovel, hoe.
- Minimum TIER comes from incorrect_for_<tier>_tool: the first tier whose tag does not list the block.
- Whether the block drops anything WITHOUT the tool is not in the data (stone gives nothing by hand,
  logs do). Rule used: a tool is "required" for pickaxe blocks and for any block that needs better
  than a wooden tool; axe, shovel and hoe blocks only mine faster with the tool. Exceptions exist.
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


# --------------------------------------------------------------------------- read

def read_jar(jar: Path) -> tuple[dict, dict]:
    """Return (block loot tables by block id, block tags by tag id) from the jar."""
    loot, tags = {}, {}
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            m = re.fullmatch(r"data/minecraft/loot_table/blocks/(.+)\.json", name)
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


# --------------------------------------------------------------------------- tools

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

def classify(conditions: list) -> str | None:
    """Reduce loot conditions to one label: silk_touch, shears, state, chance, or None for always."""
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


def entry_count(entry: dict) -> float:
    """Typical item count from a set_count function; 1 if none. Bonuses are ignored."""
    for f in entry.get("functions", []):
        if f.get("function", "").endswith("set_count"):
            c = f.get("count")
            if isinstance(c, (int, float)):
                return float(c)
            if isinstance(c, dict):
                if "value" in c:
                    return float(c["value"])
                if "min" in c and "max" in c:
                    return (float(c["min"]) + float(c["max"])) / 2
    return 1.0


def walk(entries: list, inherited: str | None):
    """Yield (item id, count, condition) for item entries, descending into alternatives and groups."""
    for e in entries:
        cond = classify(e.get("conditions", [])) or inherited
        kind = e.get("type", "").split(":")[-1]
        if kind == "item":
            yield e["name"], entry_count(e), cond or "default"
        elif kind in ("alternatives", "group", "sequence"):
            yield from walk(e.get("children", []), cond)
        # tag, loot_table, dynamic and empty entries are skipped


def drops(loot: dict) -> dict:
    """Distinct (item, condition) -> typical count for one block loot table."""
    out: dict[tuple[str, str], float] = {}
    for pool in loot.get("pools", []):
        rolls = pool.get("rolls", 1)
        rolls = float(rolls) if isinstance(rolls, (int, float)) else 1.0
        for item, count, cond in walk(pool.get("entries", []), classify(pool.get("conditions", []))):
            out.setdefault((item, cond), count * rolls)
    return out


# --------------------------------------------------------------------------- build

def build(loot: dict, tags: dict) -> dict:
    """Turn the jar data into node and relationship rows."""
    cache: dict = {}
    blocks, harvests, creates, requires, items = [], [], [], [], {}
    stats = Counter()
    for block_id in sorted(loot):
        blocks.append({"id": block_id, "name": display_name(block_id)})
        d = drops(loot[block_id])
        if not d:
            stats["no drops (skipped harvest)"] += 1
            continue
        t = analyse_tool(block_id, tags, cache)
        if not t["monotonic"]:
            stats["tier list not monotonic"] += 1
        hid = block_id.replace("minecraft:", "minecraft:blocks/")
        harvests.append({"id": hid, "name": f"Mine {display_name(block_id)}", "kind": "mine",
                         "tool": t["tool"], "minTier": t["minTier"], "toolRequired": t["toolRequired"],
                         "block": block_id})
        for (item, cond), count in sorted(d.items()):
            items[item] = display_name(item)
            creates.append({"harvest": hid, "item": item, "count": count, "condition": cond})
        if t["toolRequired"]:
            tool_item = f"minecraft:{t['minTier']}_{t['tool']}"
            items[tool_item] = display_name(tool_item)
            requires.append({"harvest": hid, "item": tool_item})
        stats[f"tool={t['tool']}"] += 1
        stats[f"minTier={t['minTier']}"] += 1
    return {"blocks": blocks, "harvests": harvests, "creates": creates, "requires": requires,
            "items": [{"id": k, "name": v} for k, v in sorted(items.items())], "stats": stats}


def write(g: dict, uri: str, user: str, password: str, database: str) -> dict:
    """Write the rows to Neo4j; return the tool items that did not already exist (for the report)."""
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as s:
            s.run("CREATE CONSTRAINT block_id IF NOT EXISTS FOR (n:Block) REQUIRE n.id IS UNIQUE")
            s.run("CREATE CONSTRAINT harvest_id IF NOT EXISTS FOR (n:Harvest) REQUIRE n.id IS UNIQUE")
            existing = {r["id"] for r in s.run("MATCH (i:Item) RETURN i.id AS id")}
            s.run("MATCH (h:Harvest) DETACH DELETE h")  # regenerate
            s.run("UNWIND $rows AS r MERGE (i:Item {id: r.id}) SET i.name = coalesce(i.name, r.name)", rows=g["items"])
            s.run("UNWIND $rows AS r MERGE (b:Block {id: r.id}) SET b.name = r.name", rows=g["blocks"])
            s.run("""UNWIND $rows AS r MERGE (h:Harvest {id: r.id})
                     SET h.name = r.name, h.kind = r.kind, h.tool = r.tool, h.minTier = r.minTier,
                         h.toolRequired = r.toolRequired
                     WITH h, r MATCH (b:Block {id: r.block}) MERGE (h)-[:FROM_BLOCK]->(b)""", rows=g["harvests"])
            s.run("""UNWIND $rows AS r MATCH (h:Harvest {id: r.harvest}) MATCH (i:Item {id: r.item})
                     MERGE (h)-[c:CREATES {condition: r.condition}]->(i) SET c.count = r.count""", rows=g["creates"])
            s.run("""UNWIND $rows AS r MATCH (h:Harvest {id: r.harvest}) MATCH (i:Item {id: r.item})
                     MERGE (h)-[q:REQUIRES {role: 'tool', slot: ''}]->(i) SET q.count = 1, q.orBetter = true""",
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
    print(f"{len(loot)} block loot tables, {len(tags)} block tags")
    print(f"  Block {len(g['blocks'])}  Harvest {len(g['harvests'])}  CREATES {len(g['creates'])}  "
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
