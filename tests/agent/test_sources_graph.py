"""The sources the real tools give, against the real graph in the throwaway database.

Two things are checked: the keys sources.py reads are the keys the tools write, and, the point of the graph patterns, every pattern a
player would be shown is a path that EXISTS in the graph: its Cypher form is run as a MATCH and must find something. A pattern on a page is
what the tool walked, not decoration.

No model is involved. The wiki tools are checked against the wiki test database in test_wiki_graph.py.
Run with:  pytest -m integration"""
from __future__ import annotations

import pytest

from minecorp_agent import sources as src
from minecorp_agent import tools

pytestmark = [pytest.mark.integration, pytest.mark.jar]

# calls that between them use every pattern builder
CALLS = [
    ("get_recipes", {"name": "eye of ender"}), ("get_recipes", {"name": "iron pickaxe"}), ("get_recipes", {"name": "pickaxe"}),
    ("get_recipes", {"name": "white bed"}), ("what_do_i_need", {"name": "torch"}), ("what_do_i_need", {"name": "iron pickaxe"}),
    ("what_do_i_need", {"name": "diamond"}), ("how_to_get", {"name": "emerald"}), ("how_to_get", {"name": "bread"}),
    ("how_to_get", {"name": "gunpowder"}), ("how_to_get", {"name": "white wool"}), ("get_uses", {"name": "diamond"}),
    ("get_uses", {"name": "iron ingot"}), ("members", {"name": "planks"}), ("members", {"name": "undead"}), ("members", {"name": "is forest"}),
    ("describe", {"name": "creeper"}), ("describe", {"name": "Diamond Ore"}), ("describe", {"name": "desert"}), ("describe", {"name": "mansion"}),
    ("describe", {"name": "farmer"}), ("describe", {"name": "villager"}), ("describe", {"name": "wandering trader"}), ("describe", {"name": "emerald"}),
    ("describe", {"name": "planks"}), ("where_to_find", {"name": "diamond"}), ("where_to_find", {"name": "coal"}), ("where_to_find", {"name": "creeper"}),
    ("where_to_find", {"name": "desert"}), ("where_to_find", {"name": "mansion"}), ("where_to_find", {"name": "gunpowder"}),
    ("where_to_find", {"name": "emerald"}), ("where_to_find", {"name": "bread"}), ("get_process", {"name": "nether"}),
    ("get_process", {"name": "dragon"}), ("get_process", {"name": "emeralds"}), ("get_process", {"name": "shelter"}), ("get_process", {"name": "mob farm"}),
    ("get_process", {"name": "nether", "step": "mine 10 obsidian"}), ("get_process", {"name": "emeralds", "step": "sell wheat"}),
    ("get_process", {"name": "dragon", "step": "kill the dragon"})]


def call(graph, tool, **args):
    return tools.TOOLS_BY_NAME[tool].fn(graph, args)


def patterns_of(graph, tool, args):
    out = call(graph, tool, **args)
    return [p for _src, patterns in src.patterned(tool, args, out) for p in patterns]


def labels(sources):
    return [(s["kind"], s["label"]) for s in sources]


@pytest.mark.parametrize("tool,args", CALLS, ids=lambda v: v if isinstance(v, str) else v.get("name", "") + ("/" + v["step"] if "step" in v else ""))
def test_every_pattern_shown_is_a_path_that_exists(agent_graph, tool, args):
    patterns = patterns_of(agent_graph, tool, args)
    assert patterns, f"{tool} {args} gave no pattern"
    for p in patterns:
        rows = agent_graph.read(f"MATCH {p.cypher()} RETURN count(*) AS n")
        assert rows[0]["n"] > 0, p.cypher()


def test_the_compact_form_is_the_cypher_form_without_the_syntax_a_player_does_not_need(agent_graph):
    for tool, args in CALLS:
        for p in patterns_of(agent_graph, tool, args):
            compact, cypher = p.compact(), p.cypher()
            assert "[:" not in compact and "{name:" not in compact and "{title:" not in compact and "(:" not in compact
            assert compact.count("(") == cypher.count("(") and compact.count("[") == cypher.count("[")


