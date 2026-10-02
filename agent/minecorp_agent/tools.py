"""Read-only graph tools for the agent.

Each tool is defined once (name, description, parameter schema, function). The same definition
describes the tool to the model and runs it, and does not depend on any LLM API, so the tools
could later be served over MCP unchanged.

How to read the graph (repeated in tool output so the model cannot misread it):
- every requirement of a recipe is needed together (AND);
- an `anyOf` entry is a category of items: use any ONE of its options, except those listed in `except`.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from .graph import Graph

READING_NOTE = ("Every entry in 'requires' is needed together. An entry with 'anyOf' is a category: "
                "use any ONE of its options (except those listed in 'except').")

# Lower is preferred when choosing the main route in what_do_i_need.
TYPE_PRIORITY = {
    "crafting_shaped": 0, "crafting_shapeless": 0, "crafting_transmute": 1,
    "smelting": 2, "smoking": 3, "campfire_cooking": 3, "blasting": 4,
    "smithing_transform": 5, "stonecutting": 6, "smithing_trim": 7,
}


@dataclass(frozen=True)
class Tool:
    """A tool the model may call."""
    name: str
    description: str
    parameters: dict
    fn: Callable[[Graph, dict], Any]

    def spec(self) -> dict:
        """The tool in OpenAI function-calling format."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


# --------------------------------------------------------------------------- finding items

def search_items(graph: Graph, text: str, limit: int = 10) -> list[dict]:
    """Items and categories matching the words in the text, best matches first.

    Words may appear in any order. With two or more words, items matching at least two of them
    are returned (those matching all rank first), so a long phrase still finds its likely items.
    """
    t = " ".join(text.lower().split())
    slug = t.replace(" ", "_")
    words = [w for w in re.split(r"[\s_]+", t) if w]
    if not words:
        return []
    return graph.read(
        """
        MATCH (i:Item)
        WITH i, size([w IN $words WHERE toLower(i.name) CONTAINS w OR i.id CONTAINS w]) AS hits
        WHERE hits >= $need
        WITH i, hits,
             CASE WHEN i.id IN [$slug, 'minecraft:' + $slug, '#minecraft:' + $slug] THEN 0
                  WHEN toLower(i.name) = $t THEN 1
                  WHEN toLower(i.name) STARTS WITH $t THEN 2
                  WHEN toLower(i.name) CONTAINS $t THEN 3
                  WHEN hits = size($words) THEN 4
                  ELSE 5 END AS rank
        RETURN i.id AS id, i.name AS name, i:ItemClass AS isCategory, i.size AS options, rank
        ORDER BY rank, hits DESC, isCategory, char_length(i.name), i.name
        LIMIT $limit
        """, t=t, slug=slug, words=words, need=min(2, len(words)), limit=limit)


def resolve_item(graph: Graph, ref: str, want_category: bool | None = None) -> tuple[dict | None, dict | None]:
    """Turn a name or id into one item. Returns (item, None) or (None, error-for-the-model)."""
    rows = search_items(graph, ref, 12)
    if want_category is not None:
        rows = [r for r in rows if r["isCategory"] == want_category]
    if not rows:
        kind = "category" if want_category else "item"
        return None, {"error": f"No {kind} matches '{ref}'. Try find_item with other words."}
    top = rows[0]
    best = [r for r in rows if r["rank"] == top["rank"]]
    if top["rank"] <= 1 or len(best) == 1:
        return top, None
    return None, {"error": f"'{ref}' is ambiguous. Pick one and call again with its id.",
                  "candidates": [_brief(r) for r in rows[:8]]}


def _brief(r: dict) -> dict:
    """Compact item description for the model."""
    out = {"id": r["id"], "name": r["name"]}
    if r.get("isCategory"):
        out["category"] = True
        out["options"] = r.get("options")
    return out


def tool_find_item(graph: Graph, args: dict) -> dict:
    """find_item: search by words."""
    rows = search_items(graph, str(args.get("query", "")), 10)
    return {"matches": [_brief(r) for r in rows]} if rows else {"matches": [], "note": "Nothing matched."}


# --------------------------------------------------------------------------- recipes

