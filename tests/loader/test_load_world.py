"""Unit tests for loader/load_world.py: mobs, classes from tags, biomes, structures, dimensions and the resolution of ore heights.

Everything runs on a small jar made here, so no server jar and no database are needed. The real jar is checked in
test_real_jar_world.py and the graph in test_world_integration.py."""
from __future__ import annotations

import json
import zipfile
from collections import Counter

import pytest

import load_world as lw

OVERWORLD = {"id": "minecraft:overworld", "minY": -64, "height": 384}
NETHER = {"id": "minecraft:the_nether", "minY": 0, "height": 256}


def make_jar(path, files: dict):
    """A jar holding data/minecraft/<name>.json for each entry of files."""
    jar = path / "world.jar"
    with zipfile.ZipFile(jar, "w") as z:
        for name, data in files.items():
            z.writestr(f"data/minecraft/{name}.json", json.dumps(data))
    return jar


def spawner(mob, weight=1, low=1, high=1):
    return {"type": f"minecraft:{mob}", "weight": weight, "minCount": low, "maxCount": high}


def biome(spawners=None, features=None, **extra):
    categories = {c: [] for c in ("ambient", "axolotls", "creature", "misc", "monster", "underground_water_creature", "water_ambient", "water_creature")}
    categories.update(spawners or {})
    return {"temperature": 0.8, "downfall": 0.4, "has_precipitation": True, "spawners": categories, "features": features or [], **extra}


def ore(size=9, targets=None, kind="ore"):
    targets = targets or [("minecraft:iron_ore", {"tag": "minecraft:stone_ore_replaceables"}),
                          ("minecraft:deepslate_iron_ore", {"tag": "minecraft:deepslate_ore_replaceables"})]
    return {"type": f"minecraft:{kind}", "config": {"size": size, "discard_chance_on_air_exposure": 0.0, "targets": [
        {"state": {"Name": block}, "target": {"predicate_type": "minecraft:tag_match" if "tag" in rule else "minecraft:block_match", **rule}}
        for block, rule in targets]}}


def placed(feature, *modifiers):
    return {"feature": feature, "placement": list(modifiers)}


def count(n):
    return {"type": "minecraft:count", "count": n}


def height(kind, low, high, **extra):
    return {"type": "minecraft:height_range", "height": {"type": f"minecraft:{kind}", "min_inclusive": low, "max_inclusive": high, **extra}}


BASE = {"in_square": {"type": "minecraft:in_square"}, "biome": {"type": "minecraft:biome"}}


def steps(**by_index):
    """11 generation steps; steps(**{"6": [...]}) is awkward, so pass index->list as strings: steps(s6=[...])."""
    out = [[] for _ in range(11)]
    for key, value in by_index.items():
        out[int(key[1:])] = value
    return out


