"""Unit tests for loader/load_model.py: validating and flattening the authored YAML model."""
from __future__ import annotations

import copy

import pytest

import load_model as lm

ACTIONS = {"mine:minecraft:iron_ore", "smelt:minecraft:iron_ingot", "craft:minecraft:stick", "craft:minecraft:oak_planks"}


def good_org():
    return {
        "players": [{"uuid": "u-1", "name": "alex"}],
        "org_units": [{"id": "co", "name": "Co"}, {"id": "ext", "name": "Extraction", "part_of": "co"},
                      {"id": "gov", "name": "Governance", "part_of": "co"}],
        "roles": [{"id": "miner", "name": "Miner", "unit": "ext", "kind": "operational"},
                  {"id": "crafter", "name": "Crafter", "unit": "ext", "kind": "operational"},
                  {"id": "owner", "name": "Owner", "unit": "gov", "kind": "governance"},
                  {"id": "steward", "name": "Steward", "unit": "gov", "kind": "governance"}],
        "filled_by": [{"player": "alex", "roles": "all", "from": "2026-10-02"}],
    }


def good_process():
    return {
        "_file": "p.yaml",
        "process": {"id": "p", "name": "P", "family": "craft", "owner": "owner"},
        "activities": [
            {"id": "a1", "name": "A1", "seq": 1, "tasks": [
                {"id": "p/mine", "name": "Mine", "role": "miner", "automatable": False,
                 "actions": [{"do": "mine:minecraft:iron_ore", "quantity": 3}]},
                {"id": "p/sticks", "name": "Sticks", "role": "crafter", "automatable": True,
                 "escalates_to": "owner", "escalation_reason": "no planks",
                 "actions": [{"do": "craft:minecraft:oak_planks", "quantity": 2}, {"do": "craft:minecraft:stick"}]},
                {"id": "p/store", "name": "Store", "role": "crafter"},
            ]}],
        "next": [{"from": "p/mine", "to": "p/sticks", "condition": "c", "outcome": "o", "default": True},
                 {"from": "p/sticks", "to": "p/store"}],
        "status": "confirmed", "confirmed_by": "steward", "confirmed_at": "2026-10-02",
    }


def errors_for(org=None, proc=None, actions=ACTIONS):
    processes = [proc] if proc is not None else [good_process()]
    return lm.validate(org if org is not None else good_org(), processes, actions)


def test_a_consistent_model_has_no_errors():
    assert errors_for() == []


# --------------------------------------------------------------------------- validate: organization

class TestValidateOrg:
    def test_unit_part_of_unknown_unit(self):
        org = good_org(); org["org_units"][1]["part_of"] = "nowhere"
        assert any("part_of unknown unit nowhere" in e for e in errors_for(org))

    def test_duplicate_unit_and_role_ids(self):
        org = good_org(); org["org_units"].append({"id": "co", "name": "Dup"}); org["roles"].append(dict(org["roles"][0]))
        errs = errors_for(org)
        assert any("duplicate org unit id" in e for e in errs) and any("duplicate role id" in e for e in errs)

    def test_role_in_unknown_unit(self):
        org = good_org(); org["roles"][0]["unit"] = "nowhere"
        assert any("role miner is in unknown unit" in e for e in errors_for(org))

    def test_role_kind_must_be_known(self):
        org = good_org(); org["roles"][0]["kind"] = "wizard"
        assert any("kind must be one of" in e for e in errors_for(org))

    def test_filled_by_unknown_player_or_role(self):
        org = good_org(); org["filled_by"] = [{"player": "ghost", "roles": ["miner", "nope"], "from": "2026-10-02"}]
        errs = errors_for(org)
        assert any("unknown player ghost" in e for e in errs) and any("unknown role nope" in e for e in errs)


# --------------------------------------------------------------------------- validate: processes

