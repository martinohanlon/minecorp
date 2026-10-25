"""Load enchantments from the Paper jar (docs/ontology.md v0.13): what each is, what it goes on, what it conflicts with, where it comes from.

    python loader/load_enchantments.py --dry-run     # parse and report, no database
    python loader/load_enchantments.py               # load

Run it after `load_recipes.py --all --wipe` (the items it points at) and before `load_trades.py` (trades link to the classes made here),
so the order is: recipes, actions, world, enchantments, trades, model, routes, wiki, links. The graph is rebuilt from source: this
loader deletes and recreates the Enchantment and EnchantmentClass nodes it owns, and adds to the item categories (ItemClass).

What is built (structure only: the effects, which are formulas in 30 kinds of effect component, are not modelled):

    (Enchantment {id, name, maxLevel, weight, anvilCost, minCostBase, minCostPerLevel, maxCostBase, maxCostPerLevel, slots, effectTypes})
    (EnchantmentClass {id, name})-[:INCLUDES]->(Enchantment | EnchantmentClass)     tags: where it comes from, what kind it is, what it conflicts with
    (Enchantment)-[:APPLIES_TO {primary}]->(ItemClass)       what it can go on; primary: the enchanting table may offer it for these too
    (ItemClass)-[:INCLUDES]->(Item | ItemClass)              the item tags `enchantable/*` and the tags they name (axes, swords...)

Classes are flat tags, as for biomes and mobs. Those kept: `curse`, `treasure`, `non_treasure`, `tradeable`, `on_random_loot`,
`on_traded_equipment`, `in_enchanting_table`, `double_trade_price` and every exclusive set (two enchantments that cannot be on one item
are in the same set: Sharpness, Smite, Bane of Arthropods, Impaling, Density and Breach). The tags about game mechanics (what stops
bees spawning, what melts ice) and the one that lists all of them (`tooltip_order`) are left out. How to get an enchantment is not stored
as a verb: it is the classes it is in (the enchanting table, random loot and fishing, villager trades, or only treasure).
The level the enchanting table needs for level N is `base + per_level_above_first * (N - 1)`, so `min` and `max` cost are kept as
a base and a per-level step.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from neo4j import GraphDatabase  # noqa: E402,F401

from quiet_driver import open_driver  # noqa: E402

from load_actions import tag_members  # noqa: E402
from load_recipes import DEFAULT_JAR, display_name  # noqa: E402
from name_index import ensure_name_index  # noqa: E402
from minecorp_agent.config import get_settings  # noqa: E402

NS = "minecraft:"
KEPT_TAGS = {                                           # enchantment tag -> the name of the class
    "curse": "Curse", "treasure": "Treasure", "non_treasure": "Non-Treasure", "tradeable": "Tradeable",
    "on_random_loot": "Random Loot", "on_traded_equipment": "Traded Equipment", "in_enchanting_table": "Enchanting Table",
    "double_trade_price": "Double Trade Price",
}
EXCLUSIVE = "exclusive_set/"


# --------------------------------------------------------------------------- read

def read_enchantments(jar: Path) -> dict:
    """The data the enchantment graph is built from: `enchantments` ({id: json}), `tags` (enchantment tags, {id: values}) and `item_tags`
    (every item tag, {id: values}). Ids are 'minecraft:<path>'; the tag path of an exclusive set keeps its folder ('exclusive_set/damage')."""
    out: dict = {"enchantments": {}, "tags": {}, "item_tags": {}}
    patterns = [(re.compile(r"data/minecraft/enchantment/([^/]+)\.json"), "enchantments"),
                (re.compile(r"data/minecraft/tags/enchantment/(.+)\.json"), "tags"),
                (re.compile(r"data/minecraft/tags/item/(.+)\.json"), "item_tags")]
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            for pattern, key in patterns:
                m = pattern.fullmatch(name)
                if m:
                    data = json.loads(z.read(name))
                    out[key][NS + m.group(1)] = data["values"] if key != "enchantments" else data
    return out


# --------------------------------------------------------------------------- parse

def tag_ref(value) -> list[str]:
    """The tag ids a `supported_items` or `primary_items` value names: a tag ('#minecraft:enchantable/armor') gives that tag; anything
    else (a list of items, one item) gives none, and `item_ids` reads it."""
    return [value[1:]] if isinstance(value, str) and value.startswith("#") else []


def item_ids(value) -> list[str]:
    if isinstance(value, str) and not value.startswith("#"):
        return [value]
    return [v for v in value if isinstance(v, str) and not v.startswith("#")] if isinstance(value, list) else []


def class_name(tag: str) -> str:
    """The name of an enchantment class: a chosen name, 'Exclusive: Damage' for an exclusive set."""
    path = tag.removeprefix(NS)
    if path.startswith(EXCLUSIVE):
        return "Exclusive: " + display_name(path.removeprefix(EXCLUSIVE))
    return KEPT_TAGS[path]


def kept_tags(tags: dict) -> list[str]:
    """The enchantment tags that become classes, as ids ('minecraft:tradeable'), in a fixed order."""
    return sorted(t for t in tags if t.removeprefix(NS) in KEPT_TAGS or t.removeprefix(NS).startswith(EXCLUSIVE))


def parse_enchantment(eid: str, e: dict) -> dict:
    cost = lambda key, field: (e.get(key) or {}).get(field)          # noqa: E731
    return {
        "id": eid, "name": display_name(eid), "maxLevel": e["max_level"], "weight": e["weight"], "anvilCost": e["anvil_cost"],
        "minCostBase": cost("min_cost", "base"), "minCostPerLevel": cost("min_cost", "per_level_above_first"),
        "maxCostBase": cost("max_cost", "base"), "maxCostPerLevel": cost("max_cost", "per_level_above_first"),
        "slots": list(e.get("slots", [])), "effectTypes": sorted(k.removeprefix(NS) for k in e.get("effects", {})),
    }


def item_classes(roots: list[str], item_tags: dict) -> tuple[list[dict], list[dict]]:
    """The item categories under the given tags: (class rows, includes rows). A tag becomes a class with an INCLUDES edge to each item and
    to each nested tag (which becomes a class too), so the hierarchy of the game's own tags is kept."""
    classes, includes, todo = {}, [], list(roots)
    while todo:
        tag = todo.pop()
        if tag in classes:
            continue
        classes[tag] = {"id": "#" + tag, "name": display_name(tag).replace("/", " ")}
        for v in item_tags.get(tag, []):
            if isinstance(v, dict):
                v = v.get("id", "")
            if v.startswith("#"):
                todo.append(v[1:])
            includes.append({"class": "#" + tag, "member": v})
    return [classes[t] for t in sorted(classes)], sorted(includes, key=lambda r: (r["class"], r["member"]))


