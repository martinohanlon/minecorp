"""Load the authored MineCorp model (the player and the processes) into Neo4j (docs/ontology.md v0.11).

    python loader/load_model.py --dry-run   # validate and report, write nothing
    python loader/load_model.py             # validate, then load

Reads model/players.yaml and model/processes/*.yaml. The YAML files are the source of truth: Process and Activity
nodes are rebuilt on every run, and tasks are merged by id. A process is a goal a player works towards in the game
(get to the Nether), made of activities (stages) and tasks (steps). A task names the game actions it performs when
the game data has them, and has none when it is a manual step. A process can `builds_on` others (a list of process ids): it is a larger goal that needs them first (defeating the dragon
builds on getting to the Nether). A bad reference (unknown player, unknown action id,
unknown task in NEXT...) fails the load with every error listed, and nothing is written.

Run it after load_recipes.py and load_actions.py (it checks the action ids against the graph).
Players, COMPLETED history and anything else not defined in the YAML are left alone. The organization of earlier
versions (OrgUnit, Role) is removed from a database that still has it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from neo4j import GraphDatabase  # noqa: E402

from minecorp_agent.config import get_settings  # noqa: E402
from minecorp_agent.graph import Graph  # noqa: E402

MODEL_DIR = ROOT / "model"
STATUSES = {"candidate", "confirmed"}
TIERS = ["wooden", "stone", "copper", "iron", "diamond", "netherite"]
LEGACY_TASKS = ["task-craft-iron-pickaxe", "task-smelt-raw-iron", "task-craft-sticks"]  # early test data
LEGACY_PLAYER = "test-player-0001"


# --------------------------------------------------------------------------- read

def read_processes(directory: Path) -> list[dict]:
    """Read every process file in a directory (none is fine: the model may have no process yet)."""
    processes = []
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        doc["_file"] = path.name
        processes.append(doc)
    return processes


def read_model() -> tuple[dict, list[dict]]:
    """Read players.yaml and every process file."""
    people = yaml.safe_load((MODEL_DIR / "players.yaml").read_text(encoding="utf-8")) or {}
    return people, read_processes(MODEL_DIR / "processes")


# --------------------------------------------------------------------------- validate

def validate(people: dict, processes: list[dict], action_ids: set) -> list[str]:
    """Return every problem found; empty means the model is consistent."""
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    players = [p.get("name") for p in people.get("players", [])]
    if len(set(players)) != len(players):
        err("players.yaml: duplicate player name")
    for p in people.get("players", []):
        if not p.get("uuid") or not p.get("name"):
            err("players.yaml: every player needs a uuid and a name")

    seen_proc, seen_task = set(), {}
    for doc in processes:
        f = doc["_file"]
        proc = doc.get("process", {})
        pid = proc.get("id")
        if not pid or pid in seen_proc:
            err(f"{f}: process id missing or duplicated")
        seen_proc.add(pid)
        status = doc.get("status", "candidate")
        if status not in STATUSES:
            err(f"{f}: status must be one of {sorted(STATUSES)}")
        if status == "confirmed" and doc.get("confirmed_by") not in players:
            err(f"{f}: confirmed needs confirmed_by naming a player")
        if status == "candidate":
            for k in ("source", "extracted_at", "confidence"):
                if k not in doc:
                    err(f"{f}: a candidate needs {k}")
        tasks_here = set()
        seqs = []
        for a in doc.get("activities", []):
            seq = a.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
                err(f"{f}: activity {a.get('id')} seq must be a whole number of 1 or more")
            elif seq in seqs:
                err(f"{f}: activity {a.get('id')} repeats seq {seq}")
            seqs.append(seq)
            for t in a.get("tasks", []):
                tid = t.get("id")
                if not tid or tid in seen_task:
                    err(f"{f}: task id missing or duplicated: {tid}")
                seen_task[tid] = pid
                tasks_here.add(tid)
                for i, act in enumerate(t.get("actions", []), 1):
                    if act.get("do") not in action_ids:
                        err(f"{f}: task {tid} action {i} {act.get('do')} is not an action in the graph")
                    q = act.get("quantity", 1)
                    if not isinstance(q, int) or isinstance(q, bool) or q < 1:
                        err(f"{f}: task {tid} action {i} quantity must be a whole number of 1 or more")
        for n in doc.get("next", []):
            for end in ("from", "to"):
                if n.get(end) not in tasks_here:
                    err(f"{f}: next {end} {n.get(end)} is not a task in this process")
    known = {doc.get("process", {}).get("id") for doc in processes}
    for doc in processes:
        pid = doc.get("process", {}).get("id")
        wanted = doc.get("builds_on", [])
        if len(set(wanted)) != len(wanted):
            err(f"{doc['_file']}: builds_on lists a process twice")
        for other in wanted:
            if other == pid:
                err(f"{doc['_file']}: a process cannot build on itself")
            elif other not in known:
                err(f"{doc['_file']}: builds_on names an unknown process {other}")
    return errors


def tool_gaps(processes: list[dict], graph: Graph) -> list[str]:
    """Warn where a gathering action needs a tool that no action in the same process makes."""
    warnings = []
    for doc in processes:
        steps = [(t, act["do"]) for ac in doc.get("activities", []) for t in ac.get("tasks", []) for act in t.get("actions", [])]
        made = {r["id"] for r in graph.read(
            "MATCH (a:Action)-[:CREATES]->(i:Item) WHERE a.id IN $ids RETURN DISTINCT i.id AS id", ids=[a for _, a in steps])}
        for t, action_id in steps:
            need = graph.read(
                "MATCH (:Action {id: $id})-[:REQUIRES {role: 'tool'}]->(i:Item) RETURN i.id AS id, i.name AS name", id=action_id)
            for n in need:
                tier, _, kind = n["id"].replace("minecraft:", "").partition("_")
                ok = {f"minecraft:{x}_{kind}" for x in TIERS[TIERS.index(tier):]} if tier in TIERS else {n["id"]}
                if not (ok & made):
                    warnings.append(f"{t['id']}: needs a {n['name']} or better; no action in {doc['process']['id']} makes one")
    return warnings


# --------------------------------------------------------------------------- write

def rows(people: dict, processes: list[dict]) -> dict:
    """Flatten the YAML into rows for the Cypher statements."""
    out = {"players": people.get("players", []),
           "processes": [], "builds_on": [], "activities": [], "tasks": [], "task_actions": [], "nexts": [], "confirmations": []}
    for doc in processes:
        p = doc["process"]
        status = doc.get("status", "candidate")
        out["processes"].append({k: p.get(k) for k in ("id", "name", "description")})
        out["builds_on"] += [{"process": p["id"], "on": other} for other in doc.get("builds_on", [])]
        for a in doc.get("activities", []):
            out["activities"].append({"id": f"{p['id']}/{a['id']}", "process": p["id"], "name": a["name"], "seq": a["seq"]})
            for seq, t in enumerate(a.get("tasks", []), 1):
                out["tasks"].append({
                    "id": t["id"], "activity": f"{p['id']}/{a['id']}", "seq": seq, "name": t["name"],
                    "description": t.get("description"), "status": status,
                    "source": doc.get("source"), "extractedAt": str(doc["extracted_at"]) if doc.get("extracted_at") else None,
                    "confidence": doc.get("confidence")})
                for order, act in enumerate(t.get("actions", []), 1):
                    out["task_actions"].append({"task": t["id"], "action": act["do"], "order": order,
                                                "quantity": act.get("quantity", 1)})
                if status == "confirmed":
                    out["confirmations"].append({"task": t["id"], "player": doc["confirmed_by"], "at": str(doc.get("confirmed_at"))})
        for n in doc.get("next", []):
            out["nexts"].append({"from": n["from"], "to": n["to"], "condition": n.get("condition"),
                                 "outcome": n.get("outcome"), "isDefault": bool(n.get("default", False))})
    return out


def write(r: dict, uri: str, user: str, password: str, database: str) -> list[str]:
    """Rebuild the authored layer from the rows. Returns warnings about stale tasks."""
    notes: list[str] = []
    task_ids = [t["id"] for t in r["tasks"]]
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as s:
            for label, key in (("Process", "id"), ("Activity", "id"), ("Task", "id"), ("Player", "uuid")):
                s.run(f"CREATE CONSTRAINT {label.lower()}_{key} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")
            for old in ("orgunit_id", "role_id"):                 # the organization of earlier versions
                s.run(f"DROP CONSTRAINT {old} IF EXISTS")

            # early test data from the recipe loader
            s.run("MATCH (t:Task) WHERE t.id IN $ids DETACH DELETE t", ids=LEGACY_TASKS)
            s.run("MATCH (p:Player {uuid: $u}) DETACH DELETE p", u=LEGACY_PLAYER)
            # tasks no longer in the YAML: remove, unless they hold completion history
            stale = s.run("MATCH (t:Task) WHERE NOT t.id IN $ids RETURN t.id AS id, EXISTS { (:Player)-[:COMPLETED]->(t) } AS used",
                          ids=task_ids).data()
            for t in stale:
                if t["used"]:
                    notes.append(f"task {t['id']} is not in the YAML but has COMPLETED history; kept")
                else:
                    s.run("MATCH (t:Task {id: $id}) DETACH DELETE t", id=t["id"])
                    notes.append(f"removed stale task {t['id']}")

            s.run("MATCH (n) WHERE n:OrgUnit OR n:Role OR n:Process OR n:Activity DETACH DELETE n")
            s.run("""MATCH (t:Task)-[x:NEXT|HANDOFF_TO|HAS_ACTION|CONFIRMED_BY|CRAFTED|HARVESTED|USED_RECIPE|USED_HARVEST]->() WHERE t.id IN $ids DELETE x""",
                  ids=task_ids)
            s.run("MATCH (t:Task) REMOVE t.automatable")

            s.run("UNWIND $rows AS r MERGE (p:Player {uuid: r.uuid}) SET p.name = r.name", rows=r["players"])
            s.run("UNWIND $rows AS r CREATE (:Process {id: r.id, name: r.name, description: r.description})", rows=r["processes"])
            s.run("UNWIND $rows AS r MATCH (a:Process {id: r.process}), (b:Process {id: r.on}) CREATE (a)-[:BUILDS_ON]->(b)", rows=r["builds_on"])
            s.run("""UNWIND $rows AS r CREATE (a:Activity {id: r.id, name: r.name})
                     WITH a, r MATCH (p:Process {id: r.process}) CREATE (p)-[:HAS_ACTIVITY {seq: r.seq}]->(a)""", rows=r["activities"])
            s.run("""UNWIND $rows AS r MERGE (t:Task {id: r.id})
                     SET t.name = r.name, t.description = r.description, t.status = r.status,
                         t.source = r.source, t.extractedAt = r.extractedAt, t.confidence = r.confidence
                     REMOVE t.type
                     WITH t, r
                     MATCH (a:Activity {id: r.activity})
                     CREATE (a)-[:HAS_TASK {seq: r.seq}]->(t)""", rows=r["tasks"])
            s.run("""UNWIND $rows AS r MATCH (t:Task {id: r.task}), (a:Action {id: r.action})
                     CREATE (t)-[:HAS_ACTION {order: r.order, quantity: r.quantity}]->(a)""", rows=r["task_actions"])
            s.run("""UNWIND $rows AS r MATCH (a:Task {id: r.from}), (b:Task {id: r.to})
                     CREATE (a)-[:NEXT {condition: r.condition, outcome: r.outcome, isDefault: r.isDefault}]->(b)""", rows=r["nexts"])
            s.run("""UNWIND $rows AS r MATCH (a:Task {id: r.task}), (p:Player {name: r.player})
                     CREATE (a)-[:CONFIRMED_BY {at: date(r.at)}]->(p)""", rows=r["confirmations"])
    return notes


def main() -> int:
    """Validate the YAML model against the graph, then load it."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    args = ap.parse_args()

    s = get_settings()
    graph = Graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    action_ids = {r["id"] for r in graph.read("MATCH (a:Action) RETURN a.id AS id")}
    if not action_ids:
        sys.exit("No actions in the graph. Run load_recipes.py --all and load_actions.py first.")

    people, processes = read_model()
    errors = validate(people, processes, action_ids)
    if errors:
        print(f"{len(errors)} problem(s) in the model; nothing written:")
        for e in errors:
            print("  -", e)
        return 1

    r = rows(people, processes)
    print(f"model OK: {len(r['players'])} player(s), {len(r['processes'])} process(es), {len(r['activities'])} activities, "
          f"{len(r['tasks'])} tasks, {len(r['task_actions'])} task actions, {len(r['nexts'])} NEXT")
    for w in tool_gaps(processes, graph):
        print("  note:", w)
    if args.dry_run:
        return 0
    for n in write(r, s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database):
        print("  ", n)
    print("loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
