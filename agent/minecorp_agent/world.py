"""The world tools: `describe` (what a thing is) and `where_to_find` (where it is in the world).

Both accept the name of any kind of thing and follow the graph from it:

    Item <-CREATES- Mine -REQUIRES-> Block <-CREATES- Feature <-PLACES- PlacedFeature <-GENERATES- Biome
    Item <-CREATES- Kill|Shear -REQUIRES-> Mob <-SPAWNS- Biome | Structure
    Structure -GENERATES_IN-> Biome | BiomeClass -INCLUDES-> Biome        Dimension -HAS_BIOME-> BiomeClass

A name that means several things (a diamond ore item and a diamond ore block) gives every one of them, each with its
kind. Biome lists are compressed ("every Overworld biome") so an answer stays short.
"""
from __future__ import annotations

from .graph import Graph
from .names import KIND_NAMES, search_names

MAX_CANDIDATES = 4        # a name that fits more things than this is returned as ambiguous
MAX_BIOMES_LISTED = 10
MAX_PLACEMENTS = 5
MAX_NAMES = 12            # names listed in one list (drops, mobs, members)
WIKI_SECTIONS = 3

LABEL_OF = {"category": "ItemClass", "item": "Item", "mob": "Mob", "block": "Block", "biome": "Biome",
            "structure": "Structure", "mob class": "MobClass", "biome class": "BiomeClass"}
WHERE_KINDS = ("item", "block", "mob", "biome", "structure")


# --------------------------------------------------------------------------- resolving

def resolve_things(graph: Graph, ref: str, kinds: tuple[str, ...] | None = None) -> tuple[list[dict], dict | None]:
    """Everything the name could mean, as (matches, None), or ([], error for the model).

    An exact name gives every kind that has it (the item and the block). Otherwise the best-ranked matches are
    returned, unless there are too many, which is reported as ambiguous with the candidates to choose from."""
    rows = search_names(graph, ref, kinds, 12)
    if not rows:
        return [], {"error": f"Nothing matches '{ref}'. Try find with other words.", "type": "bad_argument"}
    best = [r for r in rows if r["rank"] == rows[0]["rank"]]
    if rows[0]["rank"] <= 1:
        best = [r for r in rows if r["rank"] <= 1]
    if len(best) > MAX_CANDIDATES:
        return [], {"error": f"'{ref}' is ambiguous. Pick one and call again with its exact name.",
                    "candidates": [{"name": r["name"], "kind": r["kind"]} for r in rows[:8]], "type": "bad_argument"}
    return best, None


# --------------------------------------------------------------------------- biome lists

def dimension_biomes(graph: Graph) -> dict[str, dict[str, str]]:
    """{dimension name: {biome id: biome name}} (cheap: three dimensions and about 66 biomes)."""
    out: dict[str, dict[str, str]] = {}
    for r in graph.read("""
            MATCH (d:Dimension)-[:HAS_BIOME]->(:BiomeClass)-[:INCLUDES*0..]->(b:Biome)
            RETURN d.name AS dimension, b.id AS id, b.name AS name"""):
        out.setdefault(r["dimension"], {})[r["id"]] = r["name"]
    return out


def biomes_text(biomes: dict[str, str], everywhere: dict[str, str], dimension: str) -> dict:
    """A set of biomes of one dimension, as short as the truth allows."""
    n, total = len(biomes), len(everywhere)
    if n == total:
        return {"biomes": f"every {dimension} biome", "count": n}
    missing = sorted(everywhere[i] for i in everywhere if i not in biomes)
    names = sorted(biomes.values())
    if total >= 20 and len(missing) <= MAX_BIOMES_LISTED and len(missing) < n:
        return {"biomes": f"every {dimension} biome except " + ", ".join(missing), "count": n}
    if n > MAX_BIOMES_LISTED:
        return {"biomes": names[:MAX_BIOMES_LISTED] + [f"and {n - MAX_BIOMES_LISTED} more"], "count": n}
    return {"biomes": names, "count": n}


def by_dimension(graph: Graph, biomes: dict[str, str]) -> list[dict]:
    """Biomes (id to name) grouped by the dimension they are in, each group compressed."""
    out = []
    for dimension, everywhere in dimension_biomes(graph).items():
        here = {i: n for i, n in biomes.items() if i in everywhere}
        if here:
            out.append({"dimension": dimension, **biomes_text(here, everywhere, dimension)})
    return out


