"""Where an answer's facts came from, as the player is shown them.

Each tool result is turned into a few sources by code, never by the model, so a source is always something a tool really returned:

    {"kind": "game data", "label": "Recipes for Ender Eye",
     "graph": ["(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})", ...]}
    {"kind": "wiki", "label": "Tutorial:Shelters > Hole in the wall", "url": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall",
     "graph": ["(Chunk)-[PART_OF]->(Section {Hole in the wall})"]}
    {"kind": "plan", "label": "Get to the Nether (draft, not confirmed)", "graph": ["(Process {Get to the Nether})-[HAS_ACTIVITY]->..."]}

`graph` is the path through the graph the tool walked (see graphs.py), so a player or an audience can see the answer was found by
following relationships. The game data is read from the server's own files and is authoritative; the wiki is community-written text;
a plan is drafted from the wiki and not yet confirmed. The player is shown the sources and nothing else about how the answer was made
(not the tool calls, their arguments, timings or tokens: those are the developer's trace).
"""
from __future__ import annotations

from urllib.parse import unquote

from . import graphs

GAME_DATA, WIKI, PLAN = "game data", "wiki", "plan"
MAX_SOURCES = 8


def source(kind: str, label: str, url: str | None = None) -> dict:
    return {"kind": kind, "label": label, **({"url": url} if url else {})}


def label_from_url(url: str) -> str:
    """'https://minecraft.wiki/w/Tutorial:Mob_farm#Sinkhole' to 'Tutorial:Mob farm > Sinkhole'."""
    path = url.split("/w/", 1)[-1]
    page, _, anchor = path.partition("#")
    label = unquote(page).replace("_", " ")
    return label + (f" > {unquote(anchor).replace('_', ' ')}" if anchor else "")


def wiki_sources(urls: str) -> list[dict]:
    """The wiki pages a plan was drafted from: its `source` is one or more URLs separated by semicolons."""
    return [source(WIKI, label_from_url(u.strip()), u.strip()) for u in (urls or "").split(";") if u.strip().startswith("http")]


def merge_sources(*lists: list[dict], cap: int = MAX_SOURCES) -> list[dict]:
    """The sources of every tool call in order, each once, at most `cap`."""
    seen, out = set(), []
    for items in lists:
        for s in items:
            key = (s["kind"], s["label"])
            if key not in seen:
                seen.add(key)
                out.append(s)
    return out[:cap]


def with_graph(src: dict, patterns: list) -> dict:
    """The source with the compact form of its patterns, when it has any."""
    return {**src, "graph": [p.compact() for p in patterns]} if patterns else src


def _one(output: dict, template: str, patterns: list) -> list[tuple[dict, list]]:
    name = output.get("item")
    return [(source(GAME_DATA, template.format(name)), patterns)] if name else []


def patterned(tool: str, args: dict, output) -> list[tuple[dict, list]]:
    """The sources one tool result gives, each with the graph patterns it walked: none if it failed, found nothing, or only searched."""
    if not isinstance(output, dict) or "error" in output:
        return []
    if tool == "get_recipes":
        if "items" in output:
            return [(source(GAME_DATA, f"Recipes for {i['item']}"), graphs.recipe_patterns(i["item"], i.get("recipes", []))) for i in output["items"]]
        return _one(output, "Recipes for {}", graphs.recipe_patterns(output.get("item"), output.get("recipes", [])))
    if tool == "what_do_i_need":
        return _one(output, "What is needed to make {}", graphs.route_patterns(output))
    if tool == "how_to_get":
        return _one(output, "How to get {}", graphs.ways_patterns(output))
    if tool == "get_uses":
        return _one(output, "What {} is used for", graphs.uses_patterns(output))
    if tool == "rank_recipes":
        return [(source(GAME_DATA, "Recipe difficulty ranking"), [])] if output.get("items") else []
    if tool == "members":
        return [(source(GAME_DATA, f"What counts as {r['name']}"), graphs.members_patterns(r)) for r in output.get("results", [])]
    if tool == "describe":
        return [(source(GAME_DATA, f"About {r['name']}"), graphs.describe_patterns(r)) for r in output.get("results", [])]
    if tool == "where_to_find":
        return [(source(GAME_DATA, f"Where to find {r['name']}"), graphs.where_patterns(r)) for r in output.get("results", [])]
    if tool == "search_wiki":
        return [(source(WIKI, h["section"], h.get("sectionUrl")), [graphs.section_pattern(h["section"])]) for h in output.get("hits", []) if h.get("section")]
    if tool == "read_section":
        return [(source(WIKI, output["section"], output.get("sectionUrl")), [graphs.section_pattern(output["section"])])] if output.get("section") else []
    if tool == "get_process":
        return _plan(output)
    return []


def _plan(output: dict) -> list[tuple[dict, list]]:
    """A plan (an outline or one step) and the wiki sections it was drafted from; the list of plans is not a source."""
    name = output.get("process")
    if not name:
        return []
    draft = output.get("status", "candidate") == "candidate"
    label = name + (" (draft, not confirmed)" if draft else "")
    return [(source(PLAN, label), graphs.plan_patterns(output))] + [(w, []) for w in wiki_sources(output.get("source", ""))]


def sources_for(tool: str, args: dict, output) -> list[dict]:
    """The sources one tool result gives, with their graph lines."""
    return [with_graph(s, patterns) for s, patterns in patterned(tool, args, output)]