FILES = {
    # creatures: zombie, creeper and villager have loot tables (villager's is empty), bat is only a spawner, the rest are not creatures
    "loot_table/entities/zombie": {"pools": []}, "loot_table/entities/creeper": {"pools": []}, "loot_table/entities/villager": {"pools": []},
    "loot_table/entities/player": {"pools": []}, "loot_table/entities/armor_stand": {"pools": []}, "loot_table/entities/mannequin": {"pools": []},
    "loot_table/entities/sheep": {"pools": []}, "loot_table/entities/sheep/black": {"pools": []},
    "tags/entity_type/zombies": {"values": ["minecraft:zombie"]},
    "tags/entity_type/undead": {"values": ["#minecraft:zombies", "minecraft:skeleton", "minecraft:arrow"]},
    "tags/entity_type/arrows": {"values": ["minecraft:arrow"]},
    "tags/entity_type/boat": {"values": ["minecraft:oak_boat"]},
    "tags/entity_type/can_equip_saddle": {"values": ["minecraft:sheep"]},
    # biomes
    "worldgen/biome/plains": biome({"creature": [spawner("sheep", 12, 4, 4), spawner("pig", 10, 4, 4)], "monster": [spawner("zombie", 95, 4, 4)]},
                                   steps(s6=["minecraft:ore_iron_upper", "minecraft:ore_iron_middle", "minecraft:ore_diamond", "minecraft:ore_coal_upper"],
                                         s9=["minecraft:tree_oak_placed"]), creature_spawn_probability=0.1),
    "worldgen/biome/desert": biome({"monster": [spawner("zombie", 19, 4, 4)], "ambient": [spawner("bat", 10, 8, 8)]}, steps(s6=["minecraft:ore_iron_upper"]),
                                   has_precipitation=False, temperature=2.0, temperature_modifier="none"),
    "worldgen/biome/nether_wastes": biome({"monster": [spawner("zombified_piglin", 100, 4, 4)]},
                                          steps(s6=["minecraft:ore_gold_nether", "minecraft:ore_multi"], s7=["minecraft:ore_debris"])),
    "worldgen/biome/end_highlands": biome(),
    "tags/worldgen/biome/is_overworld": {"values": ["minecraft:plains", "minecraft:desert"]},
    "tags/worldgen/biome/is_nether": {"values": ["minecraft:nether_wastes"]},
    "tags/worldgen/biome/is_end": {"values": []},
    "tags/worldgen/biome/is_dry": {"values": ["minecraft:desert"]},
    "tags/worldgen/biome/has_structure/village_plains": {"values": ["minecraft:plains"]},
    "tags/worldgen/biome/has_structure/pyramid": {"values": ["#minecraft:is_dry"]},
    "tags/worldgen/biome/has_structure/fortress": {"values": ["minecraft:nether_wastes"]},
    # structures
    "worldgen/structure/village_plains": {"type": "minecraft:jigsaw", "biomes": "#minecraft:has_structure/village_plains", "step": "surface_structures",
                                          "terrain_adaptation": "beard_thin", "spawn_overrides": {}},
    "worldgen/structure/pyramid": {"type": "minecraft:desert_pyramid", "biomes": "#minecraft:has_structure/pyramid", "step": "surface_structures",
                                   "spawn_overrides": {"monster": {"bounding_box": "piece", "spawns": [spawner("zombie", 2, 1, 3)]},
                                                       "creature": {"bounding_box": "full", "spawns": []}}},
    "worldgen/structure/fortress": {"type": "minecraft:fortress", "biomes": "#minecraft:has_structure/fortress", "step": "underground_decoration",
                                    "spawn_overrides": {"monster": {"bounding_box": "piece", "spawns": [spawner("zombified_piglin", 5, 1, 4)]}}},
    "worldgen/structure/lonely": {"type": "minecraft:jigsaw", "biomes": ["minecraft:plains", "#minecraft:nope"], "step": "surface_structures", "spawn_overrides": {}},
    # dimensions
    "dimension_type/overworld": {"min_y": -64, "height": 384, "logical_height": 384, "has_skylight": True, "has_ceiling": False},
    "dimension_type/overworld_caves": {"min_y": -64, "height": 384, "logical_height": 384, "has_skylight": True, "has_ceiling": True},
    "dimension_type/the_nether": {"min_y": 0, "height": 256, "logical_height": 128, "has_skylight": False, "has_ceiling": True},
    "dimension_type/the_end": {"min_y": 0, "height": 256, "logical_height": 256, "has_skylight": True, "has_ceiling": False},
    # features
    "worldgen/configured_feature/ore_iron": ore(9),
    "worldgen/configured_feature/ore_diamond_small": ore(4, [("minecraft:diamond_ore", {"tag": "minecraft:stone_ore_replaceables"})]),
    "worldgen/configured_feature/ore_coal": ore(17, [("minecraft:coal_ore", {"tag": "minecraft:stone_ore_replaceables"})]),
    "worldgen/configured_feature/ore_gold_nether": ore(10, [("minecraft:nether_gold_ore", {"block": "minecraft:netherrack"})]),
    "worldgen/configured_feature/ore_debris": ore(3, [("minecraft:ancient_debris", {"block": "minecraft:netherrack"})], kind="scattered_ore"),
    "worldgen/configured_feature/ore_both": ore(5, [("minecraft:stone", {"tag": "minecraft:base_stone_overworld"})]),
    "worldgen/configured_feature/tree_oak": {"type": "minecraft:tree", "config": {}},
    # placements
    "worldgen/placed_feature/ore_iron_upper": placed("minecraft:ore_iron", count(90), BASE["in_square"], height("trapezoid", {"absolute": 80}, {"absolute": 384}), BASE["biome"]),
    "worldgen/placed_feature/ore_iron_middle": placed("minecraft:ore_iron", count(10), BASE["in_square"], height("trapezoid", {"absolute": -24}, {"absolute": 56}), BASE["biome"]),
    "worldgen/placed_feature/ore_diamond": placed("minecraft:ore_diamond_small", count(7), BASE["in_square"],
                                                  height("trapezoid", {"above_bottom": -80}, {"above_bottom": 80}), BASE["biome"]),
    "worldgen/placed_feature/ore_coal_upper": placed("minecraft:ore_coal", count(30), BASE["in_square"], height("uniform", {"absolute": 136}, {"below_top": 0}), BASE["biome"]),
    "worldgen/placed_feature/ore_gold_nether": placed("minecraft:ore_gold_nether", count({"type": "minecraft:uniform", "min_inclusive": 0, "max_inclusive": 1}),
                                                      {"type": "minecraft:rarity_filter", "chance": 6}, BASE["in_square"],
                                                      height("uniform", {"above_bottom": 10}, {"below_top": 10}), BASE["biome"]),
    "worldgen/placed_feature/ore_debris": placed("minecraft:ore_debris", BASE["in_square"], height("trapezoid", {"above_bottom": 8}, {"absolute": 24}, plateau=8), BASE["biome"]),
    "worldgen/placed_feature/ore_multi": placed("minecraft:ore_both", count(4), BASE["in_square"], height("uniform", {"absolute": 0}, {"absolute": 50}), BASE["biome"]),
    "worldgen/placed_feature/tree_oak_placed": placed("minecraft:tree_oak", count(5)),
    "worldgen/placed_feature/ore_inline": {"feature": {"type": "minecraft:ore", "config": {"size": 1, "targets": []}}, "placement": []},
}
# ore_multi is listed by an overworld biome and a nether biome: the dimension cannot be decided
FILES["worldgen/biome/desert"]["features"][6].append("minecraft:ore_multi")


