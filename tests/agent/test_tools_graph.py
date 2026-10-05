"""Tests for the agent's graph tools against the real graph, rebuilt from source in the throwaway database.

No model is involved: these check the deterministic part of the agent (what the tools return), which the evals
only touch indirectly. Run with:  pytest -m integration
"""
from __future__ import annotations

import json
import re

import pytest

from minecorp_agent import tools

pytestmark = [pytest.mark.integration, pytest.mark.jar]


def call(graph, name, **args):
    return tools.TOOLS_BY_NAME[name].fn(graph, args)


# --------------------------------------------------------------------------- find and name resolution

class TestFind:
    def test_the_exact_name_comes_first(self, agent_graph):
        assert call(agent_graph, "find", query="iron pickaxe")["matches"][0] == {"id": "minecraft:iron_pickaxe", "name": "Iron Pickaxe", "kind": "item"}

    @pytest.mark.parametrize("query", ["iron_ingot", "minecraft:iron_ingot", "Iron Ingot", "IRON INGOT", "  iron   ingot  "])
    def test_ids_slugs_case_and_spacing_all_find_the_same_item(self, agent_graph, query):
        assert call(agent_graph, "find", query=query)["matches"][0]["id"] == "minecraft:iron_ingot"

    def test_words_can_be_in_any_order(self, agent_graph):
        ids = [m["id"] for m in call(agent_graph, "find", query="pickaxe iron")["matches"]]
        assert "minecraft:iron_pickaxe" in ids

    def test_a_long_phrase_still_finds_items_with_two_of_its_words(self, agent_graph):
        ids = {m["id"] for m in call(agent_graph, "find", query="netherite upgrade diamond pickaxe")["matches"]}
        assert {"minecraft:netherite_pickaxe", "minecraft:diamond_pickaxe"} <= ids

    def test_at_most_ten_matches(self, agent_graph):
        assert len(call(agent_graph, "find", query="planks")["matches"]) <= 10
        assert len(call(agent_graph, "find", query="pickaxe")["matches"]) == 8     # 7 pickaxes and their category

    @pytest.mark.parametrize("query", ["flurble", "", "   "])
    def test_nothing_matched(self, agent_graph, query):
        assert call(agent_graph, "find", query=query) == {"matches": [], "note": "Nothing matched."}

    def test_categories_are_marked_with_their_size(self, agent_graph):
        matches = call(agent_graph, "find", query="planks")["matches"]
        planks = next(m for m in matches if m["id"] == "#minecraft:planks")
        assert planks == {"id": "#minecraft:planks", "name": "Planks", "kind": "category", "category": True, "options": 12}


class TestFindEveryKind:
    @pytest.mark.parametrize("query,kind,id", [
        ("creeper", "mob", "minecraft:creeper"), ("desert", "biome", "minecraft:desert"),
        ("Diamond Ore", "block", "minecraft:diamond_ore"), ("village", "structure", None),
        ("planks", "category", "#minecraft:planks")])
    def test_each_kind_is_found_and_labelled(self, agent_graph, query, kind, id):
        matches = call(agent_graph, "find", query=query)["matches"]
        hit = next((m for m in matches if m["kind"] == kind and (id is None or m["id"] == id)), None)
        assert hit, matches

    def test_a_name_that_is_an_item_and_a_block_gives_both(self, agent_graph):
        kinds = {m["kind"] for m in call(agent_graph, "find", query="Diamond Ore")["matches"] if m["name"] == "Diamond Ore"}
        assert {"item", "block"} <= kinds

    def test_the_kind_limits_the_search(self, agent_graph):
        matches = call(agent_graph, "find", query="Diamond Ore", kind="block")["matches"]
        assert matches and {m["kind"] for m in matches} == {"block"}

    def test_an_unknown_kind_is_an_error_for_the_model(self, agent_graph):
        assert "Unknown kind" in call(agent_graph, "find", query="creeper", kind="monster")["error"]

    @pytest.mark.parametrize("query,expected", [("creepers", "Creeper"), ("dimond pickaxe", "Diamond Pickaxe"), ("pick", "Iron Pickaxe")])
    def test_plurals_typos_and_prefixes_still_find_it(self, agent_graph, query, expected):
        assert expected in [m["name"] for m in call(agent_graph, "find", query=query)["matches"]]

    @pytest.mark.parametrize("query", ['iron AND (', 'a+b', '"quote', 'x*y?', 'name:creeper', "\\", '~', 'OR', '[a TO z]'])
    def test_search_syntax_in_the_text_is_not_an_error(self, agent_graph, query):
        assert isinstance(call(agent_graph, "find", query=query)["matches"], list)