# --------------------------------------------------------------------------- where: blocks and mobs

def where_block(graph: Graph, block_id: str) -> dict:
    """Where the ore generation puts a block: heights, vein sizes and the biomes, by dimension."""
    rows = graph.read("""
        MATCH (:Block {id: $id})<-[:CREATES]-(:Feature)<-[:PLACES]-(p:PlacedFeature)
        OPTIONAL MATCH (p)-[:IN_DIMENSION]->(d:Dimension)
        OPTIONAL MATCH (b:Biome)-[:GENERATES]->(p)
        WITH p, d.name AS dimension, collect(DISTINCT {id: b.id, name: b.name}) AS biomes
        MATCH (p)-[:PLACES]->(f:Feature)
        RETURN p.id AS id, dimension, p.minY AS minY, p.maxY AS maxY, p.peakY AS peakY, p.countMin AS countMin,
               p.countMax AS countMax, p.rarityChance AS rarity, f.size AS veinSize, biomes
        ORDER BY dimension, minY, id""", id=block_id)
    if not rows:
        return {"note": "The world generation data has no ore placement for this block (it is part of the terrain, a "
                        "structure, or made by something else)."}
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["dimension"], []).append(r)
    where = []
    for dimension, rs in groups.items():
        biomes = {b["id"]: b["name"] for r in rs for b in r["biomes"] if b["id"]}
        everywhere = dimension_biomes(graph).get(dimension, {})
        placements = []
        for r in rs[:MAX_PLACEMENTS]:
            count = r["countMin"] if r["countMin"] == r["countMax"] else f"{r['countMin']} to {r['countMax']}"
            p = {"y": f"{r['minY']} to {r['maxY']}", "veinsPerChunk": count, "veinSize": r["veinSize"]}
            if r["peakY"] is not None:
                p["mostCommonAround"] = r["peakY"]
            if r["rarity"]:
                p["oneChunkIn"] = r["rarity"]
            placements.append(p)
        where.append({"dimension": dimension, "placements": placements,
                      **(biomes_text(biomes, everywhere, dimension) if biomes else {})})
    return {"where": where}


def where_mob(graph: Graph, mob_id: str) -> dict:
    """Where a mob spawns naturally: the biomes (by spawn category) and the structures."""
    spawns = graph.read("""
        MATCH (b:Biome)-[s:SPAWNS]->(:Mob {id: $id}) RETURN b.id AS id, b.name AS name, s.category AS category, s.weight AS weight""", id=mob_id)
    structures = graph.read("""
        MATCH (s:Structure)-[x:SPAWNS]->(:Mob {id: $id}) RETURN s.name AS name, x.category AS category ORDER BY name""", id=mob_id)
    out: dict = {}
    if spawns:
        categories = sorted({r["category"] for r in spawns if r["category"]})
        out["spawnsIn"] = [{"category": c, **entry}
                           for c in categories
                           for entry in by_dimension(graph, {r["id"]: r["name"] for r in spawns if r["category"] == c})]
    if structures:
        out["structures"] = [r["name"] for r in structures][:MAX_NAMES]
    if not out:
        out["note"] = ("It does not spawn on its own in any biome or structure in the data: it comes from a trade, a "
                       "spawner, an egg, breeding or a game rule.")
    return out


def where_biome(graph: Graph, biome_id: str) -> dict:
    """What a biome has: its mobs (by category), the structures that generate there and the ores."""
    mobs = graph.read("""
        MATCH (:Biome {id: $id})-[s:SPAWNS]->(m:Mob) RETURN s.category AS category, collect(DISTINCT m.name) AS mobs ORDER BY category""", id=biome_id)
    structures = graph.read("""
        MATCH (s:Structure)-[:GENERATES_IN]->()-[:INCLUDES*0..]->(b:Biome {id: $id}) RETURN DISTINCT s.name AS name ORDER BY name""", id=biome_id)
    ores = graph.read("""
        MATCH (:Biome {id: $id})-[:GENERATES]->(p:PlacedFeature)-[:PLACES]->(:Feature)-[:CREATES]->(b:Block)
        RETURN b.name AS block, min(p.minY) AS minY, max(p.maxY) AS maxY ORDER BY block""", id=biome_id)
    dimension = [d for d, ids in dimension_biomes(graph).items() if biome_id in ids]
    out: dict = {"dimension": dimension[0] if dimension else None,
                 "mobs": {r["category"]: sorted(r["mobs"])[:MAX_NAMES] for r in mobs}}
    if structures:
        out["structures"] = [r["name"] for r in structures][:MAX_NAMES]
    if ores:
        out["ores"] = [{"block": r["block"], "y": f"{r['minY']} to {r['maxY']}"} for r in ores][:MAX_NAMES]
    return out


