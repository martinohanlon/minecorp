"""Run the benchmark end to end: check the gold answers, run it, print the report (docs/benchmark.md).

    python scripts/run_benchmark.py                          # verify the gold, ask, run every question on all four arms, report
    python scripts/run_benchmark.py --yes                    # do not ask (for scripts); the cost cap still applies
    python scripts/run_benchmark.py --verify-only            # only check that every gold answer still matches the graph (free)
    python scripts/run_benchmark.py --report-only            # only report on the newest run (free); --run ID for another
    python scripts/run_benchmark.py --repeats 1 s1 s2        # anything else goes to bench.run_bench: here 1 repeat, ids containing s1 or s2

The wrapper scripts (run-benchmark.ps1 and run-benchmark.sh) call this. Needs the graph (the Neo4j in .env) and OPENAI_API_KEY.
A full run (60 questions x 4 arms x 3 repeats = 720 runs) cost about 6 dollars at the cached rate (about 12 at list price) on 2026-11-18;
`--max-cost` (default 10) stops a run that would pass it. Spends money: it asks first unless --yes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))       # the bench package lives in agent/

COST_NOTE = ("A full run (720 runs) cost about $6 at the cached rate ($12 at list price) on 2026-11-18; a stopped run keeps what it has done. "
             "The cap is --max-cost (default 10 dollars).")


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Split this script's own options from what goes on to bench.run_bench."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, allow_abbrev=False)
    ap.add_argument("--yes", action="store_true", help="do not ask before spending money")
    ap.add_argument("--verify-only", action="store_true", help="only check the gold answers against the graph")
    ap.add_argument("--report-only", action="store_true", help="only report on a finished run (the newest, or --run)")
    ap.add_argument("--run", help="the run id to report on (with --report-only)")
    return ap.parse_known_args(argv)


def confirm(assume_yes: bool, ask=input, interactive: bool | None = None) -> bool:
    """True when the run may go ahead. Without --yes it asks, and refuses when nobody can answer (no terminal)."""
    if assume_yes:
        return True
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        print("This spends money and nobody can be asked: pass --yes to go ahead.", file=sys.stderr)
        return False
    try:
        return ask("Run the benchmark now? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:                       # a terminal that cannot be read (piped, or a tool): the same as nobody to ask
        print("\nThis spends money and no answer could be read: pass --yes to go ahead.", file=sys.stderr)
        return False


def main(argv: list[str], verify=None, run=None, report=None, ask=input, interactive: bool | None = None) -> int:
    own, rest = parse(argv)
    if own.run and not own.report_only:
        print("--run names a run to report on: use it with --report-only. (To run only some questions, give their id words.)", file=sys.stderr)
        return 2
    if verify is None or run is None or report is None:            # imported late: they need the agent's packages and read .env
        from bench import report as report_mod, run_bench, verify_gold
        verify, run, report = verify_gold.main, run_bench.main, report_mod.main
    if own.report_only:
        return report([own.run] if own.run else [])
    print("== 1. checking every gold answer against the graph ==")
    if verify([]) != 0:
        print("\nA gold answer no longer matches the graph (or the graph is down); fix that before spending anything.", file=sys.stderr)
        return 1
    if own.verify_only:
        return 0
    print(f"\n== 2. running the benchmark ==\n{COST_NOTE}")
    if not confirm(own.yes, ask, interactive):
        print("Not run.")
        return 1
    code = run(rest)
    if code != 0:
        return code
    print("\n== 3. the report on this run ==")
    return report([])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
