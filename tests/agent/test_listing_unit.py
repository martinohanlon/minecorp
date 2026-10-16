"""The `list` tool without a database: the shape of what it returns, its paging, the kind words, and the source and patterns it gives.

A fake graph answers each query with rows set up by the test; the real graph is tested in test_listing_graph.py."""
from __future__ import annotations

import pytest

from minecorp_agent import graphs, listing, sources, tools
from minecorp_agent.listing import PAGE, tool_list


class FakeGraph:
    """Returns `rows` for any query and remembers what it was asked."""

    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def read(self, query, **params):
        self.asked.append((query, params))
        return self.rows


def names(*items):
    return [{"name": n} for n in items]


def classes(*items):
    return [{"name": n, "size": s} for n, s in items]


# --------------------------------------------------------------------------- the kind words

class TestKindWords:
    @pytest.mark.parametrize("text,kind", [
        ("biome", "biome"), ("biomes", "biome"), (" Biomes ", "biome"), ("mob", "mob"), ("mobs", "mob"), ("creatures", "mob"),
        ("structures", "structure"), ("professions", "profession"), ("villager professions", "profession"), ("dimensions", "dimension"),
        ("items", "item"), ("blocks", "block"), ("categories", "category"), ("item categories", "category"), ("mob classes", "mob class"),
        ("biome classes", "biome class"), ("Mob   Classes", "mob class"), ("classes", "biome class")])
    def test_a_word_for_a_kind(self, text, kind):
        assert listing.kind_of(text) == kind

    @pytest.mark.parametrize("text", ["", "creeper", "iron sword", "weapons", "biomess", "the biomes"])
    def test_anything_else_is_not_a_kind(self, text):
        assert listing.kind_of(text) is None

    def test_every_kind_the_tool_lists_has_a_plural_and_a_word(self):
        for kind in listing.LIST_KINDS:
            assert kind in listing.PLURAL and listing.kind_of(kind) == kind and listing.kind_of(listing.PLURAL[kind]) == kind

    def test_every_kind_find_knows_can_be_listed(self):
        from minecorp_agent.names import KIND_NAMES
        assert set(KIND_NAMES) <= set(listing.LIST_KINDS)

    def test_the_words_to_look_for_lose_a_plural_s(self):
        assert listing._words("Swords") == ["sword"] and listing._words("iron  boots!") == ["iron", "boot"] and listing._words("") == []
        assert listing._words("bus") == ["bus"] and listing._words("ender") == ["ender"]


# --------------------------------------------------------------------------- the answers

