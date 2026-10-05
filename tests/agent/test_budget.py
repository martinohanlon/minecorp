"""Tests for tool result size budgets and error shapes (agent/minecorp_agent/budget.py). No database needed."""
from __future__ import annotations

import json

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable

from minecorp_agent import budget


def rows(n, width=100):
    return [{"i": i, "text": "y" * width} for i in range(n)]


class TestFit:
    def test_a_small_result_is_returned_unchanged(self):
        result = {"a": rows(3)}
        assert budget.fit(result, 5000) is result

    def test_the_longest_list_loses_entries_from_the_end(self):
        out = budget.fit({"hits": rows(20), "small": [1, 2, 3]}, 1500)
        assert budget.size(out) <= 1500
        assert [h["i"] for h in out["hits"]] == list(range(len(out["hits"])))   # the best (first) stay
        assert out["small"] == [1, 2, 3]
        assert out["omitted"] == {"hits": 20 - len(out["hits"])}

    def test_two_lists_are_both_shortened_when_needed(self):
        out = budget.fit({"a": rows(20), "b": rows(20)}, 1500)
        assert budget.size(out) <= 1500 and set(out["omitted"]) == {"a", "b"}

    def test_the_note_keeps_what_was_there_and_says_something_was_left_out(self):
        out = budget.fit({"note": "Read me.", "a": rows(30)}, 1000)
        assert out["note"].startswith("Read me.") and "left out" in out["note"]

    def test_the_original_is_not_changed(self):
        original = {"a": rows(30)}
        budget.fit(original, 1000)
        assert len(original["a"]) == 30 and "omitted" not in original

    def test_it_is_always_valid_json(self):
        json.loads(json.dumps(budget.fit({"a": rows(50, 300)}, 2000)))

    def test_one_huge_value_with_no_list_to_shorten_becomes_an_error(self):
        out = budget.fit({"text": "z" * 20000}, 1000)
        assert out["type"] == budget.BAD_ARGUMENT and "too large" in out["error"]

    def test_a_list_that_cannot_be_shortened_any_further_becomes_an_error(self):
        assert "error" in budget.fit({"a": [{"text": "z" * 5000}]}, 1000)

    def test_a_result_that_is_not_an_object_becomes_an_error(self):
        assert "error" in budget.fit(["x" * 5000], 1000)


class TestClassify:
    @pytest.mark.parametrize("exc", [ServiceUnavailable("x"), ClientError("x"), ConnectionError("x"), TimeoutError("x")])
    def test_service_problems_are_outages(self, exc):
        kind, message = budget.classify(exc)
        assert kind == budget.UNAVAILABLE and "not available" in message

    @pytest.mark.parametrize("exc", [ValueError("limit"), TypeError("limit"), KeyError("limit"), json.JSONDecodeError("m", "d", 0)])
    def test_bad_arguments(self, exc):
        kind, message = budget.classify(exc)
        assert kind == budget.BAD_ARGUMENT and "call the tool again" in message

    def test_anything_else_is_a_failure_that_is_not_called_an_outage(self):
        kind, message = budget.classify(RuntimeError("bug"))
        assert kind == budget.FAILED and "not available" not in message
