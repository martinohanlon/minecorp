"""Tests against the real Paper jar: invariants that must hold for any Minecraft version, plus
known vanilla facts that act as canaries when the server version is changed.

Skipped when the jar has not been downloaded (run scripts/start.ps1 once).
"""
from __future__ import annotations

import pytest

import load_actions as la
import load_recipes as lr

pytestmark = pytest.mark.jar


@pytest.fixture(scope="module")
def recipes_and_tags(jar_path):
    return lr.read_jar(jar_path)


@pytest.fixture(scope="module")
def normalised(recipes_and_tags):
    recipes, _ = recipes_and_tags
    return {rid: lr.normalise(rid, raw) for rid, raw in recipes.items()}


@pytest.fixture(scope="module")
def graph(normalised, recipes_and_tags):
    _, tags = recipes_and_tags
    return lr.to_graph(normalised, tags, set(tags), [])


@pytest.fixture(scope="module")
def actions(jar_path):
    return la.build(jar_path)


# --------------------------------------------------------------------------- recipes and tags

class TestRecipes:
    def test_the_jar_has_recipes_and_tags(self, recipes_and_tags):
        recipes, tags = recipes_and_tags
        assert len(recipes) > 1000 and len(tags) > 150

    def test_every_recipe_normalises(self, normalised):
        assert len(normalised) > 1000
        for r in normalised.values():
            assert r["id"].startswith("minecraft:") and r["name"]
            if r["result"]:
                item, count = r["result"]
                assert item.startswith("minecraft:") and count >= 1
            for role, ref, count, _slot in r["inputs"]:
                assert role and count >= 1 and isinstance(ref, (str, list))

    def test_a_recipe_without_inputs_is_flagged_special(self, normalised):
        assert all(r["special"] == (not r["inputs"]) for r in normalised.values())

    def test_every_recipe_type_is_known_or_has_no_station(self, normalised):
        for r in normalised.values():
            assert r["station"] is not None or not r["type"].startswith("crafting_")

    def test_nested_tag_references_all_resolve(self, recipes_and_tags):
        _, tags = recipes_and_tags
        for tag_id, values in tags.items():
            for v in values:
                assert isinstance(v, str)
                if v.startswith("#"):
                    assert v[1:] in tags, f"{tag_id} refers to missing tag {v}"


class TestGraphRows:
    def test_requires_targets_exist(self, graph):
        known = {i["id"] for i in graph["items"]} | {c["id"] for c in graph["classes"]}
        for edge in graph["requires"]:
            assert edge["target"] in known, edge

    def test_class_ids_are_prefixed_and_unique(self, graph):
        ids = [c["id"] for c in graph["classes"]]
        assert len(ids) == len(set(ids))
        assert all(i.startswith(("#minecraft:", "inline:")) for i in ids)
        item_ids = {i["id"] for i in graph["items"]}
        assert not (set(ids) & item_ids)          # the six tag/item name clashes are kept apart by the '#'

    def test_every_class_has_a_name_and_a_size(self, graph):
        for c in graph["classes"]:
            assert c["name"] and c["size"] >= 0

    def test_exclusions_are_a_single_member_of_the_class(self, graph):
        members = {}
        for inc in graph["includes"]:
            members.setdefault(inc["cls"], set()).add(inc["target"])
        for edge in graph["requires"]:
            assert len(edge["excludes"]) <= 1
            for ex in edge["excludes"]:
                assert ex in members[edge["target"]] or any(ex in members.get(m, ()) for m in members[edge["target"]])

    def test_only_a_handful_of_anonymous_lists_remain(self, graph):
        inline = [c for c in graph["classes"] if c["inline"]]
        assert 0 < len(inline) < 30

    def test_include_rows_are_unique(self, graph):
        rows = [(i["cls"], i["target"]) for i in graph["includes"]]
        assert len(rows) == len(set(rows))

    def test_every_station_is_a_craftable_item(self, graph):
        created = {c["item"] for c in graph["creates"]}
        for s in graph["stations"]:
            assert s["id"] in created, f"{s['id']} has no recipe"

    def test_known_class_names(self, graph):
        names = {c["id"]: c["name"] for c in graph["classes"]}
        assert names["#minecraft:beds"] == "Bed"
        assert names["#minecraft:planks"] == "Planks"
        assert names["#minecraft:slabs"] == "Slabs" and names["#minecraft:wooden_slabs"] == "Wooden Slabs"

    def test_dye_brown_bed_is_the_beds_tag_without_the_brown_bed(self, graph):
        edges = [e for e in graph["requires"] if e["recipe"] == "minecraft:dye_brown_bed"]
        bed = next(e for e in edges if e["target"] == "#minecraft:beds")
        assert bed["excludes"] == ["minecraft:brown_bed"]
        assert any(e["target"] == "minecraft:brown_dye" for e in edges)


# --------------------------------------------------------------------------- actions