class TestFlat:
    def test_a_small_kind_comes_back_whole_and_sorted_with_its_total(self):
        graph = FakeGraph(names("Armorer", "Butcher", "Farmer"))
        assert tool_list(graph, {"kind": "profession"}) == {"kind": "profession", "total": 3, "names": ["Armorer", "Butcher", "Farmer"]}

    def test_a_plural_kind_is_understood(self):
        assert tool_list(FakeGraph(names("Nether")), {"kind": "Dimensions"})["kind"] == "dimension"

    def test_a_long_kind_comes_in_pages_and_says_how_to_get_the_next(self):
        graph = FakeGraph(names(*[f"Item {n:04d}" for n in range(250)]))
        first = tool_list(graph, {"kind": "item"})
        assert first["total"] == 250 and len(first["names"]) == PAGE and first["names"][0] == "Item 0000" and "offset=100" in first["next"]
        second = tool_list(graph, {"kind": "item", "offset": 100})
        assert second["names"][0] == "Item 0100" and second["offset"] == 100 and "offset=200" in second["next"]
        last = tool_list(graph, {"kind": "item", "offset": 200})
        assert len(last["names"]) == 50 and "next" not in last

    def test_the_pages_together_are_everything_once(self):
        graph = FakeGraph(names(*[f"I{n:03d}" for n in range(230)]))
        seen = []
        for offset in (0, 100, 200):
            seen += tool_list(graph, {"kind": "block", "offset": offset})["names"]
        assert seen == [f"I{n:03d}" for n in range(230)]

    def test_a_long_list_tells_the_model_to_summarise_it_for_chat(self):
        out = tool_list(FakeGraph(names(*[f"S{n:02d}" for n in range(34)])), {"kind": "structure"})
        assert out["total"] == 34 and "Do not print all 34" in out["tip"] and "give a few examples" in out["tip"] and "by group" not in out["tip"]

    def test_a_short_list_has_no_such_tip(self):
        assert "tip" not in tool_list(FakeGraph(names(*[f"S{n:02d}" for n in range(15)])), {"kind": "structure"})
        assert "tip" in tool_list(FakeGraph(names(*[f"S{n:02d}" for n in range(16)])), {"kind": "structure"})

    def test_a_grouped_list_says_to_give_the_count_of_each_group(self):
        rows = [{"name": f"B{n}", "dimension": "Overworld"} for n in range(20)]
        assert "by group" in tool_list(FakeGraph(rows), {"kind": "biome"})["tip"]
        assert "tip" not in tool_list(FakeGraph(rows[:5]), {"kind": "biome"})

    def test_a_page_of_a_huge_kind_still_gets_the_tip(self):
        assert "Do not print all 250" in tool_list(FakeGraph(names(*[f"I{n:03d}" for n in range(250)])), {"kind": "item"})["tip"]

    def test_an_offset_past_the_end_says_so(self):
        out = tool_list(FakeGraph(names("A", "B")), {"kind": "item", "offset": 50})
        assert out["names"] == [] and out["total"] == 2 and "past the end" in out["note"] and "next" not in out

    @pytest.mark.parametrize("offset", ["many", [1], "1.5"])
    def test_a_bad_offset_is_an_error_for_the_model(self, offset):
        assert tool_list(FakeGraph([]), {"kind": "item", "offset": offset})["type"] == "bad_argument"

    def test_a_negative_offset_is_the_start(self):
        assert tool_list(FakeGraph(names("A")), {"kind": "item", "offset": -5})["names"] == ["A"]

    def test_the_words_to_look_for_go_to_the_query_not_into_it(self):
        graph = FakeGraph(names("Iron Sword"))
        out = tool_list(graph, {"kind": "item", "contains": "Iron'; DROP DATABASE x; // swords"})
        query, params = graph.asked[0]
        assert "DROP" not in query and params["words"] == ["iron", "drop", "database", "x", "sword"] and out["matching"].startswith("Iron")

    def test_nothing_found_says_so_in_words(self):
        out = tool_list(FakeGraph([]), {"kind": "mob", "contains": "zzz"})
        assert out["total"] == 0 and out["names"] == [] and out["note"] == "No mobs match 'zzz'."
        assert tool_list(FakeGraph([]), {"kind": "mob"})["note"] == "No mobs found."

    @pytest.mark.parametrize("args", [{}, {"kind": ""}, {"kind": "weapons"}, {"kind": None}, {"contains": "sword"}])
    def test_a_missing_or_unknown_kind_lists_the_kinds(self, args):
        out = tool_list(FakeGraph([]), args)
        assert out["type"] == "bad_argument" and all(k in out["error"] for k in ("biome", "mob class", "dimension"))

    def test_items_leave_out_categories_and_categories_leave_out_the_generated_ones(self):
        item, category = FakeGraph([]), FakeGraph([])
        tool_list(item, {"kind": "item"})
        tool_list(category, {"kind": "category"})
        assert "NOT n:ItemClass" in item.asked[0][0]
        assert "NOT n.name STARTS WITH 'Any of'" in category.asked[0][0]


class TestClasses:
    def test_a_class_comes_with_the_number_of_things_in_it_and_a_warning_that_it_is_not_a_tree(self):
        out = tool_list(FakeGraph(classes(("Planks", 13), ("Wool", 16))), {"kind": "category"})
        assert out["names"] == ["Planks (13)", "Wool (16)"] and "not a tree" in out["note"] and "members" in out["note"]

    def test_mob_classes_the_same(self):
        out = tool_list(FakeGraph(classes(("Undead", 17))), {"kind": "mob class"})
        assert out["names"] == ["Undead (17)"] and "not a tree" in out["note"]

    def test_biome_classes_are_grouped_by_their_first_word_and_a_small_group_is_folded_away(self):
        rows = classes(*[(f"Has Structure {n}", n) for n in range(5)], ("Is Forest", 7), ("Is Ocean", 9), ("Is Taiga", 3),
                       ("Allows Surface Slime Spawns", 4), ("Polar Bears Spawn", 2))
        out = tool_list(FakeGraph(rows), {"kind": "biome class"})
        assert [(g["group"], g["count"]) for g in out["groups"]] == [("Has ...", 5), ("Is ...", 3), ("Other", 2)]
        assert out["groups"][1]["names"] == ["Is Forest (7)", "Is Ocean (9)", "Is Taiga (3)"]
        assert out["groups"][2]["names"] == ["Allows Surface Slime Spawns (4)", "Polar Bears Spawn (2)"] and out["total"] == 10
        assert "not a tree" in out["note"]

    def test_biome_classes_with_no_small_groups_have_no_other(self):
        out = tool_list(FakeGraph(classes(*[(f"Is {n}", 1) for n in "abc"])), {"kind": "biome class"})
        assert [g["group"] for g in out["groups"]] == ["Is ..."]