@pytest.fixture(scope="module")
def jar(tmp_path_factory):
    return make_jar(tmp_path_factory.mktemp("world"), FILES)


@pytest.fixture(scope="module")
def g(jar):
    return lw.build(jar)


def by_id(rows, key="id"):
    return {r[key]: r for r in rows}


# --------------------------------------------------------------------------- reading

class TestReadWorld:
    def test_ids_carry_the_minecraft_namespace(self, jar):
        w = lw.read_world(jar)
        assert "minecraft:plains" in w["biomes"] and "minecraft:village_plains" in w["structures"] and "minecraft:ore_iron" in w["configured"]

    def test_tags_are_their_list_of_values_and_nested_folders_keep_their_path(self, jar):
        w = lw.read_world(jar)
        assert w["biome_tags"]["minecraft:has_structure/pyramid"] == ["#minecraft:is_dry"]
        assert w["entity_tags"]["minecraft:undead"] == ["#minecraft:zombies", "minecraft:skeleton", "minecraft:arrow"]

    def test_only_top_level_entity_loot_tables_count(self, jar):
        assert lw.read_world(jar)["entity_loot"] == {"zombie", "creeper", "villager", "player", "armor_stand", "mannequin", "sheep"}

    def test_dimension_types_are_read(self, jar):
        assert lw.read_world(jar)["dimension_types"]["minecraft:the_nether"]["min_y"] == 0

    def test_a_jar_with_none_of_it_reads_as_empty(self, tmp_path):
        w = lw.read_world(make_jar(tmp_path, {"recipe/stick": {}}))
        assert w["biomes"] == {} and w["entity_loot"] == set() and w["placed"] == {}


# --------------------------------------------------------------------------- mobs and mob classes

