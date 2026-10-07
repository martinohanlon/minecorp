"""Tests for `describe` and `where_to_find` against the real graph, rebuilt from source in the throwaway database.

No model is involved. Run with:  pytest -m integration
"""
from __future__ import annotations

import json

import pytest

from minecorp_agent import tools, world
from minecorp_agent.budget import fit

pytestmark = [pytest.mark.integration, pytest.mark.jar]


def call(graph, tool, **args):
    return tools.TOOLS_BY_NAME[tool].fn(graph, args)


def result_for(out, kind):
    return next(r for r in out["results"] if r["kind"] == kind)


class TestWhereToFindAnItem:
    def test_diamonds_are_mined_deep_in_the_overworld(self, agent_graph):
        found = result_for(call(agent_graph, "where_to_find", name="diamond"), "item")["found"]
        assert len(found) == 1 and found[0]["how"] == "mine"
        assert set(found[0]["from"]) == {"Diamond Ore", "Deepslate Diamond Ore"}   # same places, so one entry
        assert found[0]["tool"] == "Iron Pickaxe or better"
        where = found[0]["where"][0]
        assert where["dimension"] == "Overworld" and where["biomes"] == "every Overworld biome"
        assert {p["y"] for p in where["placements"]} == {"-64 to 16", "-64 to -4"}

    def test_each_placement_says_how_it_is_spread_and_the_whole_span_is_given(self, agent_graph):
        where = result_for(call(agent_graph, "where_to_find", name="coal"), "item")["found"][-1]["where"][0]
        assert where["anywhereFrom"] == "y 0 to 319"
        assert {p["spread"] for p in where["placements"]} == {"most common around y 96, fewer towards the ends of the range",
                                                              "spread evenly over the range"}

    def test_a_placement_says_how_many_veins_and_how_big(self, agent_graph):
        placements = result_for(call(agent_graph, "where_to_find", name="diamond"), "item")["found"][0]["where"][0]["placements"]
        assert all({"veinsPerChunk", "veinSize"} <= set(p) for p in placements)

    def test_gunpowder_comes_from_mobs_and_says_where_they_are(self, agent_graph):
        found = result_for(call(agent_graph, "where_to_find", name="gunpowder"), "item")["found"]
        sources = {f["from"]: f for f in found}
        assert {"Creeper", "Ghast", "Witch"} <= set(sources)
        assert sources["Ghast"]["spawnsIn"][0]["dimension"] == "The Nether"
        assert "Swamp Hut (Overworld)" in sources["Witch"]["structures"]
        assert any(s.startswith("Fortress") and "The Nether" in s for s in sources["Wither Skeleton"]["structures"]) if "Wither Skeleton" in sources else True

    def test_an_item_nothing_drops_has_no_place(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="Iron Pickaxe"), "item")
        assert "found" not in out and "get_recipes" in out["note"]


class TestWhereToFindOtherKinds:
    def test_a_name_that_is_an_item_and_a_block_gives_both(self, agent_graph):
        kinds = {r["kind"] for r in call(agent_graph, "where_to_find", name="Diamond Ore")["results"]}
        assert kinds == {"item", "block"}

    def test_a_block_has_its_placements(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="Diamond Ore"), "block")
        assert out["where"][0]["dimension"] == "Overworld"

    def test_a_block_with_no_ore_placement_says_so(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="Crafting Table"), "block")
        assert "no ore placement" in out["note"]

    def test_a_creeper_spawns_in_most_of_the_overworld_but_not_everywhere(self, agent_graph):
        spawns = call(agent_graph, "where_to_find", name="creeper")["results"][0]["spawnsIn"]
        assert spawns[0]["category"] == "monster" and spawns[0]["dimension"] == "Overworld"
        assert spawns[0]["biomes"].startswith("every Overworld biome except ") and "Deep Dark" in spawns[0]["biomes"]

    def test_a_ghast_is_found_in_the_nether_only(self, agent_graph):
        spawns = call(agent_graph, "where_to_find", name="ghast")["results"][0]["spawnsIn"]
        assert {s["dimension"] for s in spawns} == {"The Nether"}

    def test_a_mob_that_never_spawns_on_its_own_says_so(self, agent_graph):
        out = call(agent_graph, "where_to_find", name="villager")["results"][0]
        assert "spawnsIn" not in out and "trade" in out["note"]

    def test_a_desert_has_its_mobs_structures_and_ores(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="desert"), "biome")
        assert out["dimension"] == "Overworld"
        assert {"Husk", "Camel"} <= {m for names in out["mobs"].values() for m in names}
        assert "Desert Pyramid" in out["structures"]
        assert any(o["block"] == "Coal Ore" for o in out["ores"])

    def test_a_structure_says_which_biomes_it_is_in_and_what_it_spawns(self, agent_graph):
        out = call(agent_graph, "where_to_find", name="fortress")["results"][0]
        assert out["kind"] == "structure" and out["generatesIn"][0]["dimension"] == "The Nether"
        assert "Blaze" in out["spawns"]