class TestBiomes:
    ROWS = [{"name": "Badlands", "dimension": "Overworld"}, {"name": "Basalt Deltas", "dimension": "The Nether"}, {"name": "Desert", "dimension": "Overworld"},
            {"name": "Small End Islands", "dimension": "The End"}, {"name": "The Void", "dimension": None}, {"name": "Warped Forest", "dimension": "The Nether"}]

    def test_biomes_are_grouped_by_dimension_in_the_order_a_player_travels(self):
        out = tool_list(FakeGraph(self.ROWS), {"kind": "biome"})
        assert [(g["group"], g["count"]) for g in out["groups"]] == [("Overworld", 2), ("The Nether", 2), ("The End", 1), ("Other", 1)]
        assert out["groups"][0]["names"] == ["Badlands", "Desert"] and out["groups"][3]["names"] == ["The Void"]
        assert out["total"] == 6 and out["note"] == "Grouped by dimension."

    def test_a_dimension_nobody_expected_is_still_shown_before_other(self):
        rows = [{"name": "A", "dimension": "Overworld"}, {"name": "B", "dimension": "The Aether"}, {"name": "C", "dimension": None}]
        assert [g["group"] for g in tool_list(FakeGraph(rows), {"kind": "biome"})["groups"]] == ["Overworld", "The Aether", "Other"]

    def test_a_dimension_with_no_biomes_is_not_shown(self):
        assert [g["group"] for g in tool_list(FakeGraph(self.ROWS[:1]), {"kind": "biome"})["groups"]] == ["Overworld"]

    def test_the_filter_applies_to_biomes_too(self):
        graph = FakeGraph(self.ROWS[:1])
        tool_list(graph, {"kind": "biome", "contains": "forest"})
        assert graph.asked[0][1]["words"] == ["forest"]

    def test_no_biomes_matching_says_so(self):
        out = tool_list(FakeGraph([]), {"kind": "biome", "contains": "zzz"})
        assert out["total"] == 0 and out["groups"] == [] and out["note"] == "No biomes match 'zzz'."


# --------------------------------------------------------------------------- find sends a kind word to list

class TestFindHint:
    def search(self, monkeypatch, rows):
        monkeypatch.setattr(tools, "search_names", lambda graph, text, kinds=None, limit=10: rows)

    @pytest.mark.parametrize("query,kind", [("biomes", "biome"), ("Biome", "biome"), ("mobs", "mob"), ("structures", "structure"), ("items", "item")])
    def test_a_kind_word_gets_a_hint_instead_of_near_misses(self, monkeypatch, query, kind):
        self.search(monkeypatch, [{"id": "minecraft:bone", "name": "Bone", "kind": "item", "rank": 5, "options": None}])
        out = tools.tool_find(None, {"query": query})
        assert out["matches"] == [] and f"list with kind '{kind}'" in out["hint"] and "kind of thing, not a name" in out["hint"]

    def test_a_real_name_that_is_also_a_kind_word_is_still_found(self, monkeypatch):
        self.search(monkeypatch, [{"id": "minecraft:x", "name": "Item", "kind": "block", "rank": 1, "options": None}])
        out = tools.tool_find(None, {"query": "item"})
        assert out["matches"] and "hint" not in out

    def test_other_searches_are_unchanged(self, monkeypatch):
        self.search(monkeypatch, [{"id": "minecraft:creeper", "name": "Creeper", "kind": "mob", "rank": 1, "options": None}])
        assert tools.tool_find(None, {"query": "creeper"})["matches"][0]["name"] == "Creeper"
        self.search(monkeypatch, [])
        assert tools.tool_find(None, {"query": "flurble"}) == {"matches": [], "note": "Nothing matched."}

    def test_asking_for_one_kind_is_left_alone(self, monkeypatch):
        self.search(monkeypatch, [])
        assert "hint" not in tools.tool_find(None, {"query": "biomes", "kind": "item"})


