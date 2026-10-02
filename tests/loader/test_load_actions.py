"""Unit tests for loader/load_actions.py: loot tables, tool requirements and action rows."""
from __future__ import annotations

import json
import zipfile

import pytest

import load_actions as la


# --------------------------------------------------------------------------- tags

def test_tag_members_nested_and_cycle():
    tags = {"minecraft:a": ["#minecraft:b", "minecraft:x"], "minecraft:b": ["minecraft:y", "#minecraft:a"]}
    assert la.tag_members("minecraft:a", tags, {}) == {"minecraft:x", "minecraft:y"}


def test_tag_members_unknown_tag_is_empty():
    assert la.tag_members("minecraft:nope", {}, {}) == set()


# --------------------------------------------------------------------------- tool requirements

def _tool_tags():
    needs = lambda name: f"#minecraft:needs_{name}_tool"  # noqa: E731
    return {
        "minecraft:mineable/pickaxe": ["minecraft:stone", "minecraft:iron_ore", "minecraft:diamond_ore", "minecraft:obsidian",
                                       "minecraft:oak_log"],
        "minecraft:mineable/axe": ["minecraft:oak_log", "minecraft:chest"],
        "minecraft:mineable/shovel": ["minecraft:dirt"],
        "minecraft:needs_stone_tool": ["minecraft:iron_ore"],
        "minecraft:needs_iron_tool": ["minecraft:diamond_ore"],
        "minecraft:needs_diamond_tool": ["minecraft:obsidian"],
        "minecraft:incorrect_for_wooden_tool": [needs("stone"), needs("iron"), needs("diamond")],
        "minecraft:incorrect_for_stone_tool": [needs("iron"), needs("diamond")],
        "minecraft:incorrect_for_copper_tool": [needs("iron"), needs("diamond")],
        "minecraft:incorrect_for_iron_tool": [needs("diamond")],
        "minecraft:incorrect_for_diamond_tool": [],
        "minecraft:incorrect_for_netherite_tool": [],
    }


class TestAnalyseTool:
    @pytest.mark.parametrize("block,tier", [
        ("minecraft:stone", "wooden"), ("minecraft:iron_ore", "stone"),
        ("minecraft:diamond_ore", "iron"), ("minecraft:obsidian", "diamond")])
    def test_pickaxe_blocks_get_the_weakest_tier_not_marked_incorrect(self, block, tier):
        t = la.analyse_tool(block, _tool_tags(), {})
        assert (t["tool"], t["minTier"], t["toolRequired"], t["monotonic"]) == ("pickaxe", tier, True, True)

    def test_axe_block_needs_no_tool(self):
        t = la.analyse_tool("minecraft:chest", _tool_tags(), {})
        assert (t["tool"], t["minTier"], t["toolRequired"]) == ("axe", "wooden", False)

    def test_shovel_block_needs_no_tool(self):
        assert la.analyse_tool("minecraft:dirt", _tool_tags(), {})["toolRequired"] is False

    def test_pickaxe_is_preferred_when_a_block_allows_several_tools(self):
        assert la.analyse_tool("minecraft:oak_log", _tool_tags(), {})["tool"] == "pickaxe"

    def test_block_with_no_mineable_tag_has_no_tool(self):
        t = la.analyse_tool("minecraft:air", _tool_tags(), {})
        assert (t["tool"], t["minTier"], t["toolRequired"]) == (None, None, False)

    def test_non_monotonic_tier_list_is_reported(self):
        tags = {"minecraft:mineable/pickaxe": ["minecraft:odd"], "minecraft:incorrect_for_stone_tool": ["minecraft:odd"]}
        assert la.analyse_tool("minecraft:odd", tags, {})["monotonic"] is False

    def test_block_no_tier_can_mine_needs_no_tool_requirement(self):
        tags = {"minecraft:mineable/pickaxe": ["minecraft:bedrock"]}
        for tier in la.TIERS:
            tags[f"minecraft:incorrect_for_{tier}_tool"] = ["minecraft:bedrock"]
        t = la.analyse_tool("minecraft:bedrock", tags, {})
        assert (t["minTier"], t["toolRequired"]) == (None, False)


# --------------------------------------------------------------------------- conditions and numbers

