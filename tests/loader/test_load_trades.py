"""Unit tests for loader/load_trades.py: reading trades, trade sets and tags, and turning them into rows.

Everything runs on a small jar made here, so no server jar and no database are needed. The real jar is checked in
test_real_jar_trades.py and the graph in test_trades_integration.py."""
from __future__ import annotations

import json
import zipfile

import pytest

import load_trades as lt


def make_jar(path, files: dict, name="trades.jar"):
    """A jar holding data/minecraft/<name>.json for each entry of files."""
    jar = path / name
    with zipfile.ZipFile(jar, "w") as z:
        for key, data in files.items():
            z.writestr(f"data/minecraft/{key}.json", json.dumps(data))
    return jar


def trade(wants="minecraft:wheat", count=20.0, gives="minecraft:emerald", **extra):
    out = {"wants": {"id": wants}, "gives": {"id": gives}, "max_uses": 16.0, "reputation_discount": 0.05, "xp": 2.0, **extra}
    if count is not None:
        out["wants"]["count"] = count
    return out


def farmer_files(**more):
    return {
        "villager_trade/farmer/1/wheat_emerald": trade(),
        "villager_trade/farmer/1/emerald_bread": trade(wants="minecraft:emerald", count=None, gives="minecraft:bread"),
        "tags/villager_trade/farmer/level_1": {"values": ["minecraft:farmer/1/wheat_emerald", "minecraft:farmer/1/emerald_bread"]},
        "trade_set/farmer/level_1": {"amount": 2.0, "random_sequence": "x", "trades": "#minecraft:farmer/level_1"},
        **more,
    }


def parse(**kw):
    return lt.parse_trade("farmer/1/x", trade(**kw), {})


# --------------------------------------------------------------------------- parse_trade

class TestParseTrade:
    def test_the_id_is_a_trade_action_id_with_the_namespace(self):
        t = parse()
        assert t["id"] == "trade:minecraft:farmer/1/x" and t["key"] == "farmer/1/x"

    def test_counts_written_as_floats_are_whole_numbers(self):
        t = parse(count=20.0)
        assert t["pay"] == [{"item": "minecraft:wheat", "count": 20, "role": "price"}]
        assert t["maxUses"] == 16 and t["xp"] == 2 and t["reputationDiscount"] == 0.05

    def test_a_missing_count_is_one_for_the_price_and_for_what_you_get(self):
        t = parse(count=None)
        assert t["pay"][0]["count"] == 1 and t["gives"] == {"item": "minecraft:emerald", "count": 1}

    def test_what_you_get_can_be_several(self):
        t = lt.parse_trade("k", {"wants": {"id": "minecraft:emerald"}, "gives": {"id": "minecraft:bread", "count": 6}}, {})
        assert t["gives"]["count"] == 6

    def test_a_missing_xp_or_max_uses_is_left_empty(self):
        t = lt.parse_trade("k", {"wants": {"id": "minecraft:emerald"}, "gives": {"id": "minecraft:bread"}}, {})
        assert t["xp"] is None and t["maxUses"] is None and t["reputationDiscount"] is None

    def test_a_price_of_zero_means_the_price_varies(self):
        t = parse(count=0.0)
        assert t["variablePrice"] is True and t["pay"][0]["count"] is None

    def test_a_normal_price_is_not_variable(self):
        assert parse()["variablePrice"] is False

    def test_a_second_wanted_item_is_a_pay_entry_with_its_own_role(self):
        t = parse(additional_wants={"id": "minecraft:book"})
        assert t["pay"][1] == {"item": "minecraft:book", "count": 1, "role": "additional"}

    def test_an_enchanted_item_with_levels(self):
        t = parse(given_item_modifiers=[{"function": "minecraft:enchant_with_levels", "levels": {"type": "minecraft:uniform", "min": 5.0, "max": 19.0}}])
        assert t["enchanted"] is True and t["enchantLevels"] == "5 to 19"

    def test_a_random_enchantment(self):
        t = parse(given_item_modifiers=[{"function": "minecraft:enchant_randomly"}])
        assert t["enchanted"] is True and t["enchantLevels"] is None

    def test_the_enchantment_class_a_book_or_gear_draws_from_is_kept(self):
        book = parse(given_item_modifiers=[{"function": "minecraft:enchant_randomly", "options": "#minecraft:tradeable"}])
        gear = parse(given_item_modifiers=[{"function": "minecraft:enchant_with_levels", "levels": {"min": 5.0, "max": 19.0},
                                            "options": "#minecraft:on_traded_equipment"}])
        assert book["enchantOptions"] == ["#minecraft:tradeable"] and gear["enchantOptions"] == ["#minecraft:on_traded_equipment"]

    def test_no_options_or_a_single_enchantment_names_no_class(self):
        assert parse(given_item_modifiers=[{"function": "minecraft:enchant_randomly"}])["enchantOptions"] == []
        assert parse(given_item_modifiers=[{"function": "minecraft:enchant_randomly", "options": "minecraft:mending"}])["enchantOptions"] == []
        assert parse()["enchantOptions"] == []

    def test_other_modifiers_do_not_make_it_enchanted(self):
        assert parse(given_item_modifiers=[{"function": "minecraft:set_name"}])["enchanted"] is False

    def test_a_trade_for_some_village_types_only(self):
        t = parse(merchant_predicate={"condition": "minecraft:entity_properties", "entity": "this", "predicate": {
            "minecraft:predicates": {"minecraft:villager/variant": ["minecraft:swamp", "minecraft:desert"]}}})
        assert t["villagerTypes"] == ["desert", "swamp"]

    def test_one_village_type_is_written_as_a_string_not_a_list(self):
        t = parse(merchant_predicate={"condition": "minecraft:entity_properties", "entity": "this", "predicate": {
            "minecraft:predicates": {"minecraft:villager/variant": "minecraft:savanna"}}})
        assert t["villagerTypes"] == ["savanna"]

    def test_a_predicate_about_something_else_gives_no_types(self):
        assert parse(merchant_predicate={"condition": "minecraft:random_chance", "chance": 0.5})["villagerTypes"] == []

    def test_a_trade_for_every_village_has_no_types(self):
        assert parse()["villagerTypes"] == []

    def test_a_map_locates_the_structures_of_its_destination_tag(self):
        tags = {"on_swamp_explorer_maps": {"values": ["minecraft:swamp_hut"]}}
        t = lt.parse_trade("k", trade(given_item_modifiers=[{"function": "minecraft:exploration_map", "decoration": "minecraft:swamp_hut",
                                                              "destination": "minecraft:on_swamp_explorer_maps", "search_radius": 100}]), tags)
        assert t["locates"] == [{"structure": "minecraft:swamp_hut", "decoration": "swamp_hut", "radius": 100}]

    def test_a_destination_tag_can_hold_several_structures_and_nested_tags(self):
        tags = {"maps": {"values": ["minecraft:a", "#minecraft:more"]}, "more": {"values": ["minecraft:b"]}}
        t = lt.parse_trade("k", trade(given_item_modifiers=[{"function": "minecraft:exploration_map", "destination": "#minecraft:maps"}]), tags)
        assert sorted(x["structure"] for x in t["locates"]) == ["minecraft:a", "minecraft:b"]
        assert t["locates"][0]["decoration"] is None and t["locates"][0]["radius"] is None

    def test_an_ordinary_trade_locates_nothing(self):
        assert parse()["locates"] == []


