"""The world loader against the REAL Paper jar: what must hold for any version, and known vanilla facts that act as canaries.

Skipped when the jar has not been downloaded. If a fact fails after a version upgrade, check the game first: it may have changed.
The facts are ones commonly quoted for the game (iron peaks at y=232, diamond sits at the bottom of the world, ancient debris
is only in the nether), which is a sanity check: the heights are worked out from the data, not typed in."""
from __future__ import annotations

import pytest

import load_world as lw

pytestmark = pytest.mark.jar


@pytest.fixture(scope="module")
def g(jar_path):
    return lw.build(jar_path)


def reach(includes: list[dict], start: str) -> set[str]:
    """Everything a class includes, directly or through other classes."""
    graph: dict = {}
    for r in includes:
        graph.setdefault(r["class"], set()).add(r["member"])
    seen, todo = set(), [start]
    while todo:
        for m in graph.get(todo.pop(), ()):
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return seen


def placed(g):
    return {p["id"]: p for p in g["placed"]}


def placements_creating(g, block: str) -> list[dict]:
    features = {c["feature"] for c in g["creates"] if c["block"] == block}
    return [p for p in g["placed"] if p["feature"] in features]


# --------------------------------------------------------------------------- shape, true for any version

class TestShape:
    def test_every_spawn_names_a_mob_and_a_biome_that_exist(self, g):
        mobs, biomes = {m["id"] for m in g["mobs"]}, {b["id"] for b in g["biomes"]}
        assert all(s["mob"] in mobs and s["biome"] in biomes for s in g["spawns"])
        assert all(s["mob"] in mobs for s in g["structureSpawns"])

    def test_every_structure_generates_in_a_biome_class_that_exists(self, g):
        classes = {c["id"] for c in g["biomeClasses"]}
        links = {}
        for r in g["generatesIn"]:
            assert r["biome"] in classes
            links.setdefault(r["structure"], []).append(r["biome"])
        assert set(links) == {s["id"] for s in g["structures"]}

    def test_no_biome_is_in_two_dimensions_and_only_the_void_is_in_none(self, g):
        by_dim = {r["dimension"]: reach(g["biomeIncludes"], r["class"]) for r in g["hasBiome"]}
        assert len(by_dim) == 3
        counts = {b["id"]: sum(b["id"] in members for members in by_dim.values()) for b in g["biomes"]}
        assert max(counts.values()) == 1
        assert {b for b, n in counts.items() if n == 0} == {"minecraft:the_void"}, "the void biome is used by no dimension"

    def test_every_ore_placement_is_listed_by_a_biome_and_has_real_heights(self, g):
        assert all(p["biomes"] > 0 for p in g["placed"])
        assert all(p["dimension"] and p["minY"] is not None and p["minY"] <= p["maxY"] for p in g["placed"])
        assert "ore placements generated in more than one dimension (heights not resolved)" not in g["stats"]

    def test_every_feature_is_placed_and_makes_a_block(self, g):
        placed_features = {p["feature"] for p in g["placed"]}
        made = {c["feature"] for c in g["creates"]}
        assert {f["id"] for f in g["features"]} == placed_features == made

    def test_ore_heights_stay_inside_their_dimension(self, g):
        dims = {d["id"]: d for d in g["dimensions"]}
        for p in g["placed"]:
            d = dims[p["dimension"]]
            assert d["minY"] <= p["minY"] <= p["maxY"] <= d["minY"] + d["height"] - 1, p["id"]

    def test_class_ids_are_tag_references(self, g):
        assert all(c["id"].startswith("#minecraft:") for c in g["mobClasses"] + g["biomeClasses"])


# --------------------------------------------------------------------------- vanilla facts (canaries)