class TestConditions:
    SILK = [{"condition": "minecraft:match_tool", "predicate": {"predicates": {
        "minecraft:enchantments": [{"enchantments": "minecraft:silk_touch", "levels": {"min": 1}}]}}}]

    def test_block_conditions(self):
        assert la.classify_block(self.SILK) == "silk_touch"
        assert la.classify_block([{"condition": "minecraft:match_tool", "predicate": {"items": "#minecraft:shears"}}]) == "shears"
        assert la.classify_block([{"condition": "minecraft:block_state_property", "block": "minecraft:wheat"}]) == "state"
        assert la.classify_block([{"condition": "minecraft:random_chance", "chance": 0.1}]) == "chance"
        assert la.classify_block([{"condition": "minecraft:table_bonus"}]) == "chance"
        assert la.classify_block([{"condition": "minecraft:survives_explosion"}]) is None
        assert la.classify_block([]) is None

    def test_block_condition_priority(self):
        both = self.SILK + [{"condition": "minecraft:random_chance"}]
        assert la.classify_block(both) == "silk_touch"

    def test_mob_conditions(self):
        assert la.classify_mob([{"condition": "minecraft:killed_by_player"}]) == "killed_by_player"
        assert la.classify_mob([{"condition": "minecraft:random_chance_with_enchanted_bonus"}]) == "chance"
        both = [{"condition": "minecraft:killed_by_player"}, {"condition": "minecraft:random_chance"}]
        assert la.classify_mob(both) == "chance+killed_by_player"
        assert la.classify_mob([{"condition": "minecraft:damage_source_properties"}]) == "state"
        assert la.classify_mob([{"condition": "minecraft:entity_properties", "predicate": {"vehicle": {}}}]) == "state"

    def test_sheep_colour_and_mooshroom_variant_are_not_conditions(self):
        sheep = [{"condition": "minecraft:entity_properties", "predicate": {"minecraft:components": {"minecraft:sheep/color": "white"}}}]
        moo = [{"condition": "minecraft:entity_properties", "predicate": {"minecraft:components": {"minecraft:mooshroom/variant": "red"}}}]
        assert la.classify_mob(sheep) is None and la.classify_mob(moo) is None

    def test_override_keeps_the_innermost(self):
        assert la.override("a", "b") == "a"
        assert la.override(None, "b") == "b"
        assert la.override(None, None) is None

    def test_union_adds_labels_sorted(self):
        assert la.union("killed_by_player", "chance") == "chance+killed_by_player"
        assert la.union("chance", "chance") == "chance"
        assert la.union(None, "state") == "state"
        assert la.union(None, None) is None


class TestNumbers:
    def test_number(self):
        assert la.number(3) == 3.0
        assert la.number({"value": 2}) == 2.0
        assert la.number({"min": 1, "max": 3}) == 2.0
        assert la.number(None) == 1.0
        assert la.number("x", default=7) == 7

    def test_a_negative_range_counts_as_none_not_below_zero(self):
        assert la.number({"min": -1, "max": 1}) == 0.5
        assert la.number({"min": -3, "max": -1}) == 0.0

    def test_entry_count_reads_set_count_only(self):
        assert la.entry_count({"functions": [{"function": "minecraft:set_count", "count": {"min": 0, "max": 2}}]}) == 1.0
        assert la.entry_count({"functions": [{"function": "minecraft:apply_bonus"}]}) == 1.0
        assert la.entry_count({}) == 1.0


# --------------------------------------------------------------------------- drops from loot tables

def _pool(entries, rolls=1, conditions=None):
    pool = {"rolls": rolls, "entries": entries}
    if conditions:
        pool["conditions"] = conditions
    return pool


def _item(name, **extra):
    return {"type": "minecraft:item", "name": name, **extra}


SILK = TestConditions.SILK


