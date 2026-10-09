"""Unit tests for the pure parts of agent/minecorp_agent/processes.py: matching words, the usual route, the views. No database."""
from __future__ import annotations

import pytest

from minecorp_agent import processes as pr


def task(id_, name, activity="Stage", description="", actions=(), status="candidate"):
    return {"id": id_, "name": name, "activity": activity, "description": description, "status": status,
            "source": "https://minecraft.wiki/w/Tutorial:X", "actions": list(actions)}


def edge(frm, to, name, condition, usual):
    return {"fromId": frm, "toId": to, "toName": name, "condition": condition, "usual": usual}


def sample():
    tasks = [task("p/a", "Choose a way"), task("p/b", "Mine iron", actions=[{"name": "Mine Iron Ore", "quantity": 3}]),
             task("p/c", "Use a bucket", actions=[{"name": "Craft Bucket", "quantity": 1}]), task("p/d", "Finish")]
    nxt = {"p/a": [edge("p/a", "p/b", "Mine iron", "can mine", True), edge("p/a", "p/c", "Use a bucket", "cannot mine", False)],
           "p/b": [edge("p/b", "p/d", "Finish", "iron mined", True)], "p/c": [edge("p/c", "p/d", "Finish", "bucket made", False)]}
    return {"id": "p", "name": "Get iron", "about": "Get some iron.", "tasks": tasks, "next": nxt, "buildsOn": []}


class TestWords:
    def test_lowercase_and_a_plural_s_is_removed(self):
        assert pr.words_of("Diamonds, Nether!") == ["diamond", "nether"]

    def test_short_words_and_double_s_keep_their_s(self):
        assert pr.words_of("gas glass") == ["gas", "glass"]

    def test_hyphens_slashes_and_underscores_split_words(self):
        assert pr.words_of("mob-farm/eyes_of_ender") == ["mob", "farm", "eye", "of", "ender"]


class TestScore:
    def test_the_name_as_written_is_best(self):
        assert pr.score(pr.words_of("get emeralds"), "Get emeralds", "emeralds", "") == 0

    def test_the_id_as_written(self):
        assert pr.score(pr.words_of("nether portal"), "Get to the Nether", "nether-portal", "") == 0

    def test_every_word_in_the_name(self):
        assert pr.score(pr.words_of("nether"), "Get to the Nether", "nether-portal", "") == 1

    def test_words_found_only_in_the_text_rank_after_the_name(self):
        assert pr.score(pr.words_of("lava"), "Get to the Nether", "nether-portal", "Pour water on lava") == 2

    def test_a_word_that_is_nowhere_does_not_match(self):
        assert pr.score(pr.words_of("zombie"), "Get to the Nether", "nether-portal", "text") is None

    def test_no_words_match_nothing(self):
        assert pr.score([], "A", "a", "a") is None


class TestFind:
    def processes(self):
        return {"a": {"id": "a", "name": "Get emeralds", "about": "villagers"}, "b": {"id": "b", "name": "Get to the Nether", "about": "portal"},
                "c": {"id": "c", "name": "Defeat the ender dragon", "about": ""}}

    def test_one_match(self):
        process, err = pr.find_process(self.processes(), "dragon")
        assert err is None and process["id"] == "c"

    def test_several_matches_are_returned_for_the_model_to_choose(self):
        process, err = pr.find_process(self.processes(), "get")
        assert process is None and err["candidates"] == ["Get emeralds", "Get to the Nether"] and err["type"] == "bad_argument"

    def test_no_match_lists_what_there_is(self):
        process, err = pr.find_process(self.processes(), "zzz")
        assert process is None and err["processes"] == ["Get emeralds", "Get to the Nether", "Defeat the ender dragon"]

    def test_an_exact_name_beats_a_partial_one(self):
        processes = {"a": {"id": "a", "name": "Get", "about": ""}, "b": {"id": "b", "name": "Get more", "about": ""}}
        assert pr.find_process(processes, "get")[0]["id"] == "a"

    def test_a_step_by_its_name_across_processes(self):
        p = sample()
        found, err = pr.find_step([(p, t) for t in p["tasks"]], "bucket")
        assert err is None and found[1]["id"] == "p/c"

    def test_an_ambiguous_step_names_its_process(self):
        p = sample()
        found, err = pr.find_step([(p, p["tasks"][0]), (p, task("p/e", "Choose a bucket"))], "choose")
        assert found is None and err["candidates"] == ["Get iron: Choose a bucket", "Get iron: Choose a way"]


