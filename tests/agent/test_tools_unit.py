"""Unit tests for agent/minecorp_agent/tools.py that need no database: the tool registry, how results are
worded for the model, and how a route is turned into steps."""
from __future__ import annotations

import re

import pytest

from minecorp_agent import tools

EXPECTED = {"find", "get_recipes", "what_do_i_need", "get_uses", "members", "how_to_get", "rank_recipes",
            "search_wiki", "read_section", "describe", "where_to_find", "get_process"}


# --------------------------------------------------------------------------- registry

def test_registry_has_exactly_the_expected_tools():
    names = [t.name for t in tools.TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == EXPECTED
    assert set(tools.TOOLS_BY_NAME) == EXPECTED


@pytest.mark.parametrize("tool", tools.TOOLS, ids=lambda t: t.name)
def test_every_tool_is_a_valid_openai_function_definition(tool):
    spec = tool.spec()
    assert spec["type"] == "function"
    fn = spec["function"]
    assert re.fullmatch(r"[a-z][a-z_]*", fn["name"])
    assert len(fn["description"]) > 30, "the model chooses tools from the description"
    params = fn["parameters"]
    assert params["type"] == "object" and params["additionalProperties"] is False
    assert set(params.get("required", [])) <= set(params["properties"])
    for name, prop in params["properties"].items():
        assert prop["type"] in {"string", "integer"}, name
        assert prop.get("description"), f"{tool.name}.{name} needs a description"
    assert callable(tool.fn)


@pytest.mark.parametrize("name,required", [
    ("find", ["query"]), ("get_recipes", ["name"]), ("what_do_i_need", ["name"]), ("get_uses", ["name"]),
    ("members", ["name"]), ("how_to_get", ["name"]), ("rank_recipes", []),
    ("describe", ["name"]), ("where_to_find", ["name"]), ("get_process", []), ("search_wiki", ["question"]), ("read_section", ["section"])])
def test_required_arguments(name, required):
    assert tools.TOOLS_BY_NAME[name].parameters.get("required", []) == required


def test_rank_recipes_order_is_limited_to_two_values():
    assert tools.TOOLS_BY_NAME["rank_recipes"].parameters["properties"]["order"]["enum"] == ["hardest", "easiest"]


def test_the_reading_note_explains_and_or():
    assert "needed together" in tools.READING_NOTE and "ONE" in tools.READING_NOTE


# --------------------------------------------------------------------------- wording for the model

@pytest.mark.parametrize("condition,expected", [
    ("default", None),
    ("killed_by_player", "only when a player kills it"),
    ("chance", "rare drop"),
    ("chance+killed_by_player", "rare drop, only when a player kills it"),
    ("silk_touch", "only with silk touch"),
    ("shears", "only with shears"),
    ("state", "only in some cases"),
    ("something_new", "something_new")])
def test_when_text(condition, expected):
    assert tools.when_text(condition) == expected


def _req(**over):
    base = {"isCategory": False, "name": "Stick", "options": None, "examples": [], "excluded": None,
            "count": 2, "role": "ingredient", "slot": "#"}
    return {**base, **over}


class TestRequirementViews:
    def test_a_plain_ingredient(self):
        assert tools._req_view(_req()) == {"item": "Stick", "count": 2}

    def test_a_category_says_any_of_with_options_and_examples(self):
        v = tools._req_view(_req(isCategory=True, name="Planks", options=12, examples=["Oak Planks", "Birch Planks"], count=3))
        assert v == {"anyOf": "Planks", "options": 12, "examples": ["Oak Planks", "Birch Planks"], "count": 3}

    def test_a_category_with_an_exclusion_lists_it(self):
        v = tools._req_view(_req(isCategory=True, name="Bed", options=16, examples=["White Bed"], excluded=["Brown Bed"], count=1))
        assert v["except"] == ["Brown Bed"]

    def test_an_empty_exclusion_is_left_out(self):
        assert "except" not in tools._req_view(_req(isCategory=True, name="Bed", options=16, excluded=[]))

    def test_a_non_ingredient_role_is_shown(self):
        assert tools._req_view(_req(role="template", name="Template", count=1)) == {"item": "Template", "count": 1, "role": "template"}


class TestRecipeView:
    RECIPE = {"name": "Iron Pickaxe", "type": "crafting_shaped", "station": "Crafting Table", "yields": 1, "special": False,
              "pattern": ["XXX", " # ", " # "],
              "reqs": [_req(), _req(name="Iron Ingot", count=3, slot="X"),
                       _req(isCategory=True, name="Planks", options=12, examples=["Oak Planks"], count=1, slot="P")]}

    def test_shape(self):
        v = tools._recipe_view(self.RECIPE)
        assert v["recipe"] == "Iron Pickaxe" and v["type"] == "crafting_shaped"
        assert v["station"] == "Crafting Table" and v["makes"] == 1
        assert len(v["requires"]) == 3 and "note" not in v

    def test_the_grid_and_its_key_are_included_with_any_for_categories(self):
        v = tools._recipe_view(self.RECIPE)
        assert v["grid"] == ["XXX", " # ", " # "]
        assert v["gridKey"] == {"#": "Stick", "X": "Iron Ingot", "P": "any Planks"}

    def test_a_shapeless_recipe_has_no_grid(self):
        v = tools._recipe_view({**self.RECIPE, "pattern": None, "reqs": [_req(slot="")]})
        assert "grid" not in v and "gridKey" not in v

    def test_a_special_recipe_says_its_inputs_are_not_listed(self):
        v = tools._recipe_view({**self.RECIPE, "special": True, "pattern": None, "reqs": []})
        assert v["requires"] == [] and "not listed" in v["note"]


# --------------------------------------------------------------------------- route to steps (state set by hand)

def _step(makes, amount, recipe, rtype, station, times, uses, other=None):
    return {"makes": makes, "amount": amount, "recipe": recipe, "type": rtype, "station": station,
            "times": times, "uses": uses, "otherWays": other or []}


class TestRouteFormatting:
    def route(self):
        r = tools.Route(None, 6)
        r.steps = {
            "minecraft:stick": _step("Stick", 4, "Stick", "crafting_shaped", "Crafting Table", 1,
                                     {"any Planks": {"anyOf": "Planks", "count": 2, "examples": ["Oak Planks"]}},
                                     other=["Stick From Bamboo (crafting_shaped)"]),
            "minecraft:iron_ingot": _step("Iron Ingot", 3, "Iron Ingot From Smelting Raw Iron", "smelting", "Furnace", 3,
                                          {"Raw Iron": {"item": "Raw Iron", "count": 3}}),
            "minecraft:iron_pickaxe": _step("Iron Pickaxe", 1, "Iron Pickaxe", "crafting_shaped", "Crafting Table", 1,
                                            {"Stick": {"item": "Stick", "count": 2}, "Iron Ingot": {"item": "Iron Ingot", "count": 3}}),
        }
        r.levels = 2
        r._add_total("Raw Iron", False, 3)
        r._add_total("Planks", True, 2, ["Oak Planks"])
        return r

    def test_steps_are_numbered_in_order_with_a_readable_line(self):
        steps = self.route().ordered_steps()
        assert [s["step"] for s in steps] == [1, 2, 3]
        assert steps[0]["do"] == "Craft: 2 Planks (any) -> 4 Stick at Crafting Table"
        assert steps[1]["do"] == "Smelt: 3 Raw Iron -> 3 Iron Ingot at Furnace (3 times)"
        assert steps[2]["do"] == "Craft: 2 Stick, 3 Iron Ingot -> 1 Iron Pickaxe at Crafting Table"

    def test_alternatives_are_kept_only_when_there_are_some(self):
        steps = self.route().ordered_steps()
        assert steps[0]["otherWays"] == ["Stick From Bamboo (crafting_shaped)"]
        assert "otherWays" not in steps[1]

    def test_each_step_lists_what_it_uses(self):
        steps = self.route().ordered_steps()
        assert steps[1]["uses"] == [{"item": "Raw Iron", "count": 3}]

    @pytest.mark.parametrize("rtype,verb", [("smelting", "Smelt"), ("blasting", "Blast"), ("smoking", "Smoke"),
                                            ("campfire_cooking", "Cook"), ("stonecutting", "Cut"),
                                            ("smithing_transform", "Smith"), ("crafting_shapeless", "Craft")])
    def test_the_verb_follows_the_recipe_type(self, rtype, verb):
        r = tools.Route(None, 6)
        r.steps = {"x": _step("Thing", 1, "R", rtype, "Station", 1, {"A": {"item": "A", "count": 1}})}
        assert r.ordered_steps()[0]["do"].startswith(verb + ": ")

    def test_a_step_with_no_station_has_no_at_clause(self):
        r = tools.Route(None, 6)
        r.steps = {"x": _step("Thing", 1, "R", "crafting_shaped", None, 1, {"A": {"item": "A", "count": 1}})}
        assert r.ordered_steps()[0]["do"] == "Craft: 1 A -> 1 Thing"

    def test_gather_lists_base_materials_sorted_with_totals(self):
        gather = self.route().gather()
        assert gather == [{"anyOf": "Planks", "total": 2, "examples": ["Oak Planks"]}, {"item": "Raw Iron", "total": 3}]

    def test_totals_add_up(self):
        r = tools.Route(None, 6)
        r._add_total("Raw Iron", False, 3)
        r._add_total("Raw Iron", False, 4)
        assert r.totals[("Raw Iron", False)]["count"] == 7

    def test_overview(self):
        o = self.route().overview()
        assert o == {"craftingSteps": 3, "chainLength": 2, "baseMaterials": 2,
                     "stations": ["Crafting Table", "Furnace"], "truncated": False}

    def test_overview_reports_truncation(self):
        r = self.route()
        r.not_expanded.add("Deep Thing")
        assert r.overview()["truncated"] is True

    def test_routes_can_share_caches(self):
        shared = {}
        a, b = tools.Route(None, 6, shared), tools.Route(None, 6, shared)
        assert a.recipes is b.recipes and a.reversal is b.reversal and a.mined is b.mined
        assert tools.Route(None, 6).recipes is not a.recipes
