"""Load the world from the Paper jar: mobs, biomes, structures, dimensions and ore (docs/ontology.md v0.10).

    python loader/load_world.py --dry-run     # parse and report, no database
    python loader/load_world.py               # load

Run it after `load_recipes.py --all --wipe` and `load_actions.py` (it needs the Block and Mob nodes they make) and before
`load_model.py`. The graph is rebuilt from source: this loader deletes and recreates the nodes it owns.

What is built:

    (Mob)                      every creature, not only those that drop something (90; `load_actions` makes 63)
    (MobClass)-[:INCLUDES]->(Mob | MobClass)          entity-type tags: Undead, Illager, Can Equip Saddle...
    (Biome {temperature, downfall, hasPrecipitation}) and (BiomeClass)-[:INCLUDES]->(Biome | BiomeClass)   biome tags
    (Biome)-[:SPAWNS {category, weight, min, max}]->(Mob)
    (Structure {type, step, terrainAdaptation, suppressedSpawns})-[:GENERATES_IN]->(Biome | BiomeClass)
    (Structure)-[:SPAWNS {category, boundingBox, weight, min, max}]->(Mob)
    (Dimension {minY, height})-[:HAS_BIOME]->(BiomeClass)                 the overworld, the nether and the end

    (Biome)-[:GENERATES {step}]->(PlacedFeature:Ore)-[:PLACES]->(Feature)-[:CREATES {replaces}]->(Block)
    (PlacedFeature)-[:IN_DIMENSION]->(Dimension)

An ore is made of two things in Minecraft's data, and both are shared. A Feature (a configured feature) is what is made: an
ore vein of a size that turns one kind of stone into one kind of ore. A PlacedFeature is where and how often: how many veins
per chunk, between which heights, in how rare a chunk. The same Feature has several PlacedFeatures (ore_iron is placed high
and middle), and a PlacedFeature is listed by many biomes. Only ore placements are loaded for now (40 of 262); trees,
vegetation, lakes and geodes use the same shape.

Heights: the data gives each end as an anchor (absolute, above the bottom of the world, or below the top). They are resolved
with the height of the dimension the feature is generated in (found through the biomes that list it) and clamped to the world,
so minY and maxY are real block heights. peakY is where a trapezoid is most common (its middle, before clamping).
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

from load_actions import tag_members  # noqa: E402
from name_index import ensure_name_index  # noqa: E402
from load_recipes import DEFAULT_JAR, display_name  # noqa: E402
from minecorp_agent.config import get_settings  # noqa: E402

NOT_CREATURES = {"armor_stand", "player", "mannequin"}          # have entity loot tables but are not creatures
DIMENSION_TAGS = {"minecraft:overworld": "minecraft:is_overworld", "minecraft:the_nether": "minecraft:is_nether",
                  "minecraft:the_end": "minecraft:is_end"}
ORE_TYPES = {"ore", "scattered_ore"}
STEPS = ["raw_generation", "lakes", "local_modifications", "underground_structures", "surface_structures", "strongholds",
         "underground_ores", "underground_decoration", "fluid_springs", "vegetal_decoration", "top_layer_modification"]

READ = {                                                         # folder in the jar -> key in the dict read_world returns
    "worldgen/biome": "biomes", "tags/worldgen/biome": "biome_tags", "tags/entity_type": "entity_tags",
    "worldgen/structure": "structures", "dimension_type": "dimension_types",
    "worldgen/placed_feature": "placed", "worldgen/configured_feature": "configured",
}


# --------------------------------------------------------------------------- read

def read_world(jar: Path) -> dict:
    """The JSON the world graph is built from. Ids are 'minecraft:<path>'; tags are {id: list of values}; entity_loot is the
    set of names with a top-level entity loot table (the nested ones are sheep colours, not creatures)."""
    out: dict = {key: {} for key in READ.values()}
    out["entity_loot"] = set()
    pattern = re.compile(r"data/minecraft/(" + "|".join(re.escape(f) for f in READ) + r")/(.+)\.json")
    with zipfile.ZipFile(jar) as z:
        for name in z.namelist():
            m = pattern.fullmatch(name)
            if m:
                data = json.loads(z.read(name))
                out[READ[m.group(1)]][f"minecraft:{m.group(2)}"] = data["values"] if m.group(1).startswith("tags/") else data
                continue
            m = re.fullmatch(r"data/minecraft/loot_table/entities/([^/]+)\.json", name)
            if m:
                out["entity_loot"].add(m.group(1))
    return out


# --------------------------------------------------------------------------- tags as classes

def classes(tags: dict, members: set, kind: str) -> tuple[list[dict], list[dict]]:
    """Tags as classes with INCLUDES, like item categories. Returns (class rows, includes rows).

    A class is kept when it holds at least one of `members` (directly or through other tags), so tags about boats and arrows
    are not loaded as mob classes. Class ids keep the '#' of a tag reference ('#minecraft:undead')."""
    cache: dict = {}
    kept = {t for t in tags if tag_members(t, tags, cache) & members}
    rows = [{"id": "#" + t, "name": display_name(t.replace("/", "_"))} for t in sorted(kept)]
    includes = []
    for t in sorted(kept):
        for v in tags[t]:
            if isinstance(v, dict):
                v = v.get("id", "")
            if v.startswith("#"):
                if v[1:] in kept:
                    includes.append({"class": "#" + t, "member": v})
            elif v in members:
                includes.append({"class": "#" + t, "member": v})
    return rows, includes


# --------------------------------------------------------------------------- ore placement

def short(t: str) -> str:
    return t.split(":", 1)[-1]


def parse_count(value) -> tuple[int, int] | None:
    """A count provider as (min, max): a number, a constant, or a uniform range. None for anything else."""
    if isinstance(value, int):
        return value, value
    if isinstance(value, dict):
        kind = short(value.get("type", ""))
        if kind == "constant":
            return value["value"], value["value"]
        if kind == "uniform":
            return value["min_inclusive"], value["max_inclusive"]
    return None


def anchor_text(anchor: dict) -> str:
    """'absolute:80', 'above_bottom:-80', 'below_top:7'."""
    (key, value), = anchor.items()
    return f"{key}:{value}"


def anchor_y(anchor: dict, min_y: int, height: int) -> int:
    """A vertical anchor as a block height in a dimension that starts at min_y and is `height` blocks tall."""
    if "absolute" in anchor:
        return anchor["absolute"]
    if "above_bottom" in anchor:
        return min_y + anchor["above_bottom"]
    if "below_top" in anchor:
        return min_y + height - 1 - anchor["below_top"]
    raise ValueError(f"unknown vertical anchor {anchor}")


def parse_placement(placement: list[dict]) -> dict:
    """What the placement modifiers of an ore say: count per chunk, rarity, and the height range as anchors."""
    out = {"countMin": 1, "countMax": 1, "rarityChance": None, "shape": None, "minAnchor": None, "maxAnchor": None, "plateau": None}
    for m in placement:
        kind = short(m["type"])
        if kind == "count":
            parsed = parse_count(m["count"])
            if parsed:
                out["countMin"], out["countMax"] = parsed
        elif kind == "rarity_filter":
            out["rarityChance"] = m["chance"]
        elif kind == "height_range":
            h = m["height"]
            if "type" in h:
                out["shape"] = short(h["type"])
                out["minAnchor"], out["maxAnchor"] = h.get("min_inclusive"), h.get("max_inclusive")
                out["plateau"] = h.get("plateau")
            else:                                               # a single anchor
                out["shape"], out["minAnchor"], out["maxAnchor"] = "constant", h, h
    return out


def resolve_heights(p: dict, dimension: dict | None) -> dict:
    """minY, maxY and peakY as block heights, clamped to the world. None when the dimension or the range is not known."""
    keys = {"minY": None, "maxY": None, "peakY": None}
    if not dimension or not p["minAnchor"] or not p["maxAnchor"]:
        return keys
    low = anchor_y(p["minAnchor"], dimension["minY"], dimension["height"])
    high = anchor_y(p["maxAnchor"], dimension["minY"], dimension["height"])
    bottom, top = dimension["minY"], dimension["minY"] + dimension["height"] - 1
    clamp = lambda y: max(bottom, min(top, y))  # noqa: E731
    keys.update(minY=clamp(low), maxY=clamp(high))
    if p["shape"] == "trapezoid":
        keys["peakY"] = clamp((low + high) // 2)
    return keys


def ore_rows(w: dict, biome_dimension: dict, dimensions: dict, listed_by: dict, stats: Counter) -> dict:
    """Features, placed features and their relationships, for the ore placements only."""
    features, creates, placed_rows, places = {}, [], [], []
    for pid, pf in sorted(w["placed"].items()):
        ref = pf["feature"]
        if not isinstance(ref, str):
            stats["placed features with an inline feature (skipped)"] += 1
            continue
        cf = w["configured"].get(ref)
        if not cf or short(cf["type"]) not in ORE_TYPES:
            continue
        if ref not in features:
            config = cf["config"]
            features[ref] = {"id": ref, "name": display_name(ref), "type": short(cf["type"]), "size": config["size"],
                             "discardChance": config.get("discard_chance_on_air_exposure", 0.0)}
            for t in config["targets"]:
                rule = t["target"]
                replaces = "#" + rule["tag"] if "tag" in rule else rule.get("block", "")
                creates.append({"feature": ref, "block": t["state"]["Name"], "replaces": replaces})
        p = parse_placement(pf["placement"])
        dims = {biome_dimension[b] for b in listed_by.get(pid, ()) if b in biome_dimension}
        if len(dims) > 1:
            stats["ore placements generated in more than one dimension (heights not resolved)"] += 1
        dimension_id = next(iter(dims)) if len(dims) == 1 else None
        placed_rows.append({"id": pid, "name": display_name(pid), "feature": ref, **p,
                            **resolve_heights(p, dimensions.get(dimension_id)), "dimension": dimension_id,
                            "biomes": len(listed_by.get(pid, ()))})
        places.append({"placed": pid, "feature": ref})
    return {"features": list(features.values()), "creates": creates, "placed": placed_rows, "places": places}


# --------------------------------------------------------------------------- build

def biome_refs(value, biome_ids: set, kept_classes: set) -> list[str]:
    """What a structure's `biomes` field names: a biome tag ('#...'), a biome id, or a list of them, as node ids."""
    out = []
    for v in value if isinstance(value, list) else [value]:
        if isinstance(v, str) and v.startswith("#") and v[1:] in kept_classes:
            out.append(v)
        elif isinstance(v, str) and v in biome_ids:
            out.append(v)
    return out


