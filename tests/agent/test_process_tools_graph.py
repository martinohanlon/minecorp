"""Tests for the get_process tool against the real graph, rebuilt from source in the throwaway database.

The database holds the processes in model/processes (shelter, emeralds, the Nether portal, the ender dragon and a mob farm) and the
sample process the loader tests use. No model is involved. Run with:  pytest -m integration"""
from __future__ import annotations

import json

import pytest

from minecorp_agent import tools
from minecorp_agent.budget import fit

pytestmark = [pytest.mark.integration, pytest.mark.jar]

REAL = {"Build a first-night shelter", "Build a simple mob farm", "Defeat the ender dragon", "Get emeralds", "Get to the Nether"}


def call(graph, **args):
    return tools.TOOLS_BY_NAME["get_process"].fn(graph, args)


class TestList:
    def test_every_real_process_is_listed_with_what_it_is_about(self, agent_graph):
        listed = call(agent_graph)["processes"]
        assert REAL <= {p["name"] for p in listed}
        assert all(p["about"] and p["status"] in {"candidate", "confirmed"} for p in listed)

    def test_the_dragon_builds_on_the_nether(self, agent_graph):
        dragon = next(p for p in call(agent_graph)["processes"] if p["name"] == "Defeat the ender dragon")
        assert dragon["buildsOn"] == ["Get to the Nether"]

    def test_the_list_says_a_plan_is_a_candidate(self, agent_graph):
        note = call(agent_graph)["note"]
        assert "candidate" in note and "as a plan, not as a fact" in note and "according to the wiki" not in note


class TestOutline:
    def test_the_nether_usual_route_runs_from_choosing_to_the_room_in_the_nether(self, agent_graph):
        route = call(agent_graph, name="nether")["usualRoute"]
        assert route[0] == "1. Choose how to get the obsidian (by hand)"
        assert route[-1].startswith("13. Build a room around the Nether portal")
        assert "5. Mine 10 obsidian (Mine Obsidian x10)" in route

    def test_a_step_that_is_a_game_action_names_it_and_a_manual_one_says_by_hand(self, agent_graph):
        route = call(agent_graph, name="nether")["usualRoute"]
        assert "3. Mine three diamonds (Mine Diamond Ore x3)" in route and "8. Build the frame (by hand)" in route

    def test_the_branch_for_getting_obsidian_is_an_alternative(self, agent_graph):
        alt = call(agent_graph, name="nether")["alternatives"][0]
        assert alt["after"] == "Choose how to get the obsidian" and alt["usually"]["go to"] == "Make an iron pickaxe"
        assert {o["go to"] for o in alt["or"]} == {"Make a bucket", "Find a ruined portal"}

    def test_flint_can_be_bought_instead_of_dug(self, agent_graph):
        alts = call(agent_graph, name="nether")["alternatives"]
        flint = next(a for a in alts if a["after"] == "Get flint from gravel")
        assert flint["or"] == [{"go to": "Buy flint from a fletcher", "when": "no flint after a lot of gravel and a village nearby"}]

    def test_every_step_is_listed_by_stage(self, agent_graph):
        out = call(agent_graph, name="nether")
        assert [s["stage"] for s in out["stages"]] == ["Choose a way", "Get obsidian", "Get a light", "Build and light the portal", "Go through"]
        assert sum(len(s["steps"]) for s in out["stages"]) == 18

    def test_a_process_that_builds_on_another_says_so_and_to_do_it_first(self, agent_graph):
        out = call(agent_graph, name="dragon")
        assert out["buildsOn"] == ["Get to the Nether"] and "first" in out["note"]

    def test_a_trade_is_named_as_a_game_action(self, agent_graph):
        route = call(agent_graph, name="emeralds")["usualRoute"]
        assert any("Trade 20 Wheat for Emerald" in step for step in route)

    def test_it_is_a_candidate_with_its_source_and_how_to_read_it(self, agent_graph):
        out = call(agent_graph, name="shelter")
        assert out["status"] == "candidate" and "minecraft.wiki/w/Tutorial" in out["source"]
        assert "candidate" in out["note"] and "'usualRoute'" in out["note"]

    @pytest.mark.parametrize("name,expected", [("nether", "Get to the Nether"), ("nether portal", "Get to the Nether"), ("dragon", "Defeat the ender dragon"),
                                               ("ender dragon", "Defeat the ender dragon"), ("emeralds", "Get emeralds"),
                                               ("shelter", "Build a first-night shelter"), ("mob farm", "Build a simple mob farm"),
                                               ("farm", "Build a simple mob farm"), ("Get to the Nether", "Get to the Nether")])
    def test_a_name_finds_its_process(self, agent_graph, name, expected):
        assert call(agent_graph, name=name)["process"] == expected

    def test_a_name_that_fits_several_returns_them(self, agent_graph):
        out = call(agent_graph, name="get")
        assert out["type"] == "bad_argument" and {"Get emeralds", "Get to the Nether"} <= set(out["candidates"])

    def test_an_unknown_name_lists_the_processes(self, agent_graph):
        out = call(agent_graph, name="build a rocket")
        assert out["type"] == "bad_argument" and REAL <= set(out["processes"])

    @pytest.mark.parametrize("name", ["nether", "dragon", "emeralds", "shelter", "mob farm"])
    def test_every_outline_fits_its_budget_without_losing_anything(self, agent_graph, name):
        out = call(agent_graph, name=name)
        assert "omitted" not in fit(out, tools.TOOLS_BY_NAME["get_process"].budget), (name, len(json.dumps(out)))