def fetch_recipes(graph: Graph, item_id: str) -> list[dict]:
    """All recipes that create the item, each with its requirements and (for categories) examples."""
    recipes = graph.read(
        """
        MATCH (i:Item {id: $id})<-[cr:CREATES]-(r:Recipe)
        OPTIONAL MATCH (r)-[:USES_STATION]->(s:Station)
        RETURN r.id AS id, r.name AS name, r.type AS type, cr.count AS yields, s.name AS station,
               r.pattern AS pattern, r.special AS special
        ORDER BY r.id
        """, id=item_id)
    if not recipes:
        return []
    ids = [r["id"] for r in recipes]
    reqs = graph.read(
        """
        MATCH (r:Recipe)-[q:REQUIRES]->(t:Item) WHERE r.id IN $ids
        RETURN r.id AS rid, t.id AS id, t.name AS name, t:ItemClass AS isCategory, t.size AS options,
               q.count AS count, q.role AS role, q.slot AS slot, q.excludesNames AS excluded
        ORDER BY rid, q.role, q.slot
        """, ids=ids)
    cat_ids = sorted({q["id"] for q in reqs if q["isCategory"]})
    members: dict[str, list[dict]] = {}
    if cat_ids:
        for row in graph.read(
                """
                MATCH (c:ItemClass)-[:INCLUDES*]->(m:Item) WHERE c.id IN $ids AND NOT m:ItemClass
                WITH c, m ORDER BY m.name
                WITH c, collect(DISTINCT {id: m.id, name: m.name}) AS ms
                RETURN c.id AS id, ms[0..40] AS members
                """, ids=cat_ids):
            members[row["id"]] = row["members"]
    by_recipe: dict[str, list] = {}
    for q in reqs:
        q = {**q, "examples": []}
        if q["isCategory"]:
            ms = [m for m in members.get(q["id"], []) if m["name"] not in (q["excluded"] or [])]
            if q["options"] == 1 and ms:
                # a category with a single member is just that item
                q.update(id=ms[0]["id"], name=ms[0]["name"], isCategory=False, options=None)
            else:
                q["examples"] = [m["name"] for m in ms[:6]]
        by_recipe.setdefault(q["rid"], []).append(q)
    for r in recipes:
        r["reqs"] = by_recipe.get(r["id"], [])
    return recipes


def _req_view(q: dict) -> dict:
    """One requirement as the model should see it."""
    out: dict = {}
    if q["isCategory"]:
        out["anyOf"] = q["name"]
        out["options"] = q["options"]
        out["examples"] = q["examples"]
        if q["excluded"]:
            out["except"] = q["excluded"]
    else:
        out["item"] = q["name"]
    out["count"] = q["count"]
    if q["role"] != "ingredient":
        out["role"] = q["role"]
    return out


def _recipe_view(r: dict) -> dict:
    """One recipe as the model should see it, including the crafting grid when there is one."""
    out = {"recipe": r["name"], "type": r["type"], "station": r["station"], "makes": r["yields"],
           "requires": [_req_view(q) for q in r["reqs"]]}
    if r["special"]:
        out["note"] = "Special recipe: inputs are not listed in the data."
    if r.get("pattern"):
        out["grid"] = r["pattern"]
        out["gridKey"] = {q["slot"]: (f"any {q['name']}" if q["isCategory"] else q["name"])
                          for q in r["reqs"] if q["slot"]}
    return out


def tool_get_recipes(graph: Graph, args: dict) -> dict:
    """get_recipes: every way to make one item."""
    item, err = resolve_item(graph, str(args.get("item", "")), want_category=False)
    if err:
        return err
    recipes = fetch_recipes(graph, item["id"])
    if not recipes:
        return {"item": item["name"], "recipes": [],
                "note": "No recipe creates this item. It is probably found, mined or dropped in the world."}
    out = {"item": item["name"], "note": READING_NOTE, "recipes": [_recipe_view(r) for r in recipes]}
    overview = route_overview(graph, item["id"])
    if overview:
        out["overview"] = {**overview, "meaning": "size of the main route from base materials to this item"}
    return out


# --------------------------------------------------------------------------- uses

