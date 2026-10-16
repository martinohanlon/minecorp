"""The `list` tool against the real graph, rebuilt from source in the throwaway database. No model is involved.

The numbers are checked against the graph itself (a count of the nodes), not copied from the game, so they stay right when the game's data changes.
Run with:  pytest -m integration"""
from __future__ import annotations

import pytest

from minecorp_agent import listing, tools
from minecorp_agent.budget import fit

pytestmark = [pytest.mark.integration, pytest.mark.jar]


def call(graph, tool, **args):
    return tools.TOOLS_BY_NAME[tool].fn(graph, args)


def count(graph, cypher):
    return graph.read(cypher)[0]["n"]


class TestEveryKind:
    @pytest.mark.parametrize("kind,cypher", [
        ("mob", "MATCH (n:Mob) RETURN count(DISTINCT n.name) AS n"), ("structure", "MATCH (n:Structure) RETURN count(DISTINCT n.name) AS n"),
        ("profession", "MATCH (n:Profession) RETURN count(n) AS n"), ("dimension", "MATCH (n:Dimension) RETURN count(n) AS n"),
        ("biome", "MATCH (n:Biome) RETURN count(n) AS n"), ("mob class", "MATCH (n:MobClass) RETURN count(n) AS n"),
        ("biome class", "MATCH (n:BiomeClass) RETURN count(n) AS n"),
        ("item", "MATCH (n:Item) WHERE NOT n:ItemClass RETURN count(DISTINCT n.name) AS n"), ("block", "MATCH (n:Block) RETURN count(DISTINCT n.name) AS n"),
        ("category", "MATCH (n:ItemClass) WHERE NOT n.name STARTS WITH 'Any of' RETURN count(DISTINCT n.name) AS n")])
    def test_the_total_is_the_number_of_nodes_of_that_kind(self, agent_graph, kind, cypher):
        out = call(agent_graph, "list", kind=kind)
        assert out["kind"] == kind and out["total"] == count(agent_graph, cypher) > 0

    def test_every_kind_can_be_listed_and_the_answer_fits_its_budget(self, agent_graph):
        budget = tools.TOOLS_BY_NAME["list"].budget
        for kind in listing.LIST_KINDS:
            out = call(agent_graph, "list", kind=kind)
            assert "error" not in out and fit(out, budget) == out, kind          # nothing had to be cut

    def test_names_are_sorted_and_have_no_repeats(self, agent_graph):
        for kind in ("mob", "structure", "profession", "item", "block"):
            names = call(agent_graph, "list", kind=kind)["names"]
            assert names == sorted(names) and len(names) == len(set(names)), kind


class TestBiomes:
    def test_they_are_grouped_by_dimension_and_add_up(self, agent_graph):
        out = call(agent_graph, "list", kind="biome")
        by_group = {g["group"]: g["names"] for g in out["groups"]}
        assert list(by_group)[:3] == ["Overworld", "The Nether", "The End"]
        assert sum(len(v) for v in by_group.values()) == out["total"] == count(agent_graph, "MATCH (b:Biome) RETURN count(b) AS n")
        assert "Plains" in by_group["Overworld"] and "Basalt Deltas" in by_group["The Nether"] and "Small End Islands" in by_group["The End"]

    def test_a_biome_is_in_one_dimension_only(self, agent_graph):
        groups = call(agent_graph, "list", kind="biome")["groups"]
        everything = [n for g in groups for n in g["names"]]
        assert len(everything) == len(set(everything))

    def test_the_void_belongs_to_no_dimension(self, agent_graph):
        other = next(g for g in call(agent_graph, "list", kind="biome")["groups"] if g["group"] == "Other")
        assert other["names"] == ["The Void"]

    def test_a_filter_keeps_the_groups(self, agent_graph):
        out = call(agent_graph, "list", kind="biomes", contains="forest")
        assert {g["group"] for g in out["groups"]} == {"Overworld", "The Nether"}
        assert all("forest" in n.lower() for g in out["groups"] for n in g["names"])


