"""The `list` tool: everything of one kind (all the biomes, all the mobs...), flat, with the few groupings that are real.

`find` searches by name and `members` opens one class, so nothing could answer "list the biomes". This does, for every kind of named node:

- Most kinds come back flat, sorted, with a total. Items and blocks are many (over a thousand each): they come in pages of 100, and
  `contains` narrows them ("sword").
- **Biomes are grouped by dimension**, which is a real partition (every biome but The Void is in exactly one).
- **Classes** (item categories, mob classes, biome classes) come back with the number of things in each. They are Minecraft's own tags, not a
  taxonomy: a biome is in about nine of them, and "Is Forest" also holds Grove and Pale Garden. So they are not shown as a tree. Biome classes
  are grouped by the first word of their name (Is, Has, Spawns...); to see inside one class, `members` does it, one level at a time.
- The anonymous "Any of: ..." categories the loaders make for recipes are never listed.
"""
from __future__ import annotations

import re

from .graph import Graph
from .names import KIND_NAMES

LIST_KINDS = (*KIND_NAMES, "dimension")
PAGE = 100                                     # names in one answer; the rest come with `offset`
LONG = 15                                      # more names than this are too many for a chat message: the result tells the model to summarise
GROUP_MIN = 3                                  # a biome-class group smaller than this is folded into "Other"
DIMENSION_ORDER = ("Overworld", "The Nether", "The End")

LABEL = {"item": "Item", "block": "Block", "mob": "Mob", "biome": "Biome", "structure": "Structure", "profession": "Profession",
         "dimension": "Dimension", "category": "ItemClass", "mob class": "MobClass", "biome class": "BiomeClass"}
LEAF = {"category": "Item", "mob class": "Mob", "biome class": "Biome"}      # what a class is a class of
CLASS_KINDS = tuple(LEAF)
PLURAL = {"item": "items", "block": "blocks", "mob": "mobs", "biome": "biomes", "structure": "structures", "profession": "villager professions",
          "dimension": "dimensions", "category": "item categories", "mob class": "mob classes", "biome class": "biome classes"}

# the words a player (or the model) uses for a whole kind: "biomes", "mobs", "creatures"
KIND_WORDS: dict[str, str] = {}
for _kind, _plural in PLURAL.items():
    KIND_WORDS[_kind] = KIND_WORDS[_plural] = _kind
KIND_WORDS.update({"creature": "mob", "creatures": "mob", "categories": "category", "classes": "biome class", "professions": "profession",
                   "villager profession": "profession", "item category": "category", "item categories": "category"})


def kind_of(text: str) -> str | None:
    """The kind a word or phrase names ("biomes", "Mob Classes"), or None if it is not a kind."""
    return KIND_WORDS.get(re.sub(r"\s+", " ", text.strip().lower()))


def _words(contains: str) -> list[str]:
    """The words to look for in a name: lower case, a plural 's' dropped ("swords" finds Iron Sword)."""
    out = []
    for w in re.findall(r"[a-z0-9]+", contains.lower()):
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def _where(kind: str) -> str:
    """The conditions that keep a node out of the list: a category made by a loader, an item that is really a category."""
    if kind == "item":
        return "NOT n:ItemClass AND "
    if kind == "category":
        return "NOT n.name STARTS WITH 'Any of' AND "
    return ""


def _flat(graph: Graph, kind: str, words: list[str]) -> list[dict]:
    label = LABEL[kind]
    cond = f"{_where(kind)}ALL(w IN $words WHERE toLower(n.name) CONTAINS w)"
    if kind in CLASS_KINDS:
        return graph.read(f"""
            MATCH (n:{label}) WHERE {cond}
            OPTIONAL MATCH (n)-[:INCLUDES*1..]->(m:{LEAF[kind]}) WHERE NOT m:ItemClass AND NOT m:MobClass AND NOT m:BiomeClass
            RETURN n.name AS name, count(DISTINCT m) AS size ORDER BY name""", words=words)
    return graph.read(f"MATCH (n:{label}) WHERE {cond} RETURN DISTINCT n.name AS name ORDER BY name", words=words)


def _biomes_by_dimension(graph: Graph, words: list[str]) -> list[dict]:
    rows = graph.read("""
        MATCH (b:Biome) WHERE ALL(w IN $words WHERE toLower(b.name) CONTAINS w)
        OPTIONAL MATCH (d:Dimension)-[:HAS_BIOME]->(:BiomeClass)-[:INCLUDES*0..]->(b)
        RETURN b.name AS name, d.name AS dimension ORDER BY name""", words=words)
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(r["dimension"] or "Other", []).append(r["name"])
    order = [d for d in DIMENSION_ORDER if d in groups] + sorted(d for d in groups if d not in DIMENSION_ORDER and d != "Other") + (["Other"] if "Other" in groups else [])
    return [{"group": d, "count": len(groups[d]), "names": groups[d]} for d in order]


def _class_groups(rows: list[dict]) -> list[dict]:
    """Biome classes grouped by the first word of their name; a small group is folded into Other."""
    by_word: dict[str, list[str]] = {}
    for r in rows:
        by_word.setdefault(r["name"].split(" ")[0], []).append(f"{r['name']} ({r['size']})")
    groups, other = [], []
    for word, names in sorted(by_word.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(names) >= GROUP_MIN:
            groups.append({"group": f"{word} ...", "count": len(names), "names": names})
        else:
            other += names
    if other:
        groups.append({"group": "Other", "count": len(other), "names": sorted(other)})
    return groups


def tool_list(graph: Graph, args: dict) -> dict:
    """list: everything of one kind, flat and sorted (biomes by dimension, biome classes by first word), items and blocks a page at a time."""
    raw = str(args.get("kind") or "").strip()
    kind = kind_of(raw)
    if not raw or kind is None:
        return {"error": f"Give a kind to list: one of {', '.join(LIST_KINDS)}." + (f" '{raw}' is not one." if raw else ""), "type": "bad_argument"}
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        return {"error": "offset must be a whole number.", "type": "bad_argument"}
    contains = str(args.get("contains") or "").strip()
    words = _words(contains)
    out: dict = {"kind": kind}
    if contains:
        out["matching"] = contains

    if kind == "biome":
        groups = _biomes_by_dimension(graph, words)
        out.update(total=sum(g["count"] for g in groups), groups=groups, note="Grouped by dimension.")
    else:
        rows = _flat(graph, kind, words)
        total = len(rows)
        page = rows[offset:offset + PAGE]
        if kind == "biome class":
            out.update(total=total, groups=_class_groups(page))
        elif kind in CLASS_KINDS:
            out.update(total=total, names=[f"{r['name']} ({r['size']})" for r in page])
        else:
            out.update(total=total, names=[r["name"] for r in page])
        if offset:
            out["offset"] = offset
        if offset and offset >= total:
            out["note"] = f"offset {offset} is past the end: there are {total}."
        if offset + PAGE < total:
            out["next"] = f"{total - offset - PAGE} more: call list again with offset={offset + PAGE}, or narrow it with contains."
    if kind in CLASS_KINDS:
        out["note"] = ("The number is how many things are in the class. These are Minecraft's own tags, not a tree of types: one thing is in "
                       "many of them, and a name is not always what it sounds like. members lists what one class holds.")
    if out["total"] > LONG:
        out["tip"] = (f"Chat is short. Do not print all {out['total']}: say how many there are" + (", by group," if "groups" in out else "") +
                      ", give a few examples, and offer to narrow it (a word, or one group).")
    if not out["total"]:
        out["note"] = f"No {PLURAL[kind]} match '{contains}'." if contains else f"No {PLURAL[kind]} found."
    return out