def build_rows(data: dict) -> dict:
    """The rows `write` loads, from what `read_enchantments` returned, with the counts for the report."""
    enchantments = {eid: parse_enchantment(eid, e) for eid, e in data["enchantments"].items()}
    kept = kept_tags(data["tags"])
    classes = [{"id": "#" + t, "name": class_name(t)} for t in kept]
    includes = []
    for t in kept:
        for v in data["tags"][t]:
            if v.startswith("#"):
                if v[1:] in kept:
                    includes.append({"class": "#" + t, "member": v})
            elif v in enchantments:
                includes.append({"class": "#" + t, "member": v})
            else:
                raise ValueError(f"enchantment tag {t} lists {v}, which is not an enchantment")
    applies, direct, roots = [], [], set()
    for eid, e in sorted(data["enchantments"].items()):
        for field, primary in (("supported_items", False), ("primary_items", True)):
            if field not in e:
                continue
            for tag in tag_ref(e[field]):
                applies.append({"enchantment": eid, "class": "#" + tag, "primary": primary})
                roots.add(tag)
            direct += [{"enchantment": eid, "item": i, "primary": primary} for i in item_ids(e[field])]
    item_class_rows, item_includes = item_classes(sorted(roots), data["item_tags"])
    no_class = [eid for eid in enchantments if not any(a["enchantment"] == eid for a in applies) and not any(d["enchantment"] == eid for d in direct)]
    stats = {
        "enchantments": len(enchantments), "enchantment classes": len(classes), "enchantment class members": len(includes),
        "exclusive sets": sum(1 for t in kept if t.removeprefix(NS).startswith(EXCLUSIVE)),
        "applies to": len(applies) + len(direct), "item classes": len(item_class_rows), "item class members": len(item_includes),
        "enchantments that apply to nothing": len(no_class),
        "enchantments the table can offer": len(table_enchantments(includes)),
    }
    return {"enchantments": list(enchantments.values()), "classes": classes, "includes": includes, "applies": applies, "direct": direct,
            "item_classes": item_class_rows, "item_includes": item_includes, "stats": stats}


