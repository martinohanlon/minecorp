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


# --------------------------------------------------------------------------- the question file and conversation history

import json  # noqa: E402

from evals.run_evals import QUESTIONS, context_of  # noqa: E402

KNOWN_KEYS = {"id", "question", "history", "expect_tools", "expect_any_tools", "expect_not_tools", "expect_no_tools", "expect_all", "expect_any", "expect_none", "max_lines"}


def test_a_case_with_history_becomes_the_conversation_so_far():
    case = {"history": [{"question": "q1", "answer": "line one\nline two"}, {"question": "q2", "answer": "a2"}]}
    got = context_of(case)
    assert [(e.question, e.lines) for e in got] == [("q1", ("line one", "line two")), ("q2", ("a2",))]


def test_a_case_without_history_has_no_context():
    assert context_of({"question": "q"}) == []


def test_the_question_file_is_well_formed():
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    assert len({c["id"] for c in cases}) == len(cases), "ids must be unique"
    for c in cases:
        assert set(c) <= KNOWN_KEYS, (c["id"], set(c) - KNOWN_KEYS)
        assert c["question"].strip()
        assert any(k.startswith("expect") for k in c), f"{c['id']} checks nothing"
        for h in c.get("history", []):
            assert set(h) == {"question", "answer"} and h["question"].strip() and h["answer"].strip(), c["id"]


def test_the_follow_up_cases_all_have_a_conversation_to_follow():
    cases = {c["id"]: c for c in json.loads(QUESTIONS.read_text(encoding="utf-8"))}
    follow_ups = [c for i, c in cases.items() if i.startswith("followup-")]
    assert len(follow_ups) >= 3 and all(c.get("history") for c in follow_ups)


def test_an_answer_longer_than_max_lines_fails_and_blank_lines_do_not_count():
    case = {"max_lines": 3}
    assert check(case, "a\nb\nc", []) == []
    assert check(case, "a\n\nb\n\nc\n", []) == []
    assert check(case, "a\nb\nc\nd", []) == ["4 lines, more than 3"]


def test_without_max_lines_any_length_will_do():
    assert check({"expect_any": ["a"]}, "a\n" * 30, []) == []


def test_the_list_cases_all_have_a_line_limit():
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    lists = [c for c in cases if c["id"].startswith("list-") and c["id"] != "list-not-for-one-thing"]
    assert len(lists) >= 8 and all(c.get("max_lines") for c in lists)
