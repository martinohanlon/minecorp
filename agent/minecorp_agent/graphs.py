"""Graph patterns: the path through the graph a tool walked to give its result, written as a Cypher pattern.

A player is shown them in the explain book, so they can see the answer came from walking a graph:

    (Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})

Each pattern can be written two ways from the same parts: `compact()` for the book (labels and names, no colons or quotes) and `cypher()`,
which is a real Cypher pattern. The tests run the Cypher form against the graph, so a pattern on a page is a path that exists, not
decoration. A name comes from the tool's own result, never from the model. The wiki's nodes are named by `title`; everything else by `name`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    """A node in a pattern: its label, and the name of the one in the result (none for 'any')."""
    label: str | None = None
    name: str | None = None
    key: str = "name"                  # the property holding the name: `title` for the wiki's pages and sections


@dataclass(frozen=True)
class Rel:
    """A relationship, pointing out (`->`) or in (`<-`) as the pattern is read left to right; `length` is e.g. '*0..'."""
    type: str
    out: bool = True
    length: str = ""


@dataclass(frozen=True)
class Pattern:
    """Nodes and relationships alternating, starting and ending with a node."""
    parts: tuple

    def compact(self) -> str:
        out = []
        for part in self.parts:
            if isinstance(part, Node):
                inner = " ".join(x for x in (part.label, f"{{{part.name}}}" if part.name else None) if x)
                out.append(f"({inner})")
            else:
                body = f"[{part.type}{part.length}]"
                out.append(f"-{body}->" if part.out else f"<-{body}-")
        return "".join(out)

    def cypher(self) -> str:
        out = []
        for part in self.parts:
            if isinstance(part, Node):
                label = f":{part.label}" if part.label else ""
                props = f" {{{part.key}: {json.dumps(part.name, ensure_ascii=False)}}}" if part.name else ""
                out.append(f"({label}{props})")
            else:
                body = f"[:{part.type}{part.length}]"
                out.append(f"-{body}->" if part.out else f"<-{body}-")
        return "".join(out)


def path(*parts) -> Pattern:
    """A pattern from nodes and relationships; a string stands for a node of that label with no name."""
    return Pattern(tuple(Node(p) if isinstance(p, str) else p for p in parts))


def named(label: str, name: str | None, key: str = "name") -> Node:
    return Node(label, name, key)


IN, OUT = False, True


def strip_count(action: str) -> str:
    """'Mine Obsidian x10' to 'Mine Obsidian' (the quantity is not part of the action's name)."""
    return re.sub(r" x\d+$", "", action)


# --------------------------------------------------------------------------- recipes and routes

def ingredient(q: dict) -> list:
    """The part of a pattern from a recipe to what it needs: a category (any of its members), or an item, through its category if the
    tool showed the single member of a category as that item."""
    if "anyOf" in q:
        return [Rel("REQUIRES"), named("ItemClass", q["anyOf"])]
    if q.get("_via"):
        return [Rel("REQUIRES"), named("ItemClass", q["_via"]), Rel("INCLUDES"), named("Item", q.get("item"))]
    return [Rel("REQUIRES"), named("Item", q.get("item"))]


def recipe_patterns(item: str, recipes: list[dict]) -> list[Pattern]:
    """What the first recipe for an item needs (up to three ingredients) and where it is made."""
    if not recipes:
        return []
    r = recipes[0]
    head = (named("Item", item), Rel("CREATES", IN), Node("Recipe"))
    out = []
    for q in r.get("requires", [])[:3]:
        out.append(path(*head, *ingredient(q)))
    if r.get("station"):
        out.append(path(*head, Rel("USES_STATION"), named("Item", r["station"])))
    return out[:4]


def source_of_item(item: str, way: dict) -> Pattern | None:
    """How an item is got from the world: `(Item)<-[CREATES]-(Mine)-[REQUIRES]->(Block)`, or a mob, or a trade."""
    how, who = way.get("how"), way.get("from")
    if how == "mine" and isinstance(who, str):
        return path(named("Item", item), Rel("CREATES", IN), Node("Mine"), Rel("REQUIRES"), named("Block", who))
    if how in ("kill", "shear") and isinstance(who, str):
        return path(named("Item", item), Rel("CREATES", IN), Node(how.capitalize()), Rel("REQUIRES"), named("Mob", who))
    if how == "trade" and way.get("with"):
        seller = named("Mob", way["with"]) if way["with"] == "Wandering Trader" else named("Profession", way["with"])
        return path(named("Item", item), Rel("CREATES", IN), Node("Trade"), Rel("INCLUDES", IN), Node("TradeSet"), Rel("OFFERS", IN), seller)
    return None


def route_patterns(output: dict) -> list[Pattern]:
    """The last step of a route (what it needs) and where the raw materials come from."""
    item, out = output.get("item"), []
    steps = output.get("steps") or []
    if steps and item:
        for use in steps[-1].get("uses", [])[:2]:
            out.append(path(named("Item", item), Rel("CREATES", IN), Node("Recipe"), *ingredient(use)))
    for g in output.get("gather", []):
        if len(out) >= 4:
            break
        way = (g.get("getFrom") or [None])[0]
        pattern = source_of_item(g["item"], way) if way and g.get("item") else None
        if pattern:
            out.append(pattern)
    return out[:4]


def ways_patterns(output: dict) -> list[Pattern]:
    """The ways to get an item (up to four): mining, killing, shearing or trading."""
    if output.get("category") or not output.get("item"):
        return []
    found = [source_of_item(output["item"], w) for w in output.get("ways", [])]
    return [p for p in found if p][:4]


def uses_patterns(output: dict) -> list[Pattern]:
    item, out = output.get("item"), []
    for c in output.get("canMake", [])[:3] if item else []:
        via = c.get("viaCategory")                                     # the recipe asks for a category the item is in
        start = [named("Item", item)] + ([Rel("INCLUDES", IN), named("ItemClass", via[0])] if via else [])
        out.append(path(*start, Rel("REQUIRES", IN), Node("Recipe"), Rel("CREATES"), named("Item", c["makes"])))
    return out


def members_patterns(result: dict) -> list[Pattern]:
    labels = {"category": ("ItemClass", "Item"), "mob class": ("MobClass", "Mob"), "biome class": ("BiomeClass", "Biome")}
    cls = labels.get(result.get("kind"))
    return [path(named(cls[0], result["name"]), Rel("INCLUDES", length="*1.."), named(cls[1], m)) for m in result.get("members", [])[:2]] if cls else []


# --------------------------------------------------------------------------- the world

def ore_biomes(block: str) -> Pattern:
    """The biomes that have a block as ore: block, the feature that makes it, where the feature is placed, the biome that generates it."""
    return path(named("Block", block), Rel("CREATES", IN), Node("Feature"), Rel("PLACES", IN), Node("PlacedFeature"), Rel("GENERATES", IN), Node("Biome"))


def structure_biomes(structure: str) -> Pattern:
    """A structure generates in a biome class (or a biome) and the class includes the biomes."""
    return path(named("Structure", structure), Rel("GENERATES_IN"), Node(), Rel("INCLUDES", length="*0.."), Node("Biome"))


def map_seller(structure: str) -> Pattern:
    """A map that leads to a structure is a trade in a profession's trade set."""
    return path(named("Structure", structure), Rel("LOCATES", IN), Node("Trade"), Rel("INCLUDES", IN), Node("TradeSet"), Rel("OFFERS", IN), Node("Profession"))


def where_patterns(result: dict) -> list[Pattern]:
    """Where something is: the path from an item to the biomes that have its ore or mob, a mob's biomes, a structure's biomes."""
    kind, name, out = result.get("kind"), result.get("name"), []
    if kind == "item":
        for way in result.get("found", [])[:2]:
            who = way["from"][0] if isinstance(way.get("from"), list) else way.get("from")
            pattern = source_of_item(name, {**way, "from": who})
            if pattern:
                out.append(pattern)
                if way.get("how") == "mine" and way.get("where"):
                    out.append(ore_biomes(who))
                elif way.get("how") in ("kill", "shear") and way.get("spawnsIn"):
                    out.append(path(named("Mob", who), Rel("SPAWNS", IN), Node("Biome")))
                elif way.get("how") in ("kill", "shear") and way.get("structures"):
                    out.append(path(named("Mob", who), Rel("SPAWNS", IN), Node("Structure")))
        if result.get("canBeTradedFor"):
            pattern = source_of_item(name, result["canBeTradedFor"][0])
            if pattern:
                out.append(pattern)
    elif kind == "block" and result.get("where"):
        out.append(ore_biomes(name))
    elif kind == "mob":
        if result.get("spawnsIn"):
            out.append(path(named("Mob", name), Rel("SPAWNS", IN), Node("Biome")))
        if result.get("structures"):
            out.append(path(named("Mob", name), Rel("SPAWNS", IN), Node("Structure")))
    elif kind == "biome":
        out.append(path(named("Biome", name), Rel("SPAWNS"), Node("Mob")))
        if result.get("structures"):
            out.append(path(Node("Structure"), Rel("GENERATES_IN"), Node(), Rel("INCLUDES", length="*0.."), named("Biome", name)))
        if result.get("ores"):
            out.append(path(named("Biome", name), Rel("GENERATES"), Node("PlacedFeature"), Rel("PLACES"), Node("Feature"), Rel("CREATES"), Node("Block")))
    elif kind == "structure":
        if result.get("generatesIn"):
            out.append(structure_biomes(name))
        if result.get("mapToFindIt"):
            out.append(map_seller(name))
        if result.get("spawns"):
            out.append(path(named("Structure", name), Rel("SPAWNS"), Node("Mob")))
    return out[:4]


def describe_patterns(result: dict) -> list[Pattern]:
    """What `describe` read for a thing: the relationships its short view counts."""
    kind, name, out = result.get("kind"), result.get("name"), []
    if kind == "item":
        if result.get("recipes"):
            out.append(path(named("Item", name), Rel("CREATES", IN), Node("Recipe")))
        if result.get("waysFromWorld"):
            out.append(path(named("Item", name), Rel("CREATES", IN), Node("Action")))
        if result.get("usedInRecipes"):
            out.append(path(named("Item", name), Rel("REQUIRES", IN), Node("Recipe")))
        if result.get("tradesGivingIt"):
            out.append(path(named("Item", name), Rel("CREATES", IN), Node("Trade")))
    elif kind == "block":
        for d in result.get("drops", [])[:2]:
            out.append(path(named("Block", name), Rel("REQUIRES", IN), Node("Mine"), Rel("CREATES"), named("Item", d["item"])))
    elif kind == "mob":
        for d in result.get("drops", [])[:2]:
            out.append(path(named("Mob", name), Rel("REQUIRES", IN), Node("Kill"), Rel("CREATES"), named("Item", d["item"])))
        if result.get("spawnsInBiomes"):
            out.append(path(named("Mob", name), Rel("SPAWNS", IN), Node("Biome")))
        if result.get("professions"):
            out.append(path(named("Mob", name), Rel("HAS_PROFESSION"), Node("Profession")))
        if result.get("offers"):
            out.append(path(named("Mob", name), Rel("OFFERS"), Node("TradeSet")))
    elif kind == "biome":
        out.append(path(named("Biome", name), Rel("SPAWNS"), Node("Mob")))
        out.append(path(Node("Structure"), Rel("GENERATES_IN"), Node(), Rel("INCLUDES", length="*0.."), named("Biome", name)))
    elif kind == "structure":
        out.append(structure_biomes(name))
        if result.get("mapSoldBy"):
            out.append(map_seller(name))
    elif kind == "profession":
        out.append(path(named("Profession", name), Rel("OFFERS"), Node("TradeSet"), Rel("INCLUDES"), Node("Trade")))
    elif kind in ("category", "mob class", "biome class"):
        out += members_patterns({**result, "members": result.get("examples", [])})
    return out[:4]


# --------------------------------------------------------------------------- plans and the wiki

def plan_patterns(output: dict) -> list[Pattern]:
    """A plan: its process, a stage and a step, how steps follow each other, the game action a step performs, what it builds on."""
    name, out = output.get("process"), []
    if not name:
        return []
    if "step" in output:                                              # one step in full
        step = output["step"]
        if output.get("stage"):
            out.append(path(named("Activity", output["stage"]), Rel("HAS_TASK"), named("Task", step)))
        for a in output.get("gameActions", [])[:1]:
            out.append(path(named("Task", step), Rel("HAS_ACTION"), named("Action", strip_count(a))))
        nxt = output.get("next")
        if isinstance(nxt, list) and nxt:
            out.append(path(named("Task", step), Rel("NEXT"), named("Task", nxt[0]["go to"])))
        return out[:4]
    stages = output.get("stages") or []
    if stages and stages[0]["steps"]:
        out.append(path(named("Process", name), Rel("HAS_ACTIVITY"), named("Activity", stages[0]["stage"]), Rel("HAS_TASK"), named("Task", stages[0]["steps"][0])))
    alt = (output.get("alternatives") or [None])[0]
    if alt and alt.get("usually"):
        out.append(path(named("Task", alt["after"]), Rel("NEXT"), named("Task", alt["usually"]["go to"])))
    for line in output.get("usualRoute", []):                         # the first step that performs a game action
        m = re.match(r"^\d+\. (.+?) \((.+)\)$", line)
        if m and m.group(2) != "by hand":
            out.append(path(named("Task", m.group(1)), Rel("HAS_ACTION"), named("Action", strip_count(m.group(2).split("; ")[0]))))
            break
    for other in output.get("buildsOn", [])[:1]:
        out.append(path(named("Process", name), Rel("BUILDS_ON"), named("Process", other)))
    return out[:4]


def section_pattern(section_path: str) -> Pattern:
    """A wiki passage: `(Chunk)-[PART_OF]->(Section {title})`; the section is named by the last part of its path."""
    return path(Node("Chunk"), Rel("PART_OF"), named("Section", section_path.split(" > ")[-1], key="title"))