class TestActions:
    def test_counts_are_in_the_expected_range(self, actions):
        by_verb = {}
        for a in actions["actions"] + actions["transform"]:
            by_verb[a["verb"]] = by_verb.get(a["verb"], 0) + 1
        assert by_verb["Mine"] > 1000 and by_verb["Kill"] > 50 and 3 <= by_verb["Shear"] <= 10
        assert by_verb["Craft"] > 800 and by_verb["Cut"] > 100 and by_verb["Smelt"] > 40 and by_verb["Smith"] >= 10

    def test_action_ids_are_unique_and_prefixed_by_verb(self, actions):
        everything = actions["actions"] + actions["transform"]
        ids = [a["id"] for a in everything]
        assert len(ids) == len(set(ids))
        for a in everything:
            assert a["id"].startswith(a["verb"].lower() + ":minecraft:")

    def test_every_row_points_at_a_real_action_and_item(self, actions):
        ids = {a["id"] for a in actions["actions"]}
        items = {i["id"] for i in actions["items"]}
        for row in actions["creates"]:
            assert row["action"] in ids and row["item"] in items
        for row in actions["tools"]:
            assert row["action"] in ids and row["item"] in items
        for row in actions["targets"]:
            assert row["action"] in ids

    def test_every_target_exists(self, actions):
        blocks = {b["id"] for b in actions["blocks"]}
        mobs = {m["id"] for m in actions["mobs"]}
        for t in actions["targets"]:
            assert t["target"] in (blocks if t["kind"] == "Block" else mobs)

    def test_every_gathering_action_has_a_target_and_a_drop(self, actions):
        with_target = {t["action"] for t in actions["targets"]}
        with_drop = {c["action"] for c in actions["creates"]}
        for a in actions["actions"]:
            assert a["id"] in with_target and a["id"] in with_drop

    def test_tier_lists_are_consistent_for_every_block(self, actions):
        assert actions["stats"].get("tier list not monotonic", 0) == 0

    def test_transforming_actions_match_a_recipe_of_their_kind(self, actions, normalised):
        made = {}
        for r in normalised.values():
            if r["result"]:
                made.setdefault(la.RECIPE_VERB.get(r["type"], "Craft"), set()).add(r["result"][0])
        for t in actions["transform"]:
            assert t["item"] in made[t["verb"]], t

    def test_drop_counts_and_conditions_are_sane(self, actions):
        valid_parts = {"default", "silk_touch", "shears", "state", "chance", "killed_by_player"}
        for c in actions["creates"]:
            assert c["count"] >= 0
            assert set(c["condition"].split("+")) <= valid_parts, c

    def test_every_tool_is_a_craftable_item(self, actions, normalised):
        craftable = {r["result"][0] for r in normalised.values() if r["result"]}
        for t in actions["tools"]:
            assert t["item"] in craftable, t


class TestKnownVanillaFacts:
    """These describe Minecraft itself. If one fails after a version change, check the game first."""

    def _tool(self, actions, action_id):
        return next((t["item"] for t in actions["tools"] if t["action"] == action_id), None)

    def _drops(self, actions, action_id):
        return {(c["item"], c["condition"]) for c in actions["creates"] if c["action"] == action_id}

    @pytest.mark.parametrize("block,tool", [
        ("minecraft:stone", "minecraft:wooden_pickaxe"), ("minecraft:coal_ore", "minecraft:wooden_pickaxe"),
        ("minecraft:iron_ore", "minecraft:stone_pickaxe"), ("minecraft:deepslate_iron_ore", "minecraft:stone_pickaxe"),
        ("minecraft:diamond_ore", "minecraft:iron_pickaxe"), ("minecraft:gold_ore", "minecraft:iron_pickaxe"),
        ("minecraft:obsidian", "minecraft:diamond_pickaxe"), ("minecraft:ancient_debris", "minecraft:diamond_pickaxe")])
    def test_minimum_pickaxe(self, actions, block, tool):
        assert self._tool(actions, f"mine:{block}") == tool

    def test_logs_and_dirt_need_no_tool(self, actions):
        assert self._tool(actions, "mine:minecraft:oak_log") is None
        assert self._tool(actions, "mine:minecraft:dirt") is None

    def test_ore_drops(self, actions):
        d = self._drops(actions, "mine:minecraft:iron_ore")
        assert ("minecraft:raw_iron", "default") in d and ("minecraft:iron_ore", "silk_touch") in d
        assert ("minecraft:diamond", "default") in self._drops(actions, "mine:minecraft:diamond_ore")

    def test_mob_drops(self, actions):
        assert ("minecraft:string", "default") in self._drops(actions, "kill:minecraft:spider")
        assert ("minecraft:gunpowder", "default") in self._drops(actions, "kill:minecraft:creeper")
        assert ("minecraft:blaze_rod", "killed_by_player") in self._drops(actions, "kill:minecraft:blaze")
        cow = {i for i, _ in self._drops(actions, "kill:minecraft:cow")}
        assert {"minecraft:beef", "minecraft:leather"} <= cow

    def test_shearing(self, actions):
        sheep = {i for i, _ in self._drops(actions, "shear:minecraft:sheep")}
        assert {"minecraft:white_wool", "minecraft:red_wool", "minecraft:black_wool"} <= sheep
        assert self._tool(actions, "shear:minecraft:sheep") == "minecraft:shears"
        mooshroom = {i for i, _ in self._drops(actions, "shear:minecraft:mooshroom")}
        assert {"minecraft:red_mushroom", "minecraft:brown_mushroom"} <= mooshroom

    def test_killing_a_sheep_gives_wool_and_mutton(self, actions):
        got = {i for i, _ in self._drops(actions, "kill:minecraft:sheep")}
        assert "minecraft:mutton" in got and "minecraft:white_wool" in got

    def test_smelting_covers_the_iron_ingot(self, actions):
        assert any(t["id"] == "smelt:minecraft:iron_ingot" for t in actions["transform"])
        assert any(t["id"] == "craft:minecraft:iron_pickaxe" for t in actions["transform"])
        assert any(t["id"] == "smith:minecraft:netherite_pickaxe" for t in actions["transform"])
        assert any(t["id"] == "cut:minecraft:stone_bricks" for t in actions["transform"])
