"""Benchmark: does a knowledge layer let a small model match a large one? Design: docs/benchmark.md.

    cd agent
    python -m bench.run_bench --models gpt-6-luna,gpt-6.2-astra          # every question, both arms, both models
    python -m bench.run_bench --arms bare --models gpt-6-luna iron       # only model-only, only questions whose id has "iron"
    python -m bench.run_bench --repeats 3 --max-cost 10

Arms: `kl` is the full agent (tools and graph), `bare` is the model alone (no tools, minimal prompt, same chat style).
Calls the real model, so it costs money: every run's cost is estimated from tokens and bench/prices.json, a running total is printed and the
run stops before it passes --max-cost. Results go to bench/results/<run>.jsonl (git-ignored), one line per run.
Scoring is deterministic: the answer is right when every entry in the question's "expect" appears in it ("a|b" = either; numbers as whole numbers).
A miss is "abstained" (the model said it did not know) or "wrong" (a confident wrong answer); both count as failures. A model that cannot answer
because the game data is newer than its training is a failure of that arm, not a question to remove.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from minecorp_agent.agent import Agent
from minecorp_agent.config import get_settings
from minecorp_agent.graph import Graph
from minecorp_agent.models import Question

HERE = Path(__file__).parent
QUESTIONS = HERE / "questions.json"
PRICES = HERE / "prices.json"
RESULTS = HERE / "results"
CACHED_FRACTION = 0.10      # assumed when a model has no cached price: cached input costs 10 percent of input


# ------------------------------------------------------------------ pure parts (unit tested)

def load_prices(path: Path = PRICES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["models"]


def cost_of(usage: dict, price: dict) -> dict:
    """Approximate dollars for one run. `cached` prices cached input at its own rate; `uncached` ignores the discount (the safe upper bound).
    Reasoning tokens are already inside completion_tokens, so they are billed as output without being added again."""
    prompt, completion = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    cached_tokens = min(usage.get("cached_tokens", 0), prompt)
    cached_rate = price["cached_input"] if price.get("cached_input") is not None else price["input"] * CACHED_FRACTION
    out = completion * price["output"] / 1e6
    uncached = prompt * price["input"] / 1e6 + out
    cached = (prompt - cached_tokens) * price["input"] / 1e6 + cached_tokens * cached_rate / 1e6 + out
    return {"cached": round(cached, 6), "uncached": round(uncached, 6)}


def _found(answer_lower: str, e: str) -> bool:
    """One expected string, or alternatives written "a|b" (any one will do). A number must stand alone: 232 is not in 2320 or 1232 or 232.5."""
    for alt in e.lower().split("|"):
        if re.fullmatch(r"-?\d+(\.\d+)?", alt):
            pattern = rf"(?<![\d.]){re.escape(alt)}(?![\d]|\.\d)"
        elif re.fullmatch(r"[ivx]+", alt):                 # a roman numeral is a whole word ("v" is not in "level")
            pattern = rf"(?<![a-z0-9]){alt}(?![a-z0-9])"
        else:                                              # words may carry a plural but must start at a word ("one" is not in "stone")
            pattern = rf"(?<![a-z0-9]){re.escape(alt)}"
        if re.search(pattern, answer_lower):
            return True
    return False


def is_correct(answer: str, expect: list[str]) -> bool:
    """Every expected entry is in the answer (case-insensitive); an entry may list alternatives with "|"."""
    text = answer.lower()
    return all(_found(text, e) for e in expect)


ABSTAIN = ("i don't know", "i do not know", "don't know", "not sure", "i'm not certain", "i am not certain", "could not find", "couldn't find",
           "cannot find", "can't find", "no information", "not aware", "unable to", "i can't", "i cannot", "doesn't exist", "does not exist")


def outcome_of(answer: str, expect: list[str], status: str = "ok") -> str:
    """right, abstained (said it did not know or could not find it) or wrong (an answer that is not the expected one: a confident miss).
    Both of the last two are failures; they are told apart because a wrong confident answer is worse than an honest 'I don't know'."""
    if status != "ok":
        return "error"
    if is_correct(answer, expect):
        return "right"
    return "abstained" if any(a in answer.lower() for a in ABSTAIN) else "wrong"


