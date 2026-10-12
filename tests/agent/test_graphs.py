"""Unit tests for agent/minecorp_agent/graphs.py: the graph patterns shown as sources. No database, no model.

That a pattern is a path that really exists is checked against the graph in test_sources_graph.py (and test_wiki_graph.py for the wiki)."""
from __future__ import annotations

import pytest

from minecorp_agent import graphs as g
from minecorp_agent.graphs import IN, Node, Rel, named, path


def compact(patterns):
    return [p.compact() for p in patterns]


def cypher(patterns):
    return [p.cypher() for p in patterns]


class TestRendering:
    def test_a_path_in_the_compact_form(self):
        p = path(named("Item", "Ender Eye"), Rel("CREATES", IN), "Recipe", Rel("REQUIRES"), named("Item", "Blaze Powder"))
        assert p.compact() == "(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})"

    def test_the_same_path_as_cypher(self):
        p = path(named("Item", "Ender Eye"), Rel("CREATES", IN), "Recipe", Rel("REQUIRES"), named("Item", "Blaze Powder"))
        assert p.cypher() == '(:Item {name: "Ender Eye"})<-[:CREATES]-(:Recipe)-[:REQUIRES]->(:Item {name: "Blaze Powder"})'

    def test_a_node_with_no_label_or_name_is_empty_parentheses(self):
        assert path(Node(), Rel("X"), Node()).compact() == "()-[X]->()" and path(Node(), Rel("X"), Node()).cypher() == "()-[:X]->()"

    def test_a_variable_length_relationship(self):
        p = path(Node("Structure"), Rel("GENERATES_IN"), Node(), Rel("INCLUDES", length="*0.."), Node("Biome"))
        assert p.compact() == "(Structure)-[GENERATES_IN]->()-[INCLUDES*0..]->(Biome)"
        assert p.cypher() == "(:Structure)-[:GENERATES_IN]->()-[:INCLUDES*0..]->(:Biome)"

    def test_a_name_with_quotes_and_a_backslash_is_escaped_in_cypher_only(self):
        p = path(named("Section", 'The "Beginner\'s" way \\ back', key="title"), Rel("X"), "Chunk")
        assert p.cypher() == '(:Section {title: "The \\"Beginner\'s\\" way \\\\ back"})-[:X]->(:Chunk)'
        assert p.compact() == "(Section {The \"Beginner's\" way \\ back})-[X]->(Chunk)"

    def test_the_wiki_names_nodes_by_title_everything_else_by_name(self):
        assert path(named("Section", "Walls", key="title"), Rel("X"), "Y").cypher() == '(:Section {title: "Walls"})-[:X]->(:Y)'
        assert path(named("Item", "Torch"), Rel("X"), "Y").cypher() == '(:Item {name: "Torch"})-[:X]->(:Y)'

    def test_a_string_in_path_is_a_label_with_no_name(self):
        assert path("Recipe", Rel("CREATES"), "Item").compact() == "(Recipe)-[CREATES]->(Item)"

    def test_the_names_are_not_changed_for_a_non_english_character(self):
        assert path(named("Item", "Crème"), Rel("X"), "Y").cypher() == '(:Item {name: "Crème"})-[:X]->(:Y)'