class TestMobs:
    def test_ninety_creatures(self, g):
        assert len(g["mobs"]) == 90

    @pytest.mark.parametrize("name", ["creeper", "zombie", "villager", "wolf", "bat", "wither", "ender_dragon", "iron_golem"])
    def test_these_are_mobs(self, g, name):
        assert f"minecraft:{name}" in {m["id"] for m in g["mobs"]}

    @pytest.mark.parametrize("name", ["armor_stand", "player", "mannequin", "black", "arrow", "oak_boat"])
    def test_these_are_not(self, g, name):
        assert f"minecraft:{name}" not in {m["id"] for m in g["mobs"]}

    def test_undead_includes_the_zombies_skeletons_and_the_wither(self, g):
        members = reach(g["mobIncludes"], "#minecraft:undead")
        assert {"minecraft:zombie", "minecraft:skeleton", "minecraft:wither", "minecraft:phantom"} <= members
        assert "minecraft:cow" not in members

    def test_illagers_and_raiders(self, g):
        assert {"minecraft:pillager", "minecraft:vindicator", "minecraft:evoker"} <= reach(g["mobIncludes"], "#minecraft:illager")
        assert {"minecraft:pillager", "minecraft:ravager"} <= reach(g["mobIncludes"], "#minecraft:raiders")

    def test_no_class_about_boats_or_projectiles(self, g):
        ids = {c["id"] for c in g["mobClasses"]}
        assert not ({"#minecraft:boat", "#minecraft:arrows", "#minecraft:impact_projectiles"} & ids)


class TestBiomesAndSpawns:
    def test_sixty_six_biomes(self, g):
        assert len(g["biomes"]) == 66

    def test_plains_spawns_sheep_with_these_numbers(self, g):
        sheep = next(s for s in g["spawns"] if s["biome"] == "minecraft:plains" and s["mob"] == "minecraft:sheep")
        assert (sheep["category"], sheep["weight"], sheep["min"], sheep["max"]) == ("creature", 12, 4, 4)

    def test_creepers_spawn_as_monsters_in_most_overworld_biomes_and_never_in_the_nether(self, g):
        rows = [s for s in g["spawns"] if s["mob"] == "minecraft:creeper"]
        assert {s["category"] for s in rows} == {"monster"} and len(rows) > 40
        assert not {s["biome"] for s in rows} & reach(g["biomeIncludes"], "#minecraft:is_nether")

    def test_the_nether_spawns_piglins_and_ghasts_and_the_end_endermen(self, g):
        nether, end = reach(g["biomeIncludes"], "#minecraft:is_nether"), reach(g["biomeIncludes"], "#minecraft:is_end")
        assert {s["mob"] for s in g["spawns"] if s["biome"] in nether} >= {"minecraft:ghast", "minecraft:zombified_piglin"}
        assert any(s["mob"] == "minecraft:enderman" and s["biome"] in end for s in g["spawns"])

    def test_the_desert_is_dry_and_hot_and_the_snowy_plains_cold(self, g):
        b = {x["id"]: x for x in g["biomes"]}
        assert b["minecraft:desert"]["hasPrecipitation"] is False and b["minecraft:desert"]["temperature"] > 1.5
        assert b["minecraft:snowy_plains"]["temperature"] < 0.1


class TestDimensions:
    def test_the_heights(self, g):
        d = {x["id"]: x for x in g["dimensions"]}
        assert (d["minecraft:overworld"]["minY"], d["minecraft:overworld"]["height"]) == (-64, 384)
        assert (d["minecraft:the_nether"]["minY"], d["minecraft:the_nether"]["height"], d["minecraft:the_nether"]["logicalHeight"]) == (0, 256, 128)
        assert (d["minecraft:the_end"]["minY"], d["minecraft:the_end"]["height"]) == (0, 256)