def tool_get_uses(graph: Graph, args: dict) -> dict:
    """get_uses: what an item (or something that counts as it) can be used to make."""
    item, err = resolve_item(graph, str(args.get("item", "")), want_category=False)
    if err:
        return err
    limit = max(1, min(int(args.get("limit", 25) or 25), 60))
    rows = graph.read(
        """
        MATCH (x:Item {id: $id})<-[:INCLUDES*0..]-(t:Item)<-[q:REQUIRES]-(r:Recipe)-[:CREATES]->(o:Item)
        RETURN o.name AS makes, collect(DISTINCT r.name)[0..3] AS recipes,
               collect(DISTINCT CASE WHEN t.id = $id THEN null ELSE t.name END) AS viaCategory,
               min(q.count) AS count
        ORDER BY makes
        """, id=item["id"])
    for r in rows:
        r["viaCategory"] = [v for v in r["viaCategory"] if v]
        if not r["viaCategory"]:
            del r["viaCategory"]
    return {"item": item["name"], "totalProducts": len(rows), "showing": min(limit, len(rows)),
            "canMake": rows[:limit],
            "note": "viaCategory means the item counts as that category in the recipe."}


# --------------------------------------------------------------------------- category members

def tool_class_members(graph: Graph, args: dict) -> dict:
    """class_members: the items that count as a category (e.g. planks, beds)."""
    cat, err = resolve_item(graph, str(args.get("category", "")), want_category=True)
    if err:
        return err
    rows = graph.read(
        """
        MATCH (c:ItemClass {id: $id})-[:INCLUDES*]->(m:Item) WHERE NOT m:ItemClass
        RETURN DISTINCT m.name AS name ORDER BY name
        """, id=cat["id"])
    names = [r["name"] for r in rows]
    return {"category": cat["name"], "total": len(names), "members": names[:60],
            **({"note": "List truncated to 60."} if len(names) > 60 else {})}


# --------------------------------------------------------------------------- what do I need