class TestResolveItem:
    def test_an_exact_name_resolves(self, agent_graph):
        item, err = tools.resolve_item(agent_graph, "Iron Pickaxe")
        assert err is None and item["id"] == "minecraft:iron_pickaxe"

    def test_an_ambiguous_name_lists_candidates(self, agent_graph):
        item, err = tools.resolve_item(agent_graph, "pickaxe", want_category=False)
        assert item is None and "ambiguous" in err["error"]
        assert 2 <= len(err["candidates"]) <= 8 and all("category" not in c for c in err["candidates"])

    def test_wanting_a_category_returns_the_category(self, agent_graph):
        item, err = tools.resolve_item(agent_graph, "planks", want_category=True)
        assert err is None and item["id"] == "#minecraft:planks"

    def test_wanting_a_category_for_a_plain_item_fails(self, agent_graph):
        item, err = tools.resolve_item(agent_graph, "iron pickaxe", want_category=True)
        assert item is None and "No category matches" in err["error"]

    def test_nothing_found(self, agent_graph):
        item, err = tools.resolve_item(agent_graph, "flurble")
        assert item is None and "No item matches 'flurble'" in err["error"]


# --------------------------------------------------------------------------- get_recipes

class TestGetRecipes:
    def test_the_iron_pickaxe(self, agent_graph):
        r = call(agent_graph, "get_recipes", item="iron pickaxe")
        assert r["item"] == "Iron Pickaxe" and r["note"] == tools.READING_NOTE
        (recipe,) = r["recipes"]
        assert recipe["recipe"] == "Iron Pickaxe" and recipe["station"] == "Crafting Table" and recipe["makes"] == 1
        reqs = {x["item"]: x["count"] for x in recipe["requires"]}
        assert reqs == {"Stick": 2, "Iron Ingot": 3}
        assert recipe["grid"] == ["XXX", " # ", " # "] and recipe["gridKey"] == {"#": "Stick", "X": "Iron Ingot"}

    def test_a_category_with_one_member_is_shown_as_that_item(self, agent_graph):
        recipe = call(agent_graph, "get_recipes", item="iron pickaxe")["recipes"][0]
        assert all("anyOf" not in x for x in recipe["requires"])        # "Iron Tool Materials" would only confuse the model

    def test_the_brown_bed_has_two_ways_and_the_dye_way_excludes_brown(self, agent_graph):
        recipes = call(agent_graph, "get_recipes", item="brown bed")["recipes"]
        assert {r["recipe"] for r in recipes} == {"Brown Bed", "Dye Brown Bed"}
        dye = next(r for r in recipes if r["recipe"] == "Dye Brown Bed")
        bed = next(x for x in dye["requires"] if "anyOf" in x)
        assert bed["anyOf"] == "Bed" and bed["options"] == 16 and bed["except"] == ["Brown Bed"] and bed["count"] == 1
        assert "Brown Bed" not in bed["examples"] and len(bed["examples"]) == 6
        assert {"item": "Brown Dye", "count": 1} in dye["requires"]
        assert "grid" not in dye                                          # shapeless

    def test_every_way_to_make_an_iron_ingot(self, agent_graph):
        recipes = call(agent_graph, "get_recipes", item="iron ingot")["recipes"]
        assert len(recipes) == 8
        assert {"Furnace", "Blast Furnace", "Crafting Table"} <= {r["station"] for r in recipes}
        assert {r["type"] for r in recipes} >= {"smelting", "blasting", "crafting_shaped"}

    def test_an_overview_of_the_route_is_included(self, agent_graph):
        o = call(agent_graph, "get_recipes", item="iron pickaxe")["overview"]
        assert o["craftingSteps"] == 3 and o["chainLength"] == 2 and o["stations"] == ["Crafting Table", "Furnace"]
        assert o["truncated"] is False and "meaning" in o

    def test_an_item_nobody_crafts_points_at_how_to_get(self, agent_graph):
        r = call(agent_graph, "get_recipes", item="nether star")
        assert r["recipes"] == [] and "how_to_get" in r["note"]

    def test_unknown_and_ambiguous_names_are_errors_for_the_model(self, agent_graph):
        assert "No item matches" in call(agent_graph, "get_recipes", item="flurble")["error"]

    def test_a_generic_name_shows_every_item_it_fits_in_one_call(self, agent_graph):
        r = call(agent_graph, "get_recipes", item="pickaxe")
        assert "error" not in r
        assert {i["item"] for i in r["items"]} >= {"Iron Pickaxe", "Diamond Pickaxe", "Netherite Pickaxe"}
        assert all(i["recipes"] for i in r["items"])

    def test_a_name_that_fits_too_many_items_is_ambiguous(self, agent_graph):
        r = call(agent_graph, "get_recipes", item="ore")
        assert "ambiguous" in r["error"] and r["candidates"]

    def test_a_category_name_is_not_an_item(self, agent_graph):
        assert "error" in call(agent_graph, "get_recipes", item="planks")