class TestTableDrops:
    def test_simple_drop(self):
        t = {"pools": [_pool([_item("minecraft:oak_log")])]}
        assert la.table_drops(t, la.classify_block, la.override, {}) == {("minecraft:oak_log", "default"): 1.0}

    def test_ore_style_alternatives_split_silk_touch_from_default(self):
        entry = {"type": "minecraft:alternatives", "children": [
            _item("minecraft:iron_ore", conditions=SILK), _item("minecraft:raw_iron")]}
        d = la.table_drops({"pools": [_pool([entry])]}, la.classify_block, la.override, {})
        assert d == {("minecraft:iron_ore", "silk_touch"): 1.0, ("minecraft:raw_iron", "default"): 1.0}

    def test_rolls_multiply_the_count(self):
        t = {"pools": [_pool([_item("minecraft:white_wool")], rolls={"min": 1, "max": 3})]}
        assert la.table_drops(t, la.classify_mob, la.union, {}) == {("minecraft:white_wool", "default"): 2.0}

    def test_pool_condition_applies_to_its_entries(self):
        t = {"pools": [_pool([_item("minecraft:blaze_rod")], conditions=[{"condition": "minecraft:killed_by_player"}])]}
        assert la.table_drops(t, la.classify_mob, la.union, {}) == {("minecraft:blaze_rod", "killed_by_player"): 1.0}

    def test_pool_and_entry_conditions_combine_for_mobs(self):
        entry = _item("minecraft:iron_ingot", conditions=[{"condition": "minecraft:random_chance"}])
        t = {"pools": [_pool([entry], conditions=[{"condition": "minecraft:killed_by_player"}])]}
        assert la.table_drops(t, la.classify_mob, la.union, {}) == {("minecraft:iron_ingot", "chance+killed_by_player"): 1.0}

    def test_referenced_loot_table_is_followed(self):
        tables = {"minecraft:entities/sheep/white": {"pools": [_pool([_item("minecraft:white_wool")])]}}
        entry = {"type": "minecraft:loot_table", "value": "minecraft:entities/sheep/white"}
        d = la.table_drops({"pools": [_pool([entry])]}, la.classify_mob, la.union, tables)
        assert d == {("minecraft:white_wool", "default"): 1.0}

    def test_reference_loops_stop(self):
        loop = {"pools": [_pool([{"type": "minecraft:loot_table", "value": "minecraft:blocks/loop"}, _item("minecraft:dirt")])]}
        d = la.table_drops(loop, la.classify_block, la.override, {"minecraft:blocks/loop": loop})
        assert d == {("minecraft:dirt", "default"): 1.0}

    def test_other_entry_kinds_are_skipped(self):
        entries = [{"type": "minecraft:tag", "name": "#minecraft:x"}, {"type": "minecraft:empty"}, _item("minecraft:dirt")]
        assert la.table_drops({"pools": [_pool(entries)]}, la.classify_block, la.override, {}) == {("minecraft:dirt", "default"): 1.0}

    def test_table_with_no_pools_has_no_drops(self):
        assert la.table_drops({"type": "minecraft:block"}, la.classify_block, la.override, {}) == {}


# --------------------------------------------------------------------------- build() from a small jar

def _write_jar(path, files: dict):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, json.dumps(content))


def _loot(*entries, rolls=1, conditions=None):
    return {"type": "minecraft:block", "pools": [_pool(list(entries), rolls=rolls, conditions=conditions)]}