class TestNamesAndErrors:
    def test_an_unknown_name(self, agent_graph):
        out = call(agent_graph, "where_to_find", name="flurble")
        assert out["type"] == "bad_argument" and "find" in out["error"]

    def test_a_name_that_fits_too_many_things_lists_candidates(self, agent_graph):
        out = call(agent_graph, "where_to_find", name="village")
        assert "ambiguous" in out["error"] and len(out["candidates"]) >= 5
        assert all({"name", "kind"} <= set(c) for c in out["candidates"])

    @pytest.mark.parametrize("tool", ["describe", "where_to_find"])
    @pytest.mark.parametrize("name", ["", "   "])
    def test_a_missing_name_is_a_bad_argument(self, agent_graph, tool, name):
        assert call(agent_graph, tool, name=name)["type"] == "bad_argument"

    def test_a_category_is_not_a_place(self, agent_graph):
        assert "error" in call(agent_graph, "where_to_find", name="planks")

    @pytest.mark.parametrize("tool,name", [("where_to_find", "diamond"), ("where_to_find", "desert"), ("describe", "desert"),
                                           ("describe", "diamond ore"), ("describe", "creeper")])
    def test_results_fit_their_budget_without_losing_anything(self, agent_graph, tool, name):
        out = call(agent_graph, tool, name=name)
        assert "omitted" not in fit(out, tools.TOOLS_BY_NAME[tool].budget)


class TestBiomeLists:
    def test_all_of_a_dimension(self, agent_graph):
        everywhere = world.dimension_biomes(agent_graph)["Overworld"]
        assert world.biomes_text(dict(everywhere), everywhere, "Overworld") == {"biomes": "every Overworld biome", "count": len(everywhere)}

    def test_all_but_a_few(self):
        everywhere = {f"b{i}": f"Biome {i}" for i in range(30)}
        here = {k: v for k, v in everywhere.items() if k not in ("b1", "b2")}
        assert world.biomes_text(here, everywhere, "Overworld")["biomes"] == "every Overworld biome except Biome 1, Biome 2"

    def test_a_few_are_listed(self):
        everywhere = {f"b{i}": f"Biome {i}" for i in range(30)}
        assert world.biomes_text({"b3": "Biome 3", "b1": "Biome 1"}, everywhere, "Overworld") == {"biomes": ["Biome 1", "Biome 3"], "count": 2}

    def test_many_are_cut_with_a_count(self):
        everywhere = {f"b{i}": f"Biome {i}" for i in range(60)}
        out = world.biomes_text({k: v for k, v in list(everywhere.items())[:20]}, everywhere, "Overworld")
        assert len(out["biomes"]) == world.MAX_BIOMES_LISTED + 1 and out["biomes"][-1] == "and 10 more" and out["count"] == 20

    def test_the_dimensions_are_the_three_the_game_has(self, agent_graph):
        assert set(world.dimension_biomes(agent_graph)) == {"Overworld", "The Nether", "The End"}


class TestDescribe:
    def test_an_item(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="Iron Pickaxe"), "item")
        assert out["craftable"] is True and out["recipes"] >= 1

    def test_a_raw_material_is_not_craftable_and_has_ways_from_the_world(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="gunpowder"), "item")
        assert out["waysFromWorld"] >= 3

    def test_a_block_says_what_it_drops_and_with_what(self, agent_graph):
        drops = result_for(call(agent_graph, "describe", name="Diamond Ore"), "block")["drops"]
        assert {"item": "Diamond", "tool": "Iron Pickaxe or better"} in drops
        assert any(d.get("when") == "silk touch" for d in drops)

    def test_a_mob_says_what_it_drops_and_where_it_spawns_in_numbers(self, agent_graph):
        out = call(agent_graph, "describe", name="creeper")["results"][0]
        assert any(d["item"] == "Gunpowder" for d in out["drops"])
        assert out["spawnsInBiomes"] > 40

    def test_a_villager_drops_nothing_and_spawns_nowhere_on_its_own(self, agent_graph):
        out = call(agent_graph, "describe", name="villager")["results"][0]
        assert out["drops"] == [] and out["spawnsInBiomes"] == 0

    def test_a_biome(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="desert"), "biome")
        assert out["dimension"] == "Overworld" and out["hasPrecipitation"] is False and out["structures"] >= 5

    def test_a_structure(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="fortress"), "structure")
        assert out["spawnsMobs"] >= 1

    def test_a_category_lists_examples(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="planks"), "category")
        assert out["members"] == 12 and "Oak Planks" in out["examples"]

    def test_a_thing_is_not_listed_as_counting_as_itself(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="Diamond Ore"), "item")
        assert "Diamond Ore" not in out.get("countsAs", [])

    def test_unknown(self, agent_graph):
        assert "error" in call(agent_graph, "describe", name="flurble")

    def test_everything_is_json(self, agent_graph):
        json.dumps(call(agent_graph, "describe", name="Diamond Ore"))


class TestDescribeWiki:
    def test_wiki_sections_are_listed_when_the_wiki_is_linked(self, agent_graph, db):
        db.q("MERGE (p:Page {url: 'https://w/test'}) SET p.title = 'Test'")
        db.q("""MATCH (p:Page {url: 'https://w/test'}), (m:Mob {id: 'minecraft:creeper'})
                MERGE (p)-[:HAS_SECTION]->(s:Section {url: 'https://w/test#a', title: 'Test'})
                MERGE (c:Chunk {id: 'c-test'}) MERGE (c)-[:PART_OF]->(s) MERGE (c)-[:MENTIONS {count: 3, linkCount: 1}]->(m)""")
        try:
            out = call(agent_graph, "describe", name="creeper")["results"][0]
            assert out["wiki"] == [{"section": "Test", "sectionUrl": "https://w/test#a"}]
            assert "read_section" in call(agent_graph, "describe", name="creeper")["note"]
        finally:
            db.q("MATCH (n) WHERE n.id = 'c-test' OR n.url STARTS WITH 'https://w/test' DETACH DELETE n")

    def test_no_wiki_key_when_nothing_mentions_it(self, agent_graph):
        assert "wiki" not in call(agent_graph, "describe", name="ghast")["results"][0]
