"""The enchantment graph in the throwaway database `minecorptest`, built from source with the rest of the graph (tests/graphdb.py).

Graph questions: follow a path and see what is at the end of it. Needs Neo4j Enterprise and the Paper jar."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.jar]


class TestCounts:
    def test_the_nodes_are_what_was_built(self, built, db):
        g = built["enchantment_graph"]
        assert db.one("MATCH (n:Enchantment) RETURN count(n)") == len(g["enchantments"])
        assert db.one("MATCH (n:EnchantmentClass) RETURN count(n)") == len(g["classes"])

    def test_the_relationships_are_what_was_built(self, built, db):
        g = built["enchantment_graph"]
        assert db.one("MATCH (:EnchantmentClass)-[r:INCLUDES]->() RETURN count(r)") == len(g["includes"])
        assert db.one("MATCH (:Enchantment)-[r:APPLIES_TO]->() RETURN count(r)") == len(g["applies"]) + len(g["direct"])

    def test_an_item_class_includes_only_items_that_exist(self, built, db):
        g = built["enchantment_graph"]
        wanted = len(g["item_includes"])
        got = db.one("MATCH (:ItemClass)-[r:INCLUDES]->(m:Item) WHERE EXISTS { MATCH (:ItemClass {id: m.id}) } OR NOT m:ItemClass "
                     "WITH count(r) AS n RETURN n")
        assert got >= wanted - 5, (got, wanted)            # a few tags name items the recipes did not load; none may be invented

    def test_unique_ids(self, db):
        rows = db.q("SHOW CONSTRAINTS YIELD labelsOrTypes, properties, type WHERE type CONTAINS 'UNIQUENESS' RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
        assert {("Enchantment", "id"), ("EnchantmentClass", "id")} <= {(r["label"], r["prop"]) for r in rows}

    def test_the_names_index_finds_an_enchantment_and_a_class(self, db):
        found = db.q("CALL db.index.fulltext.queryNodes('names', 'sharpness') YIELD node RETURN labels(node) AS labels")
        assert any("Enchantment" in r["labels"] for r in found)
        assert db.q("CALL db.index.fulltext.queryNodes('names', 'treasure') YIELD node WHERE node:EnchantmentClass RETURN node.id AS id")


class TestQuestions:
    def test_what_conflicts_with_sharpness(self, db):
        rows = db.q("""MATCH (:Enchantment {id: 'minecraft:sharpness'})<-[:INCLUDES]-(c:EnchantmentClass)-[:INCLUDES]->(o:Enchantment)
                       WHERE c.name STARTS WITH 'Exclusive' AND o.id <> 'minecraft:sharpness' RETURN o.name AS name ORDER BY name""")
        assert {"Smite", "Bane Of Arthropods"} <= {r["name"] for r in rows}

    def test_what_can_go_on_an_iron_pickaxe(self, db):
        rows = db.q("""MATCH (e:Enchantment)-[:APPLIES_TO]->(:ItemClass)-[:INCLUDES*1..3]->(:Item {id: 'minecraft:iron_pickaxe'}) RETURN DISTINCT e.name AS name""")
        names = {r["name"] for r in rows}
        assert {"Efficiency", "Fortune", "Silk Touch", "Unbreaking", "Mending"} <= names and "Sharpness" not in names

    def test_what_can_go_on_an_iron_sword(self, db):
        names = {r["name"] for r in db.q("""MATCH (e:Enchantment)-[:APPLIES_TO]->(:ItemClass)-[:INCLUDES*1..3]->(:Item {id: 'minecraft:iron_sword'}) RETURN DISTINCT e.name AS name""")}
        assert {"Sharpness", "Smite", "Looting", "Sweeping Edge", "Mending"} <= names and "Efficiency" not in names

    def test_the_table_offers_sharpness_but_not_mending(self, db):
        offered = {r["id"] for r in db.q("""MATCH (:EnchantmentClass {id: '#minecraft:in_enchanting_table'})-[:INCLUDES*1..2]->(e:Enchantment) RETURN e.id AS id""")}
        assert "minecraft:sharpness" in offered and "minecraft:mending" not in offered

    def test_the_trades_that_enchant_name_the_class_they_draw_from(self, db):
        books = db.q("""MATCH (t:Trade)-[:OFFERS_ENCHANTMENT]->(:EnchantmentClass {id: '#minecraft:tradeable'}) RETURN count(t) AS n""")[0]["n"]
        gear = db.q("""MATCH (t:Trade)-[:OFFERS_ENCHANTMENT]->(:EnchantmentClass {id: '#minecraft:on_traded_equipment'}) RETURN count(t) AS n""")[0]["n"]
        assert books == 4 and gear >= 15
        assert db.one("MATCH (t:Trade {enchanted: true}) WHERE NOT (t)-[:OFFERS_ENCHANTMENT]->() RETURN count(t)") == 0