def summarise(rows: list[dict]) -> list[dict]:
    """One line per (arm, model): runs, correct, rate, total and per-run cost, cost per correct answer, median latency, calls."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["arm"], r["model"]), []).append(r)
    out = []
    for (arm, model), g in groups.items():
        n, right = len(g), sum(1 for r in g if r["correct"])
        total = sum(r["cost"]["cached"] for r in g)
        ms = sorted(r["ms"] for r in g)
        out.append({"arm": arm, "model": model, "runs": n, "correct": right, "rate": right / n,
                    "abstained": sum(1 for r in g if r.get("outcome") == "abstained"), "wrong": sum(1 for r in g if r.get("outcome") == "wrong"), "cost": total, "cost_per_run": total / n,
                    "cost_per_correct": total / right if right else None, "median_ms": ms[len(ms) // 2],
                    "tool_calls": sum(len(r["tools"]) for r in g) / n, "errors": sum(1 for r in g if r["status"] != "ok")})
    return sorted(out, key=lambda s: (s["arm"], s["model"]))


def plan(cases: list[dict], arms: list[tuple], repeats: int, seed: int | None) -> list[tuple]:
    """Every run to make, as (case, repeat, (arm, model)). With a seed the order is shuffled (reproducibly), so no question, arm or model gets
    a run of consecutive calls: order effects, such as a warm prompt cache from the same question just before, spread over all arms evenly.
    Without one it goes question by question."""
    tasks = [(case, repeat, arm) for case in cases for repeat in range(1, repeats + 1) for arm in arms]
    if seed is not None:
        random.Random(seed).shuffle(tasks)
    return tasks


# ------------------------------------------------------------------ running

def build_agent(arm: str, model: str, effort: str | None, trace_dir: Path, graph: Graph | None, api: str = "responses") -> Agent:
    settings = replace(get_settings(), openai_model=model, reasoning_effort=effort, use_tools=arm == "kl", trace_dir=trace_dir, api=api)
    return Agent(settings, graph)   # the model-only arm never touches the graph


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("filters", nargs="*", help="only questions whose id contains one of these")
    ap.add_argument("--models", default="gpt-6-luna,gpt-6.2-astra", help="comma separated model ids (all must be in prices.json)")
    ap.add_argument("--arms", default="kl,bare", help="kl, bare or both")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--effort", default="medium", help="reasoning effort sent to every model (none, low, medium, high...); 'default' sends nothing")
    ap.add_argument("--api", default="responses", choices=["responses", "chat"],
                    help="OpenAI endpoint for EVERY arm (the gpt-6 models reject function tools plus an effort on chat); responses by default")
    ap.add_argument("--seed", type=int, default=1, help="shuffle the order of all runs with this seed (the default); the seed is stored with every run")
    ap.add_argument("--no-shuffle", action="store_true", help="run question by question instead of shuffled (a stopped run then compares like with like)")
    ap.add_argument("--max-cost", type=float, default=10.0, help="stop before the estimated total passes this many dollars")
    ap.add_argument("--questions", type=Path, default=QUESTIONS)
    ap.add_argument("-v", "--verbose", action="store_true", help="print every answer")
    args = ap.parse_args(argv)

    prices, models = load_prices(), [m.strip() for m in args.models.split(",") if m.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    effort = None if args.effort == "default" else args.effort
    if unknown := [m for m in models if m not in prices]:
        print(f"no price for {unknown}: add them to bench/prices.json", file=sys.stderr)
        return 2
    if bad := [a for a in arms if a not in ("kl", "bare")]:
        print(f"unknown arm {bad}: use kl or bare", file=sys.stderr)
        return 2
    cases = json.loads(args.questions.read_text(encoding="utf-8"))
    if args.filters:
        cases = [c for c in cases if any(f in c["id"] for f in args.filters)]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{run_id}.jsonl"
    graph = None
    if "kl" in arms:
        s = get_settings()
        graph = Graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
        graph.verify()          # fail now, not after the model-only runs have been paid for
    agents = {(a, m): build_agent(a, m, effort, RESULTS / f"{run_id}-traces", graph, args.api) for a in arms for m in models}

    print(f"run {run_id}: {len(cases)} question(s) x {len(arms)} arm(s) x {len(models)} model(s) x {args.repeats} repeat(s) "
          f"= {len(cases) * len(arms) * len(models) * args.repeats} runs, effort {effort or 'default'}, budget ${args.max_cost:.2f}")
    rows, spent = [], 0.0
    tasks = plan(cases, list(agents), args.repeats, None if args.no_shuffle else args.seed)
    with out_path.open("w", encoding="utf-8") as f:
        for position, (case, repeat, (arm, model)) in enumerate(tasks, 1):
            if spent >= args.max_cost:
                print(f"\nstopped: estimated spend ${spent:.4f} reached the budget ${args.max_cost:.2f}")
                return finish(rows, out_path, spent)
            started = time.monotonic()
            r = agents[(arm, model)].run(Question("bench", "Evaluator", "bench", case["question"]), f"bench-{uuid.uuid4().hex[:6]}")
            cost = cost_of(r.usage, prices[model])
            spent += cost["cached"]
            row = {"run": run_id, "order": position, "seed": None if args.no_shuffle else args.seed, "id": case["id"], "stratum": case.get("stratum"),
                   "arm": arm, "model": model, "api_model": r.usage.get("api_model"), "effort": effort, "repeat": repeat, "question": case["question"],
                   "gold": case.get("gold"), "answer": r.answer, "outcome": outcome_of(r.answer, case["expect"], r.status),
                   "correct": r.status == "ok" and is_correct(r.answer, case["expect"]),
                   "status": r.status, "error": r.error, "ms": r.ms or int((time.monotonic() - started) * 1000),
                   "tools": [s.tool for s in r.steps], "usage": r.usage, "cost": cost}
            rows.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"{position:>4}/{len(tasks)} {'RIGHT' if row['correct'] else 'WRONG'}  {case['id']:<26} {arm:<4} {model:<16} {row['ms']:>6} ms "
                  f"{r.usage.get('prompt_tokens', 0):>6} in {r.usage.get('completion_tokens', 0):>5} out  ${cost['cached']:.5f}  total ${spent:.4f}"
                  f"{'' if r.status == 'ok' else '  status=' + r.status}")
            if args.verbose or row["status"] != "ok":
                for line in (r.answer or "").splitlines():
                    print(f"        | {line}")
                if r.error:
                    print(f"        ! {r.error}")
    return finish(rows, out_path, spent)


def finish(rows: list[dict], out_path: Path, spent: float) -> int:
    print(f"\n{'arm':<5} {'model':<16} {'right':>7} {'abst':>5} {'wrong':>6} {'$ total':>9} {'$/run':>9} {'$/correct':>10} {'median ms':>10} {'tools/run':>9}")
    for s in summarise(rows):
        per = f"{s['cost_per_correct']:.5f}" if s["cost_per_correct"] is not None else "-"
        print(f"{s['arm']:<5} {s['model']:<16} {s['correct']:>3}/{s['runs']:<3} {s['abstained']:>5} {s['wrong']:>6} {s['cost']:>9.5f} {s['cost_per_run']:>9.5f} {per:>10} "
              f"{s['median_ms']:>10} {s['tool_calls']:>9.1f}")
    print(f"\nestimated total ${spent:.4f} (cached rate where the API reported cached tokens; assumptions in bench/prices.json)\nresults: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