class Route:
    """Works out the main route from base materials to one item, as an ordered list of steps."""

    VERBS = {"smelting": "Smelt", "blasting": "Blast", "smoking": "Smoke", "campfire_cooking": "Cook",
             "stonecutting": "Cut", "smithing_transform": "Smith", "smithing_trim": "Smith"}

    def __init__(self, graph: Graph, max_depth: int, shared: dict | None = None) -> None:
        """`shared` is an optional dict that lets many routes reuse one recipe/reversal cache."""
        self.graph = graph
        self.max_depth = max_depth
        shared = shared if shared is not None else {}
        self.recipes: dict[str, list[dict]] = shared.setdefault("recipes", {})
        self.reversal: dict[tuple[str, str], bool] = shared.setdefault("reversal", {})
        self.silk: dict[str, bool] = shared.setdefault("silk", {})
        self.mined: dict[str, list] = shared.setdefault("mined", {})
        self.sources: dict[str, list] = shared.setdefault("sources", {})
        self.totals: dict[tuple[str, bool], dict] = {}   # (name, is_category) -> {"count", "examples"}
        self.steps: dict[str, dict] = {}                 # item id -> step, in the order they must be done
        self.levels = 0                                  # deepest chain of crafted items
        self.not_expanded: set[str] = set()

    def recipes_for(self, item_id: str) -> list[dict]:
        if item_id not in self.recipes:
            self.recipes[item_id] = fetch_recipes(self.graph, item_id)
        return self.recipes[item_id]

    def is_reversal(self, made_id: str, input_id: str) -> bool:
        """True if the input can itself be made from the product (e.g. ingot <- nuggets <- ingot)."""
        key = (made_id, input_id)
        if key not in self.reversal:
            rows = self.graph.read(
                """
                MATCH (a:Item {id: $a})<-[:CREATES]-(r:Recipe)-[:REQUIRES]->(t:Item)
                WHERE t.id = $x OR EXISTS { (t)-[:INCLUDES*]->(:Item {id: $x}) }
                RETURN count(r) > 0 AS rev
                """, a=input_id, x=made_id)
            self.reversal[key] = bool(rows and rows[0]["rev"])
        return self.reversal[key]

    def mined_from(self, item_id: str) -> list[dict]:
        """Blocks whose normal drop is this item AND which are a different block (iron ore drops raw iron).

        Used to decide where a route stops: an item that comes from a different block (raw iron from iron ore)
        is a mined resource. Empty if a block of the same name also drops the item (a log, obsidian): the data
        cannot tell a natural block from a placed one. For display use sources_of, which has no such exclusion.
        """
        if item_id not in self.mined:
            self.mined[item_id] = self.graph.read(
                """
                MATCH (i:Item {id: $id})
                WHERE NOT EXISTS { (:Block {id: $id})<-[:FROM_BLOCK]-(:Harvest)-[:CREATES {condition: 'default'}]->(i) }
                MATCH (h:Harvest)-[c:CREATES {condition: 'default'}]->(i)
                MATCH (h)-[:FROM_BLOCK]->(b:Block) WHERE b.id <> i.id
                OPTIONAL MATCH (h)-[:REQUIRES {role: 'tool'}]->(t:Item)
                RETURN b.name AS block, t.name AS tool ORDER BY (t IS NULL), b.name LIMIT 3
                """, id=item_id)
        return self.mined[item_id]

    def sources_of(self, item_id: str) -> list[dict]:
        """Every block that normally drops this item (including a block of the same name), for display.

        Each entry has the block and the minimum tool for that block. Blocks that need a tool come first,
        and a block that drops itself comes before others (obsidian before ender chest).
        """
        if item_id not in self.sources:
            self.sources[item_id] = self.graph.read(
                """
                MATCH (h:Harvest)-[c:CREATES {condition: 'default'}]->(i:Item {id: $id})
                MATCH (h)-[:FROM_BLOCK]->(b:Block)
                OPTIONAL MATCH (h)-[:REQUIRES {role: 'tool'}]->(t:Item)
                RETURN b.name AS block, t.name AS tool
                ORDER BY (b.id <> i.id), (t IS NULL), b.name LIMIT 3
                """, id=item_id)
        return self.sources[item_id]

    def silk_only(self, item_id: str) -> bool:
        """True if mining only gives this item with silk touch (iron ore: the block drops raw iron)."""
        if item_id not in self.silk:
            rows = self.graph.read(
                "MATCH (:Harvest)-[c:CREATES]->(:Item {id: $id}) RETURN collect(DISTINCT c.condition) AS conds", id=item_id)
            conds = set(rows[0]["conds"]) if rows else set()
            self.silk[item_id] = bool(conds) and conds <= {"silk_touch"}
        return self.silk[item_id]

    def choose(self, item_id: str, path: frozenset) -> tuple[dict | None, list[dict], bool]:
        """Pick the preferred recipe for an item. Returns (recipe, other usable recipes, good).

        `good` is False when the best recipe only reverses a compaction (raw iron from a raw iron block) or
        needs an item that is only available with silk touch; such an item is better treated as mined.
        """
        usable = []
        for r in self.recipes_for(item_id):
            if r["special"] or not r["reqs"]:
                continue
            ids = {q["id"] for q in r["reqs"] if not q["isCategory"]}
            if ids & path:
                continue  # would need something already on this route
            reversal = any(self.is_reversal(item_id, i) for i in ids)
            # an input that mining only gives with silk touch is a poor route (smelt raw iron, not iron ore)
            silk = any(self.silk_only(i) and not self.recipes_for(i) for i in ids)
            usable.append((reversal, silk, TYPE_PRIORITY.get(r["type"], 9), len(r["reqs"]), len(r["id"]), r["id"], r))
        usable.sort(key=lambda x: x[:6])  # simplest recipe first: shorter id, then alphabetical
        ordered = [u[6] for u in usable]
        good = bool(usable) and not usable[0][0] and not usable[0][1]
        return (ordered[0] if ordered else None), ordered[1:], good

    def _add_total(self, name: str, is_category: bool, count: int, examples: list | None = None,
                   item_id: str | None = None) -> None:
        entry = self.totals.setdefault((name, is_category), {"count": 0, "examples": examples or [], "id": item_id})
        entry["count"] += count

    def build(self, item_id: str, name: str, needed: int, path: frozenset, depth: int) -> None:
        """Record what is needed to get `needed` of this item. Steps are stored children-first."""
        too_deep = depth >= self.max_depth
        recipe, others, good = (None, [], False) if too_deep else self.choose(item_id, path | {item_id})
        mined = depth > 0 and not good and bool(self.mined_from(item_id))   # a mined resource is a base material
        if mined:
            recipe, others = None, []
        if recipe is None:
            if too_deep and not mined:
                self.not_expanded.add(name)
            self._add_total(name, False, needed, item_id=item_id)
            return
        self.levels = max(self.levels, depth + 1)
        crafts = math.ceil(needed / (recipe["yields"] or 1))
        uses: dict[str, dict] = {}
        for q in recipe["reqs"]:
            count = (q["count"] or 1) * crafts
            if q["isCategory"]:
                self._add_total(q["name"], True, count, q["examples"])
                uses[f"any {q['name']}"] = {"anyOf": q["name"], "count": count, "examples": q["examples"],
                                            **({"except": q["excluded"]} if q["excluded"] else {})}
            else:
                self.build(q["id"], q["name"], count, path | {item_id}, depth + 1)
                uses[q["name"]] = {"item": q["name"], "count": count}
        step = self.steps.get(item_id)
        if step is None:
            self.steps[item_id] = {
                "makes": name, "amount": crafts * (recipe["yields"] or 1), "recipe": recipe["name"],
                "type": recipe["type"], "station": recipe["station"], "times": crafts, "uses": uses,
                "otherWays": [f"{o['name']} ({o['type']})" for o in others[:4]],
            }
        else:  # the same item is needed in two places: do it once, in bulk
            step["times"] += crafts
            step["amount"] += crafts * (recipe["yields"] or 1)
            for key, u in uses.items():
                if key in step["uses"]:
                    step["uses"][key]["count"] += u["count"]
                else:
                    step["uses"][key] = u

    def ordered_steps(self) -> list[dict]:
        """The steps in the order to do them, each with a ready-to-read `do` line."""
        out = []
        for n, s in enumerate(self.steps.values(), 1):
            verb = self.VERBS.get(s["type"], "Craft")
            inputs = ", ".join(
                f"{u['count']} {u['anyOf']} (any)" if "anyOf" in u else f"{u['count']} {u['item']}"
                for u in s["uses"].values())
            do = f"{verb}: {inputs} -> {s['amount']} {s['makes']}"
            if s["station"]:
                do += f" at {s['station']}"
            if s["times"] > 1:
                do += f" ({s['times']} times)"
            step = {"step": n, "do": do, "uses": list(s["uses"].values())}
            if s["otherWays"]:
                step["otherWays"] = s["otherWays"]
            out.append(step)
        return out

    def gather(self) -> list[dict]:
        """Base materials to collect first, with totals."""
        out = []
        for (name, is_cat), v in sorted(self.totals.items()):
            entry = {("anyOf" if is_cat else "item"): name, "total": v["count"]}
            if is_cat and v["examples"]:
                entry["examples"] = v["examples"]
            if not is_cat and v.get("id"):
                how = self.sources_of(v["id"])
                if how:
                    entry["mineFrom"] = [{"block": h["block"],
                                          **({"tool": h["tool"] + " or better"} if h["tool"] else {})} for h in how]
            out.append(entry)
        return out

    def overview(self) -> dict:
        """A small summary of how big the job is."""
        return {"craftingSteps": len(self.steps), "chainLength": self.levels,
                "baseMaterials": len(self.totals),
                "stations": sorted({s["station"] for s in self.steps.values() if s["station"]}),
                "truncated": bool(self.not_expanded)}