class TestMobs:
    def test_every_creature_the_data_names_is_a_mob(self, g):
        assert {m["id"] for m in g["mobs"]} == {"minecraft:zombie", "minecraft:creeper", "minecraft:villager", "minecraft:sheep", "minecraft:bat",
                                                "minecraft:pig", "minecraft:zombified_piglin"}

    def test_a_creature_with_an_empty_loot_table_is_still_a_mob(self, g):
        assert "minecraft:villager" in {m["id"] for m in g["mobs"]}

    def test_a_creature_that_only_spawns_is_a_mob(self, g):
        assert "minecraft:bat" in {m["id"] for m in g["mobs"]}

    @pytest.mark.parametrize("name", ["player", "armor_stand", "mannequin", "black"])
    def test_things_that_are_not_creatures_are_not_mobs(self, g, name):
        assert f"minecraft:{name}" not in {m["id"] for m in g["mobs"]}

    def test_names_are_readable(self, g):
        assert by_id(g["mobs"])["minecraft:zombified_piglin"]["name"] == "Zombified Piglin"


class TestClasses:
    def test_a_tag_with_mobs_in_it_is_a_class_with_the_hash_in_its_id(self, g):
        assert {c["id"] for c in g["mobClasses"]} == {"#minecraft:zombies", "#minecraft:undead", "#minecraft:can_equip_saddle"}

    def test_tags_about_boats_and_arrows_are_dropped(self, g):
        ids = {c["id"] for c in g["mobClasses"]}
        assert "#minecraft:arrows" not in ids and "#minecraft:boat" not in ids

    def test_a_class_includes_its_mobs_and_its_nested_classes_but_not_non_mobs(self, g):
        inc = {(r["class"], r["member"]) for r in g["mobIncludes"]}
        assert inc == {("#minecraft:zombies", "minecraft:zombie"), ("#minecraft:undead", "#minecraft:zombies"),
                       ("#minecraft:can_equip_saddle", "minecraft:sheep")}, "skeleton and arrow are not mobs here"

    def test_a_class_name_is_readable(self, g):
        assert by_id(g["mobClasses"])["#minecraft:can_equip_saddle"]["name"] == "Can Equip Saddle"

    def test_a_class_that_only_reaches_a_mob_through_another_tag_is_kept(self):
        rows, includes = lw.classes({"minecraft:a": ["#minecraft:b"], "minecraft:b": ["minecraft:x"]}, {"minecraft:x"}, "Mob")
        assert {r["id"] for r in rows} == {"#minecraft:a", "#minecraft:b"} and {("#minecraft:a", "#minecraft:b"), ("#minecraft:b", "minecraft:x")} == {
            (r["class"], r["member"]) for r in includes}

    def test_a_cycle_of_tags_does_not_hang(self):
        """Real tags have no cycles; this only checks that a bad one cannot hang the loader (the shared tag walk is order-dependent here)."""
        rows, _ = lw.classes({"minecraft:a": ["#minecraft:b", "minecraft:x"], "minecraft:b": ["#minecraft:a"]}, {"minecraft:x"}, "Mob")
        assert "#minecraft:a" in {r["id"] for r in rows}

    def test_a_slash_in_a_tag_path_gives_a_clean_name(self):
        rows, _ = lw.classes({"minecraft:has_structure/village_plains": ["minecraft:plains"]}, {"minecraft:plains"}, "Biome")
        assert rows[0]["name"] == "Has Structure Village Plains"


# --------------------------------------------------------------------------- biomes

