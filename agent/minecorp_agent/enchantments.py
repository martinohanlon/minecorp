"""What the agent says about enchantments, read from the enchantment graph (docs/ontology.md v0.13).

An enchantment, the item classes it goes on (`APPLIES_TO`), the enchantment classes it is in (`INCLUDES`: where it comes from, what kind it is,
what it conflicts with) and the trades that offer a class of them. Structure only: what an enchantment DOES is not in the graph, the wiki
(linked by `MENTIONS`) says that. `world.py` calls these from `describe` and `where_to_find`.
"""
from __future__ import annotations

from .graph import Graph

MAX_NAMES = 12            # names listed in one list
TABLE = "#minecraft:in_enchanting_table"
TRADEABLE = "#minecraft:tradeable"
GEAR = "#minecraft:on_traded_equipment"
RANDOM_LOOT = "#minecraft:on_random_loot"
TREASURE = "#minecraft:treasure"
CURSE = "#minecraft:curse"


def _classes(graph: Graph, enchantment_id: str) -> list[dict]:
    """Every enchantment class the enchantment is in, directly or through another class (the table's class includes non-treasure)."""
    return graph.read("""
        MATCH (c:EnchantmentClass)-[:INCLUDES*1..]->(:Enchantment {id: $id})
        RETURN DISTINCT c.id AS id, c.name AS name ORDER BY name""", id=enchantment_id)


def comes_from(class_ids: set[str]) -> list[str]:
    """Where an enchantment can come from, in words, from the classes it is in. None of these is a place: they are ways to get it."""
    out = []
    if TABLE in class_ids:
        out.append("the enchanting table")
    if TRADEABLE in class_ids:
        out.append("villager trades (enchanted books)")
    if RANDOM_LOOT in class_ids:
        out.append("random loot (loot that enchants at random)")
    return out


def power_range(e: dict, level: int) -> str:
    """The range of enchanting power at which the table can offer this level: `base + per_level_above_first * (level - 1)`."""
    step = level - 1
    low = e["minCostBase"] + e["minCostPerLevel"] * step
    high = e["maxCostBase"] + e["maxCostPerLevel"] * step
    return f"{low} to {high}"


def describe_enchantment(graph: Graph, enchantment_id: str) -> dict:
    e = graph.read("MATCH (e:Enchantment {id: $id}) RETURN e", id=enchantment_id)[0]["e"]
    classes = _classes(graph, enchantment_id)
    class_ids = {c["id"] for c in classes}
    goes_on = graph.read("""
        MATCH (:Enchantment {id: $id})-[a:APPLIES_TO]->(c) RETURN c.name AS name, a.primary AS primary, c.id AS id ORDER BY name""", id=enchantment_id)
    supported = [r["name"] for r in goes_on if not r["primary"]]
    primary = [r["name"] for r in goes_on if r["primary"]]
    items = graph.read("""
        MATCH (:Enchantment {id: $id})-[:APPLIES_TO {primary: false}]->(c)
        OPTIONAL MATCH (c)-[:INCLUDES*1..]->(m:Item) WHERE NOT m:ItemClass
        WITH c, collect(DISTINCT m.name) AS members
        UNWIND (CASE WHEN c:ItemClass THEN members ELSE [c.name] END) AS name
        RETURN DISTINCT name ORDER BY name""", id=enchantment_id)
    names = [r["name"] for r in items]
    conflicts = [r["name"] for r in graph.read("""
        MATCH (e:Enchantment {id: $id})<-[:INCLUDES]-(c:EnchantmentClass)-[:INCLUDES]->(o:Enchantment)
        WHERE c.name STARTS WITH 'Exclusive' AND o <> e RETURN DISTINCT o.name AS name ORDER BY name""", id=enchantment_id)]
    out: dict = {"maxLevel": e["maxLevel"]}
    out["goesOn"] = supported
    if names:
        out["goesOnItems"] = {"total": len(names), "examples": names[:MAX_NAMES]}
    if conflicts:
        out["cannotBeCombinedWith"] = conflicts
    sources = comes_from(class_ids)
    out["comesFrom"] = sources or ["no ordinary source in the data (not the enchanting table, villagers or random loot)"]
    if TREASURE in class_ids:
        out["treasure"] = "Treasure: the enchanting table never offers it."
    if CURSE in class_ids:
        out["curse"] = True
    if TABLE in class_ids:
        out["tableOffersItFor"] = primary or supported
        out["enchantingPower"] = {str(n): power_range(e, n) for n in range(1, e["maxLevel"] + 1)}
    out["weight"] = e["weight"]
    out["anvilCost"] = e["anvilCost"]
    out["inClasses"] = [c["name"] for c in classes if not c["name"].startswith("Exclusive")]
    out["next"] = ("where_to_find lists the villagers that offer it. 'enchantingPower' is the range at which the enchanting table can offer each level "
                   "(it is not the XP cost); 'weight' is how often it is picked among those possible. What it does is not in the game data: use search_wiki.")
    return out


def where_enchantment(graph: Graph, enchantment_id: str) -> dict:
    """The ways to get an enchantment, and which villagers offer it (their enchanted books, or enchanted gear that may carry it)."""
    class_ids = {c["id"] for c in _classes(graph, enchantment_id)}
    ways = comes_from(class_ids)
    rows = graph.read("""
        MATCH (p:Profession)-[o:OFFERS]->(:TradeSet)-[:INCLUDES]->(:Trade)-[:OFFERS_ENCHANTMENT]->(c:EnchantmentClass)-[:INCLUDES*0..]->(:Enchantment {id: $id})
        RETURN c.id AS cls, p.name AS profession, collect(DISTINCT o.level) AS levels ORDER BY profession""", id=enchantment_id)
    out: dict = {}
    if ways:
        out["comesFrom"] = ways
    for cls, key in ((TRADEABLE, "enchantedBooksFrom"), (GEAR, "enchantedGearFrom")):
        found = [{"villager": r["profession"], "levels": sorted(r["levels"])} for r in rows if r["cls"] == cls]
        if found:
            out[key] = found
    if TREASURE in class_ids:
        out["treasure"] = "Treasure: the enchanting table never offers it."
    if not out:
        out["note"] = "No source in the data: not the enchanting table, villagers or random loot."
    else:
        out["note"] = ("A villager's enchanted book or gear carries a random enchantment from its class, so 'offers' means it can appear, not that it will. "
                       "The enchanting table and random loot are ways to get it, not places.")
    return out


def enchantments_for_item(graph: Graph, item_id: str) -> list[str]:
    """The enchantments an anvil can put on an item (the enchanting table offers only some of them), by name."""
    rows = graph.read("""
        MATCH (e:Enchantment)-[:APPLIES_TO {primary: false}]->(c)
        WHERE (c:ItemClass AND EXISTS { (c)-[:INCLUDES*1..]->(:Item {id: $id}) }) OR c.id = $id
        RETURN DISTINCT e.name AS name ORDER BY name""", id=item_id)
    return [r["name"] for r in rows]