def route_overview(graph: Graph, item_id: str) -> dict | None:
    """Overview of the main route for one item, or None if it cannot be worked out."""
    try:
        route = Route(graph, 6)
        route.build(item_id, "", 1, frozenset(), 0)
        return route.overview() if route.steps else None
    except Exception:
        return None


def tool_what_do_i_need(graph: Graph, args: dict) -> dict:
    """what_do_i_need: ordered steps from base materials to an item, with totals to gather."""
    item, err = resolve_item(graph, str(args.get("item", "")), want_category=False)
    if err:
        return err
    quantity = max(1, min(int(args.get("quantity", 1) or 1), 64))
    for depth in (6, 4, 3, 2):  # shrink if the answer would be too large for the model
        route = Route(graph, depth)
        route.build(item["id"], item["name"], quantity, frozenset(), 0)
        result = {"item": item["name"], "quantity": quantity,
                  "gather": route.gather(),
                  "steps": route.ordered_steps(),
                  "overview": route.overview(),
                  "note": ("'gather' lists base materials (found, mined or dropped in the world, not crafted). "
                           "'steps' are in the order to do them and form the main route only; 'otherWays' lists "
                           "alternatives. Everything in a step's 'uses' is needed together; an 'anyOf' entry "
                           "means use any ONE of its options (except those listed in 'except'). "
                           "'overview' sizes the whole job.")}
        if route.not_expanded:
            result["notExpanded"] = sorted(route.not_expanded)
        if len(json.dumps(result)) <= 9000:
            return result
    return result