class TestBiomes:
    def test_a_biome_has_its_climate(self, g):
        plains, desert = by_id(g["biomes"])["minecraft:plains"], by_id(g["biomes"])["minecraft:desert"]
        assert (plains["name"], plains["temperature"], plains["downfall"], plains["hasPrecipitation"], plains["spawnProbability"]) == ("Plains", 0.8, 0.4, True, 0.1)
        assert (desert["temperature"], desert["hasPrecipitation"], desert["temperatureModifier"]) == (2.0, False, "none")

    def test_optional_fields_are_none_when_absent(self, g):
        assert by_id(g["biomes"])["minecraft:desert"]["spawnProbability"] is None and by_id(g["biomes"])["minecraft:plains"]["temperatureModifier"] is None

    def test_every_spawner_entry_is_a_spawn_with_its_category_and_counts(self, g):
        sheep = next(s for s in g["spawns"] if s["biome"] == "minecraft:plains" and s["mob"] == "minecraft:sheep")
        assert sheep == {"biome": "minecraft:plains", "mob": "minecraft:sheep", "category": "creature", "weight": 12, "min": 4, "max": 4}
        assert len(g["spawns"]) == 6, "sheep, pig, zombie in plains; zombie, bat in desert; zombified piglin in the nether"

    def test_the_smallest_and_largest_group_are_not_mixed_up(self, tmp_path):
        files = {"loot_table/entities/wolf": {"pools": []},
                 "worldgen/biome/taiga": biome({"creature": [spawner("wolf", 8, 2, 5)], "monster": [spawner("wolf", 1, 7, 7)]})}
        rows = {s["category"]: s for s in lw.build(make_jar(tmp_path, files))["spawns"]}
        assert (rows["creature"]["min"], rows["creature"]["max"], rows["creature"]["weight"]) == (2, 5, 8)
        assert (rows["monster"]["min"], rows["monster"]["max"]) == (7, 7)

    def test_a_biome_with_nothing_to_spawn_has_no_spawn_rows(self, g):
        assert not [s for s in g["spawns"] if s["biome"] == "minecraft:end_highlands"]

    def test_every_spawned_mob_is_a_mob(self, g):
        mobs = {m["id"] for m in g["mobs"]}
        assert {s["mob"] for s in g["spawns"]} <= mobs

    def test_biome_classes_come_from_the_biome_tags_with_their_nesting(self, g):
        assert {c["id"] for c in g["biomeClasses"]} == {"#minecraft:is_overworld", "#minecraft:is_nether", "#minecraft:is_dry",
                                                       "#minecraft:has_structure/village_plains", "#minecraft:has_structure/pyramid", "#minecraft:has_structure/fortress"}
        inc = {(r["class"], r["member"]) for r in g["biomeIncludes"]}
        assert ("#minecraft:has_structure/pyramid", "#minecraft:is_dry") in inc and ("#minecraft:is_dry", "minecraft:desert") in inc

    def test_a_tag_with_no_biomes_is_not_a_class(self, g):
        assert "#minecraft:is_end" not in {c["id"] for c in g["biomeClasses"]}


class TestDimensions:
    def test_the_three_dimensions_with_their_height(self, g):
        d = by_id(g["dimensions"])
        assert set(d) == {"minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"}
        assert (d["minecraft:overworld"]["minY"], d["minecraft:overworld"]["height"]) == (-64, 384)
        assert (d["minecraft:the_nether"]["minY"], d["minecraft:the_nether"]["height"], d["minecraft:the_nether"]["logicalHeight"]) == (0, 256, 128)
        assert d["minecraft:the_nether"]["hasCeiling"] is True and d["minecraft:the_nether"]["hasSkylight"] is False

    def test_the_odd_overworld_caves_type_is_left_out(self, g):
        assert "minecraft:overworld_caves" not in {d["id"] for d in g["dimensions"]}

    def test_a_dimension_has_the_biomes_of_its_tag(self, g):
        assert {(r["dimension"], r["class"]) for r in g["hasBiome"]} == {("minecraft:overworld", "#minecraft:is_overworld"), ("minecraft:the_nether", "#minecraft:is_nether")}


# --------------------------------------------------------------------------- structures

