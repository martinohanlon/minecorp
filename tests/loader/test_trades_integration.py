"""The trade graph in the throwaway database `minecorptest`, built from source with the rest of the graph (tests/graphdb.py).

The questions are graph questions: follow a path and see what is at the end of it. Needs Neo4j Enterprise and the Paper jar."""
from __future__ import annotations

import pytest

import load_model as lm
import load_trades as ltr
from graphdb import TEST_DB

pytestmark = [pytest.mark.integration, pytest.mark.jar]


class TestCounts:
    def test_the_nodes_are_what_was_built(self, built, db):
        t = built["trade_graph"]
        assert db.one("MATCH (n:Trade) RETURN count(n)") == len(t["trades"])
        assert db.one("MATCH (n:TradeSet) RETURN count(n)") == len(t["sets"])
        assert db.one("MATCH (n:Profession) RETURN count(n)") == len(t["professions"])

    def test_the_relationships_are_what_was_built(self, built, db):
        t = built["trade_graph"]
        assert db.one("MATCH (:TradeSet)-[r:INCLUDES]->(:Trade) RETURN count(r)") == len(t["includes"])
        assert db.one("MATCH (:Profession)-[r:OFFERS]->(:TradeSet) RETURN count(r)") == len([o for o in t["offers"] if o["from"] != ltr.TRADER])
        assert db.one("MATCH (:Mob {id: 'minecraft:wandering_trader'})-[r:OFFERS]->(:TradeSet) RETURN count(r)") == 3
        assert db.one("MATCH (:Trade)-[r:REQUIRES]->(:Item) RETURN count(r)") == sum(len(x["pay"]) for x in t["trades"])
        assert db.one("MATCH (:Trade)-[r:CREATES]->(:Item) RETURN count(r)") == len(t["trades"])
        assert db.one("MATCH (:Trade)-[r:LOCATES]->(:Structure) RETURN count(r)") == sum(len(x["locates"]) for x in t["trades"])

    def test_a_trade_is_an_action_with_the_trade_verb(self, db):
        assert db.one("MATCH (t:Trade) WHERE NOT t:Action RETURN count(t)") == 0
        assert db.one("MATCH (t:Trade) WHERE t:Mine OR t:Kill OR t:Shear OR t:Craft OR t:Smelt RETURN count(t)") == 0

    def test_unique_ids(self, db):
        rows = db.q("SHOW CONSTRAINTS YIELD labelsOrTypes, properties, type WHERE type CONTAINS 'UNIQUENESS' RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
        assert {("Profession", "id"), ("TradeSet", "id"), ("Action", "id")} <= {(r["label"], r["prop"]) for r in rows}

    def test_every_item_a_trade_mentions_is_an_item_node_with_a_name(self, db):
        assert db.one("MATCH (:Trade)-[:REQUIRES|CREATES]->(i) WHERE NOT i:Item OR i.name IS NULL RETURN count(i)") == 0


class TestShape:
    def test_every_trade_pays_with_a_price_and_gives_exactly_one_item(self, db):
        assert db.one("MATCH (t:Trade) WHERE size([(t)-[:REQUIRES {role: 'price'}]->() | 1]) <> 1 RETURN count(t)") == 0
        assert db.one("MATCH (t:Trade) WHERE size([(t)-[:CREATES]->() | 1]) <> 1 RETURN count(t)") == 0

    def test_every_trade_is_in_a_set_and_every_set_is_offered_once_or_by_a_profession(self, db):
        assert db.one("MATCH (t:Trade) WHERE NOT (:TradeSet)-[:INCLUDES]->(t) RETURN count(t)") == 0
        assert db.one("MATCH (s:TradeSet) WHERE size([(s)<-[:OFFERS]-() | 1]) <> 1 RETURN count(s)") == 0

    def test_a_profession_has_five_levels_numbered_on_the_relationship(self, db):
        rows = db.q("MATCH (p:Profession)-[o:OFFERS]->() WITH p, collect(o.level) AS levels RETURN p.id AS id, levels")
        assert len(rows) == 13 and all(sorted(r["levels"]) == [1, 2, 3, 4, 5] for r in rows)

    def test_the_variable_price_has_no_count(self, db):
        assert db.one("MATCH (t:Trade {variablePrice: true})-[r:REQUIRES {role: 'price'}]->() WHERE r.count IS NOT NULL RETURN count(r)") == 0
        assert db.one("MATCH (t:Trade {variablePrice: false})-[r:REQUIRES {role: 'price'}]->() WHERE r.count IS NULL RETURN count(r)") == 0

    def test_a_trade_is_not_a_gathering_action_so_the_gathering_queries_do_not_see_it(self, db):
        assert db.one("MATCH (a:Mine|Kill|Shear)-[:CREATES]->(i) WHERE (a)<-[:INCLUDES]-(:TradeSet) RETURN count(a)") == 0


class TestPaths:
    def test_the_villager_has_every_profession(self, db):
        assert db.one("MATCH (:Mob {id: 'minecraft:villager'})-[:HAS_PROFESSION]->(p:Profession) RETURN count(p)") == 13

    def test_who_buys_wheat(self, db):
        rows = db.q("""MATCH (i:Item {id: 'minecraft:wheat'})<-[r:REQUIRES {role: 'price'}]-(t:Trade)<-[:INCLUDES]-(s:TradeSet)<-[o:OFFERS]-(p:Profession)
                       RETURN p.name AS profession, o.level AS level, r.count AS count""")
        assert {"profession": "Farmer", "level": 1, "count": 20} in rows

    def test_where_do_emeralds_come_from_by_trade(self, db):
        rows = db.q("""MATCH (:Item {id: 'minecraft:emerald'})<-[:CREATES]-(t:Trade)<-[:INCLUDES]-(:TradeSet)<-[:OFFERS]-(p)
                       RETURN DISTINCT coalesce(p.name, 'trader') AS who""")
        who = {r["who"] for r in rows}
        assert {"Farmer", "Fisherman", "Librarian", "Wandering Trader"} <= who
        assert db.one("MATCH (:Item {id: 'minecraft:emerald'})<-[:CREATES]-(t:Trade) RETURN count(t)") == 87

    def test_the_wandering_trader_offers_its_pools(self, db):
        rows = db.q("MATCH (:Mob {id: 'minecraft:wandering_trader'})-[o:OFFERS]->(s:TradeSet) RETURN o.pool AS pool, s.amount AS amount ORDER BY pool")
        assert rows == [{"pool": "buying", "amount": 2}, {"pool": "common", "amount": 5}, {"pool": "uncommon", "amount": 2}]

    def test_a_map_leads_to_a_structure_in_the_world_graph(self, db):
        row = db.q("""MATCH (t:Trade)-[l:LOCATES]->(s:Structure {id: 'minecraft:mansion'})
                      RETURN t.name AS trade, l.radius AS radius, t.villagerTypes AS types""")
        assert len(row) == 1 and row[0]["radius"] == 100 and row[0]["types"] == []

    def test_a_map_to_a_swamp_hut_is_only_sold_by_some_villagers(self, db):
        types = db.one("MATCH (t:Trade)-[:LOCATES]->(:Structure {id: 'minecraft:swamp_hut'}) RETURN t.villagerTypes")
        assert types == ["jungle", "snow", "taiga"]

    def test_a_shared_trade_is_in_the_sets_of_several_professions(self, db):
        rows = db.q("MATCH (t:Trade)<-[:INCLUDES]-(s:TradeSet) WITH t, count(s) AS n WHERE n > 1 RETURN count(t) AS shared")
        assert rows == [{"shared": 3}]

    def test_the_trade_graph_and_the_world_graph_meet_at_the_structure_and_the_mob(self, db):
        assert db.one("""MATCH (:Mob {id: 'minecraft:villager'})-[:HAS_PROFESSION]->(:Profession {id: 'minecraft:cartographer'})
                          -[:OFFERS]->(:TradeSet)-[:INCLUDES]->(:Trade)-[:LOCATES]->(s:Structure) RETURN count(DISTINCT s)""") == 10


class TestRebuild:
    def test_loading_the_trades_again_gives_the_same_graph_once_the_model_is_loaded_again(self, built, db):
        before = db.q("MATCH (n) WHERE n:Trade OR n:TradeSet OR n:Profession UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY l")
        edges = db.one("MATCH ()-[r]->() RETURN count(r)")
        to_trades = db.one("MATCH (:Task)-[h:HAS_ACTION]->(:Trade) RETURN count(h)")
        assert to_trades > 0, "a process task performs a trade"
        ltr.write(built["trade_graph"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.q("MATCH (n) WHERE n:Trade OR n:TradeSet OR n:Profession UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY l") == before
        assert db.one("MATCH (:Task)-[h:HAS_ACTION]->(:Trade) RETURN count(h)") == 0, "the trades were recreated, so the task links went"
        lm.write(built["model_rows"], built["uri"], built["user"], built["password"], TEST_DB)       # why the model is loaded after the trades
        assert db.one("MATCH (:Task)-[h:HAS_ACTION]->(:Trade) RETURN count(h)") == to_trades
        assert db.one("MATCH ()-[r]->() RETURN count(r)") == edges
