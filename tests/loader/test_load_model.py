"""Unit tests for loader/load_model.py: validating and flattening the authored YAML model (a player and processes)."""
from __future__ import annotations

from pathlib import Path

import pytest

import load_model as lm

ACTIONS = {"mine:minecraft:iron_ore", "smelt:minecraft:iron_ingot", "craft:minecraft:stick", "craft:minecraft:oak_planks"}
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def good_people():
    return {"players": [{"uuid": "u-1", "name": "alex"}]}


def good_process():
    return {
        "_file": "p.yaml",
        "process": {"id": "p", "name": "P", "description": "A goal."},
        "activities": [
            {"id": "a1", "name": "A1", "seq": 1, "tasks": [
                {"id": "p/mine", "name": "Mine", "actions": [{"do": "mine:minecraft:iron_ore", "quantity": 3}]},
                {"id": "p/sticks", "name": "Sticks",
                 "actions": [{"do": "craft:minecraft:oak_planks", "quantity": 2}, {"do": "craft:minecraft:stick"}]},
                {"id": "p/build", "name": "Build something"},
            ]}],
        "next": [{"from": "p/mine", "to": "p/sticks", "condition": "c", "outcome": "o", "default": True},
                 {"from": "p/sticks", "to": "p/build"}],
        "status": "confirmed", "confirmed_by": "alex", "confirmed_at": "2026-10-07",
    }


def errors_for(people=None, proc=None, actions=ACTIONS):
    processes = [proc] if proc is not None else [good_process()]
    return lm.validate(people if people is not None else good_people(), processes, actions)


def test_a_consistent_model_has_no_errors():
    assert errors_for() == []


def test_a_model_with_no_process_is_valid():
    assert lm.validate(good_people(), [], ACTIONS) == []


# --------------------------------------------------------------------------- validate: players

class TestValidatePlayers:
    def test_duplicate_player_names(self):
        people = {"players": [{"uuid": "u-1", "name": "alex"}, {"uuid": "u-2", "name": "alex"}]}
        assert any("duplicate player name" in e for e in errors_for(people))

    def test_a_player_needs_a_uuid_and_a_name(self):
        assert any("needs a uuid and a name" in e for e in errors_for({"players": [{"name": "alex"}]}))
        assert any("needs a uuid and a name" in e for e in errors_for({"players": [{"uuid": "u-1"}]}))


# --------------------------------------------------------------------------- validate: processes