class TestValidateProcess:
    def _bad(self, mutate):
        proc = good_process(); mutate(proc); return errors_for(proc=proc)

    def test_duplicate_process_id(self):
        errs = lm.validate(good_org(), [good_process(), good_process()], ACTIONS)
        assert any("process id missing or duplicated" in e for e in errs)

    def test_family_owner_and_status(self):
        errs = self._bad(lambda p: (p["process"].update(family="fun", owner="nobody"), p.update(status="maybe")))
        assert any("family must be one of" in e for e in errs)
        assert any("owner nobody is not a role" in e for e in errs)
        assert any("status must be one of" in e for e in errs)

    def test_confirmed_needs_a_confirming_role(self):
        assert any("confirmed needs confirmed_by" in e for e in self._bad(lambda p: p.update(confirmed_by="nobody")))

    def test_candidate_needs_provenance(self):
        def make_candidate(p):
            p["status"] = "candidate"
        errs = self._bad(make_candidate)
        for field in ("source", "extracted_at", "confidence"):
            assert any(f"a candidate needs {field}" in e for e in errs)

    def test_a_complete_candidate_is_valid(self):
        proc = good_process(); proc.update(status="candidate", source="llm", extracted_at="2026-10-02", confidence=0.7)
        assert errors_for(proc=proc) == []

    def test_duplicate_task_id_across_processes(self):
        other = good_process(); other["process"]["id"] = "q"
        errs = lm.validate(good_org(), [good_process(), other], ACTIONS)
        assert any("task id missing or duplicated" in e for e in errs)

    def test_task_role_must_exist(self):
        errs = self._bad(lambda p: p["activities"][0]["tasks"][0].update(role="wizard"))
        assert any("role wizard is not a role" in e for e in errs)

    def test_unknown_action_id(self):
        errs = self._bad(lambda p: p["activities"][0]["tasks"][0]["actions"].append({"do": "mine:minecraft:flurble"}))
        assert any("mine:minecraft:flurble is not an action in the graph" in e for e in errs)

    @pytest.mark.parametrize("bad", [0, -1, 1.5, "3", True, None])
    def test_quantity_must_be_a_whole_number_of_at_least_one(self, bad):
        errs = self._bad(lambda p: p["activities"][0]["tasks"][0]["actions"][0].update(quantity=bad))
        assert any("quantity must be a whole number" in e for e in errs)

    def test_escalates_to_unknown_role(self):
        errs = self._bad(lambda p: p["activities"][0]["tasks"][1].update(escalates_to="nobody"))
        assert any("escalates_to unknown role nobody" in e for e in errs)

    def test_next_must_stay_inside_the_process(self):
        errs = self._bad(lambda p: p["next"].append({"from": "p/mine", "to": "other/task"}))
        assert any("next to other/task is not a task in this process" in e for e in errs)
        errs = self._bad(lambda p: p["next"].append({"from": "ghost", "to": "p/mine"}))
        assert any("next from ghost" in e for e in errs)

    def test_every_error_is_reported_not_just_the_first(self):
        def many(p):
            p["activities"][0]["tasks"][0].update(role="wizard")
            p["activities"][0]["tasks"][1]["actions"][0].update(do="nope")
            p["process"]["owner"] = "nobody"
        assert len(self._bad(many)) >= 3


# --------------------------------------------------------------------------- rows