class TestClasses:
    def test_a_size_is_what_members_lists(self, agent_graph):
        listed = {n.rsplit(" (", 1)[0]: int(n.rsplit("(", 1)[1].rstrip(")")) for n in call(agent_graph, "list", kind="mob class")["names"]}
        for name in ("Undead", "Aquatic", "Arthropod"):
            members = call(agent_graph, "members", name=name)["results"][0]
            assert listed[name] == members["total"] > 0, name

    def test_a_biome_classs_size_is_what_members_lists(self, agent_graph):
        groups = call(agent_graph, "list", kind="biome class")["groups"]
        sizes = {n.rsplit(" (", 1)[0]: int(n.rsplit("(", 1)[1].rstrip(")")) for g in groups for n in g["names"]}
        members = call(agent_graph, "members", name="Is Forest")["results"][0]
        assert sizes["Is Forest"] == members["total"] > 0

    def test_biome_classes_are_grouped_and_the_group_with_the_land_types_is_there(self, agent_graph):
        out = call(agent_graph, "list", kind="biome class")
        groups = {g["group"]: g["names"] for g in out["groups"]}
        assert {"Has ...", "Is ...", "Spawns ..."} <= set(groups)
        assert sum(g["count"] for g in out["groups"]) == out["total"] == count(agent_graph, "MATCH (n:BiomeClass) RETURN count(n) AS n")
        assert any(n.startswith("Is Forest (") for n in groups["Is ..."]) and any(n.startswith("Is Ocean (") for n in groups["Is ..."])

    def test_the_categories_the_loaders_made_for_recipes_are_never_listed(self, agent_graph):
        names = call(agent_graph, "list", kind="category", contains="any of")["names"]
        assert names == [] or not any(n.startswith("Any of") for n in names)
        assert not any(n.startswith("Any of") for n in call(agent_graph, "list", kind="category")["names"])

    def test_categories_say_how_many_things_are_in_them(self, agent_graph):
        out = call(agent_graph, "list", kind="category", contains="planks")
        assert any(n.startswith("Planks (") for n in out["names"])
        planks = next(n for n in out["names"] if n.startswith("Planks ("))
        assert int(planks.rsplit("(", 1)[1].rstrip(")")) == call(agent_graph, "members", name="Planks")["results"][0]["total"]


class TestItemsAndBlocks:
    def test_a_long_kind_is_a_page_and_the_pages_cover_it_exactly_once(self, agent_graph):
        first = call(agent_graph, "list", kind="item")
        assert first["total"] > listing.PAGE and len(first["names"]) == listing.PAGE and "offset=100" in first["next"]
        seen, offset = [], 0
        while offset < first["total"]:
            page = call(agent_graph, "list", kind="item", offset=offset)
            seen += page["names"]
            offset += listing.PAGE
        assert seen == sorted(seen) and len(seen) == len(set(seen)) == first["total"]

    def test_no_item_is_only_a_category(self, agent_graph):
        """A category can share its name with a real item (Anvil), so the check is: every listed name is the name of at least one non-category item."""
        offset, items = 0, []
        while True:
            page = call(agent_graph, "list", kind="item", offset=offset)
            items += page["names"]
            if "next" not in page:
                break
            offset += listing.PAGE
        real = {r["name"] for r in agent_graph.read("MATCH (n:Item) WHERE NOT n:ItemClass RETURN n.name AS name")}
        assert items and set(items) == real

    def test_swords_are_found_by_the_word_and_its_plural(self, agent_graph):
        for text in ("sword", "swords", "SWORDS"):
            names = call(agent_graph, "list", kind="item", contains=text)["names"]
            assert "Iron Sword" in names and "Diamond Sword" in names and all("sword" in n.lower() for n in names), text

    def test_several_words_must_all_be_in_the_name(self, agent_graph):
        names = call(agent_graph, "list", kind="item", contains="iron sword")["names"]
        assert names == ["Iron Sword"]

    def test_blocks_can_be_filtered_too(self, agent_graph):
        assert "Ender Chest" in call(agent_graph, "list", kind="block", contains="ender")["names"]


class TestFind:
    @pytest.mark.parametrize("query", ["biomes", "biome", "mobs", "structures", "villager professions"])
    def test_a_kind_word_is_sent_to_list(self, agent_graph, query):
        out = call(agent_graph, "find", query=query)
        assert out["matches"] == [] and "Call list with kind" in out["hint"]

    def test_real_names_are_still_found(self, agent_graph):
        assert call(agent_graph, "find", query="creeper")["matches"][0]["name"] == "Creeper"
        assert call(agent_graph, "find", query="desert")["matches"]