class TestStructures:
    def test_a_structure_has_its_type_step_and_terrain(self, g):
        v = by_id(g["structures"])["minecraft:village_plains"]
        assert (v["name"], v["type"], v["step"], v["terrainAdaptation"], v["suppressedSpawns"]) == ("Village Plains", "jigsaw", "surface_structures", "beard_thin", [])

    def test_a_structure_generates_in_the_biome_class_its_tag_names(self, g):
        links = {(r["structure"], r["biome"]) for r in g["generatesIn"]}
        assert ("minecraft:village_plains", "#minecraft:has_structure/village_plains") in links
        assert ("minecraft:pyramid", "#minecraft:has_structure/pyramid") in links

    def test_a_list_of_biomes_gives_the_biomes_that_exist_and_skips_unknown_tags(self, g):
        links = {r["biome"] for r in g["generatesIn"] if r["structure"] == "minecraft:lonely"}
        assert links == {"minecraft:plains"}

    def test_spawn_overrides_become_spawns_with_the_bounding_box(self, g):
        s = [r for r in g["structureSpawns"] if r["structure"] == "minecraft:pyramid"]
        assert s == [{"structure": "minecraft:pyramid", "mob": "minecraft:zombie", "category": "monster", "boundingBox": "piece", "weight": 2, "min": 1, "max": 3}]

    def test_an_override_with_an_empty_list_means_no_natural_spawns_of_that_category(self, g):
        assert by_id(g["structures"])["minecraft:pyramid"]["suppressedSpawns"] == ["creature"]

    def test_biome_refs_forms(self):
        assert lw.biome_refs("#minecraft:t", {"minecraft:b"}, {"minecraft:t"}) == ["#minecraft:t"]
        assert lw.biome_refs("minecraft:b", {"minecraft:b"}, set()) == ["minecraft:b"]
        assert lw.biome_refs(["minecraft:b", "#minecraft:x", "minecraft:zz"], {"minecraft:b"}, set()) == ["minecraft:b"]


# --------------------------------------------------------------------------- vertical anchors and heights

class TestAnchors:
    def test_absolute(self):
        assert lw.anchor_y({"absolute": 80}, -64, 384) == 80

    def test_above_the_bottom_of_the_world(self):
        assert lw.anchor_y({"above_bottom": 0}, -64, 384) == -64 and lw.anchor_y({"above_bottom": 10}, 0, 256) == 10
        assert lw.anchor_y({"above_bottom": -80}, -64, 384) == -144

    def test_below_the_top_of_the_world_counts_from_the_top_block(self):
        assert lw.anchor_y({"below_top": 0}, -64, 384) == 319 and lw.anchor_y({"below_top": 10}, 0, 256) == 245

    def test_an_unknown_anchor_is_an_error(self):
        with pytest.raises(ValueError, match="unknown vertical anchor"):
            lw.anchor_y({"sideways": 1}, 0, 10)

    def test_anchor_text(self):
        assert lw.anchor_text({"above_bottom": -80}) == "above_bottom:-80" and lw.anchor_text({"absolute": 80}) == "absolute:80"


class TestParseCount:
    @pytest.mark.parametrize("value,expected", [(7, (7, 7)), ({"type": "minecraft:constant", "value": 3}, (3, 3)),
                                                ({"type": "minecraft:uniform", "min_inclusive": 0, "max_inclusive": 1}, (0, 1)),
                                                ({"type": "minecraft:weighted"}, None), ("many", None)])
    def test_forms(self, value, expected):
        assert lw.parse_count(value) == expected


class TestParsePlacement:
    def test_count_rarity_and_height(self):
        p = lw.parse_placement([count(6), {"type": "minecraft:rarity_filter", "chance": 9}, height("trapezoid", {"absolute": 1}, {"absolute": 9}, plateau=2)])
        assert (p["countMin"], p["countMax"], p["rarityChance"], p["shape"], p["minAnchor"], p["maxAnchor"], p["plateau"]) == (
            6, 6, 9, "trapezoid", {"absolute": 1}, {"absolute": 9}, 2)

    def test_no_count_means_one_attempt(self):
        p = lw.parse_placement([BASE["in_square"], height("uniform", {"absolute": 1}, {"absolute": 2})])
        assert (p["countMin"], p["countMax"], p["rarityChance"]) == (1, 1, None)

    def test_a_single_anchor_is_a_constant_height(self):
        p = lw.parse_placement([{"type": "minecraft:height_range", "height": {"absolute": 64}}])
        assert (p["shape"], p["minAnchor"], p["maxAnchor"]) == ("constant", {"absolute": 64}, {"absolute": 64})

    def test_no_height_range_means_no_height(self):
        assert lw.parse_placement([count(2)])["shape"] is None