class TestGameData:
    @pytest.mark.parametrize("tool,args,label", [
        ("get_recipes", {"name": "iron pickaxe"}, "Recipes for Iron Pickaxe"),
        ("what_do_i_need", {"name": "torch"}, "What is needed to make Torch"),
        ("how_to_get", {"name": "emerald"}, "How to get Emerald"),
        ("get_uses", {"name": "diamond"}, "What Diamond is used for"),
        ("members", {"name": "planks"}, "What counts as Planks"),
        ("describe", {"name": "creeper"}, "About Creeper"),
        ("where_to_find", {"name": "desert"}, "Where to find Desert"),
        ("rank_recipes", {}, "Recipe difficulty ranking")])
    def test_each_game_data_tool_gives_one_named_source(self, agent_graph, tool, args, label):
        assert labels(src.sources_for(tool, args, call(agent_graph, tool, **args))) == [("game data", label)]

    def test_the_sources_carry_their_graph_lines(self, agent_graph):
        (s,) = src.sources_for("get_recipes", {"name": "eye of ender"}, call(agent_graph, "get_recipes", name="eye of ender"))
        assert s["graph"] == ["(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})",
                              "(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Ender Pearl})",
                              "(Item {Ender Eye})<-[CREATES]-(Recipe)-[USES_STATION]->(Item {Crafting Table})"]

    def test_an_iron_pickaxe_goes_through_the_category_the_graph_really_requires(self, agent_graph):
        lines = "\n".join(patterns_of(agent_graph, "get_recipes", {"name": "iron pickaxe"})[i].compact() for i in range(3))
        assert "(ItemClass {Iron Tool Materials})-[INCLUDES]->(Item {Iron Ingot})" in lines

    def test_the_category_is_not_in_what_the_model_is_given(self, agent_graph):
        # the model sees "Iron Ingot" as the ingredient: the category is private to the sources (budget.without_private)
        from minecorp_agent.budget import without_private
        import json
        out = call(agent_graph, "get_recipes", name="iron pickaxe")
        assert "_via" in json.dumps(out) and "_via" not in json.dumps(without_private(out)) and "Iron Tool Materials" not in json.dumps(without_private(out))

    def test_a_trade_is_drawn_through_its_trade_set_and_profession(self, agent_graph):
        lines = [p.compact() for p in patterns_of(agent_graph, "how_to_get", {"name": "bread"})]
        assert lines == ["(Item {Bread})<-[CREATES]-(Trade)<-[INCLUDES]-(TradeSet)<-[OFFERS]-(Profession {Farmer})"]

    def test_the_path_from_an_ore_to_its_biomes(self, agent_graph):
        lines = [p.compact() for p in patterns_of(agent_graph, "where_to_find", {"name": "diamond"})]
        assert "(Block {Deepslate Diamond Ore})<-[CREATES]-(Feature)<-[PLACES]-(PlacedFeature)<-[GENERATES]-(Biome)" in lines

    def test_a_name_that_is_an_item_and_a_block_gives_both(self, agent_graph):
        labels_ = [s["label"] for s in src.sources_for("describe", {}, call(agent_graph, "describe", name="Diamond Ore"))]
        assert labels_.count("About Diamond Ore") >= 2

    def test_a_generic_name_that_showed_every_pickaxe_names_each(self, agent_graph):
        got = {s["label"] for s in src.sources_for("get_recipes", {}, call(agent_graph, "get_recipes", name="pickaxe"))}
        assert {"Recipes for Iron Pickaxe", "Recipes for Diamond Pickaxe", "Recipes for Netherite Pickaxe"} <= got

    def test_every_source_has_at_most_four_graph_lines(self, agent_graph):
        for tool, args in CALLS:
            for s in src.sources_for(tool, args, call(agent_graph, tool, **args)):
                assert len(s.get("graph", [])) <= 4, (tool, args, s["label"])


class TestPlans:
    def test_a_plan_gives_the_plan_and_the_wiki_pages_it_came_from_with_links(self, agent_graph):
        got = src.sources_for("get_process", {}, call(agent_graph, "get_process", name="nether"))
        assert (got[0]["kind"], got[0]["label"]) == ("plan", "Get to the Nether (draft, not confirmed)")
        assert got[0]["graph"][0] == "(Process {Get to the Nether})-[HAS_ACTIVITY]->(Activity {Choose a way})-[HAS_TASK]->(Task {Choose how to get the obsidian})"
        wiki = {s["label"]: s["url"] for s in got[1:]}
        assert wiki == {"Tutorial:Shelters > Nether portal room": "https://minecraft.wiki/w/Tutorial:Shelters#Nether_portal_room",
                        "Tutorial:Nether portals": "https://minecraft.wiki/w/Tutorial:Nether_portals"}
        assert all("graph" not in s for s in got[1:])             # a link out to the wiki, not a path in the graph

    def test_the_dragon_shows_that_it_builds_on_the_nether(self, agent_graph):
        lines = [p.compact() for p in patterns_of(agent_graph, "get_process", {"name": "dragon"})]
        assert "(Process {Defeat the ender dragon})-[BUILDS_ON]->(Process {Get to the Nether})" in lines

    def test_a_step_that_performs_a_trade_shows_the_trade_as_its_action(self, agent_graph):
        lines = [p.compact() for p in patterns_of(agent_graph, "get_process", {"name": "emeralds", "step": "sell wheat"})]
        assert "(Task {Sell wheat to a farmer})-[HAS_ACTION]->(Action {Trade 20 Wheat for Emerald})" in lines

    def test_one_step_gives_the_same_plan_source_as_its_outline(self, agent_graph):
        plan = src.sources_for("get_process", {}, call(agent_graph, "get_process", name="nether"))
        step = src.sources_for("get_process", {}, call(agent_graph, "get_process", name="nether", step="mine 10 obsidian"))
        assert labels(src.merge_sources(plan, step)) == labels(plan)

    def test_every_plan_names_wiki_pages_that_are_real_links(self, agent_graph):
        for name in ("nether", "dragon", "emeralds", "shelter", "mob farm"):
            wiki = [s for s in src.sources_for("get_process", {}, call(agent_graph, "get_process", name=name)) if s["kind"] == "wiki"]
            assert wiki and all(s["url"].startswith("https://minecraft.wiki/w/Tutorial:") for s in wiki), name


class TestNoSource:
    def test_the_list_of_plans_and_a_name_search_are_not_sources(self, agent_graph):
        assert src.sources_for("get_process", {}, call(agent_graph, "get_process")) == []
        assert src.sources_for("find", {}, call(agent_graph, "find", query="iron")) == []

    def test_a_failed_lookup_gives_no_source(self, agent_graph):
        assert src.sources_for("get_recipes", {}, call(agent_graph, "get_recipes", name="flurble")) == []
        assert src.sources_for("get_process", {}, call(agent_graph, "get_process", name="build a rocket")) == []