class TestValidateProcess:
    def _bad(self, mutate):
        proc = good_process()
        mutate(proc)
        return errors_for(proc=proc)

    def test_duplicate_process_id(self):
        errs = lm.validate(good_people(), [good_process(), good_process()], ACTIONS)
        assert any("process id missing or duplicated" in e for e in errs)

    def test_a_process_needs_an_id(self):
        assert any("process id missing" in e for e in self._bad(lambda p: p["process"].pop("id")))

    def test_status_must_be_known(self):
        assert any("status must be one of" in e for e in self._bad(lambda p: p.update(status="maybe")))

    def test_confirmed_needs_a_confirming_player(self):
        assert any("confirmed needs confirmed_by naming a player" in e for e in self._bad(lambda p: p.update(confirmed_by="nobody")))

    def test_a_role_is_not_a_confirmer(self):
        assert any("confirmed needs" in e for e in self._bad(lambda p: p.update(confirmed_by="steward")))

    def test_candidate_needs_provenance(self):
        errs = self._bad(lambda p: p.update(status="candidate"))
        for field in ("source", "extracted_at", "confidence"):
            assert any(f"a candidate needs {field}" in e for e in errs)

    def test_a_status_left_out_is_a_candidate(self):
        proc = good_process()
        del proc["status"]
        assert any("a candidate needs source" in e for e in errors_for(proc=proc))

    def test_a_complete_candidate_is_valid(self):
        proc = good_process()
        proc.update(status="candidate", source="https://minecraft.wiki/w/Tutorial:Shelters", extracted_at="2026-10-07", confidence=0.7)
        assert errors_for(proc=proc) == []

    def test_duplicate_task_id_across_processes(self):
        other = good_process()
        other["process"]["id"] = "q"
        errs = lm.validate(good_people(), [good_process(), other], ACTIONS)
        assert any("task id missing or duplicated" in e for e in errs)

    def test_unknown_action_id(self):
        errs = self._bad(lambda p: p["activities"][0]["tasks"][0]["actions"].append({"do": "mine:minecraft:flurble"}))
        assert any("mine:minecraft:flurble is not an action in the graph" in e for e in errs)

    @pytest.mark.parametrize("bad", [0, -1, 1.5, "3", True, None])
    def test_quantity_must_be_a_whole_number_of_at_least_one(self, bad):
        errs = self._bad(lambda p: p["activities"][0]["tasks"][0]["actions"][0].update(quantity=bad))
        assert any("quantity must be a whole number" in e for e in errs)

    def test_next_must_stay_inside_the_process(self):
        errs = self._bad(lambda p: p["next"].append({"from": "p/mine", "to": "other/task"}))
        assert any("next to other/task is not a task in this process" in e for e in errs)
        errs = self._bad(lambda p: p["next"].append({"from": "ghost", "to": "p/mine"}))
        assert any("next from ghost" in e for e in errs)

    def test_every_error_is_reported_not_just_the_first(self):
        def many(p):
            p["activities"][0]["tasks"][1]["actions"][0].update(do="nope")
            p["confirmed_by"] = "nobody"
            p["next"].append({"from": "ghost", "to": "p/mine"})
        assert len(self._bad(many)) >= 3

    def test_roles_and_owners_are_no_longer_part_of_a_process(self):
        # an old file that still names a role, an owner or a family is not rejected: those keys are simply not read
        proc = good_process()
        proc["process"].update(owner="process-owner", family="craft")
        proc["activities"][0]["tasks"][0].update(role="miner", automatable=True, escalates_to="process-owner")
        assert errors_for(proc=proc) == []
        r = lm.rows(good_people(), [proc])
        assert all("role" not in t and "automatable" not in t for t in r["tasks"])
        assert all(set(p) == {"id", "name", "description"} for p in r["processes"])


# --------------------------------------------------------------------------- rows

class TestRows:
    def rows(self, proc=None):
        return lm.rows(good_people(), [proc or good_process()])

    def test_the_players_are_passed_through(self):
        assert self.rows()["players"] == [{"uuid": "u-1", "name": "alex"}]

    def test_there_is_nothing_about_an_organization(self):
        r = self.rows()
        assert not ({"units", "roles", "fills", "handoffs", "escalations"} & set(r))

    def test_task_actions_are_numbered_in_order_with_quantities(self):
        assert self.rows()["task_actions"] == [
            {"task": "p/mine", "action": "mine:minecraft:iron_ore", "order": 1, "quantity": 3},
            {"task": "p/sticks", "action": "craft:minecraft:oak_planks", "order": 1, "quantity": 2},
            {"task": "p/sticks", "action": "craft:minecraft:stick", "order": 2, "quantity": 1}]

    def test_a_manual_task_has_no_actions_and_tasks_carry_no_type(self):
        r = self.rows()
        assert "p/build" not in {a["task"] for a in r["task_actions"]}
        assert all("type" not in t and "recipe" not in t and "harvest" not in t for t in r["tasks"])

    def test_next_defaults(self):
        n = self.rows()["nexts"]
        assert n[0] == {"from": "p/mine", "to": "p/sticks", "condition": "c", "outcome": "o", "isDefault": True}
        assert n[1]["isDefault"] is False and n[1]["condition"] is None

    def test_activity_ids_are_prefixed_with_the_process(self):
        r = self.rows()
        assert r["activities"] == [{"id": "p/a1", "process": "p", "name": "A1", "seq": 1}]
        assert {t["activity"] for t in r["tasks"]} == {"p/a1"}

    def test_confirmed_process_is_confirmed_by_a_player_for_every_task(self):
        r = self.rows()
        assert {c["task"] for c in r["confirmations"]} == {"p/mine", "p/sticks", "p/build"}
        assert all(c["player"] == "alex" and c["at"] == "2026-10-07" for c in r["confirmations"])
        assert all(t["status"] == "confirmed" for t in r["tasks"])

    def test_candidate_carries_provenance_and_no_confirmation(self):
        proc = good_process()
        proc.update(status="candidate", source="wiki", extracted_at="2026-10-07", confidence=0.7)
        r = self.rows(proc)
        assert r["confirmations"] == []
        assert all(t["source"] == "wiki" and t["confidence"] == 0.7 and t["extractedAt"] == "2026-10-07" for t in r["tasks"])

    def test_no_process_gives_no_rows(self):
        r = lm.rows(good_people(), [])
        assert r["processes"] == r["activities"] == r["tasks"] == r["nexts"] == r["confirmations"] == []


