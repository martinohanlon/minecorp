"""Unit tests for loader/load_recipes.py: parsing recipes and turning them into graph rows."""
from __future__ import annotations

import pytest

import load_recipes as lr


# --------------------------------------------------------------------------- names

def test_display_name():
    assert lr.display_name("minecraft:iron_ingot") == "Iron Ingot"
    assert lr.display_name("iron_ingot") == "Iron Ingot"


def test_short_type():
    assert lr.short_type("minecraft:smelting") == "smelting"
    assert lr.short_type("smelting") == "smelting"


# --------------------------------------------------------------------------- ingredient references

class TestNormRef:
    def test_item(self):
        assert lr.norm_ref("minecraft:stick") == ("item", "minecraft:stick", None)

    def test_tag_loses_the_hash(self):
        assert lr.norm_ref("#minecraft:planks") == ("tag", "minecraft:planks", None)

    def test_inline_list_is_sorted_unique_with_a_stable_id(self):
        a = lr.norm_ref(["minecraft:b", "minecraft:a", "minecraft:a"])
        b = lr.norm_ref(["minecraft:a", "minecraft:b"])
        assert a == b
        kind, ident, members = a
        assert kind == "inline" and members == ["minecraft:a", "minecraft:b"]
        assert ident.startswith("inline:") and len(ident) == len("inline:") + 12

    def test_different_lists_get_different_ids(self):
        assert lr.norm_ref(["minecraft:a"])[1] != lr.norm_ref(["minecraft:b"])[1]


# --------------------------------------------------------------------------- the 2x2 inventory grid

