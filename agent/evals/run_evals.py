"""Run the agent against agent/evals/questions.json and report pass/fail.

    cd agent
    python -m evals.run_evals            # all questions
    python -m evals.run_evals brown-bed  # only ids containing the text
    python -m evals.run_evals -v         # also print each answer

Calls the agent directly (not over HTTP) and the real model and graph, so it costs tokens.
Checks are simple substring tests on the lower-cased answer; they catch gross failures, not nuance.
Read the answers (-v) before trusting a pass.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from minecorp_agent.core import get_agent
from minecorp_agent.models import Question

QUESTIONS = Path(__file__).with_name("questions.json")


def check(case: dict, answer: str, tools: list[str]) -> list[str]:
    """Return the list of failed checks (empty means pass)."""
    text = answer.lower()
    failures = []
    for t in case.get("expect_tools", []):
        if t not in tools:
            failures.append(f"tool not used: {t}")
    if case.get("expect_no_tools") and tools:
        failures.append(f"tools used but none expected: {tools}")
    for s in case.get("expect_all", []):
        if s not in text:
            failures.append(f"missing: {s!r}")
    any_of = case.get("expect_any", [])
    if any_of and not any(s in text for s in any_of):
        failures.append(f"none of: {any_of}")
    for s in case.get("expect_none", []):
        if s in text:
            failures.append(f"should not contain: {s!r}")
    return failures


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    filters = [a for a in argv if not a.startswith("-")]
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if filters:
        cases = [c for c in cases if any(f in c["id"] for f in filters)]
    agent = get_agent()
    passed = 0
    total_tokens = 0
    for c in cases:
        q = Question("eval", "Evaluator", "eval", c["question"])
        r = agent.run(q, "eval-" + uuid.uuid4().hex[:6])
        tools = [s.tool for s in r.steps]
        fails = check(c, r.answer, tools)
        tokens = r.usage.get("prompt_tokens", 0) + r.usage.get("completion_tokens", 0)
        total_tokens += tokens
        passed += not fails
        print(f"{'PASS' if not fails else 'FAIL'}  {c['id']:<24} {r.ms:>5} ms  {tokens:>6} tok  tools={tools}")
        for f in fails:
            print(f"        - {f}")
        if r.status != "ok":
            print(f"        status={r.status} {r.error or ''}")
        if verbose or fails:
            for line in r.answer.splitlines():
                print(f"        | {line}")
    print(f"\n{passed}/{len(cases)} passed, {total_tokens} tokens, model {agent.settings.openai_model}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