class TestStructures:
    def test_thirty_four_structures(self, g):
        assert len(g["structures"]) == 34

    @pytest.mark.parametrize("structure,biome", [("village_plains", "plains"), ("desert_pyramid", "desert"), ("fortress", "nether_wastes"),
                                                 ("igloo", "snowy_taiga"), ("swamp_hut", "swamp"), ("end_city", "end_highlands")])
    def test_where_a_structure_generates(self, g, structure, biome):
        classes = [r["biome"] for r in g["generatesIn"] if r["structure"] == f"minecraft:{structure}"]
        assert any(f"minecraft:{biome}" in reach(g["biomeIncludes"], c) for c in classes)

    def test_a_fortress_spawns_blazes_and_wither_skeletons(self, g):
        mobs = {s["mob"] for s in g["structureSpawns"] if s["structure"] == "minecraft:fortress"}
        assert {"minecraft:blaze", "minecraft:wither_skeleton"} <= mobs

    def test_a_monument_spawns_guardians_and_an_ancient_city_nothing(self, g):
        assert any(s["structure"] == "minecraft:monument" and s["mob"] == "minecraft:guardian" for s in g["structureSpawns"])
        city = next(s for s in g["structures"] if s["id"] == "minecraft:ancient_city")
        assert len(city["suppressedSpawns"]) == 8 and not [s for s in g["structureSpawns"] if s["structure"] == "minecraft:ancient_city"]


class TestOre:
    def test_forty_placements_of_thirty_two_features(self, g):
        assert (len(g["placed"]), len(g["features"])) == (40, 32)

    def test_iron_is_placed_high_and_middle_from_the_same_vein_and_peaks_at_232(self, g):
        p = placed(g)
        assert p["minecraft:ore_iron_upper"]["feature"] == p["minecraft:ore_iron_middle"]["feature"] == "minecraft:ore_iron"
        assert (p["minecraft:ore_iron_upper"]["minY"], p["minecraft:ore_iron_upper"]["maxY"], p["minecraft:ore_iron_upper"]["peakY"]) == (80, 319, 232)
        assert (p["minecraft:ore_iron_middle"]["minY"], p["minecraft:ore_iron_middle"]["maxY"], p["minecraft:ore_iron_middle"]["peakY"]) == (-24, 56, 16)

    def test_every_diamond_placement_is_at_the_bottom_of_the_world(self, g):
        found = placements_creating(g, "minecraft:diamond_ore")
        assert len(found) == 4 and min(p["minY"] for p in found) == -64 and max(p["maxY"] for p in found) == 16

    @pytest.mark.parametrize("ore,peak", [("coal_lower", 96), ("lapis", 0), ("copper", 48), ("emerald", 232), ("gold", -16)])
    def test_where_a_trapezoid_ore_is_commonest(self, g, ore, peak):
        assert placed(g)[f"minecraft:ore_{ore}"]["peakY"] == peak

    def test_emerald_only_in_a_few_biomes(self, g):
        assert placed(g)["minecraft:ore_emerald"]["biomes"] == 10

    def test_ancient_debris_and_nether_gold_only_in_the_nether(self, g):
        for block in ("minecraft:ancient_debris", "minecraft:nether_gold_ore", "minecraft:nether_quartz_ore"):
            found = placements_creating(g, block)
            assert found and {p["dimension"] for p in found} == {"minecraft:the_nether"}, block
        assert {p["dimension"] for p in placements_creating(g, "minecraft:diamond_ore")} == {"minecraft:overworld"}

    def test_a_vein_makes_a_stone_and_a_deepslate_ore(self, g):
        made = {(c["block"], c["replaces"]) for c in g["creates"] if c["feature"] == "minecraft:ore_iron"}
        assert made == {("minecraft:iron_ore", "#minecraft:stone_ore_replaceables"), ("minecraft:deepslate_iron_ore", "#minecraft:deepslate_ore_replaceables")}
        assert next(f for f in g["features"] if f["id"] == "minecraft:ore_iron")["size"] == 9

    def test_ores_are_generated_in_the_underground_ore_step(self, g):
        assert {r["step"] for r in g["generates"]} <= {"underground_ores", "underground_decoration"}
        assert {r["step"] for r in g["generates"] if r["placed"] == "minecraft:ore_iron_upper"} == {"underground_ores"}