# --------------------------------------------------------------------------- get_uses and class_members

class TestGetUses:
    def test_diamond(self, agent_graph):
        r = call(agent_graph, "get_uses", item="diamond", limit=5)
        assert r["item"] == "Diamond" and len(r["canMake"]) == 5 and r["showing"] == 5 and r["totalProducts"] >= 20
        everything = {x["makes"] for x in call(agent_graph, "get_uses", item="diamond", limit=60)["canMake"]}
        assert {"Diamond Block", "Diamond Pickaxe", "Jukebox"} <= everything

    def test_products_are_sorted_and_name_their_recipes(self, agent_graph):
        r = call(agent_graph, "get_uses", item="diamond", limit=60)
        names = [x["makes"] for x in r["canMake"]]
        assert names == sorted(names)
        assert all(1 <= len(x["recipes"]) <= 3 for x in r["canMake"])

    def test_something_made_through_a_category_says_so(self, agent_graph):
        r = call(agent_graph, "get_uses", item="diamond", limit=60)
        assert any(x.get("viaCategory") for x in r["canMake"])
        assert all(x["viaCategory"] for x in r["canMake"] if "viaCategory" in x)     # never an empty list

    def test_the_limit_is_clamped(self, agent_graph):
        assert call(agent_graph, "get_uses", item="iron ingot", limit=1000)["showing"] <= 60
        assert call(agent_graph, "get_uses", item="iron ingot", limit=0)["showing"] <= 25
        assert call(agent_graph, "get_uses", item="iron ingot", limit=-3)["showing"] == 1

    def test_unknown_item(self, agent_graph):
        assert "error" in call(agent_graph, "get_uses", item="flurble")


class TestClassMembers:
    @pytest.mark.parametrize("name,total,member", [("planks", 12, "Oak Planks"), ("beds", 16, "Red Bed"), ("wool", 16, "White Wool")])
    def test_members(self, agent_graph, name, total, member):
        r = call(agent_graph, "class_members", category=name)
        assert r["total"] == total and member in r["members"]
        assert r["members"] == sorted(r["members"])

    def test_a_plain_item_is_not_a_category(self, agent_graph):
        assert "No category matches" in call(agent_graph, "class_members", category="iron pickaxe")["error"]

    def test_long_lists_are_cut_at_sixty_with_a_note(self, agent_graph, db):
        big = db.q("MATCH (c:ItemClass) WHERE c.size > 60 AND c.id STARTS WITH '#minecraft:' RETURN c.id AS id, c.size AS size ORDER BY size DESC LIMIT 1")
        if not big:
            pytest.skip("no category has more than 60 items")
        r = call(agent_graph, "class_members", category=big[0]["id"].replace("#minecraft:", ""))
        assert r["total"] == big[0]["size"] and len(r["members"]) == 60 and r["note"] == "List truncated to 60."