class TestStep:
    def test_a_step_in_full(self, agent_graph):
        out = call(agent_graph, name="nether", step="mine 10 obsidian")
        assert out["step"] == "Mine 10 obsidian" and out["gameActions"] == ["Mine Obsidian x10"]
        assert "At least 10 obsidian" in out["details"]
        assert out["next"] == [{"go to": "Get flint from gravel", "when": "10 obsidian collected", "usually": True}]

    def test_a_manual_step_says_it_is_by_hand(self, agent_graph):
        out = call(agent_graph, name="dragon", step="kill the dragon")
        assert "byHand" in out and "gameActions" not in out
        assert "no kill action" in out["details"]

    def test_a_branch_lists_every_way_on(self, agent_graph):
        out = call(agent_graph, name="nether", step="choose how to get the obsidian")
        assert len(out["next"]) == 3 and sum(1 for n in out["next"] if n.get("usually")) == 1

    def test_the_last_step_says_so(self, agent_graph):
        assert call(agent_graph, name="nether", step="build a room")["next"] == "This is the last step."

    def test_a_step_can_be_found_without_a_process_name(self, agent_graph):
        out = call(agent_graph, step="triangulate")
        assert out["process"] == "Defeat the ender dragon" and out["step"] == "Triangulate the stronghold"

    def test_an_ambiguous_step_names_the_candidates(self, agent_graph):
        out = call(agent_graph, name="nether", step="diamonds")
        assert out["type"] == "bad_argument" and "Get to the Nether: Mine three diamonds" in out["candidates"]

    def test_an_unknown_step_lists_the_steps(self, agent_graph):
        out = call(agent_graph, name="nether", step="fly to the moon")
        assert out["type"] == "bad_argument" and "Mine 10 obsidian" in out["steps"]

    def test_a_step_carries_its_status_and_its_source(self, agent_graph):
        out = call(agent_graph, name="emeralds", step="sell wheat")
        assert out["status"] == "candidate" and "minecraft.wiki" in out["source"] and "candidate" in out["note"]

    def test_the_description_names_where_it_came_from(self, agent_graph):
        assert "(Mob farm > Sinkhole" in call(agent_graph, name="mob farm", step="trapdoors")["details"]

    def test_every_step_of_every_process_can_be_read_and_fits(self, agent_graph):
        graph_tool = tools.TOOLS_BY_NAME["get_process"]
        for name in ("nether", "dragon", "emeralds", "shelter", "mob farm"):
            for stage in call(agent_graph, name=name)["stages"]:
                for step in stage["steps"]:
                    out = call(agent_graph, name=name, step=step)
                    if "error" in out:                        # a name that is a prefix of another step is ambiguous: that is allowed
                        assert out["candidates"], (name, step)
                        continue
                    assert out["step"] == step and "omitted" not in fit(out, graph_tool.budget), (name, step)


class TestIntegration:
    def test_a_sample_process_in_the_test_database_is_not_a_plan_for_a_player(self, agent_graph):
        listed = {p["name"] for p in call(agent_graph)["processes"]}
        assert "Get an iron pickaxe" in listed          # the loader tests' sample: the tool lists whatever is in the graph

    def test_a_trade_step_is_looked_up_with_the_trade_tools(self, agent_graph):
        action = call(agent_graph, name="nether", step="buy flint")["gameActions"]
        assert action == ["Trade 10 Gravel and Emerald for 10 Flint"]


class TestAStepNamedInsteadOfAPlan:
    def test_getting_wool_finds_the_shelter_step(self, agent_graph):
        out = call(agent_graph, name="wool")
        assert out["process"] == "Build a first-night shelter" and out["step"] == "Get wool" and "not a plan" in out["matchedAs"]

    def test_a_word_that_is_no_step_either_keeps_the_error_and_lists_the_plans(self, agent_graph):
        out = call(agent_graph, name="zebra")
        assert out["error"] == "No process matches 'zebra'." and REAL <= set(out["processes"])