# --------------------------------------------------------------------------- reading the files

class TestReading:
    def test_a_missing_or_empty_directory_has_no_process(self, tmp_path):
        assert lm.read_processes(tmp_path) == []
        assert lm.read_processes(tmp_path / "nowhere") == []

    def test_each_file_is_a_process_and_remembers_its_name(self, tmp_path):
        (tmp_path / "b.yaml").write_text("process: {id: b, name: B}\n", encoding="utf-8")
        (tmp_path / "a.yaml").write_text("process: {id: a, name: A}\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("not a process", encoding="utf-8")
        assert [(p["_file"], p["process"]["id"]) for p in lm.read_processes(tmp_path)] == [("a.yaml", "a"), ("b.yaml", "b")]


# --------------------------------------------------------------------------- the real model files

@pytest.fixture(scope="module")
def model():
    return lm.read_model()


class TestRealModel:
    def test_there_is_one_player(self, model):
        people, _ = model
        assert [p["name"] for p in people["players"]] == ["martinohanlon"]

    def test_there_is_no_organization_in_the_model_files(self, model):
        people, _ = model
        assert not ({"org_units", "roles", "filled_by"} & set(people))

    def test_structurally_valid_when_every_named_action_exists(self, model):
        people, processes = model
        named = {a["do"] for p in processes for ac in p["activities"] for t in ac["tasks"] for a in t.get("actions", [])}
        assert lm.validate(people, processes, named) == []

    def test_action_ids_follow_the_verb_colon_item_pattern(self, model):
        _people, processes = model
        valid = {"mine", "kill", "shear", "craft", "smelt", "smith", "cut"}
        for p in processes:
            for ac in p["activities"]:
                for t in ac["tasks"]:
                    for a in t.get("actions", []):
                        verb, _, item = a["do"].partition(":")
                        assert verb in valid and item.startswith("minecraft:"), a["do"]

    def test_task_ids_are_prefixed_with_their_process_id(self, model):
        _people, processes = model
        for p in processes:
            for ac in p["activities"]:
                for t in ac["tasks"]:
                    assert t["id"].startswith(p["process"]["id"] + "/")


class TestSampleProcess:
    """The process the tests load into the throwaway database (the real model has none yet)."""

    def test_it_is_valid_and_has_structured_and_manual_tasks(self):
        processes = lm.read_processes(FIXTURES)
        named = {a["do"] for p in processes for ac in p["activities"] for t in ac["tasks"] for a in t.get("actions", [])}
        assert [p["process"]["id"] for p in processes] == ["sample"]
        assert lm.validate(lm.read_model()[0], processes, named) == []
        r = lm.rows(lm.read_model()[0], processes)
        manual = {t["id"] for t in r["tasks"]} - {a["task"] for a in r["task_actions"]}
        assert manual == {"sample/show-off"}