def tool_rank_recipes(graph: Graph, args: dict) -> dict:
    """rank_recipes: craftable items ordered by how big the job is (steps, base materials, chain)."""
    order = "easiest" if str(args.get("order", "hardest")).lower().startswith("e") else "hardest"
    limit = max(1, min(int(args.get("limit", 8) or 8), 25))
    direction = "ASC" if order == "easiest" else "DESC"
    rows = graph.read(
        f"""
        MATCH (i:Item) WHERE i.routeSteps IS NOT NULL AND NOT coalesce(i.routeTruncated, false)
        RETURN i.name AS item, i.routeSteps AS craftingSteps, i.routeLevels AS chainLength,
               i.routeBaseMaterials AS baseMaterials, i.routeStations AS stations
        ORDER BY craftingSteps {direction}, baseMaterials {direction}, chainLength {direction}, item
        LIMIT $limit
        """, limit=limit)
    if not rows:
        return {"error": "No route data is stored in the graph yet (run loader/compute_routes.py)."}
    return {"order": order, "items": rows,
            "note": ("Ranked by crafting steps on the main route, then distinct base materials, then chain "
                     "length. This measures how many things must be made, not how rare the materials are. "
                     "Use what_do_i_need on one of these items for the full steps.")}


# --------------------------------------------------------------------------- registry

TOOLS: list[Tool] = [
    Tool("find_item",
         "Search for Minecraft items and item categories by name. Use this first when unsure of the exact name.",
         {"type": "object", "properties": {"query": {"type": "string", "description": "words from the name"}},
          "required": ["query"], "additionalProperties": False},
         tool_find_item),
    Tool("get_recipes",
         "Every way to make one item: station, ingredients and crafting grid. Use for 'how do I make X'. Give the item that is the RESULT (for an upgrade, the upgraded item such as netherite pickaxe).",
         {"type": "object", "properties": {"item": {"type": "string", "description": "item name or id"}},
          "required": ["item"], "additionalProperties": False},
         tool_get_recipes),
    Tool("what_do_i_need",
         "The ordered steps to make an item from base materials (mined, found or dropped), plus the list "
         "of base materials to gather. Use for 'what do I need for X from scratch' or 'walk me through X'.",
         {"type": "object", "properties": {
             "item": {"type": "string", "description": "item name or id"},
             "quantity": {"type": "integer", "description": "how many to make (default 1)"}},
          "required": ["item"], "additionalProperties": False},
         tool_what_do_i_need),
    Tool("rank_recipes",
         "Craftable items ranked by how big the job is (crafting steps, base materials, chain length). Use "
         "when the player asks for a hard, easy, long or short recipe, or wants a challenge. Then offer to "
         "walk through one of them.",
         {"type": "object", "properties": {
             "order": {"type": "string", "enum": ["hardest", "easiest"], "description": "default hardest"},
             "limit": {"type": "integer", "description": "how many items (default 8, max 25)"}},
          "additionalProperties": False},
         tool_rank_recipes),
    Tool("get_uses",
         "What an item can be used to make. Use for 'what can I make with X'.",
         {"type": "object", "properties": {
             "item": {"type": "string", "description": "item name or id"},
             "limit": {"type": "integer", "description": "max products to list (default 25)"}},
          "required": ["item"], "additionalProperties": False},
         tool_get_uses),
    Tool("class_members",
         "List the items that count as a category such as planks, beds or logs.",
         {"type": "object", "properties": {"category": {"type": "string", "description": "category name"}},
          "required": ["category"], "additionalProperties": False},
         tool_class_members),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