def build(jar: Path) -> dict:
    """Turn the jar into node and relationship rows for the world."""
    w = read_world(jar)
    stats: Counter = Counter()
    biomes, structures = w["biomes"], w["structures"]

    # ---- mobs: every creature the data names
    spawned = {e["type"] for b in biomes.values() for lst in b.get("spawners", {}).values() for e in lst}
    spawned |= {e["type"] for s in structures.values() for ov in s.get("spawn_overrides", {}).values() for e in ov.get("spawns", [])}
    mob_ids = ({f"minecraft:{n}" for n in w["entity_loot"]} | spawned) - {f"minecraft:{n}" for n in NOT_CREATURES}
    mob_classes, mob_includes = classes(w["entity_tags"], mob_ids, "Mob")

    # ---- biomes
    biome_ids = set(biomes)
    biome_classes, biome_includes = classes(w["biome_tags"], biome_ids, "Biome")
    kept = {c["id"][1:] for c in biome_classes}
    cache: dict = {}
    biome_dimension = {}
    for dim, tag in DIMENSION_TAGS.items():
        for b in tag_members(tag, w["biome_tags"], cache) & biome_ids:
            biome_dimension[b] = dim
    biome_rows, spawns = [], []
    for bid, b in sorted(biomes.items()):
        row = {"id": bid, "name": display_name(bid), "temperature": b.get("temperature"), "downfall": b.get("downfall"),
               "hasPrecipitation": b.get("has_precipitation"), "temperatureModifier": b.get("temperature_modifier"),
               "spawnProbability": b.get("creature_spawn_probability")}
        biome_rows.append(row)
        for category, entries in sorted(b.get("spawners", {}).items()):
            for e in entries:
                spawns.append({"biome": bid, "mob": e["type"], "category": category, "weight": e["weight"],
                               "min": e["minCount"], "max": e["maxCount"]})

    # ---- dimensions
    dimensions = {}
    for dim, tag in DIMENSION_TAGS.items():
        d = w["dimension_types"].get(dim)
        if d:
            dimensions[dim] = {"id": dim, "name": display_name(dim), "minY": d["min_y"], "height": d["height"],
                               "logicalHeight": d.get("logical_height"), "hasSkylight": d.get("has_skylight"),
                               "hasCeiling": d.get("has_ceiling"), "tag": "#" + tag if tag in kept else None}
    has_biome = [{"dimension": d["id"], "class": d["tag"]} for d in dimensions.values() if d["tag"]]

    # ---- structures
    structure_rows, generates_in, structure_spawns = [], [], []
    for sid, s in sorted(structures.items()):
        suppressed = sorted(c for c, ov in s.get("spawn_overrides", {}).items() if not ov.get("spawns"))
        structure_rows.append({"id": sid, "name": display_name(sid), "type": short(s["type"]), "step": s.get("step"),
                               "terrainAdaptation": s.get("terrain_adaptation"), "suppressedSpawns": suppressed})
        for ref in biome_refs(s.get("biomes", []), biome_ids, kept):
            generates_in.append({"structure": sid, "biome": ref})
        for category, ov in sorted(s.get("spawn_overrides", {}).items()):
            for e in ov.get("spawns", []):
                structure_spawns.append({"structure": sid, "mob": e["type"], "category": category, "boundingBox": ov.get("bounding_box"),
                                         "weight": e["weight"], "min": e["minCount"], "max": e["maxCount"]})

    # ---- ore
    listed_by: dict = {}
    generates = []
    for bid, b in sorted(biomes.items()):
        for index, step in enumerate(b.get("features", [])):
            for f in step:
                listed_by.setdefault(f, set()).add(bid)
    ores = ore_rows(w, biome_dimension, dimensions, listed_by, stats)
    ore_ids = {p["id"] for p in ores["placed"]}
    for bid, b in sorted(biomes.items()):
        for index, step in enumerate(b.get("features", [])):
            for f in step:
                if f in ore_ids:
                    generates.append({"biome": bid, "placed": f, "step": STEPS[index] if index < len(STEPS) else f"step_{index}"})

    stats.update({"mobs": len(mob_ids), "mob classes": len(mob_classes), "biomes": len(biome_rows), "biome classes": len(biome_classes),
                  "structures": len(structure_rows), "dimensions": len(dimensions), "ore features": len(ores["features"]),
                  "ore placements": len(ores["placed"]), "biome spawn relationships": len(spawns),
                  "structure spawn relationships": len(structure_spawns), "structure to biome links": len(generates_in),
                  "biome to ore placement links": len(generates)})
    return {"mobs": [{"id": m, "name": display_name(m)} for m in sorted(mob_ids)], "mobClasses": mob_classes, "mobIncludes": mob_includes,
            "biomes": biome_rows, "biomeClasses": biome_classes, "biomeIncludes": biome_includes, "spawns": spawns,
            "dimensions": list(dimensions.values()), "hasBiome": has_biome, "structures": structure_rows, "generatesIn": generates_in,
            "structureSpawns": structure_spawns, "generates": generates, **ores, "stats": stats}


