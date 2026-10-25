"""Unit tests for loader/load_enchantments.py: the rows built from enchantment data, with no jar (but one made in the test) and no database."""
from __future__ import annotations

import json
import zipfile

import pytest

import load_enchantments as le

NS = "minecraft:"


def enchantment(**kw) -> dict:
    base = {"max_level": 3, "weight": 5, "anvil_cost": 2, "min_cost": {"base": 10, "per_level_above_first": 8},
            "max_cost": {"base": 40, "per_level_above_first": 8}, "slots": ["mainhand"], "supported_items": "#minecraft:enchantable/weapon",
            "effects": {"minecraft:damage": [], "minecraft:knockback": []}}
    return {**base, **kw}


def data(**kw) -> dict:
    d = {"enchantments": {"minecraft:sharpness": enchantment(exclusive_set="#minecraft:exclusive_set/damage"),
                          "minecraft:smite": enchantment(),
                          "minecraft:mending": enchantment(max_level=1, supported_items="#minecraft:enchantable/durability")},
         "tags": {"minecraft:treasure": ["minecraft:mending"],
                  "minecraft:non_treasure": ["minecraft:sharpness", "minecraft:smite"],
                  "minecraft:in_enchanting_table": ["#minecraft:non_treasure"],
                  "minecraft:exclusive_set/damage": ["minecraft:sharpness", "minecraft:smite"],
                  "minecraft:tooltip_order": ["minecraft:sharpness", "minecraft:smite", "minecraft:mending"],
                  "minecraft:prevents_ice_melting": ["minecraft:smite"]},
         "item_tags": {"minecraft:enchantable/weapon": ["minecraft:iron_sword", "#minecraft:axes"],
                       "minecraft:axes": ["minecraft:iron_axe"],
                       "minecraft:enchantable/durability": ["minecraft:iron_sword", "minecraft:bow"]}}
    d.update(kw)
    return d


@pytest.fixture
def rows():
    return le.build_rows(data())


class TestEnchantments:
    def test_one_row_per_enchantment_with_the_numbers_from_the_data(self, rows):
        by_id = {e["id"]: e for e in rows["enchantments"]}
        assert set(by_id) == {"minecraft:sharpness", "minecraft:smite", "minecraft:mending"}
        s = by_id["minecraft:sharpness"]
        assert (s["name"], s["maxLevel"], s["weight"], s["anvilCost"]) == ("Sharpness", 3, 5, 2)
        assert (s["minCostBase"], s["minCostPerLevel"], s["maxCostBase"], s["maxCostPerLevel"]) == (10, 8, 40, 8)
        assert s["slots"] == ["mainhand"]

    def test_effects_are_kept_as_names_only_without_the_namespace_and_in_order(self, rows):
        assert rows["enchantments"][0]["effectTypes"] == ["damage", "knockback"]

    def test_an_enchantment_with_no_effects_has_none(self):
        e = enchantment()
        del e["effects"]
        assert le.parse_enchantment("minecraft:x", e)["effectTypes"] == []

    def test_the_name_comes_from_the_id(self):
        assert le.parse_enchantment("minecraft:bane_of_arthropods", enchantment())["name"] == "Bane Of Arthropods"


class TestClasses:
    def test_only_the_tags_that_say_where_it_comes_from_or_what_it_conflicts_with_are_kept(self, rows):
        assert {c["id"] for c in rows["classes"]} == {"#minecraft:treasure", "#minecraft:non_treasure", "#minecraft:in_enchanting_table",
                                                      "#minecraft:exclusive_set/damage"}

    def test_names_are_chosen_and_an_exclusive_set_says_so(self, rows):
        names = {c["id"]: c["name"] for c in rows["classes"]}
        assert names["#minecraft:treasure"] == "Treasure" and names["#minecraft:non_treasure"] == "Non-Treasure"
        assert names["#minecraft:in_enchanting_table"] == "Enchanting Table"
        assert names["#minecraft:exclusive_set/damage"] == "Exclusive: Damage"

    def test_a_class_includes_its_enchantments_and_the_classes_it_names(self, rows):
        includes = {(i["class"], i["member"]) for i in rows["includes"]}
        assert ("#minecraft:treasure", "minecraft:mending") in includes
        assert ("#minecraft:in_enchanting_table", "#minecraft:non_treasure") in includes
        assert ("#minecraft:exclusive_set/damage", "minecraft:smite") in includes

    def test_a_nested_tag_that_is_not_kept_is_not_linked(self):
        d = data()
        d["tags"]["minecraft:in_enchanting_table"] = ["#minecraft:prevents_ice_melting"]
        assert not [i for i in le.build_rows(d)["includes"] if i["class"] == "#minecraft:in_enchanting_table"]

    def test_a_member_that_is_not_an_enchantment_is_an_error(self):
        d = data()
        d["tags"]["minecraft:treasure"].append("minecraft:no_such")
        with pytest.raises(ValueError, match="no_such"):
            le.build_rows(d)

    def test_the_enchanting_table_offers_what_is_in_non_treasure_but_not_treasure(self, rows):
        assert le.table_enchantments(rows["includes"]) == {"minecraft:sharpness", "minecraft:smite"}
        assert rows["stats"]["enchantments the table can offer"] == 2

    def test_exclusive_sets_are_counted(self, rows):
        assert rows["stats"]["exclusive sets"] == 1


