"""The sources the real tools give, against the real graph in the throwaway database: the keys sources.py reads are the keys the tools write.

No model is involved. The wiki search embeds the question with OpenAI, so it is checked with a section read instead.
Run with:  pytest -m integration"""
from __future__ import annotations

import pytest

from minecorp_agent import sources as src
from minecorp_agent import tools

pytestmark = [pytest.mark.integration, pytest.mark.jar]


def sources_of(graph, tool, **args):
    return src.sources_for(tool, args, tools.TOOLS_BY_NAME[tool].fn(graph, args))


@pytest.mark.parametrize("tool,args,label", [
    ("get_recipes", {"name": "iron pickaxe"}, "Recipes for Iron Pickaxe"),
    ("what_do_i_need", {"name": "torch"}, "What is needed to make Torch"),
    ("how_to_get", {"name": "emerald"}, "How to get Emerald"),
    ("get_uses", {"name": "diamond"}, "What Diamond is used for"),
    ("members", {"name": "planks"}, "What counts as Planks"),
    ("describe", {"name": "creeper"}, "About Creeper"),
    ("where_to_find", {"name": "desert"}, "Where to find Desert"),
    ("rank_recipes", {}, "Recipe difficulty ranking")])
def test_each_game_data_tool_gives_one_named_source(agent_graph, tool, args, label):
    assert sources_of(agent_graph, tool, **args) == [{"kind": "game data", "label": label}]


def test_a_name_that_is_an_item_and_a_block_gives_both(agent_graph):
    labels = [s["label"] for s in sources_of(agent_graph, "describe", name="Diamond Ore")]
    assert labels.count("About Diamond Ore") >= 2


def test_a_generic_name_that_showed_every_pickaxe_names_each(agent_graph):
    labels = {s["label"] for s in sources_of(agent_graph, "get_recipes", name="pickaxe")}
    assert {"Recipes for Iron Pickaxe", "Recipes for Diamond Pickaxe", "Recipes for Netherite Pickaxe"} <= labels


def test_a_plan_gives_the_plan_and_the_wiki_pages_it_came_from_with_links(agent_graph):
    got = sources_of(agent_graph, "get_process", name="nether")
    assert got[0] == {"kind": "plan", "label": "Get to the Nether (draft, not confirmed)"}
    wiki = {s["label"]: s["url"] for s in got[1:]}
    assert wiki == {"Tutorial:Shelters > Nether portal room": "https://minecraft.wiki/w/Tutorial:Shelters#Nether_portal_room",
                    "Tutorial:Nether portals": "https://minecraft.wiki/w/Tutorial:Nether_portals"}


def test_one_step_gives_the_same_sources_as_the_plan(agent_graph):
    plan = sources_of(agent_graph, "get_process", name="nether")
    step = sources_of(agent_graph, "get_process", name="nether", step="mine 10 obsidian")
    assert src.merge_sources(plan, step) == plan


def test_the_list_of_plans_and_a_name_search_are_not_sources(agent_graph):
    assert sources_of(agent_graph, "get_process") == []
    assert sources_of(agent_graph, "find", query="iron") == []


def test_a_failed_lookup_gives_no_source(agent_graph):
    assert sources_of(agent_graph, "get_recipes", name="flurble") == []
    assert sources_of(agent_graph, "get_process", name="build a rocket") == []


# the wiki tools are checked against the wiki test database in test_wiki_graph.py


def test_every_plan_names_wiki_pages_that_are_real_links(agent_graph):
    for name in ("nether", "dragon", "emeralds", "shelter", "mob farm"):
        wiki = [s for s in sources_of(agent_graph, "get_process", name=name) if s["kind"] == "wiki"]
        assert wiki and all(s["url"].startswith("https://minecraft.wiki/w/Tutorial:") for s in wiki), name