# --------------------------------------------------------------------------- Neo4j

OWNED = ("Biome", "BiomeClass", "MobClass", "Structure", "Dimension", "Feature", "PlacedFeature")


def write(g: dict, uri: str, user: str, password: str, database: str) -> None:
    """Write the rows in one transaction: the nodes this loader owns are deleted and recreated, mobs are merged."""
    def work(tx) -> None:
        def run(query: str, **params) -> None:
            tx.run(query, **params).consume()

        run("MATCH (n) WHERE " + " OR ".join(f"n:{label}" for label in OWNED) + " DETACH DELETE n")
        run("UNWIND $rows AS r MERGE (m:Mob {id: r.id}) SET m.name = coalesce(m.name, r.name)", rows=g["mobs"])
        run("UNWIND $rows AS r CREATE (:MobClass {id: r.id, name: r.name})", rows=g["mobClasses"])
        run("UNWIND $rows AS r MATCH (c:MobClass {id: r.class}) MATCH (m:Mob {id: r.member}) CREATE (c)-[:INCLUDES]->(m)",
            rows=[r for r in g["mobIncludes"] if not r["member"].startswith("#")])
        run("UNWIND $rows AS r MATCH (c:MobClass {id: r.class}), (m:MobClass {id: r.member}) CREATE (c)-[:INCLUDES]->(m)",
            rows=[r for r in g["mobIncludes"] if r["member"].startswith("#")])
        run("""UNWIND $rows AS r CREATE (:Biome {id: r.id, name: r.name, temperature: r.temperature, downfall: r.downfall,
                                              hasPrecipitation: r.hasPrecipitation, temperatureModifier: r.temperatureModifier,
                                              spawnProbability: r.spawnProbability})""", rows=g["biomes"])
        run("UNWIND $rows AS r CREATE (:BiomeClass {id: r.id, name: r.name})", rows=g["biomeClasses"])
        run("UNWIND $rows AS r MATCH (c:BiomeClass {id: r.class}) MATCH (b:Biome {id: r.member}) CREATE (c)-[:INCLUDES]->(b)",
            rows=[r for r in g["biomeIncludes"] if not r["member"].startswith("#")])
        run("UNWIND $rows AS r MATCH (c:BiomeClass {id: r.class}), (b:BiomeClass {id: r.member}) CREATE (c)-[:INCLUDES]->(b)",
            rows=[r for r in g["biomeIncludes"] if r["member"].startswith("#")])
        run("""UNWIND $rows AS r MATCH (b:Biome {id: r.biome}), (m:Mob {id: r.mob})
               CREATE (b)-[:SPAWNS {category: r.category, weight: r.weight, min: r.min, max: r.max}]->(m)""", rows=g["spawns"])
        run("""UNWIND $rows AS r CREATE (:Dimension {id: r.id, name: r.name, minY: r.minY, height: r.height,
                                                    logicalHeight: r.logicalHeight, hasSkylight: r.hasSkylight, hasCeiling: r.hasCeiling})""",
            rows=g["dimensions"])
        run("UNWIND $rows AS r MATCH (d:Dimension {id: r.dimension}), (c:BiomeClass {id: r.class}) CREATE (d)-[:HAS_BIOME]->(c)", rows=g["hasBiome"])
        run("""UNWIND $rows AS r CREATE (:Structure {id: r.id, name: r.name, type: r.type, step: r.step,
                                                   terrainAdaptation: r.terrainAdaptation, suppressedSpawns: r.suppressedSpawns})""",
            rows=g["structures"])
        run("UNWIND $rows AS r MATCH (s:Structure {id: r.structure}), (b:BiomeClass {id: r.biome}) CREATE (s)-[:GENERATES_IN]->(b)",
            rows=[r for r in g["generatesIn"] if r["biome"].startswith("#")])
        run("UNWIND $rows AS r MATCH (s:Structure {id: r.structure}), (b:Biome {id: r.biome}) CREATE (s)-[:GENERATES_IN]->(b)",
            rows=[r for r in g["generatesIn"] if not r["biome"].startswith("#")])
        run("""UNWIND $rows AS r MATCH (s:Structure {id: r.structure}), (m:Mob {id: r.mob})
               CREATE (s)-[:SPAWNS {category: r.category, boundingBox: r.boundingBox, weight: r.weight, min: r.min, max: r.max}]->(m)""",
            rows=g["structureSpawns"])
        run("UNWIND $rows AS r CREATE (:Feature {id: r.id, name: r.name, type: r.type, size: r.size, discardChance: r.discardChance})",
            rows=g["features"])
        run("""UNWIND $rows AS r MATCH (f:Feature {id: r.feature}), (b:Block {id: r.block})
               CREATE (f)-[:CREATES {replaces: r.replaces}]->(b)""", rows=g["creates"])
        run("""UNWIND $rows AS r CREATE (:PlacedFeature:Ore {id: r.id, name: r.name, countMin: r.countMin, countMax: r.countMax,
                                                         rarityChance: r.rarityChance, shape: r.shape, minAnchor: r.minAnchor,
                                                         maxAnchor: r.maxAnchor, plateau: r.plateau, minY: r.minY, maxY: r.maxY,
                                                         peakY: r.peakY, biomes: r.biomes})""",
            rows=[{**r, "minAnchor": r["minAnchor"] and anchor_text(r["minAnchor"]), "maxAnchor": r["maxAnchor"] and anchor_text(r["maxAnchor"])}
                  for r in g["placed"]])
        run("UNWIND $rows AS r MATCH (p:PlacedFeature {id: r.placed}), (f:Feature {id: r.feature}) CREATE (p)-[:PLACES]->(f)", rows=g["places"])
        run("UNWIND $rows AS r MATCH (p:PlacedFeature {id: r.id}), (d:Dimension {id: r.dimension}) CREATE (p)-[:IN_DIMENSION]->(d)",
            rows=[r for r in g["placed"] if r["dimension"]])
        run("UNWIND $rows AS r MATCH (b:Biome {id: r.biome}), (p:PlacedFeature {id: r.placed}) CREATE (b)-[:GENERATES {step: r.step}]->(p)",
            rows=g["generates"])

    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as session:
            for label in (*OWNED, "Mob"):                          # schema changes cannot share a transaction with the data
                session.run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE").consume()
            ensure_name_index(session)
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