class TestUsualRoute:
    def test_it_follows_the_default_edges_from_the_first_task(self):
        assert [t["id"] for t in pr.usual_route(sample())] == ["p/a", "p/b", "p/d"]

    def test_a_loop_stops(self):
        p = sample()
        p["next"]["p/d"] = [edge("p/d", "p/a", "Choose a way", "again", True)]
        assert [t["id"] for t in pr.usual_route(p)] == ["p/a", "p/b", "p/d"]

    def test_a_process_with_no_tasks_has_no_route(self):
        assert pr.usual_route({"tasks": [], "next": {}}) == []


class TestOutline:
    def test_the_route_is_numbered_and_names_game_actions_or_says_by_hand(self):
        out = pr.outline(sample())
        assert out["usualRoute"] == ["1. Choose a way (by hand)", "2. Mine iron (Mine Iron Ore x3)", "3. Finish (by hand)"]

    def test_a_quantity_of_one_is_not_written(self):
        assert pr.action_text({"name": "Craft Bucket", "quantity": 1}) == "Craft Bucket"
        assert pr.action_text({"name": "Mine Iron Ore", "quantity": 3}) == "Mine Iron Ore x3"
        assert pr.action_text({"name": "Mine Iron Ore", "quantity": None}) == "Mine Iron Ore"

    def test_a_branch_says_what_is_usual_and_what_else_there_is(self):
        alt = pr.outline(sample())["alternatives"]
        assert alt == [{"after": "Choose a way", "usually": {"go to": "Mine iron", "when": "can mine"},
                        "or": [{"go to": "Use a bucket", "when": "cannot mine"}]}]

    def test_a_step_with_one_way_on_is_not_a_branch(self):
        assert all(a["after"] == "Choose a way" for a in pr.outline(sample())["alternatives"])

    def test_the_steps_are_grouped_by_stage_in_order(self):
        p = sample()
        p["tasks"][2]["activity"] = "Other"
        stages = pr.outline(p)["stages"]
        assert [s["stage"] for s in stages] == ["Stage", "Other"] and stages[1]["steps"] == ["Use a bucket"]

    def test_a_candidate_says_so_and_how_to_read_it(self):
        note = pr.outline(sample())["note"]
        assert "candidate" in note and "as a plan, not as a fact" in note and "'usualRoute'" in note
        assert "according to the wiki" not in note

    def test_a_confirmed_process_does_not_say_candidate(self):
        p = sample()
        for t in p["tasks"]:
            t["status"] = "confirmed"
        out = pr.outline(p)
        assert out["status"] == "confirmed" and "candidate" not in out["note"]

    def test_a_process_that_builds_on_another_says_to_do_it_first(self):
        p = sample()
        p["buildsOn"] = ["Get to the Nether"]
        out = pr.outline(p)
        assert out["buildsOn"] == ["Get to the Nether"] and "first" in out["note"]

    def test_the_source_is_given(self):
        assert pr.outline(sample())["source"] == "https://minecraft.wiki/w/Tutorial:X"


class TestStepView:
    def test_a_step_with_an_action_names_it(self):
        p = sample()
        out = pr.step_view(p, p["tasks"][1])
        assert out["gameActions"] == ["Mine Iron Ore x3"] and "byHand" not in out
        assert out["next"] == [{"go to": "Finish", "when": "iron mined", "usually": True}]

    def test_a_manual_step_says_so(self):
        p = sample()
        out = pr.step_view(p, p["tasks"][0])
        assert "byHand" in out and "gameActions" not in out
        assert [n["go to"] for n in out["next"]] == ["Mine iron", "Use a bucket"]
        assert out["next"][1].get("usually") is None

    def test_the_last_step_says_so(self):
        p = sample()
        assert pr.step_view(p, p["tasks"][3])["next"] == "This is the last step."


@pytest.mark.parametrize("args", [{}, {"name": ""}, {"name": "  ", "step": " "}])
def test_no_name_and_no_step_lists_the_processes(args):
    class Empty:
        def read(self, *a, **k):
            return []
    assert pr.tool_get_process(Empty(), args) == {"processes": [], "note": "No plans are loaded."}