class TestTradeName:
    def test_counts_over_one_are_written(self):
        assert parse()["name"] == "Trade 20 Wheat for Emerald"

    def test_a_count_of_one_is_not(self):
        assert parse(count=None)["name"] == "Trade Wheat for Emerald"

    def test_both_wanted_items_are_named(self):
        assert parse(additional_wants={"id": "minecraft:book"})["name"] == "Trade 20 Wheat and Book for Emerald"

    def test_a_varying_price_has_no_count(self):
        assert parse(count=0.0)["name"] == "Trade Wheat for Emerald"


# --------------------------------------------------------------------------- reading

class TestRead:
    def test_each_kind_of_file_is_read_by_its_id(self, tmp_path):
        data = lt.read_trades(make_jar(tmp_path, farmer_files(**{"tags/worldgen/structure/maps": {"values": []}})))
        assert set(data["trades"]) == {"farmer/1/wheat_emerald", "farmer/1/emerald_bread"}
        assert set(data["sets"]) == {"farmer/level_1"} and set(data["tags"]) == {"farmer/level_1"}
        assert set(data["structure_tags"]) == {"maps"}

    def test_the_datapacks_inside_the_jar_are_not_read(self, tmp_path):
        files = farmer_files(**{"datapacks/trade_rebalance/data/minecraft/villager_trade/farmer/1/other": trade()})
        assert set(lt.read_trades(make_jar(tmp_path, files))["trades"]) == {"farmer/1/wheat_emerald", "farmer/1/emerald_bread"}

    def test_a_jar_with_no_trades_reads_as_empty(self, tmp_path):
        data = lt.read_trades(make_jar(tmp_path, {"recipe/x": {}}))
        assert data == {"trades": {}, "sets": {}, "tags": {}, "structure_tags": {}}


# --------------------------------------------------------------------------- build