def where_structure(graph: Graph, structure_id: str) -> dict:
    """Where a structure generates (biomes by dimension) and the mobs it spawns."""
    biomes = graph.read("""
        MATCH (:Structure {id: $id})-[:GENERATES_IN]->()-[:INCLUDES*0..]->(b:Biome) RETURN DISTINCT b.id AS id, b.name AS name""", id=structure_id)
    mobs = graph.read("""
        MATCH (:Structure {id: $id})-[x:SPAWNS]->(m:Mob) RETURN DISTINCT m.name AS name ORDER BY name""", id=structure_id)
    out: dict = {"generatesIn": by_dimension(graph, {r["id"]: r["name"] for r in biomes})}
    if mobs:
        out["spawns"] = [r["name"] for r in mobs][:MAX_NAMES]
    return out


def sources_of_item(graph: Graph, item_id: str) -> list[dict]:
    """The blocks to mine and the mobs to kill or shear to get an item, with the tool and the condition."""
    return graph.read("""
        MATCH (h:Action)-[c:CREATES]->(:Item {id: $id}) WHERE h:Mine OR h:Kill OR h:Shear
        OPTIONAL MATCH (h)-[:REQUIRES {role: 'target'}]->(b:Block)
        OPTIONAL MATCH (h)-[:REQUIRES {role: 'target'}]->(m:Mob)
        OPTIONAL MATCH (h)-[q:REQUIRES {role: 'tool'}]->(t:Item)
        RETURN CASE WHEN h:Mine THEN 'mine' WHEN h:Kill THEN 'kill' ELSE 'shear' END AS how,
               b.id AS blockId, b.name AS block, m.id AS mobId, m.name AS mob, t.name AS tool,
               coalesce(q.orBetter, false) AS orBetter, c.condition AS condition
        ORDER BY (c.condition <> 'default'), how, coalesce(b.name, m.name) LIMIT 12""", id=item_id)


def where_item(graph: Graph, item_id: str) -> dict:
    """Where to get an item from the world: each way (block or mob) with where that block or mob is."""
    found = []
    for s in sources_of_item(graph, item_id):
        entry: dict = {"how": s["how"], "from": s["block"] or s["mob"]}
        if s["tool"]:
            entry["tool"] = s["tool"] + (" or better" if s["orBetter"] else "")
        if s["condition"] and s["condition"] != "default":
            entry["when"] = s["condition"].replace("_", " ")
        entry.update(where_block(graph, s["blockId"]) if s["blockId"] else where_mob(graph, s["mobId"]))
        same = next((f for f in found if {k: v for k, v in f.items() if k != "from"} == {k: v for k, v in entry.items() if k != "from"}), None)
        if same:                                   # Diamond Ore and Deepslate Diamond Ore are found in the same places
            same["from"] = [same["from"], entry["from"]] if isinstance(same["from"], str) else same["from"] + [entry["from"]]
        else:
            found.append(entry)
    if not found:
        return {"note": "No block or mob in the data drops this item, so there is no place to find it. It may be crafted "
                        "(use get_recipes)."}
    return {"found": found}


WHERE = {"item": where_item, "block": where_block, "mob": where_mob, "biome": where_biome, "structure": where_structure}