class TestInventoryGrid:
    """A crafting recipe that fits the 2x2 grid of the player's inventory does not need a crafting table (the game's rule)."""

    def shaped(self, pattern):
        return {"type": "minecraft:crafting_shaped", "pattern": pattern, "key": {"#": "minecraft:stick"}, "result": {"id": "minecraft:x"}}

    @pytest.mark.parametrize("pattern", [["#"], ["#", "#"], ["##"], ["##", "##"], ["# ", " #"]])
    def test_a_shaped_recipe_of_at_most_2_by_2_fits(self, pattern):
        assert lr.fits_inventory_grid("crafting_shaped", self.shaped(pattern)) is True

    @pytest.mark.parametrize("pattern", [["###"], ["#", "#", "#"], ["##", "##", "##"], ["###", "###"], ["###", "# #", "###"]])
    def test_a_shaped_recipe_wider_or_taller_than_2_does_not(self, pattern):
        assert lr.fits_inventory_grid("crafting_shaped", self.shaped(pattern)) is False

    def test_a_shapeless_recipe_of_four_ingredients_fits_and_five_does_not(self):
        raw = lambda n: {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:stick"] * n}      # noqa: E731
        assert lr.fits_inventory_grid("crafting_shapeless", raw(1)) and lr.fits_inventory_grid("crafting_shapeless", raw(4))
        assert not lr.fits_inventory_grid("crafting_shapeless", raw(5))

    def test_a_transmute_recipe_fits(self):
        assert lr.fits_inventory_grid("crafting_transmute", {"input": "#minecraft:bundles", "material": "minecraft:black_dye"}) is True

    @pytest.mark.parametrize("rtype", ["crafting_special_firework_rocket", "crafting_dye", "crafting_decorated_pot", "smelting"])
    def test_a_recipe_with_no_pattern_in_the_data_is_not_claimed_to_fit(self, rtype):
        assert lr.fits_inventory_grid(rtype, {}) is False

    def test_normalise_marks_only_crafting_table_recipes(self):
        small = lr.normalise("minecraft:stick", self.shaped(["#", "#"]))
        big = lr.normalise("minecraft:chest", self.shaped(["###", "# #", "###"]))
        smelt = lr.normalise("minecraft:iron_ingot", {"type": "minecraft:smelting", "ingredient": "minecraft:raw_iron", "result": {"id": "minecraft:iron_ingot"}})
        assert small["stationOptional"] is True and big["stationOptional"] is False and smelt["stationOptional"] is False

    def test_the_graph_row_says_it(self):
        chosen = {"minecraft:stick": lr.normalise("minecraft:stick", self.shaped(["#", "#"])),
                  "minecraft:chest": lr.normalise("minecraft:chest", self.shaped(["###", "# #", "###"]))}
        rows = lr.to_graph(chosen, {}, set(), [])["uses_station"]
        assert {r["recipe"]: r["optional"] for r in rows} == {"minecraft:stick": True, "minecraft:chest": False}


# --------------------------------------------------------------------------- normalise

class TestNormalise:
    def test_shaped_counts_key_symbols_in_the_pattern(self):
        raw = {"type": "minecraft:crafting_shaped", "category": "equipment",
               "key": {"#": "minecraft:stick", "X": "#minecraft:iron_tool_materials"},
               "pattern": ["XXX", " # ", " # "], "result": {"id": "minecraft:iron_pickaxe"}}
        r = lr.normalise("minecraft:iron_pickaxe", raw)
        assert r["type"] == "crafting_shaped"
        assert r["result"] == ("minecraft:iron_pickaxe", 1)
        assert sorted(r["inputs"], key=lambda i: i[3]) == [
            ("ingredient", "minecraft:stick", 2, "#"),
            ("ingredient", "#minecraft:iron_tool_materials", 3, "X")]
        assert r["station"] == ("minecraft:crafting_table", "Crafting Table")
        assert r["special"] is False
        assert r["pattern"] == ["XXX", " # ", " # "]
        assert r["name"] == "Iron Pickaxe"

    def test_shaped_full_grid_counts_nine(self):
        raw = {"type": "minecraft:crafting_shaped", "key": {"#": "minecraft:iron_nugget"},
               "pattern": ["###", "###", "###"], "result": {"id": "minecraft:iron_ingot"}}
        assert lr.normalise("minecraft:x", raw)["inputs"] == [("ingredient", "minecraft:iron_nugget", 9, "#")]

    def test_shapeless_counts_repeated_ingredients(self):
        raw = {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:a", "minecraft:a", "minecraft:b"],
               "result": {"id": "minecraft:out"}}
        inputs = {(ref, n) for _role, ref, n, _slot in lr.normalise("minecraft:x", raw)["inputs"]}
        assert inputs == {("minecraft:a", 2), ("minecraft:b", 1)}

    def test_shapeless_alternative_list_is_one_input(self):
        raw = {"type": "minecraft:crafting_shapeless",
               "ingredients": ["minecraft:brown_dye", ["minecraft:red_bed", "minecraft:blue_bed"]],
               "result": {"id": "minecraft:brown_bed"}}
        inputs = lr.normalise("minecraft:x", raw)["inputs"]
        assert len(inputs) == 2
        assert any(isinstance(ref, list) for _role, ref, _n, _slot in inputs)

    def test_result_count(self):
        raw = {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:bone"],
               "result": {"id": "minecraft:bone_meal", "count": 3}}
        assert lr.normalise("minecraft:x", raw)["result"] == ("minecraft:bone_meal", 3)

    @pytest.mark.parametrize("rtype,station", [
        ("smelting", "minecraft:furnace"), ("blasting", "minecraft:blast_furnace"), ("smoking", "minecraft:smoker"),
        ("campfire_cooking", "minecraft:campfire"), ("stonecutting", "minecraft:stonecutter")])
    def test_single_ingredient_types_and_their_stations(self, rtype, station):
        raw = {"type": f"minecraft:{rtype}", "ingredient": "minecraft:raw_iron", "result": {"id": "minecraft:iron_ingot"},
               "experience": 0.7, "cookingtime": 100}
        r = lr.normalise("minecraft:x", raw)
        assert r["inputs"] == [("ingredient", "minecraft:raw_iron", 1, "")]
        assert r["station"][0] == station
        assert r["experience"] == 0.7 and r["cookingTime"] == 100

    def test_smithing_transform_roles(self):
        raw = {"type": "minecraft:smithing_transform", "template": "minecraft:t", "base": "minecraft:b",
               "addition": "#minecraft:a", "result": {"id": "minecraft:out"}}
        r = lr.normalise("minecraft:x", raw)
        assert {role: ref for role, ref, _n, _s in r["inputs"]} == {
            "template": "minecraft:t", "base": "minecraft:b", "addition": "#minecraft:a"}
        assert r["station"][0] == "minecraft:smithing_table"

    def test_smithing_trim_has_no_result(self):
        raw = {"type": "minecraft:smithing_trim", "template": "minecraft:t", "base": "#minecraft:armor",
               "addition": "#minecraft:trim_materials", "pattern": "minecraft:bolt"}
        assert lr.normalise("minecraft:x", raw)["result"] is None

    def test_transmute_roles(self):
        raw = {"type": "minecraft:crafting_transmute", "input": "#minecraft:bundles", "material": "minecraft:black_dye",
               "result": {"id": "minecraft:black_bundle"}}
        roles = {role for role, *_ in lr.normalise("minecraft:x", raw)["inputs"]}
        assert roles == {"input", "material"}

    def test_special_recipe_is_flagged_and_uses_the_crafting_table(self):
        r = lr.normalise("minecraft:banner_duplicate", {"type": "minecraft:crafting_special_bannerduplicate"})
        assert r["special"] is True and r["inputs"] == [] and r["result"] is None
        assert r["station"][0] == "minecraft:crafting_table"

    def test_unknown_non_crafting_type_has_no_station(self):
        r = lr.normalise("minecraft:x", {"type": "minecraft:something_new", "result": {"id": "minecraft:y"}})
        assert r["station"] is None


# --------------------------------------------------------------------------- tags and subset

def test_tag_items_follows_nested_tags_and_survives_cycles():
    tags = {"a": ["#b", "minecraft:x"], "b": ["minecraft:y", "#a"]}
    items, seen = lr.tag_items("a", tags)
    assert items == {"minecraft:x", "minecraft:y"}
    assert seen == {"a", "b"}


def test_tag_items_unknown_tag_is_empty():
    assert lr.tag_items("nope", {})[0] == set()


def _norm(rid, rtype, result, **fields):
    return lr.normalise(rid, {"type": f"minecraft:{rtype}", "result": {"id": result}, **fields})


@pytest.fixture
def iron_recipes():
    return {r["id"]: r for r in [
        _norm("minecraft:iron_pickaxe", "crafting_shapeless", "minecraft:iron_pickaxe",
              ingredients=["minecraft:iron_ingot", "minecraft:stick"]),
        _norm("minecraft:ingot_from_raw", "smelting", "minecraft:iron_ingot", ingredient="minecraft:raw_iron"),
        _norm("minecraft:ingot_from_nuggets", "crafting_shapeless", "minecraft:iron_ingot",
              ingredients=["minecraft:iron_nugget"]),
        _norm("minecraft:nugget_from_ingot", "crafting_shapeless", "minecraft:iron_nugget",
              ingredients=["minecraft:iron_ingot"]),
        _norm("minecraft:stick", "crafting_shapeless", "minecraft:stick", ingredients=["#minecraft:planks"]),
        _norm("minecraft:unrelated", "crafting_shapeless", "minecraft:glass", ingredients=["minecraft:sand"]),
    ]}


class TestBuildSubset:
    def test_walks_backwards_and_ignores_unrelated_recipes(self, iron_recipes):
        chosen, used_tags = lr.build_subset(iron_recipes, {"minecraft:planks": ["minecraft:oak_planks"]},
                                            ["minecraft:iron_pickaxe"], max_depth=4)
        assert set(chosen) == {"minecraft:iron_pickaxe", "minecraft:ingot_from_raw", "minecraft:ingot_from_nuggets",
                               "minecraft:nugget_from_ingot", "minecraft:stick"}
        assert "minecraft:planks" in used_tags

    def test_cycle_between_ingot_and_nuggets_terminates(self, iron_recipes):
        chosen, _ = lr.build_subset(iron_recipes, {}, ["minecraft:iron_ingot"], max_depth=10)
        assert "minecraft:nugget_from_ingot" in chosen and "minecraft:ingot_from_nuggets" in chosen

    def test_depth_limit(self, iron_recipes):
        chosen, _ = lr.build_subset(iron_recipes, {}, ["minecraft:iron_pickaxe"], max_depth=1)
        assert set(chosen) == {"minecraft:iron_pickaxe"}


# --------------------------------------------------------------------------- class names

class TestClassNames:
    def test_common_trailing_words(self):
        assert lr.common_trailing_words(["minecraft:red_bed", "minecraft:blue_bed"]) == "Bed"
        assert lr.common_trailing_words(["minecraft:iron_ore", "minecraft:deepslate_iron_ore"]) == "Iron Ore"
        assert lr.common_trailing_words(["minecraft:oak_log", "minecraft:oak_wood"]) == ""

    def test_shared_word_wins_for_two_or_more_items(self):
        assert lr.class_name("#minecraft:beds", False, {"minecraft:red_bed", "minecraft:blue_bed"}) == "Bed"

    def test_real_tag_falls_back_to_its_title_cased_name(self):
        assert lr.class_name("#minecraft:bee_food", False, {"minecraft:poppy", "minecraft:dandelion"}) == "Bee Food"

    def test_single_member_real_tag_uses_the_tag_name(self):
        assert lr.class_name("#minecraft:iron_tool_materials", False, {"minecraft:iron_ingot"}) == "Iron Tool Materials"

    def test_nested_tag_slash_becomes_a_space(self):
        assert lr.class_name("#minecraft:enchantable/foot_armor", False, {"minecraft:a"}) == "Enchantable Foot Armor"

    def test_inline_without_a_shared_word_lists_members(self):
        name = lr.class_name("inline:abc", True, {"minecraft:b_x", "minecraft:a_y"})
        assert name == "Any of: A Y, B X"

    def test_inline_lists_at_most_four(self):
        name = lr.class_name("inline:abc", True, {f"minecraft:item{i}_{i}" for i in range(6)})
        assert name.endswith(", ...") and name.count(",") == 4


# --------------------------------------------------------------------------- to_graph

def _graph(recipes, tags, used=(), seeds=()):
    return lr.to_graph(recipes, tags, set(used), list(seeds))


class TestToGraph:
    TAGS = {"minecraft:beds": ["minecraft:white_bed", "minecraft:red_bed", "minecraft:brown_bed"]}

    def _bed_recipe(self):
        raw = {"type": "minecraft:crafting_shapeless",
               "ingredients": ["minecraft:brown_dye", ["minecraft:white_bed", "minecraft:red_bed"]],
               "result": {"id": "minecraft:brown_bed"}}
        r = lr.normalise("minecraft:dye_brown_bed", raw)
        return {r["id"]: r}

    def test_inline_list_that_is_a_tag_minus_one_becomes_the_tag_with_an_exclusion(self):
        g = _graph(self._bed_recipe(), self.TAGS)
        bed_edge = next(e for e in g["requires"] if e["target"].startswith("#"))
        assert bed_edge["target"] == "#minecraft:beds"
        assert bed_edge["excludes"] == ["minecraft:brown_bed"]
        assert bed_edge["excludesNames"] == ["Brown Bed"]
        assert not any(c["inline"] for c in g["classes"])
        cls = next(c for c in g["classes"] if c["id"] == "#minecraft:beds")
        assert cls["name"] == "Bed" and cls["size"] == 3

    def test_dye_edge_is_a_plain_item_with_no_exclusion(self):
        g = _graph(self._bed_recipe(), self.TAGS)
        dye = next(e for e in g["requires"] if e["target"] == "minecraft:brown_dye")
        assert dye["count"] == 1 and dye["excludes"] == []

    def test_list_that_is_exactly_a_tag_has_no_exclusion(self):
        raw = {"type": "minecraft:crafting_shapeless", "ingredients": [list(self.TAGS["minecraft:beds"])],
               "result": {"id": "minecraft:x"}}
        r = lr.normalise("minecraft:r", raw)
        g = _graph({r["id"]: r}, self.TAGS)
        assert g["requires"][0]["target"] == "#minecraft:beds" and g["requires"][0]["excludes"] == []

    def test_list_missing_two_items_stays_an_inline_class(self):
        tags = {"minecraft:t": ["minecraft:a", "minecraft:b", "minecraft:c", "minecraft:d"]}
        raw = {"type": "minecraft:crafting_shapeless", "ingredients": [["minecraft:a", "minecraft:b"]],
               "result": {"id": "minecraft:x"}}
        r = lr.normalise("minecraft:r", raw)
        g = _graph({r["id"]: r}, tags)
        edge = g["requires"][0]
        assert edge["target"].startswith("inline:") and edge["excludes"] == []
        assert next(c for c in g["classes"] if c["id"] == edge["target"])["inline"] is True

    def test_tag_reference_creates_a_hash_prefixed_class_with_includes(self):
        raw = {"type": "minecraft:crafting_shapeless", "ingredients": ["#minecraft:planks"], "result": {"id": "minecraft:stick"}}
        r = lr.normalise("minecraft:stick", raw)
        g = _graph({r["id"]: r}, {"minecraft:planks": ["minecraft:oak_planks", "minecraft:birch_planks"]})
        assert g["requires"][0]["target"] == "#minecraft:planks"
        assert {i["target"] for i in g["includes"] if i["cls"] == "#minecraft:planks"} == {"minecraft:oak_planks", "minecraft:birch_planks"}
        assert {"minecraft:oak_planks", "minecraft:birch_planks"} <= {i["id"] for i in g["items"]}

    def test_class_id_never_collides_with_an_item_id(self):
        tags = {"minecraft:anvil": ["minecraft:anvil", "minecraft:chipped_anvil"]}
        g = _graph({}, tags, used=["minecraft:anvil"])
        assert [c["id"] for c in g["classes"]] == ["#minecraft:anvil"]
        assert "minecraft:anvil" in {i["id"] for i in g["items"]}

    def test_two_tags_with_the_same_shared_word_use_their_full_names(self):
        tags = {"minecraft:slabs": ["minecraft:stone_slab", "minecraft:oak_slab"],
                "minecraft:wooden_slabs": ["minecraft:oak_slab", "minecraft:birch_slab"]}
        g = _graph({}, tags, used=list(tags))
        assert {c["id"]: c["name"] for c in g["classes"]} == {
            "#minecraft:slabs": "Slabs", "#minecraft:wooden_slabs": "Wooden Slabs"}

    def test_nested_tags_are_included_once(self):
        tags = {"minecraft:outer": ["#minecraft:inner", "minecraft:x_a"], "minecraft:inner": ["minecraft:y_a", "minecraft:z_a"]}
        g = _graph({}, tags, used=["minecraft:outer", "minecraft:inner"])
        rows = [(i["cls"], i["target"]) for i in g["includes"]]
        assert len(rows) == len(set(rows))
        assert ("#minecraft:outer", "#minecraft:inner") in rows
        outer = next(c for c in g["classes"] if c["id"] == "#minecraft:outer")
        assert outer["size"] == 3

    def test_creates_station_and_recipe_rows(self):
        r = _norm("minecraft:ingot", "smelting", "minecraft:iron_ingot", ingredient="minecraft:raw_iron")
        r2 = _norm("minecraft:pick", "crafting_shapeless", "minecraft:pick", ingredients=["minecraft:iron_ingot"])
        g = _graph({r["id"]: r, r2["id"]: r2}, {})
        assert {(c["recipe"], c["item"], c["count"]) for c in g["creates"]} == {
            ("minecraft:ingot", "minecraft:iron_ingot", 1), ("minecraft:pick", "minecraft:pick", 1)}
        assert {s["id"] for s in g["stations"]} == {"minecraft:furnace", "minecraft:crafting_table"}
        assert {u["station"] for u in g["uses_station"]} == {"minecraft:furnace", "minecraft:crafting_table"}
        assert {x["id"] for x in g["recipes"]} == {"minecraft:ingot", "minecraft:pick"}
        assert all("inputs" not in x and "result" not in x for x in g["recipes"])

    def test_seed_items_are_always_present(self):
        g = _graph({}, {}, seeds=["minecraft:iron_pickaxe"])
        assert [i["id"] for i in g["items"]] == ["minecraft:iron_pickaxe"]
