"""Check every gold answer against the live graph: run each question's gold_query and compare it with its "gold".

    cd agent
    python -m bench.verify_gold

Free (no model calls). Exits 1 when a query returns a different value, no row, or more than one distinct value. A gold that has a note after the
value ("Raw Iron (also ...)") is compared on the text before the first " (".
"""
from __future__ import annotations

import json
import sys

from minecorp_agent.config import get_settings
from minecorp_agent.graph import Graph

from bench.run_bench import QUESTIONS


def main(argv: list[str]) -> int:
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    s = get_settings()
    graph = Graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    graph.verify()
    bad = 0
    for c in cases:
        rows = graph.read(c["gold_query"])
        values = sorted({str(r["gold"]) for r in rows})
        want = c["gold"].split(" (")[0].strip()
        ok = values == [want]
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'}  {c['id']:<24} {c['stratum']:<20} gold={c['gold']!r}" + ("" if ok else f"  graph={values}"))
    print(f"\n{len(cases) - bad}/{len(cases)} gold answers match the graph")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