# --------------------------------------------------------------------------- rank_recipes

class TestRankRecipes:
    def _key(self, x):
        return (x["craftingSteps"], x["baseMaterials"], x["chainLength"])

    def test_hardest_is_the_default_and_sorted_descending(self, agent_graph):
        r = call(agent_graph, "rank_recipes")
        assert r["order"] == "hardest" and len(r["items"]) == 8
        keys = [self._key(x) for x in r["items"]]
        assert keys == sorted(keys, reverse=True)
        assert "Ranked by crafting steps" in r["note"] and "what_do_i_need" in r["note"]

    def test_easiest_is_ascending_and_starts_at_one_step(self, agent_graph):
        r = call(agent_graph, "rank_recipes", order="easiest", limit=10)
        keys = [self._key(x) for x in r["items"]]
        assert keys == sorted(keys) and r["items"][0]["craftingSteps"] == 1

    @pytest.mark.parametrize("order,expected", [("EASIEST", "easiest"), ("easy", "easiest"), ("hardest", "hardest"),
                                                ("whatever", "hardest"), ("", "hardest")])
    def test_order_words(self, agent_graph, order, expected):
        assert call(agent_graph, "rank_recipes", order=order, limit=2)["order"] == expected

    def test_the_limit_is_clamped(self, agent_graph):
        assert len(call(agent_graph, "rank_recipes", limit=1000)["items"]) == 25
        assert len(call(agent_graph, "rank_recipes", limit=0)["items"]) == 8
        assert len(call(agent_graph, "rank_recipes", limit=-4)["items"]) == 1

    def test_each_item_has_the_summary_the_model_needs(self, agent_graph):
        for x in call(agent_graph, "rank_recipes", limit=5)["items"]:
            assert set(x) == {"item", "craftingSteps", "chainLength", "baseMaterials", "stations"}
            assert x["stations"] and x["craftingSteps"] >= 1


# --------------------------------------------------------------------------- how_to_get

class TestHowToGet:
    def test_gunpowder_comes_from_killing_mobs_and_is_not_crafted(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="gunpowder")
        assert {w["from"] for w in r["ways"]} == {"Creeper", "Ghast", "Witch"}
        assert all(w["how"] == "kill" and w["tool"] == "none needed" for w in r["ways"])
        assert r["alsoCraftable"] is False and "tip" not in r and "howToRead" in r

    def test_raw_iron_is_mined_with_a_stone_pickaxe_or_better_and_points_at_the_route(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="raw iron")
        assert {(w["how"], w["from"], w["tool"]) for w in r["ways"]} == {
            ("mine", "Iron Ore", "Stone Pickaxe or better"), ("mine", "Deepslate Iron Ore", "Stone Pickaxe or better")}
        assert r["alsoCraftable"] is True and "what_do_i_need" in r["tip"]

    def test_wool_can_be_sheared_with_exact_shears(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="white wool")
        shear = next(w for w in r["ways"] if w["how"] == "shear")
        assert shear["from"] == "Sheep" and shear["tool"] == "Shears"
        assert {w["how"] for w in r["ways"]} == {"shear", "kill", "mine"}

    def test_conditions_are_put_into_words(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="blaze rod")
        assert r["ways"] == [{"how": "kill", "from": "Blaze", "tool": "none needed", "when": "only when a player kills it",
                              "typicalCount": 0.5}]

    def test_rare_drops_are_listed_after_the_normal_ones(self, agent_graph):
        ways = call(agent_graph, "how_to_get", item="iron ingot")["ways"]
        flags = ["when" in w for w in ways]
        assert flags == sorted(flags), "normal drops first, conditional ones after"
        assert any(w["from"] == "Iron Golem" for w in ways)

    def test_a_category_covers_all_its_members(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="wool")
        assert r["category"] is True and r["alsoCraftable"] is False
        assert {w["how"] for w in r["ways"]} == {"shear", "kill"}           # one self-dropping block per colour is left out
        assert all(1 <= len(w["gives"]) <= 6 for w in r["ways"])

    def test_a_block_that_drops_itself_says_so(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="oak planks")
        assert r["ways"][0]["note"] == "the block drops itself" and r["alsoCraftable"] is True

    def test_an_item_nothing_drops(self, agent_graph):
        r = call(agent_graph, "how_to_get", item="iron pickaxe")
        assert r["ways"] == [] and r["alsoCraftable"] is True and "get_recipes" in r["note"]

    def test_errors_for_the_model(self, agent_graph):
        assert "No item matches" in call(agent_graph, "how_to_get", item="flurble")["error"]
        assert "ambiguous" in call(agent_graph, "how_to_get", item="iron")["error"]