@pytest.fixture
def mini_jar(tmp_path):
    silk_alt = {"type": "minecraft:alternatives", "children": [_item("minecraft:iron_ore", conditions=SILK), _item("minecraft:raw_iron")]}
    sheep_ref = {"type": "minecraft:loot_table", "value": "minecraft:entities/sheep/white"}
    shear_alt = {"type": "minecraft:alternatives", "children": [{"type": "minecraft:loot_table", "value": "minecraft:shearing/sheep/white",
                                                                  "conditions": [{"condition": "minecraft:entity_properties",
                                                                                  "predicate": {"minecraft:components": {"minecraft:sheep/color": "white"}}}]}]}
    files = {
        "data/minecraft/loot_table/blocks/iron_ore.json": _loot(silk_alt),
        "data/minecraft/loot_table/blocks/oak_log.json": _loot(_item("minecraft:oak_log")),
        "data/minecraft/loot_table/blocks/stone.json": _loot(_item("minecraft:cobblestone")),
        "data/minecraft/loot_table/blocks/air.json": {"type": "minecraft:block", "pools": []},
        "data/minecraft/loot_table/entities/spider.json": {"type": "minecraft:entity", "pools": [
            _pool([_item("minecraft:string")]),
            _pool([_item("minecraft:spider_eye")], conditions=[{"condition": "minecraft:killed_by_player"}])]},
        "data/minecraft/loot_table/entities/allay.json": {"type": "minecraft:entity", "pools": []},
        "data/minecraft/loot_table/entities/sheep.json": {"type": "minecraft:entity", "pools": [_pool([_item("minecraft:mutton"), sheep_ref])]},
        "data/minecraft/loot_table/entities/sheep/white.json": {"pools": [_pool([_item("minecraft:white_wool")])]},
        "data/minecraft/loot_table/shearing/sheep.json": {"type": "minecraft:shearing", "pools": [_pool([shear_alt])]},
        "data/minecraft/loot_table/shearing/sheep/white.json": {"pools": [_pool([_item("minecraft:white_wool")], rolls={"min": 1, "max": 3})]},
        "data/minecraft/tags/block/mineable/pickaxe.json": {"values": ["minecraft:iron_ore", "minecraft:stone"]},
        "data/minecraft/tags/block/mineable/axe.json": {"values": ["minecraft:oak_log"]},
        "data/minecraft/tags/block/needs_stone_tool.json": {"values": ["minecraft:iron_ore"]},
        "data/minecraft/tags/block/incorrect_for_wooden_tool.json": {"values": ["#minecraft:needs_stone_tool"]},
        "data/minecraft/recipe/iron_pickaxe.json": {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:iron_ingot"],
                                                    "result": {"id": "minecraft:iron_pickaxe"}},
        "data/minecraft/recipe/iron_ingot_from_nuggets.json": {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:iron_nugget"],
                                                               "result": {"id": "minecraft:iron_ingot"}},
        "data/minecraft/recipe/iron_ingot_from_smelting.json": {"type": "minecraft:smelting", "ingredient": "minecraft:raw_iron",
                                                                "result": {"id": "minecraft:iron_ingot"}},
        "data/minecraft/recipe/iron_ingot_from_blasting.json": {"type": "minecraft:blasting", "ingredient": "minecraft:raw_iron",
                                                                "result": {"id": "minecraft:iron_ingot"}},
        "data/minecraft/recipe/netherite_pickaxe_smithing.json": {"type": "minecraft:smithing_transform", "template": "minecraft:t",
                                                                  "base": "minecraft:diamond_pickaxe", "addition": "minecraft:netherite_ingot",
                                                                  "result": {"id": "minecraft:netherite_pickaxe"}},
        "data/minecraft/recipe/stone_bricks_cutting.json": {"type": "minecraft:stonecutting", "ingredient": "minecraft:stone",
                                                            "result": {"id": "minecraft:stone_bricks"}},
        "data/minecraft/recipe/bolt_trim.json": {"type": "minecraft:smithing_trim", "template": "minecraft:t", "base": "minecraft:b",
                                                 "addition": "minecraft:a", "pattern": "minecraft:bolt"},
    }
    path = tmp_path / "mini.jar"
    _write_jar(path, files)
    return path