def build(jar: Path) -> dict:
    return build_rows(read_enchantments(jar))


def table_enchantments(includes: list[dict]) -> set[str]:
    """The enchantments the enchanting table can offer: those in `in_enchanting_table`, following nested tags (it names `non_treasure`)."""
    members: dict[str, list[str]] = {}
    for r in includes:
        members.setdefault(r["class"][1:], []).append(r["member"])
    return tag_members("minecraft:in_enchanting_table", members, {})


# --------------------------------------------------------------------------- Neo4j

def write(g: dict, uri: str, user: str, password: str, database: str) -> None:
    """Delete and recreate what this loader owns, in one transaction after the constraints. Item categories are merged, not deleted."""
    def work(tx) -> None:
        def run(query: str, **params) -> None:
            tx.run(query, **params).consume()

        run("MATCH (n) WHERE n:Enchantment OR n:EnchantmentClass DETACH DELETE n")
        run("""UNWIND $rows AS r CREATE (:Enchantment {id: r.id, name: r.name, maxLevel: r.maxLevel, weight: r.weight, anvilCost: r.anvilCost,
                   minCostBase: r.minCostBase, minCostPerLevel: r.minCostPerLevel, maxCostBase: r.maxCostBase,
                   maxCostPerLevel: r.maxCostPerLevel, slots: r.slots, effectTypes: r.effectTypes})""", rows=g["enchantments"])
        run("UNWIND $rows AS r CREATE (:EnchantmentClass {id: r.id, name: r.name})", rows=g["classes"])
        run("""UNWIND $rows AS r MATCH (c:EnchantmentClass {id: r.class}) MATCH (m {id: r.member}) WHERE m:Enchantment OR m:EnchantmentClass
               CREATE (c)-[:INCLUDES]->(m)""", rows=g["includes"])
        run("UNWIND $rows AS r MERGE (c:Item {id: r.id}) SET c:ItemClass, c.name = coalesce(c.name, r.name)", rows=g["item_classes"])
        run("""UNWIND $rows AS r MATCH (c:ItemClass {id: r.class}) MATCH (m:Item {id: r.member})
               MERGE (c)-[:INCLUDES]->(m)""", rows=g["item_includes"])
        run("""UNWIND $rows AS r MATCH (e:Enchantment {id: r.enchantment}) MATCH (c:ItemClass {id: r.class})
               CREATE (e)-[:APPLIES_TO {primary: r.primary}]->(c)""", rows=g["applies"])
        run("""UNWIND $rows AS r MATCH (e:Enchantment {id: r.enchantment}) MATCH (i:Item {id: r.item})
               CREATE (e)-[:APPLIES_TO {primary: r.primary}]->(i)""", rows=g["direct"])

    with open_driver(uri, user, password) as driver:
        with driver.session(database=database) as session:
            for label in ("Enchantment", "EnchantmentClass"):         # schema changes cannot share a transaction with the data
                session.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE").consume()
            ensure_name_index(session)                                  # the index covers Enchantment and EnchantmentClass
            session.execute_write(work)


def main() -> int:
    """Parse the jar, report, and (unless --dry-run) load into Neo4j."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    ap.add_argument("--dry-run", action="store_true", help="parse and report; do not touch Neo4j")
    args = ap.parse_args()
    if not args.jar.exists():
        sys.exit(f"Jar not found: {args.jar}")
    g = build(args.jar)
    for key, value in sorted(g["stats"].items()):
        print(f"  {key}: {value}")
    if args.dry_run:
        return 0
    s = get_settings()
    write(g, s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    print("loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