class TestRecipes:
    RECIPE = {"station": "Crafting Table", "requires": [{"item": "Blaze Powder", "count": 1}, {"item": "Ender Pearl", "count": 1}]}

    def test_what_a_recipe_needs_and_where_it_is_made(self):
        assert compact(g.recipe_patterns("Ender Eye", [self.RECIPE])) == [
            "(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})",
            "(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Ender Pearl})",
            "(Item {Ender Eye})<-[CREATES]-(Recipe)-[USES_STATION]->(Item {Crafting Table})"]

    def test_a_category_is_an_item_class(self):
        r = {"requires": [{"anyOf": "Planks", "options": 12, "count": 2}]}
        assert compact(g.recipe_patterns("Stick", [r])) == ["(Item {Stick})<-[CREATES]-(Recipe)-[REQUIRES]->(ItemClass {Planks})"]

    def test_an_item_that_stands_for_a_single_member_category_goes_through_the_category(self):
        r = {"requires": [{"item": "Iron Ingot", "count": 3, "_via": "Iron Tool Materials"}]}
        assert compact(g.recipe_patterns("Iron Pickaxe", [r])) == [
            "(Item {Iron Pickaxe})<-[CREATES]-(Recipe)-[REQUIRES]->(ItemClass {Iron Tool Materials})-[INCLUDES]->(Item {Iron Ingot})"]

    def test_only_the_first_recipe_is_shown(self):
        other = {"requires": [{"item": "Something Else"}]}
        assert "Something Else" not in " ".join(compact(g.recipe_patterns("X", [self.RECIPE, other])))

    def test_at_most_four_lines(self):
        r = {"station": "Crafting Table", "requires": [{"item": f"I{n}"} for n in range(9)]}
        assert len(g.recipe_patterns("X", [r])) == 4

    @pytest.mark.parametrize("recipes", [[], [{}], [{"requires": []}]])
    def test_nothing_to_show_is_no_pattern(self, recipes):
        assert g.recipe_patterns("X", recipes) == []

    def test_the_route_shows_the_last_step_and_where_raw_materials_come_from(self):
        out = {"item": "Iron Pickaxe", "steps": [{"uses": [{"item": "Raw Iron"}]}, {"uses": [{"item": "Stick"}, {"item": "Iron Ingot", "_via": "Iron Tool Materials"}]}],
               "gather": [{"item": "Raw Iron", "getFrom": [{"how": "mine", "from": "Iron Ore"}]}, {"anyOf": "Planks", "total": 2}]}
        assert compact(g.route_patterns(out)) == [
            "(Item {Iron Pickaxe})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Stick})",
            "(Item {Iron Pickaxe})<-[CREATES]-(Recipe)-[REQUIRES]->(ItemClass {Iron Tool Materials})-[INCLUDES]->(Item {Iron Ingot})",
            "(Item {Raw Iron})<-[CREATES]-(Mine)-[REQUIRES]->(Block {Iron Ore})"]

    def test_a_route_with_no_steps_has_only_its_sources(self):
        out = {"item": "Diamond", "steps": [], "gather": [{"item": "Diamond", "getFrom": [{"how": "mine", "from": "Diamond Ore"}]}]}
        assert compact(g.route_patterns(out)) == ["(Item {Diamond})<-[CREATES]-(Mine)-[REQUIRES]->(Block {Diamond Ore})"]


class TestGetting:
    @pytest.mark.parametrize("way,expected", [
        ({"how": "mine", "from": "Emerald Ore"}, "(Item {X})<-[CREATES]-(Mine)-[REQUIRES]->(Block {Emerald Ore})"),
        ({"how": "kill", "from": "Creeper"}, "(Item {X})<-[CREATES]-(Kill)-[REQUIRES]->(Mob {Creeper})"),
        ({"how": "shear", "from": "Sheep"}, "(Item {X})<-[CREATES]-(Shear)-[REQUIRES]->(Mob {Sheep})"),
        ({"how": "trade", "with": "Farmer", "level": 1}, "(Item {X})<-[CREATES]-(Trade)<-[INCLUDES]-(TradeSet)<-[OFFERS]-(Profession {Farmer})"),
        ({"how": "trade", "with": "Wandering Trader", "pool": "common"}, "(Item {X})<-[CREATES]-(Trade)<-[INCLUDES]-(TradeSet)<-[OFFERS]-(Mob {Wandering Trader})")])
    def test_each_way_of_getting_an_item(self, way, expected):
        assert g.source_of_item("X", way).compact() == expected

    @pytest.mark.parametrize("way", [{}, {"how": "mine"}, {"how": "mine", "from": ["a", "b"]}, {"how": "fly", "from": "x"}, {"how": "trade"}])
    def test_a_way_that_cannot_be_drawn_is_none(self, way):
        assert g.source_of_item("X", way) is None

    def test_the_ways_of_getting_an_item(self):
        out = {"item": "Emerald", "ways": [{"how": "mine", "from": "Emerald Ore"}, {"how": "trade", "with": "Farmer"}]}
        assert len(g.ways_patterns(out)) == 2

    def test_a_category_has_no_ways_pattern(self):
        assert g.ways_patterns({"item": "Planks", "category": True, "ways": [{"how": "mine", "from": "Oak Log"}]}) == []

    def test_at_most_four_ways(self):
        assert len(g.ways_patterns({"item": "X", "ways": [{"how": "mine", "from": f"B{n}"} for n in range(9)]})) == 4