class TestBuild:
    def test_actions_per_verb(self, mini_jar):
        g = la.build(mini_jar)
        verbs = {}
        for a in g["actions"] + g["transform"]:
            verbs.setdefault(a["verb"], set()).add(a["id"])
        assert verbs == {
            "Mine": {"mine:minecraft:iron_ore", "mine:minecraft:oak_log", "mine:minecraft:stone"},
            "Kill": {"kill:minecraft:spider", "kill:minecraft:sheep"},
            "Shear": {"shear:minecraft:sheep"},
            "Craft": {"craft:minecraft:iron_pickaxe", "craft:minecraft:iron_ingot"},
            "Smelt": {"smelt:minecraft:iron_ingot"},          # smelting and blasting give ONE action
            "Smith": {"smith:minecraft:netherite_pickaxe"},   # the trim has no result: no action
            "Cut": {"cut:minecraft:stone_bricks"}}

    def test_blocks_and_mobs_without_drops_get_no_action_but_the_block_still_exists(self, mini_jar):
        g = la.build(mini_jar)
        assert "minecraft:air" in {b["id"] for b in g["blocks"]}
        assert "mine:minecraft:air" not in {a["id"] for a in g["actions"]}
        assert "kill:minecraft:allay" not in {a["id"] for a in g["actions"]}
        assert "minecraft:allay" not in {m["id"] for m in g["mobs"]}

    def test_targets(self, mini_jar):
        g = la.build(mini_jar)
        targets = {(t["action"], t["target"], t["kind"]) for t in g["targets"]}
        assert ("mine:minecraft:iron_ore", "minecraft:iron_ore", "Block") in targets
        assert ("kill:minecraft:spider", "minecraft:spider", "Mob") in targets
        assert ("shear:minecraft:sheep", "minecraft:sheep", "Mob") in targets

    def test_tools(self, mini_jar):
        g = la.build(mini_jar)
        tools = {t["action"]: (t["item"], t["orBetter"]) for t in g["tools"]}
        assert tools["mine:minecraft:iron_ore"] == ("minecraft:stone_pickaxe", True)
        assert tools["mine:minecraft:stone"] == ("minecraft:wooden_pickaxe", True)
        assert tools["shear:minecraft:sheep"] == ("minecraft:shears", False)
        assert "mine:minecraft:oak_log" not in tools and "kill:minecraft:spider" not in tools

    def test_gathering_action_properties(self, mini_jar):
        g = la.build(mini_jar)
        by_id = {a["id"]: a for a in g["actions"]}
        assert by_id["mine:minecraft:iron_ore"]["tool"] == "pickaxe" and by_id["mine:minecraft:iron_ore"]["minTier"] == "stone"
        assert by_id["mine:minecraft:oak_log"]["tool"] == "axe" and by_id["mine:minecraft:oak_log"]["toolRequired"] is False
        assert by_id["shear:minecraft:sheep"]["tool"] == "shears" and by_id["shear:minecraft:sheep"]["toolRequired"] is True
        assert by_id["mine:minecraft:iron_ore"]["name"] == "Mine Iron Ore"

    def test_drops_and_conditions(self, mini_jar):
        g = la.build(mini_jar)
        creates = {(c["action"], c["item"], c["condition"]): c["count"] for c in g["creates"]}
        assert creates[("mine:minecraft:iron_ore", "minecraft:raw_iron", "default")] == 1.0
        assert creates[("mine:minecraft:iron_ore", "minecraft:iron_ore", "silk_touch")] == 1.0
        assert creates[("kill:minecraft:spider", "minecraft:string", "default")] == 1.0
        assert ("kill:minecraft:spider", "minecraft:spider_eye", "killed_by_player") in creates

    def test_sheep_colour_tables_are_followed_and_shearing_averages_the_rolls(self, mini_jar):
        g = la.build(mini_jar)
        creates = {(c["action"], c["item"], c["condition"]): c["count"] for c in g["creates"]}
        assert creates[("kill:minecraft:sheep", "minecraft:white_wool", "default")] == 1.0
        assert creates[("kill:minecraft:sheep", "minecraft:mutton", "default")] == 1.0
        assert creates[("shear:minecraft:sheep", "minecraft:white_wool", "default")] == 2.0

    def test_transforming_actions_create_only_their_item(self, mini_jar):
        g = la.build(mini_jar)
        assert {(t["id"], t["item"]) for t in g["transform"]} >= {("smelt:minecraft:iron_ingot", "minecraft:iron_ingot")}
        assert all(set(t) == {"id", "name", "verb", "item"} for t in g["transform"])
        assert next(t for t in g["transform"] if t["id"] == "craft:minecraft:iron_pickaxe")["name"] == "Craft Iron Pickaxe"

    def test_every_referenced_item_has_a_row(self, mini_jar):
        g = la.build(mini_jar)
        known = {i["id"] for i in g["items"]}
        for row in g["creates"]:
            assert row["item"] in known
        for t in g["tools"]:
            assert t["item"] in known
        for t in g["transform"]:
            assert t["item"] in known

    def test_action_ids_are_unique(self, mini_jar):
        g = la.build(mini_jar)
        ids = [a["id"] for a in g["actions"]] + [t["id"] for t in g["transform"]]
        assert len(ids) == len(set(ids))