class TestBuild:
    def build(self, tmp_path, files=None):
        return lt.build(make_jar(tmp_path, files or farmer_files()))

    def test_a_profession_offers_a_set_for_each_level(self, tmp_path):
        files = farmer_files(**{"villager_trade/farmer/2/x": trade(), "tags/villager_trade/farmer/level_2": {"values": ["minecraft:farmer/2/x"]},
                                "trade_set/farmer/level_2": {"amount": 2.0, "trades": "#minecraft:farmer/level_2"}})
        g = self.build(tmp_path, files)
        assert g["professions"] == [{"id": "minecraft:farmer", "name": "Farmer"}]
        assert [(o["from"], o["set"], o["level"]) for o in g["offers"]] == [("minecraft:farmer", "minecraft:farmer/level_1", 1),
                                                                            ("minecraft:farmer", "minecraft:farmer/level_2", 2)]

    def test_a_set_holds_the_trades_its_tag_lists_and_says_how_many_are_picked(self, tmp_path):
        g = self.build(tmp_path)
        assert g["sets"] == [{"id": "minecraft:farmer/level_1", "name": "Farmer level 1", "amount": 2}]
        assert sorted(i["trade"] for i in g["includes"]) == ["trade:minecraft:farmer/1/emerald_bread", "trade:minecraft:farmer/1/wheat_emerald"]

    def test_a_nested_tag_is_followed(self, tmp_path):
        files = farmer_files()
        files["tags/villager_trade/farmer/level_1"] = {"values": ["#minecraft:common/level_1"]}
        files["tags/villager_trade/common/level_1"] = {"values": ["minecraft:farmer/1/wheat_emerald"]}
        g = self.build(tmp_path, files)
        assert [i["trade"] for i in g["includes"]] == ["trade:minecraft:farmer/1/wheat_emerald"]

    def test_a_trade_shared_by_two_professions_is_one_trade_in_two_sets(self, tmp_path):
        files = farmer_files(**{"tags/villager_trade/mason/level_1": {"values": ["minecraft:farmer/1/wheat_emerald"]},
                                "trade_set/mason/level_1": {"amount": 1.0, "trades": "#minecraft:mason/level_1"}})
        g = self.build(tmp_path, files)
        shared = [i for i in g["includes"] if i["trade"] == "trade:minecraft:farmer/1/wheat_emerald"]
        assert len(shared) == 2 and len(g["trades"]) == 2 and g["stats"]["trades in more than one set"] == 1

    def test_the_wandering_trader_is_not_a_profession_and_offers_pools(self, tmp_path):
        files = farmer_files(**{"villager_trade/wandering_trader/a": trade(),
                                "tags/villager_trade/wandering_trader/common": {"values": ["minecraft:wandering_trader/a"]},
                                "trade_set/wandering_trader/common": {"amount": 5.0, "trades": "#minecraft:wandering_trader/common"}})
        g = self.build(tmp_path, files)
        assert [p["id"] for p in g["professions"]] == ["minecraft:farmer"]
        assert {"from": "minecraft:wandering_trader", "set": "minecraft:wandering_trader/common", "level": None, "pool": "common"} in g["offers"]

    def test_a_pool_that_is_only_shared_is_not_a_profession(self, tmp_path):
        files = farmer_files(**{"villager_trade/smith/1/a": trade(), "tags/villager_trade/common_smith/level_1": {"values": ["minecraft:smith/1/a"]}})
        assert [p["id"] for p in self.build(tmp_path, files)["professions"]] == ["minecraft:farmer"]

    def test_a_set_that_names_a_trade_that_does_not_exist_fails(self, tmp_path):
        files = farmer_files()
        files["tags/villager_trade/farmer/level_1"] = {"values": ["minecraft:farmer/1/ghost"]}
        with pytest.raises(ValueError, match="ghost"):
            self.build(tmp_path, files)

    def test_every_item_a_trade_mentions_is_listed_once_with_a_name(self, tmp_path):
        g = self.build(tmp_path)
        assert g["items"] == [{"id": "minecraft:bread", "name": "Bread"}, {"id": "minecraft:emerald", "name": "Emerald"},
                              {"id": "minecraft:wheat", "name": "Wheat"}]

    def test_the_report_counts(self, tmp_path):
        s = self.build(tmp_path)["stats"]
        assert s["trades"] == 2 and s["trade sets"] == 1 and s["professions"] == 1 and s["trade set to trade links"] == 2
        assert s["items traded"] == 3 and s["trades for a variable price"] == 0 and s["maps that lead to a structure"] == 0

    def test_a_jar_with_no_trades_builds_an_empty_graph(self, tmp_path):
        g = lt.build(make_jar(tmp_path, {"recipe/x": {}}))
        assert g["trades"] == g["sets"] == g["professions"] == g["offers"] == g["includes"] == g["items"] == []