class TestRows:
    def rows(self, proc=None):
        return lm.rows(good_org(), [proc or good_process()])

    def test_filled_by_all_expands_to_every_role(self):
        r = self.rows()
        assert {f["role"] for f in r["fills"]} == {"miner", "crafter", "owner", "steward"}
        assert all(f["player"] == "alex" and f["from"] == "2026-10-02" for f in r["fills"])

    def test_filled_by_a_list_expands_to_those_roles_only(self):
        org = good_org(); org["filled_by"] = [{"player": "alex", "roles": ["miner"], "from": "2026-10-02"}]
        assert [f["role"] for f in lm.rows(org, [good_process()])["fills"]] == ["miner"]

    def test_task_actions_are_numbered_in_order_with_quantities(self):
        r = self.rows()
        assert r["task_actions"] == [
            {"task": "p/mine", "action": "mine:minecraft:iron_ore", "order": 1, "quantity": 3},
            {"task": "p/sticks", "action": "craft:minecraft:oak_planks", "order": 1, "quantity": 2},
            {"task": "p/sticks", "action": "craft:minecraft:stick", "order": 2, "quantity": 1}]

    def test_manual_task_has_no_actions_and_tasks_no_longer_carry_a_type(self):
        r = self.rows()
        assert "p/store" not in {a["task"] for a in r["task_actions"]}
        assert all("type" not in t and "recipe" not in t and "harvest" not in t for t in r["tasks"])

    def test_handoff_only_where_the_role_changes(self):
        r = self.rows()
        assert r["handoffs"] == [{"from": "p/mine", "to": "p/sticks"}]    # sticks -> store is crafter to crafter
        assert len(r["nexts"]) == 2

    def test_next_defaults(self):
        n = self.rows()["nexts"]
        assert n[0] == {"from": "p/mine", "to": "p/sticks", "condition": "c", "outcome": "o", "isDefault": True}
        assert n[1]["isDefault"] is False and n[1]["condition"] is None

    def test_activity_ids_are_prefixed_with_the_process(self):
        r = self.rows()
        assert r["activities"] == [{"id": "p/a1", "process": "p", "name": "A1", "seq": 1}]
        assert {t["activity"] for t in r["tasks"]} == {"p/a1"}

    def test_confirmed_process_confirms_every_task(self):
        r = self.rows()
        assert {c["task"] for c in r["confirmations"]} == {"p/mine", "p/sticks", "p/store"}
        assert all(c["role"] == "steward" and c["at"] == "2026-10-02" for c in r["confirmations"])
        assert all(t["status"] == "confirmed" for t in r["tasks"])

    def test_candidate_carries_provenance_and_no_confirmation(self):
        proc = good_process(); proc.update(status="candidate", source="llm", extracted_at="2026-10-02", confidence=0.7)
        r = self.rows(proc)
        assert r["confirmations"] == []
        assert all(t["source"] == "llm" and t["confidence"] == 0.7 and t["extractedAt"] == "2026-10-02" for t in r["tasks"])

    def test_escalation_row(self):
        assert self.rows()["escalations"] == [{"task": "p/sticks", "role": "owner", "reason": "no planks"}]

    def test_automatable_defaults_to_false(self):
        r = self.rows()
        assert {t["id"]: t["automatable"] for t in r["tasks"]} == {"p/mine": False, "p/sticks": True, "p/store": False}


# --------------------------------------------------------------------------- the real model files

@pytest.fixture(scope="module")
def model():
    return lm.read_model()


class TestRealModel:
    def test_files_are_read(self, model):
        org, processes = model
        assert org["org_units"] and org["roles"] and processes

    def test_structurally_valid_when_every_named_action_exists(self, model):
        org, processes = model
        named = {a["do"] for p in processes for ac in p["activities"] for t in ac["tasks"] for a in t.get("actions", [])}
        assert lm.validate(org, processes, named) == []

    def test_action_ids_follow_the_verb_colon_item_pattern(self, model):
        _org, processes = model
        valid = set(lm.TIERS) | {"mine", "kill", "shear", "craft", "smelt", "smith", "cut"}
        for p in processes:
            for ac in p["activities"]:
                for t in ac["tasks"]:
                    for a in t.get("actions", []):
                        verb, _, item = a["do"].partition(":")
                        assert verb in valid and item.startswith("minecraft:"), a["do"]

    def test_one_player_fills_every_role(self, model):
        org, processes = model
        r = lm.rows(org, processes)
        assert {f["player"] for f in r["fills"]} == {"martinohanlon"}
        assert {f["role"] for f in r["fills"]} == {x["id"] for x in org["roles"]}

    def test_every_process_has_exactly_one_owner_who_is_a_governance_or_known_role(self, model):
        org, processes = model
        roles = {x["id"]: x for x in org["roles"]}
        for p in processes:
            assert p["process"]["owner"] in roles

    def test_governance_roles_from_the_design_exist(self, model):
        org, _ = model
        ids = {x["id"] for x in org["roles"] if x["kind"] == "governance"}
        assert {"process-owner", "domain-owner", "platform-owner", "steward"} <= ids

    def test_task_ids_are_prefixed_with_their_process_id(self, model):
        _org, processes = model
        for p in processes:
            for ac in p["activities"]:
                for t in ac["tasks"]:
                    assert t["id"].startswith(p["process"]["id"] + "/")

    def test_a_task_never_asks_two_roles(self, model):
        # one role per task is structural (a task has a single `role`); this guards a list sneaking in
        _org, processes = model
        for p in processes:
            for ac in p["activities"]:
                for t in ac["tasks"]:
                    assert isinstance(t["role"], str)
