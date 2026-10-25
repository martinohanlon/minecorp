"""The agent's enchantment answers against the real graph, rebuilt from source in the throwaway database. No model is involved.

Run with:  pytest -m integration
"""
from __future__ import annotations

import pytest

from minecorp_agent import graphs, tools
from minecorp_agent.budget import fit

pytestmark = [pytest.mark.integration, pytest.mark.jar]


def call(graph, tool, **args):
    return tools.TOOLS_BY_NAME[tool].fn(graph, args)


def of_kind(out, kind):
    return next(r for r in out["results"] if r["kind"] == kind)


@pytest.fixture(scope="module")
def sharpness(agent_graph):
    return of_kind(call(agent_graph, "describe", name="sharpness"), "enchantment")


class TestDescribeAnEnchantment:
    def test_sharpness_has_a_top_level_and_conflicts(self, sharpness):
        assert sharpness["maxLevel"] == 5
        assert {"Smite", "Bane Of Arthropods"} <= set(sharpness["cannotBeCombinedWith"])
        assert "Sharpness" not in sharpness["cannotBeCombinedWith"]

    def test_it_says_what_it_goes_on_with_examples_of_items(self, sharpness):
        assert sharpness["goesOn"] and sharpness["goesOnItems"]["total"] >= 10
        assert "Iron Sword" in sharpness["goesOnItems"]["examples"] or sharpness["goesOnItems"]["total"] > len(sharpness["goesOnItems"]["examples"])

    def test_it_comes_from_the_table_villagers_and_loot(self, sharpness):
        assert "the enchanting table" in sharpness["comesFrom"] and "villager trades (enchanted books)" in sharpness["comesFrom"]

    def test_the_table_cost_of_each_level_is_a_range_from_the_formula(self, sharpness):
        power = sharpness["enchantingPower"]
        assert list(power) == ["1", "2", "3", "4", "5"] and power["1"] == "1 to 21" and power["2"] == "12 to 32"

    def test_mending_is_treasure_and_the_table_does_not_offer_it(self, agent_graph):
        mending = of_kind(call(agent_graph, "describe", name="mending"), "enchantment")
        assert "Treasure" in mending["treasure"] and "the enchanting table" not in mending["comesFrom"]
        assert "enchantingPower" not in mending

    def test_a_curse_says_so(self, agent_graph):
        assert of_kind(call(agent_graph, "describe", name="binding curse"), "enchantment")["curse"] is True

    def test_what_an_enchantment_does_is_sent_to_the_wiki(self, sharpness):
        assert "search_wiki" in sharpness["next"]

    def test_the_result_fits_the_budget(self, agent_graph):
        out = call(agent_graph, "describe", name="sharpness")
        assert "omitted" not in fit(out, tools.TOOLS_BY_NAME["describe"].budget)


class TestDescribeWhatTakesEnchantments:
    def test_a_pickaxe_can_take_efficiency_fortune_and_silk_touch_but_not_sharpness(self, agent_graph):
        names = of_kind(call(agent_graph, "describe", name="iron pickaxe"), "item")["canBeEnchantedWith"]
        assert {"Efficiency", "Fortune", "Silk Touch", "Unbreaking", "Mending"} <= set(names) and "Sharpness" not in names

    def test_an_item_that_takes_nothing_says_nothing(self, agent_graph):
        assert "canBeEnchantedWith" not in of_kind(call(agent_graph, "describe", name="stick"), "item")


class TestWhereToGetOne:
    def test_a_book_enchantment_names_the_villagers_that_sell_it(self, agent_graph):
        out = of_kind(call(agent_graph, "where_to_find", name="mending"), "enchantment")
        assert any(v["villager"] == "Librarian" for v in out["enchantedBooksFrom"])
        assert "the enchanting table" not in out["comesFrom"] and "Treasure" in out["treasure"]

    def test_gear_trades_that_can_carry_it_are_listed_apart_from_books(self, agent_graph):
        out = of_kind(call(agent_graph, "where_to_find", name="sharpness"), "enchantment")
        assert out["enchantedGearFrom"] and {v["villager"] for v in out["enchantedGearFrom"]} & {"Weaponsmith", "Toolsmith", "Armorer"}
        assert "random" in out["note"]


class TestNamesListsAndClasses:
    def test_find_knows_the_kind(self, agent_graph):
        found = call(agent_graph, "find", query="sharpnes")["matches"]
        assert any(m["kind"] == "enchantment" and m["name"] == "Sharpness" for m in found)

    def test_list_enchantments_gives_all_43(self, agent_graph):
        out = call(agent_graph, "list", kind="enchantments")
        assert out["kind"] == "enchantment" and out["total"] == 43 and "Sharpness" in out["names"]

    def test_list_enchantment_classes_includes_treasure_and_the_exclusive_sets(self, agent_graph):
        out = call(agent_graph, "list", kind="enchantment classes")
        names = " ".join(out["names"])
        assert "Treasure" in names and "Curse" in names and "Exclusive: Damage" in names

    def test_members_of_treasure_has_mending_and_the_curses(self, agent_graph):
        out = call(agent_graph, "members", name="treasure")
        entry = of_kind(out, "enchantment class")
        assert {"Mending", "Binding Curse", "Vanishing Curse"} <= set(entry["members"])

    def test_members_of_the_table_class_follow_the_nested_class(self, agent_graph):
        entry = of_kind(call(agent_graph, "members", name="enchanting table"), "enchantment class")
        assert entry["total"] == 36 and "Sharpness" in entry["members"] and "Mending" not in entry["members"]


class TestPatternsShownToThePlayerAreRealPaths:
    @pytest.mark.parametrize("tool,name,kind", [("describe", "sharpness", "enchantment"), ("where_to_find", "sharpness", "enchantment"),
                                                ("where_to_find", "mending", "enchantment"), ("describe", "iron pickaxe", "item"),
                                                ("members", "treasure", "enchantment class")])
    def test_every_pattern_matches_something(self, agent_graph, tool, name, kind):
        out = of_kind(call(agent_graph, tool, name=name), kind)
        make = {"describe": graphs.describe_patterns, "where_to_find": graphs.where_patterns, "members": graphs.members_patterns}[tool]
        patterns = make(out)
        assert patterns
        for p in patterns:
            assert agent_graph.read(f"MATCH {p.cypher()} RETURN count(*) AS n")[0]["n"] > 0, p.compact()