# --------------------------------------------------------------------------- what_do_i_need

STEP = re.compile(r"^(?P<verb>\w+): (?P<inputs>.+) -> (?P<amount>\d+) (?P<name>.+?)(?: at (?P<station>.+?))?(?: \((?P<times>\d+) times\))?$")
INPUT = re.compile(r"^(?P<n>\d+) (?P<name>.+?)(?: \(any\))?$")

# Ids, so none of them is ambiguous. A spread of shallow, deep, mined, killed and smithed items.
SAMPLE = ["iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "beacon", "white_bed", "tnt", "bow", "redstone_lamp", "saddle",
          "enchanting_table", "bookshelf", "furnace", "anvil", "compass", "clock", "piston", "sticky_piston", "hopper", "rail",
          "powered_rail", "cake", "bread", "golden_apple", "shield", "bucket", "chest", "barrel", "ladder", "torch", "lantern",
          "brewing_stand", "jukebox", "firework_rocket", "spyglass", "lead", "crossbow", "fishing_rod", "carrot_on_a_stick",
          "activator_rail", "observer", "daylight_detector", "comparator", "repeater", "dispenser", "dropper", "iron_ingot", "stick"]


class TestWhatDoINeed:
    def test_the_iron_pickaxe_route(self, agent_graph):
        r = call(agent_graph, "what_do_i_need", item="iron pickaxe")
        assert r["item"] == "Iron Pickaxe" and r["quantity"] == 1
        assert [s["do"] for s in r["steps"]] == [
            "Craft: 2 Planks (any) -> 4 Stick at Crafting Table",
            "Smelt: 3 Raw Iron -> 3 Iron Ingot at Furnace (3 times)",
            "Craft: 2 Stick, 3 Iron Ingot -> 1 Iron Pickaxe at Crafting Table"]
        assert [s["step"] for s in r["steps"]] == [1, 2, 3]
        assert r["overview"] == {"craftingSteps": 3, "chainLength": 2, "baseMaterials": 2,
                                 "stations": ["Crafting Table", "Furnace"], "truncated": False}

    def test_base_materials_say_how_to_get_them(self, agent_graph):
        gather = {g.get("item") or g.get("anyOf"): g for g in call(agent_graph, "what_do_i_need", item="iron pickaxe")["gather"]}
        assert gather["Planks"]["total"] == 2 and len(gather["Planks"]["examples"]) == 6
        raw = gather["Raw Iron"]
        assert raw["total"] == 3
        assert {"how": "mine", "from": "Iron Ore", "tool": "Stone Pickaxe or better"} in raw["getFrom"]

    def test_the_route_uses_raw_iron_not_the_ore_item_which_needs_silk_touch(self, agent_graph):
        r = call(agent_graph, "what_do_i_need", item="iron ingot")
        names = {g.get("item") for g in r["gather"]}
        assert names == {"Raw Iron"}

    def test_it_does_not_go_round_in_circles_through_nuggets_or_blocks(self, agent_graph):
        text = json.dumps(call(agent_graph, "what_do_i_need", item="iron ingot")["steps"][0]["do"])
        assert "Nugget" not in text and "Block" not in text

    def test_alternatives_are_listed(self, agent_graph):
        step = call(agent_graph, "what_do_i_need", item="iron ingot")["steps"][0]
        assert len(step["otherWays"]) >= 3

    def test_quantities_multiply_through(self, agent_graph):
        r = call(agent_graph, "what_do_i_need", item="iron ingot", quantity=9)
        assert r["quantity"] == 9 and r["steps"][0]["do"] == "Smelt: 9 Raw Iron -> 9 Iron Ingot at Furnace (9 times)"
        assert r["gather"][0]["total"] == 9

    def test_a_recipe_that_makes_several_is_rounded_up(self, agent_graph):
        # a stick recipe makes 4 from 2 planks, so 5 sticks needs two crafts
        step = call(agent_graph, "what_do_i_need", item="stick", quantity=5)["steps"][0]
        assert step["do"] == "Craft: 4 Planks (any) -> 8 Stick at Crafting Table (2 times)"

    @pytest.mark.parametrize("asked,used", [(1000, 64), (0, 1), (-5, 1)])
    def test_quantity_is_clamped(self, agent_graph, asked, used):
        assert call(agent_graph, "what_do_i_need", item="iron ingot", quantity=asked)["quantity"] == used

    def test_a_mined_resource_ends_the_route(self, agent_graph):
        r = call(agent_graph, "what_do_i_need", item="diamond pickaxe")
        assert not any("Diamond" in s["do"].split("->")[1] for s in r["steps"] if "Pickaxe" not in s["do"].split("->")[1])
        diamond = next(g for g in r["gather"] if g.get("item") == "Diamond")
        assert diamond["total"] == 3
        assert {"how": "mine", "from": "Diamond Ore", "tool": "Iron Pickaxe or better"} in diamond["getFrom"]

    def test_a_killed_resource_ends_the_route(self, agent_graph):
        gather = {g.get("item"): g for g in call(agent_graph, "what_do_i_need", item="tnt")["gather"]}
        assert any(w["how"] == "kill" and w["from"] == "Creeper" for w in gather["Gunpowder"]["getFrom"])

    def test_the_netherite_pickaxe_ends_at_the_smithing_table(self, agent_graph):
        r = call(agent_graph, "what_do_i_need", item="netherite pickaxe")
        assert r["steps"][-1]["do"].startswith("Smith: ") and r["steps"][-1]["do"].endswith("at Smithing Table")
        assert "Smithing Table" in r["overview"]["stations"]

    def test_an_item_with_no_recipe_is_just_a_base_material(self, agent_graph):
        r = call(agent_graph, "what_do_i_need", item="oak log")
        assert r["steps"] == [] and r["gather"][0]["item"] == "Oak Log" and r["overview"]["craftingSteps"] == 0

    def test_errors_for_the_model(self, agent_graph):
        assert "No item matches" in call(agent_graph, "what_do_i_need", item="flurble")["error"]
        assert "ambiguous" in call(agent_graph, "what_do_i_need", item="bed")["error"]

    @pytest.mark.parametrize("item", SAMPLE)
    def test_every_step_only_uses_what_was_gathered_or_made_earlier(self, agent_graph, item):
        r = call(agent_graph, "what_do_i_need", item=f"minecraft:{item}")
        assert "error" not in r, r
        have = {g.get("item") or g.get("anyOf") for g in r["gather"]}
        for step in r["steps"]:
            m = STEP.match(step["do"])
            assert m, step["do"]
            for part in re.split(r", (?=\d+ )", m["inputs"]):                # a category reads "Any of: A, B, ..." so split only before a count
                needed = INPUT.match(part)["name"]
                assert needed in have, f"{item}: step {step['step']} needs {needed!r} before it exists: {step['do']}"
            have.add(m["name"])
        assert r["quantity"] == 1 and r["overview"]["craftingSteps"] == len(r["steps"])
        assert len(json.dumps(r)) < 12000, "the answer must stay small enough for the model"
        assert r["steps"] == [] or STEP.match(r["steps"][-1]["do"])["name"] == r["item"]


