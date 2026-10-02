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
        assert db.one("MATCH (a:Action) RETURN count(a)") == sum(verbs.values())
        assert db.one("MATCH (b:Block) RETURN count(b)") == len(ag["blocks"])
        assert db.one("MATCH (m:Mob) RETURN count(m)") == len(ag["mobs"])
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
        assert db.one("MATCH (a)-[:INCLUDES]->(b) WHERE NOT (a:ItemClass AND b:Item) RETURN count(*)") == 0

    def test_unique_constraints_exist(self, db):
        rows = db.q("SHOW CONSTRAINTS YIELD labelsOrTypes, properties, type WHERE type CONTAINS 'UNIQUENESS' RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
        pairs = {(r["label"], r["prop"]) for r in rows}
        assert {("Item", "id"), ("Recipe", "id"), ("Action", "id"), ("Block", "id"), ("Mob", "id"), ("Task", "id"),
                ("Role", "id"), ("OrgUnit", "id"), ("Process", "id"), ("Activity", "id"), ("Player", "uuid")} <= pairs

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
    def test_organization(self, db):
        assert db.one("MATCH (u:OrgUnit) RETURN count(u)") == 6
        assert db.one("MATCH (r:Role) RETURN count(r)") == 9
        assert db.one("MATCH (r:Role)-[:IN_UNIT]->(:OrgUnit) RETURN count(r)") == 9
        assert db.one("MATCH (u:OrgUnit)-[:PART_OF]->(:OrgUnit {id:'minecorp'}) RETURN count(u)") == 5

    def test_one_player_fills_every_role(self, db):
        assert db.one("MATCH (p:Player) RETURN count(p)") == 1
        assert db.one("MATCH (r:Role)-[:FILLED_BY]->(:Player {name:'martinohanlon'}) RETURN count(DISTINCT r)") == 9

    def test_process_has_one_owner(self, db):
        assert db.one("MATCH (r:Role)-[:OWNS]->(p:Process) RETURN count(p)") == db.one("MATCH (p:Process) RETURN count(p)") == 1
        assert db.one("MATCH (:Role {id:'process-owner'})-[:OWNS]->(:Process {id:'iron-tool-line'}) RETURN count(*)") == 1

    def test_every_task_has_one_role_and_one_activity(self, built, db):
        n = len(built["model_rows"]["tasks"])
        assert db.one("MATCH (t:Task) RETURN count(t)") == n
        assert db.one("MATCH (:Role)-[:PERFORMS]->(t:Task) RETURN count(DISTINCT t)") == n
        assert db.one("MATCH (t:Task) WITH t, size([(t)<-[:PERFORMS]-() | 1]) AS roles WHERE roles <> 1 RETURN count(t)") == 0
        assert db.one("MATCH (:Activity)-[:HAS_TASK]->(t:Task) RETURN count(DISTINCT t)") == n

    def test_has_action_orders_start_at_one_and_have_no_gaps(self, db):
        rows = db.q("MATCH (t:Task)-[h:HAS_ACTION]->() WITH t, collect(h.order) AS orders RETURN t.id AS task, orders")
        assert rows
        for r in rows:
            assert sorted(r["orders"]) == list(range(1, len(r["orders"]) + 1)), r
        assert db.one("MATCH ()-[h:HAS_ACTION]->() WHERE h.quantity < 1 RETURN count(h)") == 0

    def test_has_action_count_matches_the_yaml(self, built, db):
        assert db.one("MATCH ()-[h:HAS_ACTION]->() RETURN count(h)") == len(built["model_rows"]["task_actions"])

    def test_a_task_can_perform_two_ordered_actions(self, db):
        rows = db.q("MATCH (:Task {id:'iron-tool-line/make-sticks'})-[h:HAS_ACTION]->(a) RETURN h.order AS o, a.id AS action ORDER BY o")
        assert rows == [{"o": 1, "action": "craft:minecraft:oak_planks"}, {"o": 2, "action": "craft:minecraft:stick"}]

    def test_the_manual_task_has_no_actions(self, db):
        assert db.one("MATCH (t:Task {id:'iron-tool-line/stock-tools-chest'}) RETURN size([(t)-[:HAS_ACTION]->() | 1])") == 0

    def test_handoffs_exist_exactly_where_the_role_changes(self, db):
        assert db.one("""MATCH (a:Task)-[:NEXT]->(b:Task) WHERE NOT (a)-[:HANDOFF_TO]->(b)
                         AND NOT EXISTS { (:Role)-[:PERFORMS]->(a)<-[:PERFORMS]-(:Role) } WITH a, b
                         MATCH (r1:Role)-[:PERFORMS]->(a), (r2:Role)-[:PERFORMS]->(b) WHERE r1 <> r2 RETURN count(*)""") == 0
        assert db.one("""MATCH (a:Task)-[:HANDOFF_TO]->(b:Task) MATCH (r1:Role)-[:PERFORMS]->(a), (r2:Role)-[:PERFORMS]->(b)
                         WHERE r1 = r2 RETURN count(*)""") == 0

    def test_the_default_path_runs_from_mining_to_storing(self, db):
        paths = db.q("""MATCH p = (:Task {id:'iron-tool-line/mine-iron-ore'})-[:NEXT*]->(z:Task)
                        WHERE NOT (z)-[:NEXT]->() AND all(r IN relationships(p) WHERE r.isDefault)
                        RETURN [n IN nodes(p) | n.id] AS path""")
        assert paths == [{"path": ["iron-tool-line/mine-iron-ore", "iron-tool-line/smelt-raw-iron",
                                   "iron-tool-line/craft-iron-pickaxe", "iron-tool-line/stock-tools-chest"]}]

    def test_every_task_is_confirmed_by_the_steward(self, built, db):
        n = len(built["model_rows"]["tasks"])
        assert db.one("MATCH (:Task)-[:CONFIRMED_BY]->(:Role {id:'steward'}) RETURN count(*)") == n

    def test_escalation(self, db):
        assert db.one("MATCH (:Task {id:'iron-tool-line/smelt-raw-iron'})-[e:ESCALATES_TO]->(:Role {id:'process-owner'}) RETURN count(e)") == 1


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
        lm.write(built["model_rows"], built["uri"], built["user"], built["password"], TEST_DB)
        assert db.one("MATCH ()-[h:HAS_ACTION]->() RETURN count(h)") == links