class TestUsesAndMembers:
    def test_what_an_item_is_used_to_make(self):
        out = {"item": "Diamond", "canMake": [{"makes": "Diamond Block"}, {"makes": "Diamond Axe", "viaCategory": ["Diamond Tool Materials"]}]}
        assert compact(g.uses_patterns(out)) == [
            "(Item {Diamond})<-[REQUIRES]-(Recipe)-[CREATES]->(Item {Diamond Block})",
            "(Item {Diamond})<-[INCLUDES]-(ItemClass {Diamond Tool Materials})<-[REQUIRES]-(Recipe)-[CREATES]->(Item {Diamond Axe})"]

    @pytest.mark.parametrize("kind,cls,member", [("category", "ItemClass", "Item"), ("mob class", "MobClass", "Mob"), ("biome class", "BiomeClass", "Biome")])
    def test_what_a_class_includes_at_any_depth(self, kind, cls, member):
        r = {"name": "C", "kind": kind, "members": ["A", "B", "C2"]}
        assert compact(g.members_patterns(r)) == [f"({cls} {{C}})-[INCLUDES*1..]->({member} {{A}})", f"({cls} {{C}})-[INCLUDES*1..]->({member} {{B}})"]

    def test_an_unknown_kind_has_no_members_pattern(self):
        assert g.members_patterns({"name": "x", "kind": "item", "members": ["a"]}) == []


class TestWhere:
    def test_an_ore_from_the_item_to_the_biomes(self):
        r = {"kind": "item", "name": "Diamond", "found": [{"how": "mine", "from": ["Deepslate Diamond Ore", "Diamond Ore"], "where": [{"dimension": "Overworld"}]}]}
        assert compact(g.where_patterns(r)) == [
            "(Item {Diamond})<-[CREATES]-(Mine)-[REQUIRES]->(Block {Deepslate Diamond Ore})",
            "(Block {Deepslate Diamond Ore})<-[CREATES]-(Feature)<-[PLACES]-(PlacedFeature)<-[GENERATES]-(Biome)"]

    def test_a_mob_drop_with_biomes_or_a_structure(self):
        r = {"kind": "item", "name": "Gunpowder", "found": [{"how": "kill", "from": "Creeper", "spawnsIn": [{}]}, {"how": "kill", "from": "Wither Skeleton", "structures": ["Fortress"]}]}
        assert compact(g.where_patterns(r)) == [
            "(Item {Gunpowder})<-[CREATES]-(Kill)-[REQUIRES]->(Mob {Creeper})", "(Mob {Creeper})<-[SPAWNS]-(Biome)",
            "(Item {Gunpowder})<-[CREATES]-(Kill)-[REQUIRES]->(Mob {Wither Skeleton})", "(Mob {Wither Skeleton})<-[SPAWNS]-(Structure)"][:4]

    def test_an_item_a_villager_sells(self):
        r = {"kind": "item", "name": "Bread", "canBeTradedFor": [{"how": "trade", "with": "Farmer"}]}
        assert compact(g.where_patterns(r)) == ["(Item {Bread})<-[CREATES]-(Trade)<-[INCLUDES]-(TradeSet)<-[OFFERS]-(Profession {Farmer})"]

    def test_a_block_a_mob_a_biome_and_a_structure(self):
        assert compact(g.where_patterns({"kind": "block", "name": "Coal Ore", "where": [{}]})) == [
            "(Block {Coal Ore})<-[CREATES]-(Feature)<-[PLACES]-(PlacedFeature)<-[GENERATES]-(Biome)"]
        assert compact(g.where_patterns({"kind": "mob", "name": "Creeper", "spawnsIn": [{}]})) == ["(Mob {Creeper})<-[SPAWNS]-(Biome)"]
        biome = compact(g.where_patterns({"kind": "biome", "name": "Desert", "mobs": {}, "structures": ["Village"], "ores": [{}]}))
        assert biome[0] == "(Biome {Desert})-[SPAWNS]->(Mob)" and "(Structure)-[GENERATES_IN]->()-[INCLUDES*0..]->(Biome {Desert})" in biome
        structure = compact(g.where_patterns({"kind": "structure", "name": "Mansion", "generatesIn": [{}], "mapToFindIt": [{}]}))
        assert structure == ["(Structure {Mansion})-[GENERATES_IN]->()-[INCLUDES*0..]->(Biome)",
                             "(Structure {Mansion})<-[LOCATES]-(Trade)<-[INCLUDES]-(TradeSet)<-[OFFERS]-(Profession)"]

    def test_a_mob_that_spawns_nowhere_has_no_pattern(self):
        assert g.where_patterns({"kind": "mob", "name": "Villager", "note": "..."}) == []

    def test_at_most_four_lines(self):
        r = {"kind": "item", "name": "X", "found": [{"how": "mine", "from": "A", "where": [{}]}, {"how": "mine", "from": "B", "where": [{}]}],
             "canBeTradedFor": [{"how": "trade", "with": "Farmer"}]}
        assert len(g.where_patterns(r)) == 4