# --------------------------------------------------------------------------- the tool, its source and its patterns

class TestTheTool:
    def test_it_is_registered_with_every_kind_in_its_schema(self):
        tool = tools.TOOLS_BY_NAME["list"]
        spec = tool.spec()["function"]
        assert spec["parameters"]["properties"]["kind"]["enum"] == list(listing.LIST_KINDS) and spec["parameters"]["required"] == ["kind"]
        for word in ("biomes", "members", "contains", "describe"):
            assert word in spec["description"]

    def test_find_points_at_it(self):
        assert "use list" in tools.TOOLS_BY_NAME["find"].description


class TestSourceAndPatterns:
    def source(self, **out):
        return sources.sources_for("list", {}, out)

    def test_a_list_is_one_game_data_source(self):
        (s,) = self.source(kind="mob", total=90, names=["Creeper"])
        assert (s["kind"], s["label"], s["graph"]) == ("game data", "All mobs", ["(Biome)-[SPAWNS]->(Mob)"])

    def test_a_filtered_list_says_what_it_was_filtered_by(self):
        (s,) = self.source(kind="item", total=7, matching="sword", names=["Iron Sword"])
        assert s["label"] == "Items matching 'sword'"

    def test_the_kind_is_named_in_the_plural_a_player_would_say(self):
        assert self.source(kind="profession", total=1, names=["Farmer"])[0]["label"] == "All villager professions"
        assert self.source(kind="category", total=1, names=["Planks (1)"])[0]["label"] == "All item categories"
        assert self.source(kind="biome class", total=1, groups=[])[0]["label"] == "All biome classes"

    def test_biomes_show_the_dimension_each_group_came_from(self):
        (s,) = self.source(kind="biome", total=3, groups=[{"group": "Overworld", "count": 1, "names": ["A"]}, {"group": "The Nether", "count": 1, "names": ["B"]},
                                                          {"group": "Other", "count": 1, "names": ["The Void"]}])
        assert s["graph"] == ["(Dimension {Overworld})-[HAS_BIOME]->(BiomeClass)-[INCLUDES*0..]->(Biome)",
                              "(Dimension {The Nether})-[HAS_BIOME]->(BiomeClass)-[INCLUDES*0..]->(Biome)"]

    @pytest.mark.parametrize("kind,line", [
        ("biome class", "(BiomeClass)-[INCLUDES*1..]->(Biome)"), ("mob class", "(MobClass)-[INCLUDES*1..]->(Mob)"), ("category", "(ItemClass)-[INCLUDES*1..]->(Item)"),
        ("structure", "(Structure)-[GENERATES_IN]->()"), ("profession", "(Mob {Villager})-[HAS_PROFESSION]->(Profession)"),
        ("dimension", "(Dimension)-[HAS_BIOME]->(BiomeClass)"), ("item", "(Recipe)-[CREATES]->(Item)"), ("block", "(Mine)-[REQUIRES]->(Block)")])
    def test_every_other_kind_shows_the_relationship_that_makes_it_what_it_is(self, kind, line):
        assert self.source(kind=kind, total=1, names=["X"])[0]["graph"] == [line]

    def test_nothing_found_or_an_error_is_not_a_source(self):
        assert self.source(kind="mob", total=0, names=[]) == []
        assert sources.sources_for("list", {}, {"error": "x", "type": "bad_argument"}) == []

    def test_a_kind_it_does_not_know_is_not_a_source(self):
        assert self.source(kind="weapon", total=3) == []

    def test_every_kind_has_a_pattern(self):
        for kind in listing.LIST_KINDS:
            out = {"kind": kind, "total": 1, "groups": [{"group": "Overworld"}]}
            assert graphs.list_patterns(out), kind
