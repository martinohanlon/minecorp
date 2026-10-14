"""The eval runner's pass/fail rules (agent/evals/run_evals.py), without calling a model."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
from evals.run_evals import check  # noqa: E402


def test_every_expected_tool_must_be_used():
    assert check({"expect_tools": ["a", "b"]}, "", ["a"]) == ["tool not used: b"]
    assert check({"expect_tools": ["a", "b"]}, "", ["b", "a"]) == []


def test_any_of_the_alternative_tools_will_do():
    case = {"expect_any_tools": ["search_wiki", "read_section"]}
    assert check(case, "", ["search_wiki"]) == []
    assert check(case, "", ["describe", "read_section"]) == []
    assert check(case, "", ["describe", "get_recipes"]) == ["none of the tools used: ['search_wiki', 'read_section']"]
    assert check(case, "", []) == ["none of the tools used: ['search_wiki', 'read_section']"]


def test_alternatives_and_required_tools_are_checked_separately():
    case = {"expect_tools": ["find"], "expect_any_tools": ["x", "y"]}
    assert check(case, "", ["find", "y"]) == []
    assert check(case, "", ["y"]) == ["tool not used: find"]
    assert check(case, "", ["find"]) == ["none of the tools used: ['x', 'y']"]


def test_a_case_without_the_key_is_unchanged():
    assert check({"expect_any": ["chicken"]}, "Chickens lay eggs", ["x"]) == []
