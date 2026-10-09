"""Integration tests: rebuild the whole graph from source in a THROWAWAY database and check it.

Needs the local Neo4j (Enterprise Edition, for multiple databases) and the Paper jar. The graph is built once per run
by the shared `built` fixture (tests/conftest.py and tests/graphdb.py) in a database called `minecorptest`, which is
created at the start and dropped at the end; the real database is never touched. Run with:  pytest -m integration
Skipped when Neo4j is not reachable or cannot create a database.
"""
from __future__ import annotations

import pytest

import load_actions as la
import load_model as lm
import load_trades as ltr
from graphdb import TEST_DB

pytestmark = [pytest.mark.integration, pytest.mark.jar]


# --------------------------------------------------------------------------- shape of the rebuilt graph

class TestGraphShape:
    def test_label_counts_match_what_was_built(self, built, db):
        ag = built["action_graph"]
        verbs = {}
        for a in ag["actions"] + ag["transform"]:
            verbs[a["verb"]] = verbs.get(a["verb"], 0) + 1
        for verb, n in verbs.items():
            assert db.one(f"MATCH (a:Action:{verb}) RETURN count(a)") == n, verb
        assert db.one("MATCH (a:Action) RETURN count(a)") == sum(verbs.values()) + len(built["trade_graph"]["trades"]), \
            "the actions the action loader made, and the trades the trade loader added"
        assert db.one("MATCH (b:Block) RETURN count(b)") == len(ag["blocks"])
        assert db.one("MATCH (m:Mob) RETURN count(m)") == len(built["world_graph"]["mobs"]) > len(ag["mobs"]), \
            "the world loader adds the creatures that drop nothing to the ones the actions made"
        assert db.one("MATCH (r:Recipe) RETURN count(r)") == len(built["normalised"])

    def test_old_model_is_gone(self, db):
        assert db.one("MATCH (h:Harvest) RETURN count(h)") == 0
        assert db.one("MATCH ()-[r:FROM_BLOCK|FROM_MOB|CRAFTED|HARVESTED|USED_RECIPE|USED_HARVEST]->() RETURN count(r)") == 0
        assert db.one("MATCH (t:Task) WHERE t.type IS NOT NULL RETURN count(t)") == 0

    def test_a_station_is_an_item_with_a_second_label(self, db):
        assert db.one("MATCH (s:Station) WHERE NOT s:Item RETURN count(s)") == 0
        assert db.one("MATCH (s:Item:Station) RETURN count(s)") == 7
        assert db.one("MATCH (r:Recipe)-[:USES_STATION]->(s) WHERE NOT s:Station RETURN count(r)") == 0

    def test_every_recipe_station_is_a_craftable_item(self, db):
        assert db.one("MATCH (s:Station) WHERE NOT ()-[:CREATES]->(s) RETURN count(s)") == 0

    def test_uses_station_matches_the_recipes_that_have_one(self, built, db):
        expected = sum(1 for r in built["normalised"].values() if r["station"])
        assert db.one("MATCH ()-[u:USES_STATION]->() RETURN count(u)") == expected

    def test_recipes_without_a_result_are_the_smithing_trims_and_special_ones(self, built, db):
        expected = sum(1 for r in built["normalised"].values() if not r["result"])
        assert db.one("MATCH (r:Recipe) WHERE NOT (r)-[:CREATES]->() RETURN count(r)") == expected

    def test_every_action_creates_something(self, db):
        assert db.one("MATCH (a:Action) WHERE NOT (a)-[:CREATES]->() RETURN count(a)") == 0

    def test_gathering_actions_have_one_target_of_the_right_kind(self, db):
        assert db.one("MATCH (a:Mine) WHERE NOT (a)-[:REQUIRES {role:'target'}]->(:Block) RETURN count(a)") == 0
        assert db.one("MATCH (a:Kill|Shear) WHERE NOT (a)-[:REQUIRES {role:'target'}]->(:Mob) RETURN count(a)") == 0
        assert db.one("MATCH (a:Mine|Kill|Shear)-[r:REQUIRES {role:'target'}]->() WITH a, count(r) AS n WHERE n <> 1 RETURN count(a)") == 0

    def test_transforming_actions_hold_no_requirements(self, db):
        assert db.one("MATCH (a:Craft|Smelt|Smith|Cut)-[r:REQUIRES]->() RETURN count(r)") == 0

    def test_tools_are_items_and_only_shears_are_exact(self, db):
        assert db.one("MATCH (a:Action)-[r:REQUIRES {role:'tool'}]->(t) WHERE NOT t:Item RETURN count(r)") == 0
        exact = db.q("MATCH (a:Action)-[r:REQUIRES {role:'tool'}]->(t) WHERE r.orBetter = false RETURN DISTINCT t.id AS id")
        assert exact == [{"id": "minecraft:shears"}]

    def test_class_ids_and_names(self, db):
        assert db.one("MATCH (c:ItemClass) WHERE NOT (c.id STARTS WITH '#minecraft:' OR c.id STARTS WITH 'inline:') RETURN count(c)") == 0
        assert db.one("MATCH (c:ItemClass) WHERE c.name IS NULL OR c.name = '' RETURN count(c)") == 0
        assert db.one("MATCH (i:Item) WHERE i.name IS NULL RETURN count(i)") == 0

    def test_includes_edges_leave_a_class_and_reach_an_item(self, db):
        assert db.one("MATCH (a:Item)-[:INCLUDES]->(b) WHERE NOT (a:ItemClass AND b:Item) RETURN count(*)") == 0
        assert db.one("MATCH (a:ItemClass)-[:INCLUDES]->(b) WHERE NOT b:Item RETURN count(*)") == 0, "mob and biome classes use INCLUDES too, but never reach an item"

    def test_unique_constraints_exist(self, db):
        rows = db.q("SHOW CONSTRAINTS YIELD labelsOrTypes, properties, type WHERE type CONTAINS 'UNIQUENESS' RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
        pairs = {(r["label"], r["prop"]) for r in rows}
        assert {("Item", "id"), ("Recipe", "id"), ("Action", "id"), ("Block", "id"), ("Mob", "id"), ("Task", "id"),
                ("Process", "id"), ("Activity", "id"), ("Player", "uuid")} <= pairs
        assert not ({("Role", "id"), ("OrgUnit", "id")} & pairs)

    def test_route_properties_exist_for_craftable_items(self, db):
        n = db.one("MATCH (i:Item) WHERE i.routeSteps IS NOT NULL RETURN count(i)")
        assert 800 < n < 1200
        assert db.one("MATCH (i:Item) WHERE i.routeSteps IS NOT NULL AND (i.routeSteps < 1 OR i.routeBaseMaterials < 1 OR i.routeStations IS NULL) RETURN count(i)") == 0


class TestKnownFacts:
    def test_mine_iron_ore(self, db):
        row = db.q("""MATCH (a:Mine {id:'mine:minecraft:iron_ore'})-[:REQUIRES {role:'target'}]->(b:Block)
                      MATCH (a)-[q:REQUIRES {role:'tool'}]->(t) MATCH (a)-[c:CREATES {condition:'default'}]->(i)
                      RETURN b.id AS block, t.id AS tool, q.orBetter AS orBetter, i.id AS drops""")[0]
        assert row == {"block": "minecraft:iron_ore", "tool": "minecraft:stone_pickaxe", "orBetter": True, "drops": "minecraft:raw_iron"}

    def test_kill_spider_and_shear_sheep(self, db):
        assert db.one("MATCH (:Kill {id:'kill:minecraft:spider'})-[:CREATES {condition:'default'}]->(i {id:'minecraft:string'}) RETURN count(i)") == 1
        assert db.one("MATCH (:Shear {id:'shear:minecraft:sheep'})-[:CREATES]->(i) RETURN count(i)") >= 16

    def test_actions_and_recipes_meet_at_the_item(self, db):
        rows = db.q("""MATCH (a:Smelt {id:'smelt:minecraft:iron_ingot'})-[:CREATES]->(i)<-[:CREATES]-(r:Recipe)
                       WHERE r.type IN ['smelting','blasting'] RETURN count(r) AS n""")
        assert rows[0]["n"] >= 3
        kinds = db.q("MATCH (x)-[:CREATES]->(:Item {id:'minecraft:iron_ingot'}) UNWIND labels(x) AS l RETURN DISTINCT l")
        assert {"Recipe", "Smelt", "Craft", "Kill"} <= {k["l"] for k in kinds}

    def test_craft_action_and_recipe_make_the_same_pickaxe(self, db):
        assert db.one("""MATCH (:Craft {id:'craft:minecraft:iron_pickaxe'})-[:CREATES]->(i)<-[:CREATES]-(:Recipe {id:'minecraft:iron_pickaxe'})
                         RETURN count(i)""") == 1

    def test_dye_brown_bed_needs_any_bed_but_brown(self, db):
        rows = db.q("""MATCH (:Recipe {id:'minecraft:dye_brown_bed'})-[q:REQUIRES]->(t) RETURN t.id AS id, q.excludes AS excludes ORDER BY id""")
        assert {"id": "#minecraft:beds", "excludes": ["minecraft:brown_bed"]} in rows
        assert any(r["id"] == "minecraft:brown_dye" for r in rows)

    def test_route_properties_for_the_iron_pickaxe(self, db):
        row = db.q("MATCH (i:Item {id:'minecraft:iron_pickaxe'}) RETURN i.routeSteps AS steps, i.routeStations AS stations")[0]
        assert row["steps"] >= 2 and "Crafting Table" in row["stations"] and "Furnace" in row["stations"]


# --------------------------------------------------------------------------- the authored model in the graph

class TestModelInGraph:
    """The test database holds tests/loader/fixtures/sample-process.yaml, because the real model has no process yet."""

    def test_there_is_one_player_and_no_organization(self, db):
        assert db.one("MATCH (p:Player) RETURN count(p)") == 1
        assert db.one("MATCH (n) WHERE n:OrgUnit OR n:Role RETURN count(n)") == 0
        assert db.one("MATCH ()-[r:PERFORMS|FILLED_BY|IN_UNIT|OWNS|HANDOFF_TO|ESCALATES_TO]->() RETURN count(r)") == 0

    def test_a_process_has_activities_and_every_task_is_in_one_activity(self, built, db):
        n = len(built["model_rows"]["tasks"])
        assert db.one("MATCH (t:Task) RETURN count(t)") == n
        assert db.one("MATCH (:Process)-[:HAS_ACTIVITY]->(:Activity) RETURN count(*)") == len(built["model_rows"]["activities"])
        assert db.one("MATCH (:Activity)-[:HAS_TASK]->(t:Task) RETURN count(DISTINCT t)") == n
        assert db.one("MATCH (t:Task) WHERE size([(t)<-[:HAS_TASK]-() | 1]) <> 1 RETURN count(t)") == 0

    def test_the_order_is_on_the_relationships_not_on_the_nodes(self, db):
        assert db.one("MATCH (a:Activity) WHERE a.seq IS NOT NULL RETURN count(a)") == 0
        assert db.one("MATCH (t:Task) WHERE t.seq IS NOT NULL RETURN count(t)") == 0
        assert db.one("MATCH ()-[r:HAS_ACTIVITY]->() WHERE r.seq IS NULL RETURN count(r)") == 0
        assert db.one("MATCH ()-[r:HAS_TASK]->() WHERE r.seq IS NULL RETURN count(r)") == 0

    def test_activities_and_tasks_are_numbered_from_one_without_gaps(self, db):
        for rel, parent in (("HAS_ACTIVITY", "Process"), ("HAS_TASK", "Activity")):
            rows = db.q(f"MATCH (p:{parent})-[r:{rel}]->() WITH p, collect(r.seq) AS seqs RETURN p.id AS id, seqs")
            assert rows
            for r in rows:
                assert sorted(r["seqs"]) == list(range(1, len(r["seqs"]) + 1)), (rel, r)

    def test_the_sample_reads_in_order_through_its_relationships(self, db):
        rows = db.q("""MATCH (:Process {id:'sample'})-[ha:HAS_ACTIVITY]->(a)-[ht:HAS_TASK]->(t)
                       RETURN ha.seq AS activity, ht.seq AS task, t.id AS id ORDER BY activity, task""")
        assert [r["id"] for r in rows] == ["sample/mine-iron", "sample/mine-logs", "sample/smelt", "sample/sticks",
                                           "sample/pickaxe", "sample/show-off"]
        assert [(r["activity"], r["task"]) for r in rows] == [(1, 1), (1, 2), (2, 1), (2, 2), (2, 3), (2, 4)]

    def test_has_action_orders_start_at_one_and_have_no_gaps(self, db):
        rows = db.q("MATCH (t:Task)-[h:HAS_ACTION]->() WITH t, collect(h.order) AS orders RETURN t.id AS task, orders")
        assert rows
        for r in rows:
            assert sorted(r["orders"]) == list(range(1, len(r["orders"]) + 1)), r
        assert db.one("MATCH ()-[h:HAS_ACTION]->() WHERE h.quantity < 1 RETURN count(h)") == 0

    def test_has_action_count_matches_the_yaml(self, built, db):
        assert db.one("MATCH ()-[h:HAS_ACTION]->() RETURN count(h)") == len(built["model_rows"]["task_actions"])

    def test_a_task_can_perform_two_ordered_actions(self, db):
        rows = db.q("MATCH (:Task {id:'sample/sticks'})-[h:HAS_ACTION]->(a) RETURN h.order AS o, a.id AS action ORDER BY o")
        assert rows == [{"o": 1, "action": "craft:minecraft:oak_planks"}, {"o": 2, "action": "craft:minecraft:stick"}]

    def test_the_manual_task_has_no_actions(self, db):
        assert db.one("MATCH (t:Task {id:'sample/show-off'}) RETURN size([(t)-[:HAS_ACTION]->() | 1])") == 0

    def test_a_branch_keeps_its_default_flag(self, db):
        rows = db.q("MATCH (:Task {id:'sample/pickaxe'})-[n:NEXT]->(b:Task) RETURN b.id AS to, n.isDefault AS d ORDER BY to")
        assert rows == [{"to": "sample/mine-iron", "d": False}, {"to": "sample/show-off", "d": True}]

    def test_the_default_path_runs_from_mining_to_the_pickaxe(self, db):
        paths = db.q("""MATCH p = (:Task {id:'sample/mine-iron'})-[:NEXT*]->(z:Task {id:'sample/show-off'})
                        WHERE all(r IN relationships(p) WHERE r.isDefault) RETURN [n IN nodes(p) | n.id] AS path""")
        assert paths == [{"path": ["sample/mine-iron", "sample/smelt", "sample/pickaxe", "sample/show-off"]}]

    def test_a_confirmed_process_is_confirmed_by_the_player_and_a_candidate_is_not(self, built, db):
        confirmed = len([t for t in built["model_rows"]["tasks"] if t["status"] == "confirmed"])
        assert confirmed > 0
        assert db.one("MATCH (:Task)-[:CONFIRMED_BY]->(:Player {name:'martinohanlon'}) RETURN count(*)") == confirmed
        assert db.one("MATCH (t:Task {status:'candidate'}) WHERE (t)-[:CONFIRMED_BY]->() RETURN count(t)") == 0
        assert db.one("MATCH (t:Task {status:'confirmed'}) WHERE NOT (t)-[:CONFIRMED_BY]->() RETURN count(t)") == 0

    def test_the_shelter_is_loaded_as_a_candidate_with_its_source(self, db):
        assert db.one("MATCH (:Process {id:'shelter'})-[:HAS_ACTIVITY]->(:Activity) RETURN count(*)") == 4
        row = db.q("MATCH (t:Task {id:'shelter/build-walls'}) RETURN t.status AS status, t.source AS source, t.confidence AS c")[0]
        assert row["status"] == "candidate" and "Tutorial:Shelters" in row["source"] and row["c"] == 0.7
        assert db.one("MATCH (t:Task {id:'shelter/build-walls'}) RETURN size([(t)-[:HAS_ACTION]->() | 1])") == 0   # a manual step

    def test_the_emeralds_process_is_a_candidate_with_two_ways_and_trading_is_the_default(self, db):
        assert db.one("MATCH (:Process {id:'emeralds'})-[:HAS_ACTIVITY]->(:Activity) RETURN count(*)") == 3
        rows = db.q("MATCH (:Task {id:'emeralds/choose-a-way'})-[n:NEXT]->(b:Task) RETURN b.id AS to, n.isDefault AS d ORDER BY to")
        assert rows == [{"to": "emeralds/find-village", "d": True}, {"to": "emeralds/make-an-iron-pickaxe", "d": False}]
        assert db.one("MATCH (t:Task {id:'emeralds/mine-the-ore'}) RETURN t.status") == "candidate"

    def test_the_nether_portal_has_three_ways_to_get_obsidian_and_mining_is_the_default(self, db):
        assert db.one("MATCH (:Process {id:'nether-portal'})-[:HAS_ACTIVITY]->(:Activity) RETURN count(*)") == 5
        rows = db.q("MATCH (:Task {id:'nether-portal/choose-a-way'})-[n:NEXT]->(b:Task) RETURN b.id AS to, n.isDefault AS d ORDER BY to")
        assert rows == [{"to": "nether-portal/find-a-ruined-portal", "d": False}, {"to": "nether-portal/make-a-bucket", "d": False},
                        {"to": "nether-portal/make-an-iron-pickaxe", "d": True}]

    def test_every_way_to_get_obsidian_ends_at_getting_flint(self, db):
        for start in ("make-an-iron-pickaxe", "make-a-bucket", "find-a-ruined-portal"):
            assert db.one(f"""MATCH (:Task {{id:'nether-portal/{start}'}})-[:NEXT*]->(t:Task {{id:'nether-portal/get-flint'}}) RETURN count(DISTINCT t)""") == 1, start

    def test_flint_can_be_dug_or_bought_and_the_purchase_is_a_real_trade(self, db):
        assert db.one("MATCH (:Task {id:'nether-portal/get-flint'})-[:HAS_ACTION]->(a:Mine)-[:CREATES]->(i:Item {id:'minecraft:flint'}) RETURN count(a)") == 1
        row = db.q("""MATCH (:Task {id:'nether-portal/buy-flint'})-[:HAS_ACTION]->(t:Trade)-[:CREATES]->(:Item {id:'minecraft:flint'})
                      MATCH (t)-[r:REQUIRES]->(i:Item) RETURN r.role AS role, i.name AS item, r.count AS count ORDER BY role""")
        assert row == [{"role": "additional", "item": "Emerald", "count": 1}, {"role": "price", "item": "Gravel", "count": 10}]
        assert db.one("MATCH (:Task {id:'nether-portal/get-flint'})-[:NEXT]->(b) WHERE b.id IN ['nether-portal/make-flint-and-steel', 'nether-portal/buy-flint'] RETURN count(b)") == 2

    def test_obsidian_is_mined_with_a_pickaxe_the_process_makes_first(self, db):
        tool = db.one("MATCH (:Mine {id:'mine:minecraft:obsidian'})-[:REQUIRES {role:'tool'}]->(i:Item) RETURN i.id")
        assert tool == "minecraft:diamond_pickaxe"
        assert db.one("MATCH (:Task {id:'nether-portal/make-a-diamond-pickaxe'})-[:HAS_ACTION]->(:Craft {id:'craft:minecraft:diamond_pickaxe'}) RETURN count(*)") == 1

    def test_a_task_can_perform_a_trade_which_is_an_action_in_the_trade_graph(self, db):
        row = db.q("""MATCH (t:Task {id:'emeralds/sell-wheat-to-a-farmer'})-[:HAS_ACTION]->(a:Trade)-[:CREATES]->(i:Item)
                      MATCH (a)-[r:REQUIRES {role:'price'}]->(p:Item)
                      RETURN a.id AS action, i.name AS gives, p.name AS price, r.count AS count""")
        assert row == [{"action": "trade:minecraft:farmer/1/wheat_emerald", "gives": "Emerald", "price": "Wheat", "count": 20}]

    def test_a_task_can_reach_who_sells_what_it_trades(self, db):
        who = db.one("""MATCH (:Task {id:'emeralds/sell-sticks-to-a-fletcher'})-[:HAS_ACTION]->(:Trade)<-[:INCLUDES]-(:TradeSet)<-[o:OFFERS]-(p:Profession)
                        RETURN p.name + ' level ' + toString(o.level)""")
        assert who == "Fletcher level 1"

    def test_the_mining_way_performs_the_mine_action_for_emerald_ore(self, db):
        assert db.one("MATCH (:Task {id:'emeralds/mine-the-ore'})-[:HAS_ACTION]->(a:Mine)-[c:CREATES {condition:'default'}]->(i:Item) RETURN i.id") == "minecraft:emerald"

    def test_the_shelter_has_a_branch_for_coal_or_charcoal_and_the_default_is_coal(self, db):
        rows = db.q("MATCH (:Task {id:'shelter/craft-door'})-[n:NEXT]->(b:Task) RETURN b.id AS to, n.isDefault AS d ORDER BY to")
        assert rows == [{"to": "shelter/mine-coal", "d": True}, {"to": "shelter/smelt-charcoal", "d": False}]


# --------------------------------------------------------------------------- the rebuild contract (mutates the database: keep last)

class TestRebuildContract:
    def test_model_load_is_repeatable(self, built, db):
        before = db.q("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY l")
        edges = db.one("MATCH ()-[r]->() RETURN count(r)")
        lm.write(built["model_rows"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.q("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY l") == before
        assert db.one("MATCH ()-[r]->() RETURN count(r)") == edges

    def test_reloading_actions_drops_the_task_links_and_the_model_load_restores_them(self, built, db):
        links = db.one("MATCH ()-[h:HAS_ACTION]->() RETURN count(h)")
        assert links > 0
        la.write(built["action_graph"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.one("MATCH ()-[h:HAS_ACTION]->() RETURN count(h)") == 0          # why the order is a contract
        assert db.one("MATCH (t:Trade) RETURN count(t)") == 0                      # the trades were Actions too
        ltr.write(built["trade_graph"], built["uri"], built["user"], built["password"], TEST_DB)
        lm.write(built["model_rows"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.one("MATCH ()-[h:HAS_ACTION]->() RETURN count(h)") == links

    def test_the_organization_of_earlier_versions_is_removed_by_a_model_load(self, built, db):
        db.q("CREATE CONSTRAINT role_id IF NOT EXISTS FOR (n:Role) REQUIRE n.id IS UNIQUE")
        db.q("MATCH (t:Task {id: 'sample/mine-iron'}) CREATE (:OrgUnit {id: 'old'})<-[:IN_UNIT]-(:Role {id: 'old-role'})-[:PERFORMS]->(t)")
        lm.write(built["model_rows"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.one("MATCH (n) WHERE n:OrgUnit OR n:Role RETURN count(n)") == 0
        assert db.one("SHOW CONSTRAINTS YIELD name WHERE name = 'role_id' RETURN count(*)") == 0
        assert db.one("MATCH (t:Task {id: 'sample/mine-iron'}) RETURN count(t)") == 1       # the task itself stays