class TestDescribe:
    def test_an_item_shows_what_its_counts_walked(self):
        r = {"kind": "item", "name": "Emerald", "recipes": 5, "waysFromWorld": 4, "usedInRecipes": 19, "tradesGivingIt": 87}
        assert compact(g.describe_patterns(r)) == ["(Item {Emerald})<-[CREATES]-(Recipe)", "(Item {Emerald})<-[CREATES]-(Action)",
                                                   "(Item {Emerald})<-[REQUIRES]-(Recipe)", "(Item {Emerald})<-[CREATES]-(Trade)"]

    def test_an_item_with_no_recipes_does_not_show_the_recipe_line(self):
        assert compact(g.describe_patterns({"kind": "item", "name": "Gunpowder", "recipes": 0, "usedInRecipes": 3})) == ["(Item {Gunpowder})<-[REQUIRES]-(Recipe)"]

    def test_a_mob_its_drops_and_where_it_spawns(self):
        r = {"kind": "mob", "name": "Creeper", "drops": [{"item": "Gunpowder"}], "spawnsInBiomes": 53}
        assert compact(g.describe_patterns(r)) == ["(Mob {Creeper})<-[REQUIRES]-(Kill)-[CREATES]->(Item {Gunpowder})", "(Mob {Creeper})<-[SPAWNS]-(Biome)"]

    def test_a_block_and_its_drops(self):
        assert compact(g.describe_patterns({"kind": "block", "name": "Diamond Ore", "drops": [{"item": "Diamond"}]})) == [
            "(Block {Diamond Ore})<-[REQUIRES]-(Mine)-[CREATES]->(Item {Diamond})"]

    @pytest.mark.parametrize("result,line", [
        ({"kind": "profession", "name": "Farmer"}, "(Profession {Farmer})-[OFFERS]->(TradeSet)-[INCLUDES]->(Trade)"),
        ({"kind": "mob", "name": "Villager", "professions": ["Farmer"]}, "(Mob {Villager})-[HAS_PROFESSION]->(Profession)"),
        ({"kind": "mob", "name": "Wandering Trader", "offers": [{}]}, "(Mob {Wandering Trader})-[OFFERS]->(TradeSet)"),
        ({"kind": "category", "name": "Planks", "examples": ["Oak Planks"]}, "(ItemClass {Planks})-[INCLUDES*1..]->(Item {Oak Planks})")])
    def test_other_kinds(self, result, line):
        assert line in compact(g.describe_patterns(result))

    def test_a_structure_and_who_sells_a_map_to_it(self):
        got = compact(g.describe_patterns({"kind": "structure", "name": "Mansion", "mapSoldBy": [{}]}))
        assert got[0].startswith("(Structure {Mansion})-[GENERATES_IN]") and got[1].endswith("(Profession)")