def tool_where_to_find(graph: Graph, args: dict) -> dict:
    """where_to_find: where an item, block, mob, biome or structure is in the world."""
    ref = str(args.get("name", "")).strip()
    if not ref:
        return {"error": "Give the name of the item, block, mob, biome or structure.", "type": "bad_argument"}
    things, err = resolve_things(graph, ref, WHERE_KINDS)
    if err:
        return err
    results = []
    for t in things:
        results.append({"name": t["name"], "kind": t["kind"], **WHERE[t["kind"]](graph, t["id"])})
    return {"results": results,
            "note": ("Heights are y levels (the Overworld goes from -64 to 319). 'veinsPerChunk' is how many veins the game "
                     "tries to place in each chunk. Biome lists are of the data, not of the wiki. A name that is both an "
                     "item and a block gives both results.")}


# --------------------------------------------------------------------------- describe

def drops_of(graph: Graph, label: str, node_id: str) -> list[dict]:
    """What breaking a block or killing a mob gives (name, condition) with the tool for a block."""
    rows = graph.read(f"""
        MATCH (:{label} {{id: $id}})<-[:REQUIRES {{role: 'target'}}]-(h:Action)-[c:CREATES]->(i:Item) WHERE h:Mine OR h:Kill OR h:Shear
        OPTIONAL MATCH (h)-[q:REQUIRES {{role: 'tool'}}]->(t:Item)
        RETURN i.name AS item, c.condition AS condition, avg(c.count) AS count, t.name AS tool, coalesce(q.orBetter, false) AS orBetter
        ORDER BY (c.condition <> 'default'), item LIMIT 20""", id=node_id)
    out = []
    for r in rows:
        d: dict = {"item": r["item"]}
        if r["condition"] and r["condition"] != "default":
            d["when"] = r["condition"].replace("_", " ")
        if r["count"] and round(r["count"], 1) != 1:
            d["typicalCount"] = round(r["count"], 1)
        if r["tool"]:
            d["tool"] = r["tool"] + (" or better" if r["orBetter"] else "")
        out.append(d)
    return out


def classes_of(graph: Graph, label: str, class_label: str, node_id: str) -> list[str]:
    rows = graph.read(f"""
        MATCH (c:{class_label})-[:INCLUDES*]->(x:{label} {{id: $id}}) WHERE c.name <> x.name
        RETURN DISTINCT c.name AS name ORDER BY name""", id=node_id)
    return [r["name"] for r in rows][:MAX_NAMES]


def describe_item(graph: Graph, item_id: str) -> dict:
    counts = graph.read("""
        MATCH (i:Item {id: $id})
        RETURN COUNT { (i)<-[:CREATES]-(:Recipe) } AS recipes,
               COUNT { (i)<-[:CREATES]-(h:Action) WHERE h:Mine OR h:Kill OR h:Shear } AS waysFromWorld,
               COUNT { (i)<-[:INCLUDES*0..]-(:Item)<-[:REQUIRES]-(:Recipe) } AS usedInRecipes""", id=item_id)[0]
    out = {"craftable": counts["recipes"] > 0, "recipes": counts["recipes"], "waysFromWorld": counts["waysFromWorld"],
           "usedInRecipes": counts["usedInRecipes"]}
    categories = classes_of(graph, "Item", "ItemClass", item_id)
    if categories:
        out["countsAs"] = categories
    out["next"] = "get_recipes, what_do_i_need, how_to_get, where_to_find and get_uses give the detail."
    return out


def describe_class(graph: Graph, label: str, node_id: str) -> dict:
    rows = graph.read(f"""
        MATCH (c:{label} {{id: $id}})-[:INCLUDES*]->(m) WHERE NOT m:ItemClass AND NOT m:MobClass AND NOT m:BiomeClass
        RETURN DISTINCT m.name AS name ORDER BY name""", id=node_id)
    names = [r["name"] for r in rows]
    return {"members": len(names), "examples": names[:MAX_NAMES], "next": "members lists them all."}


def describe_block(graph: Graph, block_id: str) -> dict:
    out: dict = {"drops": drops_of(graph, "Block", block_id)}
    out["generatedAsOre"] = bool(where_block(graph, block_id).get("where"))
    return out