class TestWhatItGoesOn:
    def test_supported_items_is_an_applies_to_edge_that_is_not_primary(self, rows):
        assert {"enchantment": "minecraft:smite", "class": "#minecraft:enchantable/weapon", "primary": False} in rows["applies"]

    def test_primary_items_is_a_second_edge_that_is_primary(self):
        d = data()
        d["enchantments"]["minecraft:smite"]["primary_items"] = "#minecraft:enchantable/durability"
        applies = le.build_rows(d)["applies"]
        assert {"enchantment": "minecraft:smite", "class": "#minecraft:enchantable/durability", "primary": True} in applies
        assert {"enchantment": "minecraft:smite", "class": "#minecraft:enchantable/weapon", "primary": False} in applies

    def test_items_given_directly_become_edges_to_the_items(self):
        d = data()
        d["enchantments"]["minecraft:smite"]["supported_items"] = ["minecraft:bow", "minecraft:crossbow"]
        rows = le.build_rows(d)
        assert {r["item"] for r in rows["direct"] if r["enchantment"] == "minecraft:smite"} == {"minecraft:bow", "minecraft:crossbow"}
        assert not [a for a in rows["applies"] if a["enchantment"] == "minecraft:smite"]

    def test_the_item_classes_keep_the_games_own_tag_hierarchy(self, rows):
        includes = {(i["class"], i["member"]) for i in rows["item_includes"]}
        assert ("#minecraft:enchantable/weapon", "minecraft:iron_sword") in includes
        assert ("#minecraft:enchantable/weapon", "#minecraft:axes") in includes             # a nested tag is a class of its own
        assert ("#minecraft:axes", "minecraft:iron_axe") in includes

    def test_a_tag_nobody_names_is_not_loaded(self):
        d = data()
        d["item_tags"]["minecraft:swords"] = ["minecraft:iron_sword"]
        assert "#minecraft:swords" not in {c["id"] for c in le.build_rows(d)["item_classes"]}

    def test_item_class_names_are_the_tag_names(self, rows):
        names = {c["id"]: c["name"] for c in rows["item_classes"]}
        assert names["#minecraft:enchantable/weapon"] == "Enchantable Weapon" and names["#minecraft:axes"] == "Axes"

    def test_a_cycle_between_tags_does_not_loop(self):
        d = data()
        d["item_tags"]["minecraft:axes"] = ["#minecraft:enchantable/weapon"]
        assert len(le.build_rows(d)["item_classes"]) == 3

    def test_an_enchantment_that_goes_on_nothing_is_counted(self):
        d = data()
        del d["enchantments"]["minecraft:mending"]["supported_items"]
        assert le.build_rows(d)["stats"]["enchantments that apply to nothing"] == 1

    def test_an_optional_tag_entry_written_as_an_object_is_read(self):
        d = data()
        d["item_tags"]["minecraft:axes"] = [{"id": "minecraft:iron_axe", "required": False}]
        assert ("#minecraft:axes", "minecraft:iron_axe") in {(i["class"], i["member"]) for i in le.build_rows(d)["item_includes"]}


class TestReading:
    def test_the_jar_is_read_by_folder_and_the_ids_keep_their_namespace_and_folder(self, tmp_path):
        jar = tmp_path / "x.jar"
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("data/minecraft/enchantment/sharpness.json", json.dumps(enchantment()))
            z.writestr("data/minecraft/tags/enchantment/treasure.json", json.dumps({"values": ["minecraft:mending"]}))
            z.writestr("data/minecraft/tags/enchantment/exclusive_set/damage.json", json.dumps({"values": ["minecraft:sharpness"]}))
            z.writestr("data/minecraft/tags/item/enchantable/armor.json", json.dumps({"values": ["minecraft:iron_helmet"]}))
            z.writestr("data/minecraft/tags/block/ignored.json", json.dumps({"values": []}))
            z.writestr("data/minecraft/datapacks/trade_rebalance/data/minecraft/enchantment/ignored.json", "{}")
        got = le.read_enchantments(jar)
        assert set(got["enchantments"]) == {"minecraft:sharpness"}
        assert set(got["tags"]) == {"minecraft:treasure", "minecraft:exclusive_set/damage"}
        assert got["item_tags"] == {"minecraft:enchantable/armor": ["minecraft:iron_helmet"]}
