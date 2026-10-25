"""Load villager and wandering trader trades from the Paper jar (docs/ontology.md v0.12).

    python loader/load_trades.py --dry-run     # parse and report, no database
    python loader/load_trades.py               # load

Run it after `load_recipes.py --all --wipe`, `load_actions.py` (which deletes every Action, trades included) and
`load_world.py` (it links maps to structures and the villager to its professions), and before `load_model.py`. The graph is
rebuilt from source: this loader deletes and recreates the nodes it owns, so a task's link to a trade (`HAS_ACTION`) is lost
and `load_model.py` must be run again after it.

What is built:

    (Action:Trade {maxUses, reputationDiscount, xp, ...})       one per trade in the data (388)
        -[:REQUIRES {role: 'price', count}]->(Item)             what the villager wants (an emerald, 4 coal...)
        -[:REQUIRES {role: 'additional', count}]->(Item)        a second item some trades also want (a book, a compass)
        -[:CREATES {count}]->(Item)                             what you get
        -[:LOCATES {decoration, radius}]->(Structure)           an exploration map: the structure it leads to
    (Profession)-[:OFFERS {level}]->(TradeSet {amount})-[:INCLUDES]->(Trade)
    (Mob wandering_trader)-[:OFFERS {pool}]->(TradeSet {amount})-[:INCLUDES]->(Trade)
    (Mob villager)-[:HAS_PROFESSION]->(Profession)

A trade is an Action with the verb label Trade, like Mine, Kill and Shear: what it needs and what it gives are the same kind of
relationship, so a task can perform it (`trade:minecraft:farmer/1/wheat_emerald`) and a question like "how do I get X" can
walk to it. A trade set is the pool a villager draws from at one level: the game picks `amount` trades from the set at
random (or all of them when it holds fewer: eight sets ask for one more than they hold, such as the butcher's fourth level), so a villager of a profession does not offer every trade of its level. Several sets can include the same trade (the
armorer, the toolsmith and the weaponsmith share a few). Only the trades of the jar's own data are loaded; the optional trade
rebalance datapack that ships inside the jar is not.

A price that depends on the enchantment of what you buy (a librarian's enchanted book) has no fixed count: the trade says
`variablePrice` and has no `count` on its price. A trade for an enchanted item says `enchanted` (and `enchantLevels` for
armour and tools) and `OFFERS_ENCHANTMENT` to the enchantment class its enchantment is drawn from (`#minecraft:tradeable` for a
librarian's book, `#minecraft:on_traded_equipment` for gear), when `load_enchantments.py` has been run first. A trade that only some villagers offer (a map to a swamp hut
is only sold by savanna, desert and swamp villagers) lists their village types in `villagerTypes`.
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

from neo4j import GraphDatabase  # noqa: E402,F401

from quiet_driver import open_driver  # noqa: E402

from load_actions import tag_members  # noqa: E402
from load_recipes import DEFAULT_JAR, display_name  # noqa: E402
from name_index import ensure_name_index  # noqa: E402
from minecorp_agent.config import get_settings  # noqa: E402

NS = "minecraft:"
TRADER = "minecraft:wandering_trader"
VILLAGER = "minecraft:villager"
FOLDERS = {"villager_trade": "trades", "trade_set": "sets", "tags/villager_trade": "tags", "tags/worldgen/structure": "structure_tags"}


# --------------------------------------------------------------------------- read

def read_trades(jar: Path) -> dict:
    """The trade data in the jar: trades, trade sets, and the tags that list the trades of a set.

    Each entry is keyed by its id without the namespace and without `.json` ('armorer/1/emerald_iron_boots'). The datapacks
    that ship inside the jar (data/minecraft/datapacks/...) are not read."""
    out: dict = {key: {} for key in FOLDERS.values()}
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            for folder, key in FOLDERS.items():
                prefix = f"data/minecraft/{folder}/"
                if name.startswith(prefix) and name.endswith(".json"):
                    out[key][name[len(prefix):-5]] = json.loads(z.read(name))
    return out


# --------------------------------------------------------------------------- parse

def whole(value) -> int | None:
    """A count from the data (they are written as 4.0) as a whole number; None when there is none."""
    return None if value is None else int(value)


def villager_types(predicate: dict | None) -> list[str]:
    """The village types a trade is limited to, from its merchant predicate ('minecraft:swamp' -> 'swamp')."""
    found = ((predicate or {}).get("predicate") or {}).get("minecraft:predicates", {}).get("minecraft:villager/variant", [])
    if isinstance(found, str):                       # one village type is written as a string, several as a list
        found = [found]
    return sorted(v.removeprefix(NS) for v in found)


def parse_trade(key: str, t: dict, structure_tags: dict) -> dict:
    """One trade as rows: the properties, the items it wants and gives, and the structures a map leads to."""
    wants = t.get("wants", {})
    price_count = wants.get("count", 1)
    variable = price_count == 0
    modifiers = t.get("given_item_modifiers", [])
    levels = next((m.get("levels") for m in modifiers if m.get("function") == f"{NS}enchant_with_levels"), None)
    maps = [m for m in modifiers if m.get("function") == f"{NS}exploration_map"]
    locates = []
    for m in maps:
        tag = m["destination"].removeprefix("#").removeprefix(NS)
        for structure in tag_members(NS + tag, {NS + k: v.get("values", []) for k, v in structure_tags.items()}, {}):
            locates.append({"structure": structure, "decoration": (m.get("decoration") or "").removeprefix(NS) or None,
                            "radius": whole(m.get("search_radius"))})
    pay = [{"item": wants["id"], "count": None if variable else whole(price_count), "role": "price"}]
    if t.get("additional_wants"):
        pay.append({"item": t["additional_wants"]["id"], "count": whole(t["additional_wants"].get("count", 1)), "role": "additional"})
    gives = {"item": t["gives"]["id"], "count": whole(t["gives"].get("count", 1))}
    return {
        "id": f"trade:{NS}{key}", "key": key, "name": trade_name(pay, gives),
        "maxUses": whole(t.get("max_uses")), "reputationDiscount": t.get("reputation_discount"), "xp": whole(t.get("xp")),
        "variablePrice": variable, "enchanted": bool(levels) or any(m.get("function") == f"{NS}enchant_randomly" for m in modifiers),
        "enchantLevels": f"{whole(levels['min'])} to {whole(levels['max'])}" if isinstance(levels, dict) and "min" in levels else None,
        "enchantOptions": enchant_options(modifiers),
        "villagerTypes": villager_types(t.get("merchant_predicate")),
        "pay": pay, "gives": gives, "locates": locates,
    }


def enchant_options(modifiers: list[dict]) -> list[str]:
    """The enchantment classes an enchanting modifier draws from, as class ids ('#minecraft:tradeable': a librarian's book,
    '#minecraft:on_traded_equipment': enchanted gear). The classes are made by load_enchantments.py."""
    return sorted({m["options"] for m in modifiers
                   if m.get("function") in (f"{NS}enchant_randomly", f"{NS}enchant_with_levels") and str(m.get("options", "")).startswith("#")})


def trade_name(pay: list[dict], gives: dict) -> str:
    """'Trade 4 Emerald for Iron Boots' (a count of 1, or a price that varies, is not written)."""
    def part(x: dict) -> str:
        count = x.get("count")
        return (f"{count} " if count and count > 1 else "") + display_name(x["item"])
    return f"Trade {' and '.join(part(p) for p in pay)} for {part(gives)}"


def trade_keys(tag: str, tags: dict) -> list[str]:
    """The trades a tag lists, following nested tags ('#minecraft:common_smith/level_1')."""
    members = tag_members(NS + tag, {NS + k: v.get("values", []) for k, v in tags.items()}, {})
    return sorted(m.removeprefix(NS) for m in members)


def build(jar: Path) -> dict:
    """Parse the jar into the rows `write` loads, with the counts for the report."""
    data = read_trades(jar)
    trades = {key: parse_trade(key, t, data["structure_tags"]) for key, t in data["trades"].items()}
    professions: dict[str, dict] = {}
    sets, offers, includes = [], [], []
    for key, s in sorted(data["sets"].items()):
        owner, _, rest = key.partition("/")
        tag = s["trades"].removeprefix("#").removeprefix(NS)
        level = int(rest.split("_")[1]) if rest.startswith("level_") else None
        members = trade_keys(tag, data["tags"])
        missing = [m for m in members if m not in trades]
        if missing:
            raise ValueError(f"trade set {key} lists trades that do not exist: {missing[:3]}")
        sets.append({"id": NS + key, "name": f"{display_name(owner)} {rest.replace('_', ' ')}", "amount": whole(s.get("amount"))})
        if owner == "wandering_trader":
            offers.append({"from": TRADER, "set": NS + key, "level": None, "pool": rest})
        else:
            professions.setdefault(owner, {"id": NS + owner, "name": display_name(owner)})
            offers.append({"from": NS + owner, "set": NS + key, "level": level, "pool": None})
        includes += [{"set": NS + key, "trade": trades[m]["id"]} for m in members]
    items = {i["item"] for t in trades.values() for i in (*t["pay"], t["gives"])}
    stats = Counter({"trades": len(trades), "trade sets": len(sets), "professions": len(professions),
                     "items traded": len(items), "trade set to trade links": len(includes),
                     "trades in more than one set": sum(1 for n in Counter(i["trade"] for i in includes).values() if n > 1),
                     "trades for a variable price": sum(t["variablePrice"] for t in trades.values()),
                     "trades for an enchanted item": sum(t["enchanted"] for t in trades.values()),
                     "trades limited to some village types": sum(bool(t["villagerTypes"]) for t in trades.values()),
                     "maps that lead to a structure": sum(bool(t["locates"]) for t in trades.values())})
    return {"trades": list(trades.values()), "sets": sets, "professions": list(professions.values()), "offers": offers,
            "includes": includes, "items": [{"id": i, "name": display_name(i)} for i in sorted(items)], "stats": dict(stats)}


# --------------------------------------------------------------------------- Neo4j

def write(g: dict, uri: str, user: str, password: str, database: str) -> None:
    """Delete and recreate what this loader owns, in one transaction after the constraints."""
    def work(tx) -> None:
        def run(query: str, **params) -> None:
            tx.run(query, **params).consume()

        run("MATCH (n) WHERE n:Trade OR n:TradeSet OR n:Profession DETACH DELETE n")
        run("UNWIND $rows AS r MERGE (i:Item {id: r.id}) SET i.name = coalesce(i.name, r.name)", rows=g["items"])
        run("UNWIND $rows AS r CREATE (:Profession {id: r.id, name: r.name})", rows=g["professions"])
        run("UNWIND $rows AS r CREATE (:TradeSet {id: r.id, name: r.name, amount: r.amount})", rows=g["sets"])
        run("""UNWIND $rows AS r CREATE (:Action:Trade {id: r.id, name: r.name, maxUses: r.maxUses,
                   reputationDiscount: r.reputationDiscount, xp: r.xp, variablePrice: r.variablePrice,
                   enchanted: r.enchanted, enchantLevels: r.enchantLevels, villagerTypes: r.villagerTypes})""", rows=g["trades"])
        run("""UNWIND $rows AS r MATCH (t:Trade {id: r.trade}) MATCH (i:Item {id: r.item})
               CREATE (t)-[:REQUIRES {role: r.role, count: r.count}]->(i)""",
            rows=[{"trade": t["id"], **p} for t in g["trades"] for p in t["pay"]])
        run("""UNWIND $rows AS r MATCH (t:Trade {id: r.trade}) MATCH (i:Item {id: r.item})
               CREATE (t)-[:CREATES {count: r.count, condition: 'default'}]->(i)""",
            rows=[{"trade": t["id"], **t["gives"]} for t in g["trades"]])
        run("""UNWIND $rows AS r MATCH (t:Trade {id: r.trade}) MATCH (c:EnchantmentClass {id: r.cls})
               CREATE (t)-[:OFFERS_ENCHANTMENT]->(c)""",
            rows=[{"trade": t["id"], "cls": c} for t in g["trades"] for c in t["enchantOptions"]])
        run("""UNWIND $rows AS r MATCH (t:Trade {id: r.trade}) MATCH (s:Structure {id: r.structure})
               CREATE (t)-[:LOCATES {decoration: r.decoration, radius: r.radius}]->(s)""",
            rows=[{"trade": t["id"], **x} for t in g["trades"] for x in t["locates"]])
        run("UNWIND $rows AS r MATCH (p:Profession {id: r.from}) MATCH (s:TradeSet {id: r.set}) CREATE (p)-[:OFFERS {level: r.level}]->(s)",
            rows=[o for o in g["offers"] if o["from"] != TRADER])
        run("UNWIND $rows AS r MATCH (m:Mob {id: r.from}) MATCH (s:TradeSet {id: r.set}) CREATE (m)-[:OFFERS {pool: r.pool}]->(s)",
            rows=[o for o in g["offers"] if o["from"] == TRADER])
        run("UNWIND $rows AS r MATCH (s:TradeSet {id: r.set}) MATCH (t:Trade {id: r.trade}) CREATE (s)-[:INCLUDES]->(t)", rows=g["includes"])
        run("MATCH (m:Mob {id: $villager}) MATCH (p:Profession) CREATE (m)-[:HAS_PROFESSION]->(p)", villager=VILLAGER)

    with open_driver(uri, user, password) as driver:
        with driver.session(database=database) as session:
            for label in ("Profession", "TradeSet"):                  # schema changes cannot share a transaction with the data
                session.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT action_id IF NOT EXISTS FOR (n:Action) REQUIRE n.id IS UNIQUE").consume()
            ensure_name_index(session)                                  # the index covers Profession
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
