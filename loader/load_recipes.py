"""Draft loader: Paper server jar -> MineCorp recipe graph in Neo4j.

Reads recipes and item tags straight from the server jar (the authoritative source),
builds the subset reachable *backwards* from one or more seed items (everything needed
to make them), and writes it to Neo4j following docs/ontology.md (v0.4).

    python loader/load_recipes.py --dry-run                      # parse + report, no database
    python loader/load_recipes.py --seed minecraft:iron_pickaxe  # load a subset into Neo4j (see .env)
    python loader/load_recipes.py --all                          # load everything
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JAR = ROOT / "server" / "versions" / "26.2" / "paper-26.2.jar"

# Ontology: recipe type -> station (see docs/ontology.md)
STATIONS = {
    "smelting": ("minecraft:furnace", "Furnace"),
    "blasting": ("minecraft:blast_furnace", "Blast Furnace"),
    "smoking": ("minecraft:smoker", "Smoker"),
    "campfire_cooking": ("minecraft:campfire", "Campfire"),
    "stonecutting": ("minecraft:stonecutter", "Stonecutter"),
    "smithing_transform": ("minecraft:smithing_table", "Smithing Table"),
    "smithing_trim": ("minecraft:smithing_table", "Smithing Table"),
}
CRAFTING_TABLE = ("minecraft:crafting_table", "Crafting Table")

# Single-ingredient roles by recipe type (field name -> role)
SINGLE_FIELDS = {
    "stonecutting": {"ingredient": "ingredient"},
    "smelting": {"ingredient": "ingredient"},
    "blasting": {"ingredient": "ingredient"},
    "smoking": {"ingredient": "ingredient"},
    "campfire_cooking": {"ingredient": "ingredient"},
    "crafting_transmute": {"input": "input", "material": "material"},
    "smithing_transform": {"template": "template", "base": "base", "addition": "addition"},
    "smithing_trim": {"template": "template", "base": "base", "addition": "addition"},
}


def short_type(t: str) -> str:
    """Strip the namespace from a recipe type, e.g. 'minecraft:smelting' -> 'smelting'."""
    return t.split(":", 1)[-1]


def display_name(item_id: str) -> str:
    """Readable name from an item id, e.g. 'minecraft:iron_ingot' -> 'Iron Ingot'."""
    return item_id.split(":", 1)[-1].replace("_", " ").title()


# --------------------------------------------------------------------------- read

def read_jar(jar: Path):
    """Read all recipe and item-tag JSON files from the server jar.

    Returns (recipes, tags): recipes maps 'minecraft:<name>' to the raw recipe dict;
    tags maps 'minecraft:<name>' to the tag's list of values (item ids or '#tag' refs).
    """
    recipes, tags = {}, {}
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            m = re.fullmatch(r"data/minecraft/recipe/(.+)\.json", name)
            if m:
                recipes[f"minecraft:{m.group(1)}"] = json.loads(z.read(name))
                continue
            m = re.fullmatch(r"data/minecraft/tags/item/(.+)\.json", name)
            if m:
                tags[f"minecraft:{m.group(1)}"] = json.loads(z.read(name))["values"]
    return recipes, tags


# --------------------------------------------------------------------------- normalise

def norm_ref(ref):
    """Classify a recipe ingredient reference.

    A reference is an item id, a '#tag', or a list of item ids/tags (alternatives).
    Returns ('item'|'tag'|'inline', id, members): tag ids lose the '#', and an inline list
    gets a stable hash id with its members returned (otherwise members is None).
    """
    if isinstance(ref, list):
        members = sorted(set(ref))
        h = hashlib.sha1(",".join(members).encode()).hexdigest()[:12]
        return ("inline", f"inline:{h}", members)
    if ref.startswith("#"):
        return ("tag", ref[1:], None)
    return ("item", ref, None)


def fits_inventory_grid(rtype: str, raw: dict) -> bool:
    """Whether a crafting recipe can be made in the 2x2 grid of the player's inventory, so a crafting table is not needed.

    The game's rule: a shaped recipe whose pattern is at most 2 wide and 2 high; a shapeless recipe with at most 4 ingredients; a transmute
    recipe (one input and one material, such as dyeing a bundle). The special recipe types (banners, fireworks, map extending, armor dyeing...)
    have no pattern in the data, so they are not claimed to fit."""
    if rtype == "crafting_shaped":
        rows = raw["pattern"]
        return len(rows) <= 2 and max(len(row) for row in rows) <= 2
    if rtype == "crafting_shapeless":
        return len(raw["ingredients"]) <= 4
    return rtype == "crafting_transmute"


def normalise(recipe_id: str, raw: dict) -> dict:
    """Convert a raw recipe into the ontology's shape, whatever its recipe type.

    Collects inputs as (role, ref, count, slot) tuples (shaped recipes count key symbols in
    the pattern; shapeless recipes count repeats), the result as (item id, count), and the
    station implied by the type. A recipe with no inputs is flagged `special`.
    """
    rtype = short_type(raw["type"])
    inputs = []  # (role, ref, count, slot)
    if rtype == "crafting_shaped":
        symbols = "".join(raw["pattern"])
        for sym, ref in raw["key"].items():
            inputs.append(("ingredient", ref, symbols.count(sym), sym))
    elif rtype == "crafting_shapeless":
        counts = defaultdict(int)
        refs = {}
        for ref in raw["ingredients"]:
            k = json.dumps(ref, sort_keys=True)
            counts[k] += 1
            refs[k] = ref
        inputs = [("ingredient", refs[k], n, "") for k, n in counts.items()]
    elif rtype in SINGLE_FIELDS:
        for field, role in SINGLE_FIELDS[rtype].items():
            if field in raw:
                inputs.append((role, raw[field], 1, ""))
    result = raw.get("result")
    station = STATIONS.get(rtype, CRAFTING_TABLE if rtype.startswith("crafting_") else None)
    return {
        "id": recipe_id,
        "name": display_name(recipe_id),
        "type": rtype,
        "category": raw.get("category"),
        "group": raw.get("group"),
        "experience": raw.get("experience"),
        "cookingTime": raw.get("cookingtime"),
        "pattern": raw.get("pattern"),
        "key": json.dumps(raw["key"], sort_keys=True) if "key" in raw else None,
        "special": not inputs,
        "result": (result["id"], result.get("count", 1)) if isinstance(result, dict) else None,
        "inputs": inputs,
        "station": station,
        "stationOptional": station == CRAFTING_TABLE and fits_inventory_grid(rtype, raw),
    }


# --------------------------------------------------------------------------- subset

def tag_items(tag_id: str, tags: dict, seen=None):
    """Resolve a tag to all the items under it, following nested tags.

    Returns (items, seen) where seen is the set of tag ids visited (also guards against cycles).
    """
    seen = seen if seen is not None else set()
    if tag_id in seen:
        return set(), seen
    seen.add(tag_id)
    items = set()
    for v in tags.get(tag_id, []):
        if v.startswith("#"):
            sub, _ = tag_items(v[1:], tags, seen)
            items |= sub
        else:
            items.add(v)
    return items, seen


def build_subset(recipes: dict, tags: dict, seeds: list[str], max_depth: int):
    """Select the recipes needed to make the seed items, searching breadth-first backwards.

    For each item reached, every recipe that produces it is chosen and its inputs are
    queued one level deeper (tags and inline lists are expanded to their items). Stops at
    max_depth; a visited set stops loops such as ingot <-> nuggets. Returns
    (chosen recipes by id, set of tag ids visited).
    """
    producers = defaultdict(list)
    for rid, r in recipes.items():
        if r["result"]:
            producers[r["result"][0]].append(rid)

    chosen, used_tags = {}, set()
    queue = deque((s, 0) for s in seeds)
    seen_items = set(seeds)
    while queue:
        item, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for rid in producers.get(item, []):
            if rid in chosen:
                continue
            chosen[rid] = recipes[rid]
            for _role, ref, _n, _slot in recipes[rid]["inputs"]:
                kind, rid_ref, members = norm_ref(ref)
                if kind == "item":
                    nxt = {rid_ref}
                elif kind == "tag":
                    nxt, visited = tag_items(rid_ref, tags)
                    used_tags |= visited
                else:
                    nxt = set()
                    for m in members:
                        if m.startswith("#"):
                            sub, visited = tag_items(m[1:], tags)
                            nxt |= sub
                            used_tags |= visited
                        else:
                            nxt.add(m)
                for it in nxt:
                    if it not in seen_items:
                        seen_items.add(it)
                        queue.append((it, depth + 1))
    return chosen, used_tags


def common_trailing_words(item_ids) -> str:
    """The trailing word(s) shared by every item id, Title Cased; '' if there are none.

    ['minecraft:red_bed', 'minecraft:blue_bed'] -> 'Bed'.
    """
    words = [i.split(":", 1)[-1].replace("/", "_").split("_") for i in item_ids]
    shared = []
    for k in range(1, min(len(w) for w in words) + 1):
        word = words[0][-k]
        if all(w[-k] == word for w in words):
            shared.insert(0, word)
        else:
            break
    return " ".join(shared).title()


def class_name(class_id: str, inline: bool, concrete: set) -> str:
    """Readable name for an ItemClass (rule order in docs/ontology.md).

    1. two or more concrete items: the trailing word(s) they all share ("Bed");
    2. a real tag: the tag name, Title Cased (also used when rule 1 would give two real tags
       the same name, e.g. 'Slabs' and 'Wooden Slabs' instead of two 'Slab');
    3. an inline list: "Any of: A, B, C".
    """
    if len(concrete) >= 2:
        shared = common_trailing_words(concrete)
        if shared:
            return shared
    if not inline:
        return display_name(class_id.lstrip("#")).replace("/", " ")
    names = sorted(display_name(i) for i in concrete)
    return "Any of: " + ", ".join(names[:4]) + (", ..." if len(names) > 4 else "")


def to_graph(chosen: dict, tags: dict, used_tags: set, seeds: list[str]):
    """Turn the chosen recipes into node and relationship lists matching docs/ontology.md (v0.4).

    Returns a dict of plain lists (items, classes, stations, recipes, creates, requires,
    includes, uses_station) ready for loading or for writing out as JSON.

    An ingredient is an Item, or an ItemClass (a Minecraft tag, id '#minecraft:planks', or an
    anonymous list of alternatives, id 'inline:<hash>'). An anonymous list that equals a real
    tag minus at most one item is not stored as a class of its own: the recipe points at the
    real tag and records the left-out item(s) in `excludes` (e.g. "any bed except a brown one").
    """
    items, classes, stations = set(seeds), {}, {}
    creates, requires, includes, uses_station = [], [], [], []
    concrete_cache = {}

    def concrete(tag_id):
        if tag_id not in concrete_cache:
            concrete_cache[tag_id] = tag_items(tag_id, tags)[0]
        return concrete_cache[tag_id]

    def expand(members):
        out = set()
        for m in members:
            out |= concrete(m[1:]) if m.startswith("#") else {m}
        return out

    def add_class(class_id, inline, members):
        """Register a class and its INCLUDES edges (recursing into nested real tags)."""
        if class_id in classes:
            return
        conc = expand(members)
        classes[class_id] = {"id": class_id, "inline": inline, "size": len(conc),
                             "name": class_name(class_id, inline, conc)}
        for m in members:
            if m.startswith("#"):
                add_class(m, False, tags.get(m[1:], []))
                includes.append({"cls": class_id, "target": m})
            else:
                items.add(m)
                includes.append({"cls": class_id, "target": m})

    def real_tag_for(members):
        """Smallest real tag that contains all members and has at most one extra item."""
        want = expand(members)
        best = None
        for tag_id in tags:
            have = concrete(tag_id)
            if want <= have and len(have - want) <= 1:
                if best is None or len(have) < len(concrete(best)):
                    best = tag_id
        return best, (sorted(concrete(best) - want) if best else [])

    def resolve(ref):
        """Return (target id, excluded item ids) for an ingredient reference."""
        if isinstance(ref, list):
            members = sorted(set(ref))
            tag_id, excluded = real_tag_for(members)
            if tag_id:
                add_class("#" + tag_id, False, tags[tag_id])
                return "#" + tag_id, excluded
            class_id = "inline:" + hashlib.sha1(",".join(members).encode()).hexdigest()[:12]
            add_class(class_id, True, members)
            return class_id, []
        if ref.startswith("#"):
            add_class(ref, False, tags.get(ref[1:], []))
            return ref, []
        items.add(ref)
        return ref, []

    for tag_id in sorted(used_tags):
        add_class("#" + tag_id, False, tags.get(tag_id, []))

    for rid, r in chosen.items():
        if r["result"]:
            items.add(r["result"][0])
            creates.append({"recipe": rid, "item": r["result"][0], "count": r["result"][1]})
        if r["station"]:
            stations[r["station"][0]] = {"id": r["station"][0], "name": r["station"][1]}
            uses_station.append({"recipe": rid, "station": r["station"][0], "optional": r["stationOptional"]})
        for role, ref, count, slot in r["inputs"]:
            target, excluded = resolve(ref)
            requires.append({"recipe": rid, "target": target, "role": role, "count": count,
                             "slot": slot, "excludes": excluded,
                             "excludesNames": [display_name(e) for e in excluded]})

    # A shared word can hide a qualifier (wooden_slabs and slabs are both "Slab"). Where real
    # tags would end up with the same name, use their full tag names instead.
    counts = defaultdict(int)
    for c in classes.values():
        counts[c["name"]] += 1
    for c in classes.values():
        if counts[c["name"]] > 1 and not c["inline"]:
            c["name"] = display_name(c["id"].lstrip("#")).replace("/", " ")

    # de-duplicate INCLUDES rows (shared nested tags are visited more than once)
    includes = [dict(t) for t in {tuple(sorted(i.items())) for i in includes}]
    includes.sort(key=lambda i: (i["cls"], i["target"]))

    return {
        "items": [{"id": i, "name": display_name(i)} for i in sorted(items)],
        "classes": sorted(classes.values(), key=lambda c: c["id"]),
        "stations": sorted(stations.values(), key=lambda s: s["id"]),
        "recipes": [{k: v for k, v in r.items() if k not in ("inputs", "result", "station")}
                    for r in chosen.values()],
        "creates": creates, "requires": requires, "includes": includes,
        "uses_station": uses_station,
    }


# --------------------------------------------------------------------------- write

def load_into_neo4j(g: dict, uri: str, user: str, password: str, database: str, wipe: bool):
    """Write the graph to Neo4j in batches, creating uniqueness constraints first.

    Uses MERGE throughout, so repeat runs are safe; `wipe` deletes all existing data first.
    ItemClass and Station nodes are Item nodes with an extra label. `wipe` also removes the authored MineCorp
    model and the actions: reload them afterwards with load_actions.py and load_model.py.
    """
    from quiet_driver import open_driver

    with open_driver(uri, user, password) as driver:
        driver.verify_connectivity()
        with driver.session(database=database) as s:
            if wipe:
                s.run("MATCH (n) DETACH DELETE n")
            s.run("DROP CONSTRAINT tag_id IF EXISTS")  # v0.3 leftover
            for q in (
                "CREATE CONSTRAINT item_id IF NOT EXISTS FOR (n:Item) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT recipe_id IF NOT EXISTS FOR (n:Recipe) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT station_id IF NOT EXISTS FOR (n:Station) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT task_id IF NOT EXISTS FOR (n:Task) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT player_uuid IF NOT EXISTS FOR (n:Player) REQUIRE n.uuid IS UNIQUE",
            ):
                s.run(q)
            from name_index import ensure_name_index
            ensure_name_index(s)
            s.run("UNWIND $rows AS r MERGE (n:Item {id: r.id}) SET n.name = r.name",
                  rows=g["items"])
            s.run("""UNWIND $rows AS r MERGE (n:Item {id: r.id}) SET n:ItemClass,
                         n.name = r.name, n.inline = r.inline, n.size = r.size""",
                  rows=g["classes"])
            # A station is an Item with an extra label (the crafting table you craft is the one recipes use).
            # Older graphs held separate Station nodes; remove those before labelling the items.
            s.run("MATCH (n:Station) WHERE NOT n:Item DETACH DELETE n")
            s.run("UNWIND $rows AS r MERGE (n:Item {id: r.id}) SET n:Station, n.name = r.name",
                  rows=g["stations"])
            s.run("""UNWIND $rows AS r MERGE (n:Recipe {id: r.id})
                     SET n.type = r.type, n.category = r.category, n.group = r.group,
                         n.experience = r.experience, n.cookingTime = r.cookingTime,
                         n.pattern = r.pattern, n.key = r.key, n.special = r.special,
                         n.name = r.name""", rows=g["recipes"])
            s.run("""UNWIND $rows AS r MATCH (c:Item {id: r.cls}) MATCH (m:Item {id: r.target})
                     MERGE (c)-[:INCLUDES]->(m)""", rows=g["includes"])
            s.run("""UNWIND $rows AS r MATCH (a:Recipe {id: r.recipe}) MATCH (b:Item {id: r.item})
                     MERGE (a)-[p:CREATES]->(b) SET p.count = r.count""", rows=g["creates"])
            s.run("""UNWIND $rows AS r MATCH (a:Recipe {id: r.recipe}) MATCH (b:Station {id: r.station})
                     MERGE (a)-[u:USES_STATION]->(b) SET u.optional = r.optional""", rows=g["uses_station"])
            s.run("""UNWIND $rows AS r MATCH (a:Recipe {id: r.recipe}) MATCH (b:Item {id: r.target})
                     MERGE (a)-[c:REQUIRES {role: r.role, slot: r.slot}]->(b)
                     SET c.count = r.count, c.excludes = r.excludes, c.excludesNames = r.excludesNames""",
                  rows=g["requires"])


def main():
    """Command-line entry point: parse arguments, build the subset, report, and optionally load."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    ap.add_argument("--seed", action="append", default=None, help="item id to build back from (repeatable)")
    ap.add_argument("--all", action="store_true", help="load every recipe and item tag (ignores --seed/--max-depth)")
    ap.add_argument("--max-depth", type=int, default=3, help="how many recipe levels back from the seeds")
    ap.add_argument("--dry-run", action="store_true", help="parse and report; do not touch Neo4j")
    ap.add_argument("--out", type=Path, help="also write the subset graph as JSON")
    ap.add_argument("--wipe", action="store_true", help="delete everything in the database first")
    a = ap.parse_args()

    seeds = a.seed or ["minecraft:iron_pickaxe"]
    if not a.jar.exists():
        sys.exit(f"Jar not found: {a.jar}\nRun scripts/start-server.ps1 once to download Paper.")

    raw_recipes, tags = read_jar(a.jar)
    recipes = {rid: normalise(rid, r) for rid, r in raw_recipes.items()}
    if a.all:
        chosen, used_tags, seeds = recipes, set(tags), []
    else:
        chosen, used_tags = build_subset(recipes, tags, seeds, a.max_depth)
    g = to_graph(chosen, tags, used_tags, seeds)

    print(f"jar: {a.jar.name}: {len(recipes)} recipes, {len(tags)} item tags")
    print("scope: ALL recipes and item tags" if a.all else f"seeds: {seeds}  depth: {a.max_depth}")
    for k in ("items", "classes", "stations", "recipes", "creates", "requires", "includes", "uses_station"):
        print(f"  {k:13s} {len(g[k])}")
    if a.out:
        a.out.write_text(json.dumps(g, indent=2))
        print(f"wrote {a.out}")
    if a.dry_run:
        return

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD")
    if not pw:
        sys.exit("Set NEO4J_PASSWORD (and optionally NEO4J_URI / NEO4J_USER / NEO4J_DATABASE), e.g. from .env.")
    load_into_neo4j(g, uri, user, pw, database, a.wipe)
    print("loaded.")


if __name__ == "__main__":
    main()
