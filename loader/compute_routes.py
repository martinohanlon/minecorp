"""Compute route properties (docs/ontology.md v0.5) for every craftable item and store them in Neo4j.

    python loader/compute_routes.py            # compute and write
    python loader/compute_routes.py --dry-run  # compute and print the extremes only

Re-run after every `load_recipes.py` load (a --wipe removes the properties). Uses the same Route
calculation as the what_do_i_need agent tool, so the numbers match what the agent says. This is a
write job; the agent itself only reads.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from neo4j import GraphDatabase  # noqa: E402,F401

from quiet_driver import open_driver  # noqa: E402

from minecorp_agent.config import get_settings  # noqa: E402
from minecorp_agent.graph import Graph  # noqa: E402
from minecorp_agent.tools import Route  # noqa: E402

MAX_DEPTH = 6


def main() -> int:
    """Compute the route summary for each craftable concrete item and write it to its Item node."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="do not write to Neo4j")
    args = ap.parse_args()

    s = get_settings()
    graph = Graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    items = graph.read(
        """
        MATCH (i:Item)<-[:CREATES]-(:Recipe) WHERE NOT i:ItemClass
        RETURN DISTINCT i.id AS id, i.name AS name ORDER BY id
        """)
    print(f"{len(items)} craftable items")

    shared: dict = {}
    rows, started = [], time.monotonic()
    for n, item in enumerate(items, 1):
        route = Route(graph, MAX_DEPTH, shared)
        route.build(item["id"], item["name"], 1, frozenset(), 0)
        if not route.steps:
            continue  # only special or looping recipes: no usable route
        o = route.overview()
        rows.append({"id": item["id"], "name": item["name"], "routeSteps": o["craftingSteps"],
                     "routeLevels": o["chainLength"], "routeBaseMaterials": o["baseMaterials"],
                     "routeStations": o["stations"], "routeTruncated": o["truncated"]})
        if n % 200 == 0:
            print(f"  {n}/{len(items)} ({int(time.monotonic() - started)} s)")
    print(f"computed {len(rows)} routes in {int(time.monotonic() - started)} s; "
          f"{sum(r['routeTruncated'] for r in rows)} truncated at depth {MAX_DEPTH}")

    ranked = sorted((r for r in rows if not r["routeTruncated"]),
                    key=lambda r: (-r["routeSteps"], -r["routeBaseMaterials"], -r["routeLevels"], r["name"]))
    print("hardest:", [(r["name"], r["routeSteps"], r["routeBaseMaterials"]) for r in ranked[:8]])

    if args.dry_run:
        return 0
    # write with a separate, write-capable driver (Graph is read-only)
    with open_driver(s.neo4j_uri, s.neo4j_user, s.neo4j_password) as driver:
        with driver.session(database=s.neo4j_database) as session:
            session.run("MATCH (i:Item) WHERE i.routeSteps IS NOT NULL "
                        "REMOVE i.routeSteps, i.routeLevels, i.routeBaseMaterials, i.routeStations, i.routeTruncated")
            session.run(
                """
                UNWIND $rows AS r MATCH (i:Item {id: r.id})
                SET i.routeSteps = r.routeSteps, i.routeLevels = r.routeLevels,
                    i.routeBaseMaterials = r.routeBaseMaterials, i.routeStations = r.routeStations,
                    i.routeTruncated = r.routeTruncated
                """, rows=rows)
    print("written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
