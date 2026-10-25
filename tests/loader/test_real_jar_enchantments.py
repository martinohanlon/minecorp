"""The enchantment loader against the REAL Paper jar: what must hold for any version, and known vanilla facts that act as canaries.

Skipped when the jar has not been downloaded. If a fact fails after a version upgrade, check the game first: it may have changed."""
from __future__ import annotations

import pytest

import load_enchantments as le

pytestmark = pytest.mark.jar


@pytest.fixture(scope="module")
def g(jar_path):
    return le.build(jar_path)


def by_id(g):
    return {e["id"]: e for e in g["enchantments"]}


def members(g, cls):
    return {i["member"] for i in g["includes"] if i["class"] == cls}


class TestWhatMustHold:
    def test_something_was_read(self, g):
        assert len(g["enchantments"]) > 30 and len(g["classes"]) >= 10 and len(g["item_classes"]) > 10

    def test_every_enchantment_has_the_numbers_the_agent_needs(self, g):
        for e in g["enchantments"]:
            assert e["maxLevel"] >= 1 and e["weight"] >= 1 and e["anvilCost"] >= 1, e["id"]
            assert None not in (e["minCostBase"], e["minCostPerLevel"], e["maxCostBase"], e["maxCostPerLevel"]), e["id"]

    def test_every_enchantment_goes_on_something(self, g):
        assert g["stats"]["enchantments that apply to nothing"] == 0

    def test_every_kind_of_thing_it_goes_on_is_a_class_that_has_members(self, g):
        has_members = {i["class"] for i in g["item_includes"]}
        assert {a["class"] for a in g["applies"]} <= has_members

    def test_every_enchantment_is_in_the_treasure_or_non_treasure_class(self, g):
        assert {e["id"] for e in g["enchantments"]} == members(g, "#minecraft:treasure") | members(g, "#minecraft:non_treasure")

    def test_the_enchanting_table_offers_only_what_is_not_treasure(self, g):
        offered = le.table_enchantments(g["includes"])
        assert offered and not offered & members(g, "#minecraft:treasure")

    def test_an_exclusive_set_has_at_least_two_members(self, g):
        sets = [c["id"] for c in g["classes"] if c["name"].startswith("Exclusive")]
        assert sets and all(len(members(g, s)) >= 2 for s in sets)

    def test_the_classes_trades_draw_from_exist(self, g):
        assert {"#minecraft:tradeable", "#minecraft:on_traded_equipment"} <= {c["id"] for c in g["classes"]}


class TestVanillaFacts:
    def test_levels(self, g):
        e = by_id(g)
        assert e["minecraft:sharpness"]["maxLevel"] == 5 and e["minecraft:protection"]["maxLevel"] == 4
        assert e["minecraft:mending"]["maxLevel"] == 1 and e["minecraft:fortune"]["maxLevel"] == 3

    def test_sharpness_conflicts_with_smite_and_fortune_with_silk_touch(self, g):
        damage = members(g, "#minecraft:exclusive_set/damage")
        assert {"minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods"} <= damage
        mining = members(g, "#minecraft:exclusive_set/mining")
        assert {"minecraft:fortune", "minecraft:silk_touch"} <= mining

    def test_mending_is_treasure_so_the_table_does_not_offer_it(self, g):
        assert "minecraft:mending" in members(g, "#minecraft:treasure")
        assert "minecraft:mending" not in le.table_enchantments(g["includes"])
        assert "minecraft:sharpness" in le.table_enchantments(g["includes"])

    def test_the_curses(self, g):
        assert members(g, "#minecraft:curse") == {"minecraft:binding_curse", "minecraft:vanishing_curse"}

    def test_sharpness_goes_on_swords_and_axes_by_anvil_and_on_melee_weapons_from_the_table(self, g):
        applies = {(a["class"], a["primary"]) for a in g["applies"] if a["enchantment"] == "minecraft:sharpness"}
        assert ("#minecraft:enchantable/sharp_weapon", False) in applies and ("#minecraft:enchantable/melee_weapon", True) in applies
        includes = {(i["class"], i["member"]) for i in g["item_includes"]}
        assert ("#minecraft:enchantable/sharp_weapon", "#minecraft:axes") in includes

    def test_the_tags_about_game_mechanics_are_not_classes(self, g):
        ids = {c["id"] for c in g["classes"]}
        assert not ids & {"#minecraft:tooltip_order", "#minecraft:prevents_ice_melting", "#minecraft:smelts_loot"}
