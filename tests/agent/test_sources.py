"""Unit tests for agent/minecorp_agent/sources.py: what each tool result gives the player as sources. No database, no model."""
from __future__ import annotations

import pytest

from minecorp_agent import sources as src

GAME = "game data"


class TestLabelFromUrl:
    def test_a_page(self):
        assert src.label_from_url("https://minecraft.wiki/w/Tutorial:Mob_farm") == "Tutorial:Mob farm"

    def test_a_section_follows_the_page(self):
        assert src.label_from_url("https://minecraft.wiki/w/Tutorial:Mob_farm#Sinkhole") == "Tutorial:Mob farm > Sinkhole"

    def test_percent_codes_and_underscores_are_read(self):
        assert src.label_from_url("https://minecraft.wiki/w/Tutorial:Beginner%27s_guide#Making_shelter") == "Tutorial:Beginner's guide > Making shelter"


class TestWikiSources:
    def test_several_urls_separated_by_semicolons(self):
        got = src.wiki_sources("https://minecraft.wiki/w/Tutorial:Shelters#Bedroom ; https://minecraft.wiki/w/Tutorial:Nether_portals")
        assert got == [{"kind": "wiki", "label": "Tutorial:Shelters > Bedroom", "url": "https://minecraft.wiki/w/Tutorial:Shelters#Bedroom"},
                       {"kind": "wiki", "label": "Tutorial:Nether portals", "url": "https://minecraft.wiki/w/Tutorial:Nether_portals"}]

    @pytest.mark.parametrize("text", ["", None, "not a url", " ; "])
    def test_nothing_to_link(self, text):
        assert src.wiki_sources(text) == []


class TestMerge:
    def test_each_source_once_in_order(self):
        a = {"kind": GAME, "label": "A"}
        b = {"kind": GAME, "label": "B"}
        assert src.merge_sources([a, b], [b, a]) == [a, b]

    def test_the_same_label_of_a_different_kind_is_a_different_source(self):
        assert len(src.merge_sources([{"kind": "wiki", "label": "X"}], [{"kind": GAME, "label": "X"}])) == 2

    def test_at_most_the_cap(self):
        many = [{"kind": GAME, "label": str(i)} for i in range(20)]
        assert len(src.merge_sources(many)) == src.MAX_SOURCES
        assert len(src.merge_sources(many, cap=3)) == 3


class TestGameData:
    @pytest.mark.parametrize("tool,output,label", [
        ("get_recipes", {"item": "Iron Pickaxe", "recipes": []}, "Recipes for Iron Pickaxe"),
        ("what_do_i_need", {"item": "Torch"}, "What is needed to make Torch"),
        ("how_to_get", {"item": "Emerald", "ways": []}, "How to get Emerald"),
        ("get_uses", {"item": "Diamond"}, "What Diamond is used for"),
        ("rank_recipes", {"items": [{"item": "x"}]}, "Recipe difficulty ranking")])
    def test_one_lookup_one_source(self, tool, output, label):
        assert src.sources_for(tool, {}, output) == [{"kind": GAME, "label": label}]

    def test_a_generic_name_that_showed_several_items_names_each(self):
        out = {"items": [{"item": "Iron Pickaxe", "recipes": []}, {"item": "Stone Pickaxe", "recipes": []}]}
        assert [s["label"] for s in src.sources_for("get_recipes", {}, out)] == ["Recipes for Iron Pickaxe", "Recipes for Stone Pickaxe"]

    @pytest.mark.parametrize("tool,label", [("members", "What counts as Planks"), ("describe", "About Planks"), ("where_to_find", "Where to find Planks")])
    def test_a_tool_that_answers_for_several_things_names_each(self, tool, label):
        out = {"results": [{"name": "Planks", "kind": "category"}, {"name": "Planks", "kind": "item"}]}
        assert [s["label"] for s in src.sources_for(tool, {}, out)] == [label, label]

    def test_an_empty_ranking_is_no_source(self):
        assert src.sources_for("rank_recipes", {}, {"items": []}) == []


class TestWiki:
    def test_every_passage_the_search_returned_is_a_source_with_its_link(self):
        out = {"hits": [{"section": "Tutorial:Shelters > Hole in the wall", "sectionUrl": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"},
                        {"section": "Tutorial:Mining", "sectionUrl": "https://minecraft.wiki/w/Tutorial:Mining"}]}
        got = src.sources_for("search_wiki", {}, out)
        assert [s["label"] for s in got] == ["Tutorial:Shelters > Hole in the wall", "Tutorial:Mining"]
        assert got[0]["kind"] == "wiki" and got[0]["url"].endswith("#Hole_in_the_wall")

    def test_a_search_with_no_hits_is_no_source(self):
        assert src.sources_for("search_wiki", {}, {"hits": [], "note": "Nothing"}) == []

    def test_a_section_that_was_read(self):
        out = {"page": "Tutorial:Mining", "section": "Tutorial:Mining > Mining layers", "sectionUrl": "https://minecraft.wiki/w/Tutorial:Mining#Mining_layers"}
        assert src.sources_for("read_section", {}, out) == [{"kind": "wiki", "label": "Tutorial:Mining > Mining layers",
                                                             "url": "https://minecraft.wiki/w/Tutorial:Mining#Mining_layers"}]


class TestPlans:
    OUTLINE = {"process": "Get to the Nether", "status": "candidate", "usualRoute": [],
               "source": "https://minecraft.wiki/w/Tutorial:Shelters#Nether_portal_room ; https://minecraft.wiki/w/Tutorial:Nether_portals"}

    def test_a_candidate_plan_says_it_is_a_draft_and_names_the_wiki_pages_it_came_from(self):
        got = src.sources_for("get_process", {}, self.OUTLINE)
        assert got[0] == {"kind": "plan", "label": "Get to the Nether (draft, not confirmed)"}
        assert [s["label"] for s in got[1:]] == ["Tutorial:Shelters > Nether portal room", "Tutorial:Nether portals"]

    def test_a_confirmed_plan_does_not_say_draft(self):
        assert src.sources_for("get_process", {}, {**self.OUTLINE, "status": "confirmed"})[0]["label"] == "Get to the Nether"

    def test_one_step_of_a_plan_is_the_same_source_as_its_outline(self):
        step = {"process": "Get to the Nether", "step": "Mine 10 obsidian", "status": "candidate", "source": self.OUTLINE["source"]}
        assert src.merge_sources(src.sources_for("get_process", {}, self.OUTLINE), src.sources_for("get_process", {}, step)) == \
            src.sources_for("get_process", {}, self.OUTLINE)

    def test_the_list_of_plans_is_not_a_source(self):
        assert src.sources_for("get_process", {}, {"processes": [{"name": "Get emeralds"}], "note": "..."}) == []


class TestNothing:
    @pytest.mark.parametrize("output", [{"error": "x"}, {"error": "x", "item": "Iron"}, None, [], "text"])
    def test_a_failed_or_odd_result_gives_no_source(self, output):
        for tool in ("get_recipes", "describe", "search_wiki", "get_process"):
            assert src.sources_for(tool, {}, output) == []

    def test_a_search_for_a_name_is_not_a_source(self):
        assert src.sources_for("find", {}, {"matches": [{"name": "Iron Pickaxe"}]}) == []

    def test_an_unknown_tool_gives_none(self):
        assert src.sources_for("fly", {}, {"item": "x"}) == []