class TestResolveHeights:
    def p(self, shape, low, high):
        return {"shape": shape, "minAnchor": low, "maxAnchor": high}

    def test_a_range_inside_the_world_is_kept(self):
        assert lw.resolve_heights(self.p("uniform", {"absolute": -24}, {"absolute": 56}), OVERWORLD) == {"minY": -24, "maxY": 56, "peakY": None}

    def test_a_range_past_the_edges_is_clamped_to_the_world(self):
        r = lw.resolve_heights(self.p("trapezoid", {"above_bottom": -80}, {"above_bottom": 80}), OVERWORLD)
        assert (r["minY"], r["maxY"]) == (-64, 16)

    def test_the_peak_of_a_trapezoid_is_its_middle_before_clamping(self):
        assert lw.resolve_heights(self.p("trapezoid", {"absolute": 80}, {"absolute": 384}), OVERWORLD) == {"minY": 80, "maxY": 319, "peakY": 232}
        assert lw.resolve_heights(self.p("trapezoid", {"above_bottom": -80}, {"above_bottom": 80}), OVERWORLD)["peakY"] == -64

    def test_the_nether_has_its_own_height(self):
        r = lw.resolve_heights(self.p("uniform", {"above_bottom": 10}, {"below_top": 10}), NETHER)
        assert (r["minY"], r["maxY"]) == (10, 245)

    def test_unknown_when_the_dimension_or_the_range_is_unknown(self):
        none = {"minY": None, "maxY": None, "peakY": None}
        assert lw.resolve_heights(self.p("uniform", {"absolute": 1}, {"absolute": 2}), None) == none
        assert lw.resolve_heights({"shape": None, "minAnchor": None, "maxAnchor": None}, OVERWORLD) == none


# --------------------------------------------------------------------------- ore features

