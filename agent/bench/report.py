"""Report on a benchmark run: accuracy and cost per arm, per question type and per question, with a confidence interval.

    cd agent
    python -m bench.report                    # the newest run in bench/results
    python -m bench.report 20261118-155734    # a named run
    python -m bench.report --small gpt-6-luna --large gpt-6.2-astra

Answers are RE-SCORED against the current bench/questions.json, so a fix to a question's "expect" applies to a finished run without calling the
model again (the stored "correct" is ignored). Free: no model calls.

The confidence interval resamples QUESTIONS (not runs): the repeats of one question are not independent, so the honest sample size is the number
of questions. With 20 questions the interval is wide; that is the point of printing it.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from bench.run_bench import QUESTIONS, RESULTS, load_prices, outcome_of


def load(run: str | None) -> tuple[list[dict], str]:
    files = sorted(RESULTS.glob("*.jsonl"))
    path = RESULTS / f"{run}.jsonl" if run else (files[-1] if files else None)
    if not path or not path.exists():
        raise SystemExit("no results found: run bench.run_bench first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()], path.stem


def rescore(rows: list[dict]) -> list[dict]:
    cases = {c["id"]: c for c in json.loads(QUESTIONS.read_text(encoding="utf-8"))}
    out = []
    for r in rows:
        if r["id"] in cases:
            outcome = outcome_of(r["answer"], cases[r["id"]]["expect"], r["status"])
            out.append({**r, "outcome": outcome, "correct": outcome == "right"})
    return out


def accuracy_by_question(rows: list[dict], arm: str, model: str) -> dict[str, float]:
    by: dict[str, list[bool]] = {}
    for r in rows:
        if r["arm"] == arm and r["model"] == model:
            by.setdefault(r["id"], []).append(r["correct"])
    return {q: sum(v) / len(v) for q, v in by.items()}


def bootstrap_diff(a: dict[str, float], b: dict[str, float], n: int = 4000, seed: int = 1) -> tuple[float, float, float]:
    """Mean accuracy difference a - b over the questions both have, and a 95 percent interval from resampling the questions."""
    ids = sorted(set(a) & set(b))
    diffs = [a[q] - b[q] for q in ids]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return sum(diffs) / len(diffs), means[int(0.025 * n)], means[int(0.975 * n) - 1]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?")
    ap.add_argument("--small", default="gpt-6-luna")
    ap.add_argument("--large", default="gpt-6.2-astra")
    args = ap.parse_args(argv)
    rows, name = load(args.run)
    rows = rescore(rows)
    prices = load_prices()
    arms = [("bare", args.small, "A bare small"), ("bare", args.large, "C bare large"), ("kl", args.small, "B KL small"), ("kl", args.large, "D KL large")]
    arms = [a for a in arms if any(r["arm"] == a[0] and r["model"] == a[1] for r in rows)]
    strata = sorted({r["stratum"] for r in rows})
    print(f"run {name}: {len(rows)} runs, {len({r['id'] for r in rows})} questions, re-scored against bench/questions.json\n")

    def sel(arm, model, **kw):
        return [r for r in rows if r["arm"] == arm and r["model"] == model and all(r.get(k) == v for k, v in kw.items())]

    print("RIGHT, by question type")
    print(f"{'':<22}" + "".join(f"{a[2]:>15}" for a in arms))
    for s in strata:
        print(f"{s:<22}" + "".join(f"{sum(r['correct'] for r in sel(a, m, stratum=s)):>11}/{len(sel(a, m, stratum=s))}" for a, m, _ in arms))
    print(f"{'ALL':<22}" + "".join(f"{sum(r['correct'] for r in sel(a, m)):>11}/{len(sel(a, m))}" for a, m, _ in arms))
    print(f"{'  abstained / wrong':<22}" + "".join(f"{sum(r['outcome'] == 'abstained' for r in sel(a, m)):>9}/{sum(r['outcome'] == 'wrong' for r in sel(a, m)):<5}" for a, m, _ in arms))

    print("\nCOST (US dollars, approximate; 'cached' = the API's cached-token discount, 'uncached' = list price)")
    print(f"{'':<14}{'$/run cached':>14}{'$/run uncached':>16}{'$/right cached':>16}{'$/right uncached':>18}{'median s':>10}{'tools/run':>10}{'in tok':>8}")
    cost = {}
    for a, m, label in arms:
        g = sel(a, m)
        right = sum(r["correct"] for r in g) or float("nan")
        c, u = sum(r["cost"]["cached"] for r in g), sum(r["cost"]["uncached"] for r in g)
        cost[(a, m)] = (c / right, u / right)
        ms = sorted(r["ms"] for r in g)
        print(f"{label:<14}{c / len(g):>14.5f}{u / len(g):>16.5f}{c / right:>16.5f}{u / right:>18.5f}{ms[len(ms) // 2] / 1000:>10.1f}"
              f"{sum(len(r['tools']) for r in g) / len(g):>10.1f}{sum(r['usage']['prompt_tokens'] for r in g) / len(g):>8.0f}")
    print(f"total spent: ${sum(r['cost']['cached'] for r in rows):.2f} cached, ${sum(r['cost']['uncached'] for r in rows):.2f} uncached")

    def has(a, m):
        return any(x[0] == a and x[1] == m for x in arms)
    print("\nTHE CLAIM (accuracy difference over questions, 95% interval from resampling the questions)")
    for (a1, m1, l1), (a2, m2, l2) in (((("kl", args.small, "B KL small"), ("bare", args.large, "C bare large")),
                                        (("kl", args.small, "B KL small"), ("kl", args.large, "D KL large")),
                                        (("kl", args.small, "B KL small"), ("bare", args.small, "A bare small")),
                                        (("kl", args.large, "D KL large"), ("bare", args.large, "C bare large")))):
        if has(a1, m1) and has(a2, m2):
            d, lo, hi = bootstrap_diff(accuracy_by_question(rows, a1, m1), accuracy_by_question(rows, a2, m2))
            print(f"  {l1} - {l2}: {d * 100:+.1f} points  [{lo * 100:+.1f}, {hi * 100:+.1f}]")
    if has("kl", args.small) and has("bare", args.large):
        cb, ub = cost[("kl", args.small)], cost[("bare", args.large)]
        print(f"  cost per right answer, B against C: {cb[0]:.5f} vs {ub[0]:.5f} cached ({ub[0] / cb[0]:.1f}x cheaper), "
              f"{cb[1]:.5f} vs {ub[1]:.5f} uncached ({ub[1] / cb[1]:.1f}x cheaper)")

    print("\nPER QUESTION: right out of the repeats  (" + ", ".join(a[2].split()[0] for a in arms) + ")")
    for qid in dict.fromkeys(r["id"] for r in rows):
        n = len(sel(arms[0][0], arms[0][1], id=qid))
        print(f"  {qid:<24}" + "".join(f"{sum(r['correct'] for r in sel(a, m, id=qid)):>4}/{n}" for a, m, _ in arms))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
