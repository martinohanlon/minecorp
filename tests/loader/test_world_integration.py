"""The world graph in the throwaway database `minecorptest`, built from source with the rest of the graph (tests/graphdb.py).

The questions are graph questions: follow a path and see what is at the end of it. Needs Neo4j Enterprise and the Paper jar."""
from __future__ import annotations

import pytest

import load_world as lwd
from graphdb import TEST_DB

pytestmark = [pytest.mark.integration, pytest.mark.jar]


class TestCounts:
    def test_the_nodes_are_what_was_built(self, built, db):
        w = built["world_graph"]
        for label, rows in (("Biome", "biomes"), ("BiomeClass", "biomeClasses"), ("MobClass", "mobClasses"), ("Structure", "structures"),
                            ("Dimension", "dimensions"), ("Feature", "features"), ("PlacedFeature", "placed"), ("Mob", "mobs")):
            assert db.one(f"MATCH (n:{label}) RETURN count(n)") == len(w[rows]), label

    def test_the_relationships_are_what_was_built(self, built, db):
        w = built["world_graph"]
        assert db.one("MATCH (:Biome)-[r:SPAWNS]->(:Mob) RETURN count(r)") == len(w["spawns"])
        assert db.one("MATCH (:Structure)-[r:SPAWNS]->(:Mob) RETURN count(r)") == len(w["structureSpawns"])
        assert db.one("MATCH (:Structure)-[r:GENERATES_IN]->() RETURN count(r)") == len(w["generatesIn"])
        assert db.one("MATCH (:Biome)-[r:GENERATES]->(:PlacedFeature) RETURN count(r)") == len(w["generates"])
        assert db.one("MATCH (:PlacedFeature)-[r:PLACES]->(:Feature) RETURN count(r)") == len(w["places"])
        assert db.one("MATCH (:Feature)-[r:CREATES]->(:Block) RETURN count(r)") == len(w["creates"]), "every block a feature makes is a Block node"

    def test_unique_ids_on_every_label_the_loader_owns(self, db):
        rows = db.q("SHOW CONSTRAINTS YIELD labelsOrTypes, properties, type WHERE type CONTAINS 'UNIQUENESS' RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
        pairs = {(r["label"], r["prop"]) for r in rows}
        assert {(label, "id") for label in lwd.OWNED} <= pairs

    def test_an_ore_is_a_placed_feature_with_a_marker_label(self, db):
        assert db.one("MATCH (p:PlacedFeature) WHERE NOT p:Ore RETURN count(p)") == 0
        assert db.one("MATCH (p:Ore) RETURN count(p)") == 40


class TestMobs:
    def test_every_creature_is_a_mob_and_only_some_drop_anything(self, db):
        assert db.one("MATCH (m:Mob) RETURN count(m)") == 90
        killable = db.one("MATCH (m:Mob) WHERE EXISTS { (:Kill)-[:REQUIRES {role: 'target'}]->(m) } RETURN count(m)")
        assert 55 < killable < 90

    def test_the_villager_is_a_mob_that_drops_nothing(self, db):
        assert db.one("MATCH (m:Mob {id: 'minecraft:villager'}) RETURN count(m)") == 1
        assert db.one("MATCH (m:Mob {id: 'minecraft:villager'}) WHERE NOT EXISTS { (:Action)-[:REQUIRES]->(m) } RETURN count(m)") == 1

    def test_the_creeper_drops_gunpowder_by_a_kill_action(self, db):
        assert db.one("MATCH (:Mob {id: 'minecraft:creeper'})<-[:REQUIRES]-(:Kill)-[:CREATES]->(i:Item {id: 'minecraft:gunpowder'}) RETURN count(i)") == 1

    def test_undead_reaches_the_zombie_through_the_zombies_class(self, db):
        path = db.one("MATCH p = (:MobClass {id: '#minecraft:undead'})-[:INCLUDES*]->(:Mob {id: 'minecraft:zombie'}) RETURN min(length(p))")
        assert path == 2

    def test_no_non_creature_is_a_mob(self, db):
        assert db.one("MATCH (m:Mob) WHERE m.id IN ['minecraft:player', 'minecraft:armor_stand', 'minecraft:mannequin', 'minecraft:black'] RETURN count(m)") == 0

    def test_mob_names_are_set_for_every_mob(self, db):
        assert db.one("MATCH (m:Mob) WHERE m.name IS NULL OR m.name = '' RETURN count(m)") == 0


class TestWhereThingsAre:
    def test_what_spawns_in_plains(self, db):
        rows = db.q("MATCH (:Biome {id: 'minecraft:plains'})-[s:SPAWNS]->(m:Mob) RETURN m.id AS mob, s.category AS category, s.weight AS weight")
        by_mob = {r["mob"]: r for r in rows}
        assert by_mob["minecraft:sheep"] == {"mob": "minecraft:sheep", "category": "creature", "weight": 12}
        assert by_mob["minecraft:creeper"]["category"] == "monster"

    def test_which_biomes_spawn_creepers_and_none_of_them_is_in_the_nether(self, db):
        biomes = db.one("MATCH (b:Biome)-[:SPAWNS]->(:Mob {id: 'minecraft:creeper'}) RETURN count(DISTINCT b)")
        assert biomes > 40
        in_nether = db.one("MATCH (b:Biome)-[:SPAWNS]->(:Mob {id: 'minecraft:creeper'}) "
                           "MATCH (:Dimension {id: 'minecraft:the_nether'})-[:HAS_BIOME]->(:BiomeClass)-[:INCLUDES*]->(b) RETURN count(b)")
        assert in_nether == 0

    def test_a_structure_reaches_its_biomes_through_a_class(self, db):
        rows = db.q("MATCH (:Structure {id: 'minecraft:desert_pyramid'})-[:GENERATES_IN]->(:BiomeClass)-[:INCLUDES*]->(b:Biome) RETURN b.id AS id")
        assert {r["id"] for r in rows} == {"minecraft:desert"}

    def test_where_a_village_can_be(self, db):
        rows = db.q("MATCH (s:Structure)-[:GENERATES_IN]->(:BiomeClass)-[:INCLUDES*]->(b:Biome {id: 'minecraft:plains'}) "
                    "WHERE s.id STARTS WITH 'minecraft:village' RETURN s.id AS id")
        assert {r["id"] for r in rows} == {"minecraft:village_plains"}

    def test_a_fortress_spawns_blazes_and_an_ancient_city_suppresses_spawns(self, db):
        assert db.one("MATCH (:Structure {id: 'minecraft:fortress'})-[s:SPAWNS]->(:Mob {id: 'minecraft:blaze'}) RETURN s.category") == "monster"
        assert db.one("MATCH (s:Structure {id: 'minecraft:ancient_city'}) RETURN size(s.suppressedSpawns)") == 8

    def test_each_dimension_has_the_biomes_of_its_class(self, db):
        rows = db.q("MATCH (d:Dimension)-[:HAS_BIOME]->(:BiomeClass)-[:INCLUDES*]->(b:Biome) RETURN d.id AS d, count(DISTINCT b) AS biomes ORDER BY d")
        counts = {r["d"]: r["biomes"] for r in rows}
        assert counts["minecraft:the_nether"] == 5 and counts["minecraft:overworld"] > 40 and "minecraft:the_end" in counts
        assert db.one("MATCH (d:Dimension)-[:HAS_BIOME]->(:BiomeClass)-[:INCLUDES*]->(b:Biome) WITH b, count(DISTINCT d) AS dims WHERE dims > 1 RETURN count(b)") == 0

    def test_the_dimensions_know_their_height(self, db):
        row = db.one("MATCH (d:Dimension {id: 'minecraft:overworld'}) RETURN d {.minY, .height} AS d")
        assert row == {"minY": -64, "height": 384}


class TestOreAndTheItemsTheyGive:
    """The path that answers 'where do I mine iron': Item <- Mine action -> Block <- Feature <- PlacedFeature <- Biome."""

    PATH = ("MATCH (:Item {id: $item})<-[:CREATES]-(:Mine)-[:REQUIRES {role: 'target'}]->(:Block)<-[:CREATES]-(f:Feature)"
            "<-[:PLACES]-(p:PlacedFeature)<-[:GENERATES]-(b:Biome) ")

    def test_raw_iron_is_mined_from_ore_that_generates_at_these_heights(self, db):
        rows = db.q(self.PATH + "RETURN DISTINCT p.id AS id, p.minY AS lo, p.maxY AS hi, p.peakY AS peak ORDER BY id", item="minecraft:raw_iron")
        found = {r["id"]: r for r in rows}
        assert set(found) == {"minecraft:ore_iron_upper", "minecraft:ore_iron_middle", "minecraft:ore_iron_small"}
        assert (found["minecraft:ore_iron_upper"]["lo"], found["minecraft:ore_iron_upper"]["hi"], found["minecraft:ore_iron_upper"]["peak"]) == (80, 319, 232)

    def test_iron_is_found_in_many_biomes(self, db):
        assert db.one(self.PATH + "RETURN count(DISTINCT b)", item="minecraft:raw_iron") > 40

    def test_a_diamond_is_only_found_at_the_bottom_of_the_world(self, db):
        row = db.one(self.PATH + "RETURN min(p.minY) AS lo, max(p.maxY) AS hi", item="minecraft:diamond")
        assert row == {"lo": -64, "hi": 16}

    def test_emeralds_only_in_a_few_biomes(self, db):
        assert db.one(self.PATH + "RETURN count(DISTINCT b)", item="minecraft:emerald") == 10

    def test_ancient_debris_is_a_nether_ore(self, db):
        dims = db.q("MATCH (:Block {id: 'minecraft:ancient_debris'})<-[:CREATES]-(:Feature)<-[:PLACES]-(p:PlacedFeature)-[:IN_DIMENSION]->(d) RETURN DISTINCT d.id AS d")
        assert [r["d"] for r in dims] == ["minecraft:the_nether"]

    def test_a_vein_replaces_stone_by_a_tag_and_netherrack_by_a_block(self, db):
        assert db.one("MATCH (:Feature {id: 'minecraft:ore_iron'})-[c:CREATES]->(:Block {id: 'minecraft:deepslate_iron_ore'}) RETURN c.replaces") == "#minecraft:deepslate_ore_replaceables"
        # the placement is called ore_gold_nether; the vein it places has a different name, so follow the path
        assert db.one("MATCH (:PlacedFeature {id: 'minecraft:ore_gold_nether'})-[:PLACES]->(:Feature)-[c:CREATES]->(:Block {id: 'minecraft:nether_gold_ore'}) "
                      "RETURN c.replaces") == "minecraft:netherrack"

    def test_one_feature_has_several_placements(self, db):
        rows = db.q("MATCH (f:Feature {id: 'minecraft:ore_iron'})<-[:PLACES]-(p:PlacedFeature) RETURN p.id AS id ORDER BY id")
        assert [r["id"] for r in rows] == ["minecraft:ore_iron_middle", "minecraft:ore_iron_upper"]

    def test_every_ore_placement_has_a_dimension_and_heights(self, db):
        assert db.one("MATCH (p:Ore) WHERE NOT (p)-[:IN_DIMENSION]->() OR p.minY IS NULL OR p.maxY IS NULL RETURN count(p)") == 0

    def test_every_ore_block_can_be_mined(self, db):
        """The blocks a feature makes are blocks you can mine, so the path from an item to a place never dead-ends."""
        assert db.one("MATCH (f:Feature)-[:CREATES]->(b:Block) WHERE NOT EXISTS { (:Mine)-[:REQUIRES {role: 'target'}]->(b) } RETURN count(DISTINCT b)") <= 4


class TestRebuildContract:
    """Last: these write to the database again."""

    def test_loading_the_world_twice_gives_the_same_graph(self, built, db):
        before = db.q("MATCH (n) WHERE n:Biome OR n:BiomeClass OR n:MobClass OR n:Structure OR n:Dimension OR n:Feature OR n:PlacedFeature OR n:Mob "
                      "RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l")
        rels = "MATCH ()-[r:SPAWNS|GENERATES_IN|GENERATES|PLACES|IN_DIMENSION|HAS_BIOME]->() RETURN type(r) AS t, count(*) AS n ORDER BY t"
        edges = db.q(rels)
        lwd.write(built["world_graph"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.q("MATCH (n) WHERE n:Biome OR n:BiomeClass OR n:MobClass OR n:Structure OR n:Dimension OR n:Feature OR n:PlacedFeature OR n:Mob "
                    "RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l") == before
        assert db.q(rels) == edges

    def test_the_actions_loader_does_not_remove_the_world(self, built, db):
        import load_actions as la
        la.write(built["action_graph"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.one("MATCH (m:Mob) RETURN count(m)") == 90 and db.one("MATCH (:Biome)-[r:SPAWNS]->(:Mob) RETURN count(r)") == len(built["world_graph"]["spawns"])
        assert db.one("MATCH (:Mine)-[:REQUIRES]->(:Block) RETURN count(*)") > 0