class TestPlans:
    OUTLINE = {"process": "Get to the Nether", "stages": [{"stage": "Choose a way", "steps": ["Choose how to get the obsidian"]}],
               "alternatives": [{"after": "Choose how to get the obsidian", "usually": {"go to": "Make an iron pickaxe", "when": "x"}, "or": []}],
               "usualRoute": ["1. Choose how to get the obsidian (by hand)", "2. Make an iron pickaxe (Craft Iron Pickaxe)", "3. Mine obsidian (Mine Obsidian x10; Craft X)"]}

    def test_an_outline_shows_the_process_a_branch_and_a_game_action(self):
        assert compact(g.plan_patterns(self.OUTLINE)) == [
            "(Process {Get to the Nether})-[HAS_ACTIVITY]->(Activity {Choose a way})-[HAS_TASK]->(Task {Choose how to get the obsidian})",
            "(Task {Choose how to get the obsidian})-[NEXT]->(Task {Make an iron pickaxe})",
            "(Task {Make an iron pickaxe})-[HAS_ACTION]->(Action {Craft Iron Pickaxe})"]

    def test_a_plan_that_builds_on_another(self):
        got = compact(g.plan_patterns({**self.OUTLINE, "process": "Defeat the ender dragon", "buildsOn": ["Get to the Nether"]}))
        assert got[-1] == "(Process {Defeat the ender dragon})-[BUILDS_ON]->(Process {Get to the Nether})"

    def test_the_quantity_is_not_part_of_an_actions_name(self):
        out = {"process": "P", "stages": [], "usualRoute": ["1. Mine (Mine Obsidian x10)"]}
        assert compact(g.plan_patterns(out)) == ["(Task {Mine})-[HAS_ACTION]->(Action {Mine Obsidian})"]

    def test_one_step_shows_its_stage_its_action_and_where_it_leads(self):
        step = {"process": "P", "stage": "Get obsidian", "step": "Mine 10 obsidian", "gameActions": ["Mine Obsidian x10"], "next": [{"go to": "Get flint", "when": "x"}]}
        assert compact(g.plan_patterns(step)) == ["(Activity {Get obsidian})-[HAS_TASK]->(Task {Mine 10 obsidian})",
                                                  "(Task {Mine 10 obsidian})-[HAS_ACTION]->(Action {Mine Obsidian})",
                                                  "(Task {Mine 10 obsidian})-[NEXT]->(Task {Get flint})"]

    def test_the_last_step_has_no_next_and_a_manual_one_no_action(self):
        step = {"process": "P", "stage": "S", "step": "Build", "byHand": "x", "next": "This is the last step."}
        assert compact(g.plan_patterns(step)) == ["(Activity {S})-[HAS_TASK]->(Task {Build})"]

    def test_the_list_of_plans_has_no_pattern(self):
        assert g.plan_patterns({"processes": []}) == []


class TestWiki:
    def test_a_passage_is_a_chunk_in_a_section_named_by_its_title(self):
        p = g.section_pattern("Tutorial:Shelters > Shelters for the first night > Hole in the wall")
        assert p.compact() == "(Chunk)-[PART_OF]->(Section {Hole in the wall})" and p.cypher() == '(:Chunk)-[:PART_OF]->(:Section {title: "Hole in the wall"})'

    def test_a_page_intro_is_the_page_title(self):
        assert g.section_pattern("Tutorial:Mining").cypher() == '(:Chunk)-[:PART_OF]->(:Section {title: "Tutorial:Mining"})'


def test_a_result_with_missing_parts_never_raises():
    for build in (g.recipe_patterns, ):
        assert build("X", [{"requires": [{}]}]) is not None
    for build in (g.route_patterns, g.ways_patterns, g.uses_patterns, g.where_patterns, g.describe_patterns, g.members_patterns, g.plan_patterns):
        assert build({}) == []