class TestOre:
    def placed(self, g):
        return by_id(g["placed"])

    def test_only_ore_placements_are_loaded(self, g):
        assert set(self.placed(g)) == {"minecraft:ore_iron_upper", "minecraft:ore_iron_middle", "minecraft:ore_diamond", "minecraft:ore_coal_upper",
                                       "minecraft:ore_gold_nether", "minecraft:ore_debris", "minecraft:ore_multi"}

    def test_a_placement_with_an_inline_feature_is_skipped_and_counted(self, g):
        assert g["stats"]["placed features with an inline feature (skipped)"] == 1

    def test_one_feature_is_shared_by_its_placements(self, g):
        iron = [r for r in g["places"] if r["feature"] == "minecraft:ore_iron"]
        assert {r["placed"] for r in iron} == {"minecraft:ore_iron_upper", "minecraft:ore_iron_middle"}
        assert [f["id"] for f in g["features"]].count("minecraft:ore_iron") == 1

    def test_only_ore_features_are_features(self, g):
        assert {f["id"] for f in g["features"]} == {"minecraft:ore_iron", "minecraft:ore_diamond_small", "minecraft:ore_coal", "minecraft:ore_gold_nether",
                                                    "minecraft:ore_debris", "minecraft:ore_both"}

    def test_a_feature_has_its_type_and_vein_size(self, g):
        f = by_id(g["features"])
        assert (f["minecraft:ore_iron"]["size"], f["minecraft:ore_iron"]["type"], f["minecraft:ore_iron"]["name"]) == (9, "ore", "Ore Iron")
        assert f["minecraft:ore_debris"]["type"] == "scattered_ore"

    def test_a_feature_creates_the_blocks_and_says_what_they_replace(self, g):
        iron = {(c["block"], c["replaces"]) for c in g["creates"] if c["feature"] == "minecraft:ore_iron"}
        assert iron == {("minecraft:iron_ore", "#minecraft:stone_ore_replaceables"), ("minecraft:deepslate_iron_ore", "#minecraft:deepslate_ore_replaceables")}
        gold = [c for c in g["creates"] if c["feature"] == "minecraft:ore_gold_nether"]
        assert gold == [{"feature": "minecraft:ore_gold_nether", "block": "minecraft:nether_gold_ore", "replaces": "minecraft:netherrack"}]

    def test_iron_upper_is_a_trapezoid_peaking_at_232(self, g):
        p = self.placed(g)["minecraft:ore_iron_upper"]
        assert (p["countMin"], p["countMax"], p["shape"], p["minY"], p["maxY"], p["peakY"], p["dimension"], p["feature"]) == (
            90, 90, "trapezoid", 80, 319, 232, "minecraft:overworld", "minecraft:ore_iron")

    def test_diamond_is_clamped_to_the_bottom_of_the_world(self, g):
        p = self.placed(g)["minecraft:ore_diamond"]
        assert (p["minY"], p["maxY"], p["peakY"], p["minAnchor"], p["maxAnchor"]) == (-64, 16, -64, {"above_bottom": -80}, {"above_bottom": 80})

    def test_a_uniform_range_up_to_the_top_of_the_world(self, g):
        p = self.placed(g)["minecraft:ore_coal_upper"]
        assert (p["shape"], p["minY"], p["maxY"], p["peakY"]) == ("uniform", 136, 319, None)

    def test_nether_ore_uses_the_nether_height_a_rarity_and_a_count_range(self, g):
        p = self.placed(g)["minecraft:ore_gold_nether"]
        assert (p["dimension"], p["minY"], p["maxY"], p["rarityChance"], p["countMin"], p["countMax"]) == ("minecraft:the_nether", 10, 245, 6, 0, 1)

    def test_no_count_means_one_and_the_plateau_is_kept(self, g):
        p = self.placed(g)["minecraft:ore_debris"]
        assert (p["countMin"], p["countMax"], p["plateau"], p["dimension"], p["minY"], p["maxY"]) == (1, 1, 8, "minecraft:the_nether", 8, 24)

    def test_a_placement_in_two_dimensions_has_no_resolved_heights(self, g):
        p = self.placed(g)["minecraft:ore_multi"]
        assert (p["dimension"], p["minY"], p["maxY"]) == (None, None, None)
        assert g["stats"]["ore placements generated in more than one dimension (heights not resolved)"] == 1

    def test_how_many_biomes_list_a_placement(self, g):
        p = self.placed(g)
        assert p["minecraft:ore_iron_upper"]["biomes"] == 2 and p["minecraft:ore_diamond"]["biomes"] == 1 and p["minecraft:ore_gold_nether"]["biomes"] == 1

    def test_biomes_generate_the_ore_placements_they_list_with_the_step(self, g):
        links = {(r["biome"], r["placed"], r["step"]) for r in g["generates"]}
        assert ("minecraft:plains", "minecraft:ore_iron_upper", "underground_ores") in links
        assert ("minecraft:nether_wastes", "minecraft:ore_debris", "underground_decoration") in links
        assert not any(r["placed"] == "minecraft:tree_oak_placed" for r in g["generates"]), "a tree is not an ore"
        assert len(links) == 9, "plains lists four ores, the desert two, the nether three"

    def test_a_placement_nobody_lists_is_still_loaded_without_a_dimension(self, tmp_path):
        files = {"worldgen/configured_feature/ore_x": ore(), "worldgen/placed_feature/ore_x": placed("minecraft:ore_x", count(2), height("uniform", {"absolute": 0}, {"absolute": 9}))}
        p = lw.build(make_jar(tmp_path, files))["placed"][0]
        assert (p["id"], p["dimension"], p["minY"], p["biomes"]) == ("minecraft:ore_x", None, None, 0)


class TestBuild:
    def test_the_stats_count_what_was_built(self, g):
        s = g["stats"]
        assert (s["mobs"], s["mob classes"], s["biomes"], s["biome classes"], s["structures"], s["dimensions"], s["ore features"], s["ore placements"]) == (
            7, 3, 4, 6, 4, 3, 6, 7)
        assert s["biome spawn relationships"] == 6 and s["structure spawn relationships"] == 2 and s["biome to ore placement links"] == 9

    def test_an_empty_jar_builds_nothing_without_error(self, tmp_path):
        g = lw.build(make_jar(tmp_path, {"recipe/stick": {}}))
        assert g["mobs"] == [] and g["biomes"] == [] and g["placed"] == [] and g["dimensions"] == []

    def test_building_twice_gives_the_same_rows(self, jar):
        a, b = lw.build(jar), lw.build(jar)
        a.pop("stats"), b.pop("stats")
        assert a == b

    def test_the_generation_steps_are_in_the_games_order(self):
        assert lw.STEPS[6] == "underground_ores" and lw.STEPS[7] == "underground_decoration" and len(lw.STEPS) == 11