def describe_mob(graph: Graph, mob_id: str) -> dict:
    spawn = graph.read("""
        MATCH (m:Mob {id: $id})
        RETURN COUNT { (m)<-[:SPAWNS]-(:Biome) } AS biomes, COUNT { (m)<-[:SPAWNS]-(:Structure) } AS structures""", id=mob_id)[0]
    out: dict = {"drops": drops_of(graph, "Mob", mob_id), "spawnsInBiomes": spawn["biomes"], "spawnsInStructures": spawn["structures"]}
    classes = classes_of(graph, "Mob", "MobClass", mob_id)
    if classes:
        out["countsAs"] = classes
    return out


def describe_biome(graph: Graph, biome_id: str) -> dict:
    b = graph.read("""
        MATCH (b:Biome {id: $id})
        RETURN b.temperature AS temperature, b.downfall AS downfall, b.hasPrecipitation AS precipitation,
               COUNT { (b)-[:SPAWNS]->(:Mob) } AS mobs, COUNT { (:Structure)-[:GENERATES_IN]->()-[:INCLUDES*0..]->(b) } AS structures""", id=biome_id)[0]
    dimension = [d for d, ids in dimension_biomes(graph).items() if biome_id in ids]
    out = {"dimension": dimension[0] if dimension else None, "temperature": b["temperature"], "downfall": b["downfall"],
           "hasPrecipitation": b["precipitation"], "mobKinds": b["mobs"], "structures": b["structures"]}
    classes = classes_of(graph, "Biome", "BiomeClass", biome_id)
    if classes:
        out["countsAs"] = classes
    out["next"] = "where_to_find lists the mobs, structures and ores here."
    return out


def describe_structure(graph: Graph, structure_id: str) -> dict:
    s = graph.read("""
        MATCH (s:Structure {id: $id})
        RETURN s.type AS type, s.step AS step, s.suppressedSpawns AS suppressed,
               COUNT { (s)-[:SPAWNS]->(:Mob) } AS mobs""", id=structure_id)[0]
    out = {"type": s["type"], "spawnsMobs": s["mobs"]}
    if s["suppressed"]:
        out["suppressesSpawnsOf"] = s["suppressed"]
    out["next"] = "where_to_find says which biomes it generates in."
    return out


DESCRIBE = {"item": describe_item, "category": lambda g, i: describe_class(g, "ItemClass", i), "block": describe_block,
            "mob": describe_mob, "biome": describe_biome, "structure": describe_structure,
            "mob class": lambda g, i: describe_class(g, "MobClass", i), "biome class": lambda g, i: describe_class(g, "BiomeClass", i)}


def wiki_sections(graph: Graph, kind: str, node_id: str) -> list[dict]:
    """The wiki sections that name this thing most (by MENTIONS), strongest first; empty if the wiki is not linked."""
    rows = graph.read(f"""
        MATCH (c:Chunk)-[m:MENTIONS]->(n:{LABEL_OF[kind]} {{id: $id}})
        MATCH (c)-[:PART_OF]->(s:Section)
        MATCH path = (p:Page)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION*0..]->(s)
        WITH s, p, [x IN nodes(path) WHERE x:Section | x.title] AS trail, sum(coalesce(m.linkCount, 0) * 3 + coalesce(m.count, 0)) AS weight
        ORDER BY weight DESC LIMIT $limit
        RETURN p.title AS page, trail, s.url AS sectionUrl""", id=node_id, limit=WIKI_SECTIONS)
    from .wiki import path_text
    return [{"section": path_text(r["page"], r["trail"]), "sectionUrl": r["sectionUrl"]} for r in rows]


def tool_describe(graph: Graph, args: dict) -> dict:
    """describe: a short view of anything with a name, by kind, and the wiki sections that discuss it."""
    ref = str(args.get("name", "")).strip()
    if not ref:
        return {"error": "Give the name of the thing to describe.", "type": "bad_argument"}
    things, err = resolve_things(graph, ref)
    if err:
        return err
    results = []
    for t in things:
        view = {"name": t["name"], "kind": t["kind"], **DESCRIBE[t["kind"]](graph, t["id"])}
        wiki = wiki_sections(graph, t["kind"], t["id"])
        if wiki:
            view["wiki"] = wiki
        results.append(view)
    out: dict = {"results": results}
    if any("wiki" in r for r in results):
        out["note"] = "'wiki' lists sections of the Minecraft Wiki that discuss this: call read_section with a sectionUrl to read one."
    return out