# --------------------------------------------------------------------------- the route's building blocks

class TestRouteInternals:
    def route(self, graph):
        return tools.Route(graph, 6)

    def test_reversal(self, agent_graph):
        r = self.route(agent_graph)
        assert r.is_reversal("minecraft:iron_ingot", "minecraft:iron_nugget") is True      # nuggets can be made from ingots
        assert r.is_reversal("minecraft:iron_ingot", "minecraft:iron_block") is True
        assert r.is_reversal("minecraft:iron_pickaxe", "minecraft:stick") is False
        assert r.is_reversal("minecraft:iron_ingot", "minecraft:raw_iron") is False

    def test_silk_touch_only_items(self, agent_graph):
        r = self.route(agent_graph)
        assert r.silk_only("minecraft:iron_ore") is True                                      # the block drops raw iron
        assert r.silk_only("minecraft:raw_iron") is False
        assert r.silk_only("minecraft:stick") is False

    def test_mined_from_means_a_different_block_or_a_mob(self, agent_graph):
        r = self.route(agent_graph)
        assert {m["source"] for m in r.mined_from("minecraft:raw_iron")} == {"Iron Ore", "Deepslate Iron Ore"}
        assert any(m["how"] == "kill" and m["source"] == "Spider" for m in r.mined_from("minecraft:string"))
        assert r.mined_from("minecraft:oak_log") == [], "a log is dropped by a block of the same name"
        assert r.mined_from("minecraft:obsidian") == []

    def test_sources_of_puts_mobs_first_and_a_self_dropping_block_before_others(self, agent_graph):
        r = self.route(agent_graph)
        assert r.sources_of("minecraft:string")[0]["how"] == "kill"
        obsidian = r.sources_of("minecraft:obsidian")
        assert obsidian[0] == {"how": "mine", "source": "Obsidian", "tool": "Diamond Pickaxe", "orBetter": True, "selfDrop": True}
        assert any(s["source"] == "Ender Chest" for s in obsidian)
        assert len(r.sources_of("minecraft:string")) <= 4

    def test_choose_prefers_the_simple_non_reversing_recipe(self, agent_graph):
        r = self.route(agent_graph)
        best, others, good = r.choose("minecraft:iron_ingot", frozenset())
        assert best["name"] == "Iron Ingot From Smelting Raw Iron" and good is True and others

    def test_choose_marks_a_reversing_only_choice_as_not_good(self, agent_graph):
        best, _, good = self.route(agent_graph).choose("minecraft:raw_iron", frozenset())
        assert best is not None and good is False                                              # only from the raw iron block

    def test_choose_skips_a_recipe_that_needs_something_already_on_the_route(self, agent_graph):
        best, _, _ = self.route(agent_graph).choose("minecraft:iron_ingot", frozenset({"minecraft:raw_iron", "minecraft:iron_ore",
                                                                                         "minecraft:deepslate_iron_ore"}))
        assert best is None or "Raw Iron" not in best["name"]


class TestStoredRoutesMatchTheTools:
    """compute_routes.py stores the same numbers that get_recipes and what_do_i_need report."""

    def test_stored_properties_equal_a_fresh_calculation(self, agent_graph, db):
        rows = db.q("MATCH (i:Item) WHERE i.routeSteps IS NOT NULL RETURN i.id AS id, i.routeSteps AS steps, i.routeLevels AS levels, "
                    "i.routeBaseMaterials AS base, i.routeStations AS stations ORDER BY id")
        assert len(rows) > 800
        for row in rows[::40]:                                               # every 40th item keeps this quick
            fresh = tools.route_overview(agent_graph, row["id"])
            assert (fresh["craftingSteps"], fresh["chainLength"], fresh["baseMaterials"], fresh["stations"]) == (
                row["steps"], row["levels"], row["base"], row["stations"]), row["id"]
