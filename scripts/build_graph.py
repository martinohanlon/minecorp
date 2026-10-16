"""Build the whole MineCorp graph from source: the Paper jar, model/ and the wiki page list.

    python scripts/build_graph.py              # ask, then rebuild everything
    python scripts/build_graph.py --list       # show the steps and what each one needs; change nothing
    python scripts/build_graph.py --no-wiki    # the game graph only: no OpenAI, no network
    python scripts/build_graph.py --from model # carry on from a step after a failure (no wipe)
    python scripts/build_graph.py --yes        # do not ask

(or ./scripts/build-graph.ps1 and ./scripts/build-graph.sh, which find the project's Python.)

The first step wipes the WHOLE database named in .env (NEO4J_URI, NEO4J_DATABASE): the game graph, the model and the wiki graph. The
graph can be rebuilt from source at any time, which is what this does (see loader/README.md); agent memory and completed history, which
cannot be rebuilt, do not exist yet. Settings come from the environment, and from .env for anything not already set there (the loaders
do not read .env themselves). Nothing is printed from .env except the address and database name.

The steps run in the order the graph needs (docs/ontology.md): recipes, actions, world, trades, model, routes, then the wiki and its links.
It stops at the first step that fails and tells you how to carry on from there."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Step:
    key: str
    script: str
    args: tuple[str, ...]
    what: str
    needs: str = ""          # beyond Neo4j and the jar
    wiki: bool = False       # part of the wiki graph (left out by --no-wiki)


STEPS = [
    Step("recipes", "load_recipes.py", ("--all", "--wipe"), "wipe the database; items, item tags and recipes from the jar"),
    Step("actions", "load_actions.py", (), "Mine, Kill, Shear, Craft, Smelt, Smith and Cut actions from the jar"),
    Step("world", "load_world.py", (), "mobs, biomes, structures, dimensions and ore placement from the jar"),
    Step("trades", "load_trades.py", (), "villager and wandering trader trades from the jar"),
    Step("model", "load_model.py", (), "the player and the processes, from model/*.yaml"),
    Step("routes", "compute_routes.py", (), "route properties on every craftable item (for rank_recipes)"),
    Step("wiki", "load_wiki.py", ("--pages",), "the wiki pages in model/wiki_pages.yaml as sections and embedded chunks",
         needs="network and OPENAI_API_KEY (free for pages in the embedding cache)", wiki=True),
    Step("links", "link_wiki.py", (), "link wiki pages and chunks to the game graph", wiki=True),
]
KEYS = [s.key for s in STEPS]


def read_dotenv(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file: comments and blank lines skipped, one pair of quotes removed. Missing file: nothing."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def environment(base: dict[str, str], dotenv: dict[str, str]) -> dict[str, str]:
    """The environment for the loaders: .env fills what the environment has not set (an empty value counts as not set)."""
    env = dict(base)
    for key, value in dotenv.items():
        if not env.get(key):
            env[key] = value
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def plan(from_step: str | None = None, only_game: bool = False) -> list[Step]:
    """The steps to run, in order: from `from_step` on, without the wiki steps when `only_game`."""
    if from_step is not None and from_step not in KEYS:
        raise ValueError(f"unknown step {from_step!r}; the steps are: {', '.join(KEYS)}")
    steps = STEPS[KEYS.index(from_step):] if from_step else list(STEPS)
    return [s for s in steps if not (only_game and s.wiki)]


def command(step: Step) -> list[str]:
    return [sys.executable, str(ROOT / "loader" / step.script), *step.args]


def describe(steps: list[Step]) -> str:
    lines = []
    for n, s in enumerate(steps, 1):
        lines.append(f"  {n}. {s.key:8s} {s.what}" + (f"  [needs {s.needs}]" if s.needs else ""))
    return "\n".join(lines)


def confirm(target: str, steps: list[Step], ask=None) -> bool:
    if not any(s.key == "recipes" for s in steps):
        return True                    # nothing is wiped when the first step is not run
    print(f"This WIPES everything in {target} and rebuilds it (agent memory too, if it lives in the same database):\n{describe(steps)}")
    return (ask or input)("Type yes to go on: ").strip().lower() == "yes"


def run(steps: list[Step], env: dict[str, str], runner=subprocess.run, clock=time.monotonic, out=print) -> int:
    """Runs each step; returns 0, or the exit status of the step that failed (the rest are not run)."""
    began = clock()
    for n, s in enumerate(steps, 1):
        out(f"\n== {n}/{len(steps)} {s.key}: {s.what}")
        started = clock()
        result = runner(command(s), cwd=ROOT, env=env)
        if result.returncode != 0:
            out(f"\nStep '{s.key}' failed (exit {result.returncode}). Fix it, then carry on with:  "
                f"python scripts/build_graph.py --from {s.key}" + (" --no-wiki" if not any(t.wiki for t in steps) else ""))
            return result.returncode
        out(f"   done in {clock() - started:.0f} s")
    out(f"\nThe graph is built ({clock() - began:.0f} s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the whole MineCorp graph from source.", epilog="Steps: " + ", ".join(KEYS))
    ap.add_argument("--list", action="store_true", help="show the steps and change nothing")
    ap.add_argument("--no-wiki", action="store_true", help="the game graph only (no wiki, no OpenAI, no network)")
    ap.add_argument("--from", dest="from_step", metavar="STEP", help="start at this step; the database is not wiped unless it is 'recipes'")
    ap.add_argument("--yes", action="store_true", help="do not ask before wiping")
    a = ap.parse_args(argv)

    try:
        steps = plan(a.from_step, a.no_wiki)
    except ValueError as e:
        ap.error(str(e))
    env = environment(dict(os.environ), read_dotenv(ROOT / ".env"))
    target = f"{env.get('NEO4J_URI', 'bolt://localhost:7687')} (database {env.get('NEO4J_DATABASE', 'neo4j')})"
    if a.list:
        print(f"Target: {target}\n{describe(steps)}")
        return 0
    if not a.yes and not confirm(target, steps):
        print("Nothing changed.")
        return 1
    if not env.get("NEO4J_PASSWORD"):
        print("NEO4J_PASSWORD is not set (in the environment or .env).", file=sys.stderr)
        return 2
    if any(s.key == "wiki" for s in steps) and not env.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (in the environment or .env); the wiki step needs it. Use --no-wiki to skip the wiki.", file=sys.stderr)
        return 2
    return run(steps, env)


if __name__ == "__main__":
    sys.exit(main())
