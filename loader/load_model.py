"""Load the authored MineCorp model (organization and processes) into Neo4j (docs/ontology.md v0.6).

    python loader/load_model.py --dry-run   # validate and report, write nothing
    python loader/load_model.py             # validate, then load

Reads model/org.yaml and model/processes/*.yaml. The YAML files are the source of truth for the
organization and the processes: OrgUnit, Role, Process and Activity nodes are rebuilt on every run, and
tasks are merged by id. A bad reference (unknown role, recipe or harvest id, unknown task in NEXT...)
fails the load with every error listed, and nothing is written.

Run it after load_recipes.py and load_harvests.py (it checks recipe and harvest ids against the graph).
Players, COMPLETED history and anything else not defined in the YAML are left alone.
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
ROLE_KINDS = {"operational", "governance"}
FAMILIES = {"collect", "craft", "inventory"}
STATUSES = {"candidate", "confirmed"}
TIERS = ["wooden", "stone", "copper", "iron", "diamond", "netherite"]
LEGACY_TASKS = ["task-craft-iron-pickaxe", "task-smelt-raw-iron", "task-craft-sticks"]  # early test data
LEGACY_PLAYER = "test-player-0001"


# --------------------------------------------------------------------------- read

def read_model() -> tuple[dict, list[dict]]:
    """Read org.yaml and every process file."""
    org = yaml.safe_load((MODEL_DIR / "org.yaml").read_text(encoding="utf-8")) or {}
    processes = []
    for path in sorted((MODEL_DIR / "processes").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        doc["_file"] = path.name
        processes.append(doc)
    return org, processes


# --------------------------------------------------------------------------- validate

def validate(org: dict, processes: list[dict], recipe_ids: set, harvest_ids: set) -> list[str]:
    """Return every problem found; empty means the model is consistent."""
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    units = {u["id"] for u in org.get("org_units", [])}
    if len(units) != len(org.get("org_units", [])):
        err("org.yaml: duplicate org unit id")
    for u in org.get("org_units", []):
        if u.get("part_of") and u["part_of"] not in units:
            err(f"org.yaml: unit {u['id']} is part_of unknown unit {u['part_of']}")
    roles = {r["id"]: r for r in org.get("roles", [])}
    if len(roles) != len(org.get("roles", [])):
        err("org.yaml: duplicate role id")
    for r in roles.values():
        if r.get("unit") not in units:
            err(f"org.yaml: role {r['id']} is in unknown unit {r.get('unit')}")
        if r.get("kind") not in ROLE_KINDS:
            err(f"org.yaml: role {r['id']} kind must be one of {sorted(ROLE_KINDS)}")
    players = {p["name"] for p in org.get("players", [])}
    for f in org.get("filled_by", []):
        if f.get("player") not in players:
            err(f"org.yaml: filled_by names unknown player {f.get('player')}")
        if f.get("roles") != "all":
            for rid in f.get("roles", []):
                if rid not in roles:
                    err(f"org.yaml: filled_by names unknown role {rid}")

    seen_proc, seen_task = set(), {}
    for doc in processes:
        f = doc["_file"]
        proc = doc.get("process", {})
        pid = proc.get("id")
        if not pid or pid in seen_proc:
            err(f"{f}: process id missing or duplicated")
        seen_proc.add(pid)
        if proc.get("family") not in FAMILIES:
            err(f"{f}: family must be one of {sorted(FAMILIES)}")
        if proc.get("owner") not in roles:
            err(f"{f}: owner {proc.get('owner')} is not a role")
        status = doc.get("status", "candidate")
        if status not in STATUSES:
            err(f"{f}: status must be one of {sorted(STATUSES)}")
        if status == "confirmed" and doc.get("confirmed_by") not in roles:
            err(f"{f}: confirmed needs confirmed_by naming a role")
        if status == "candidate":
            for k in ("source", "extracted_at", "confidence"):
                if k not in doc:
                    err(f"{f}: a candidate needs {k}")
        tasks_here = set()
        for a in doc.get("activities", []):
            for t in a.get("tasks", []):
                tid = t.get("id")
                if not tid or tid in seen_task:
                    err(f"{f}: task id missing or duplicated: {tid}")
                seen_task[tid] = pid
                tasks_here.add(tid)
                if t.get("role") not in roles:
                    err(f"{f}: task {tid} role {t.get('role')} is not a role")
                if t.get("recipe") and t.get("harvest"):
                    err(f"{f}: task {tid} has both recipe and harvest")
                if t.get("recipe") and t["recipe"] not in recipe_ids:
                    err(f"{f}: task {tid} recipe {t['recipe']} is not in the graph")
                if t.get("harvest") and t["harvest"] not in harvest_ids:
                    err(f"{f}: task {tid} harvest {t['harvest']} is not in the graph")
                if t.get("escalates_to") and t["escalates_to"] not in roles:
                    err(f"{f}: task {tid} escalates_to unknown role {t['escalates_to']}")
        for n in doc.get("next", []):
            for end in ("from", "to"):
                if n.get(end) not in tasks_here:
                    err(f"{f}: next {end} {n.get(end)} is not a task in this process")
    return errors


def tool_gaps(processes: list[dict], graph: Graph) -> list[str]:
    """Warn where a mining task needs a tool that no task in the same process makes."""
    warnings = []
    for doc in processes:
        recipes = [t["recipe"] for a in doc.get("activities", []) for t in a.get("tasks", []) if t.get("recipe")]
        made = {r["id"] for r in graph.read(
            "MATCH (r:Recipe)-[:CREATES]->(i:Item) WHERE r.id IN $ids RETURN DISTINCT i.id AS id", ids=recipes)}
        for a in doc.get("activities", []):
            for t in a.get("tasks", []):
                if not t.get("harvest"):
                    continue
                need = graph.read(
                    """MATCH (:Harvest {id: $id})-[:REQUIRES {role: 'tool'}]->(i:Item) RETURN i.id AS id, i.name AS name""",
                    id=t["harvest"])
                for n in need:
                    tier, _, kind = n["id"].replace("minecraft:", "").partition("_")
                    ok = {f"minecraft:{x}_{kind}" for x in TIERS[TIERS.index(tier):]} if tier in TIERS else {n["id"]}
                    if not (ok & made):
                        warnings.append(f"{t['id']}: needs a {n['name']} or better; no task in {doc['process']['id']} makes one")
    return warnings


# --------------------------------------------------------------------------- write

def rows(org: dict, processes: list[dict]) -> dict:
    """Flatten the YAML into rows for the Cypher statements."""
    role_ids = [r["id"] for r in org.get("roles", [])]
    fills = []
    for f in org.get("filled_by", []):
        for rid in (role_ids if f["roles"] == "all" else f["roles"]):
            fills.append({"role": rid, "player": f["player"], "from": str(f["from"])})
    out = {
        "players": org.get("players", []),
        "units": org.get("org_units", []),
        "roles": org.get("roles", []),
        "fills": fills,
        "processes": [], "activities": [], "tasks": [], "nexts": [], "handoffs": [], "escalations": [], "confirmations": [],
    }
    for doc in processes:
        p = doc["process"]
        status = doc.get("status", "candidate")
        out["processes"].append({**{k: p.get(k) for k in ("id", "name", "description", "family", "criticality", "sla", "owner")}})
        role_of = {}
        for a in doc.get("activities", []):
            out["activities"].append({"id": f"{p['id']}/{a['id']}", "process": p["id"], "name": a["name"], "seq": a["seq"]})
            for t in a.get("tasks", []):
                role_of[t["id"]] = t["role"]
                kind = "recipe" if t.get("recipe") else "harvest" if t.get("harvest") else "manual"
                out["tasks"].append({
                    "id": t["id"], "activity": f"{p['id']}/{a['id']}", "name": t["name"],
                    "description": t.get("description"), "role": t["role"], "type": kind,
                    "recipe": t.get("recipe"), "harvest": t.get("harvest"),
                    "automatable": bool(t.get("automatable", False)), "status": status,
                    "source": doc.get("source"), "extractedAt": str(doc["extracted_at"]) if doc.get("extracted_at") else None,
                    "confidence": doc.get("confidence")})
                if t.get("escalates_to"):
                    out["escalations"].append({"task": t["id"], "role": t["escalates_to"], "reason": t.get("escalation_reason")})
                if status == "confirmed":
                    out["confirmations"].append({"task": t["id"], "role": doc["confirmed_by"], "at": str(doc.get("confirmed_at"))})
        for n in doc.get("next", []):
            out["nexts"].append({"from": n["from"], "to": n["to"], "condition": n.get("condition"),
                                 "outcome": n.get("outcome"), "isDefault": bool(n.get("default", False))})
            if role_of[n["from"]] != role_of[n["to"]]:
                out["handoffs"].append({"from": n["from"], "to": n["to"]})
    return out


def write(r: dict, uri: str, user: str, password: str, database: str) -> list[str]:
    """Rebuild the authored layer from the rows. Returns warnings about stale tasks."""
    notes: list[str] = []
    task_ids = [t["id"] for t in r["tasks"]]
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as s:
            for label, key in (("OrgUnit", "id"), ("Role", "id"), ("Process", "id"), ("Activity", "id"),
                               ("Task", "id"), ("Player", "uuid")):
                s.run(f"CREATE CONSTRAINT {label.lower()}_{key} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

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
            s.run("""MATCH (t:Task)-[x:NEXT|HANDOFF_TO|ESCALATES_TO|CRAFTED|HARVESTED|USED_RECIPE|USED_HARVEST]->() WHERE t.id IN $ids DELETE x""",
                  ids=task_ids)

            s.run("UNWIND $rows AS r MERGE (p:Player {uuid: r.uuid}) SET p.name = r.name", rows=r["players"])
            s.run("UNWIND $rows AS r CREATE (:OrgUnit {id: r.id, name: r.name, description: r.description})", rows=r["units"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.part_of IS NOT NULL
                     MATCH (a:OrgUnit {id: r.id}), (b:OrgUnit {id: r.part_of}) CREATE (a)-[:PART_OF]->(b)""", rows=r["units"])
            s.run("""UNWIND $rows AS r CREATE (n:Role {id: r.id, name: r.name, kind: r.kind, description: r.description})
                     WITH n, r MATCH (u:OrgUnit {id: r.unit}) CREATE (n)-[:IN_UNIT]->(u)""", rows=r["roles"])
            s.run("""UNWIND $rows AS r MATCH (ro:Role {id: r.role}), (p:Player {name: r.player})
                     CREATE (ro)-[:FILLED_BY {from: date(r.from)}]->(p)""", rows=r["fills"])
            s.run("""UNWIND $rows AS r CREATE (n:Process {id: r.id, name: r.name, description: r.description,
                         family: r.family, criticality: r.criticality, sla: r.sla})
                     WITH n, r MATCH (ro:Role {id: r.owner}) CREATE (ro)-[:OWNS]->(n)""", rows=r["processes"])
            s.run("""UNWIND $rows AS r CREATE (a:Activity {id: r.id, name: r.name, seq: r.seq})
                     WITH a, r MATCH (p:Process {id: r.process}) CREATE (p)-[:HAS_ACTIVITY]->(a)""", rows=r["activities"])
            s.run("""UNWIND $rows AS r MERGE (t:Task {id: r.id})
                     SET t.name = r.name, t.description = r.description, t.type = r.type, t.status = r.status,
                         t.automatable = r.automatable, t.source = r.source, t.extractedAt = r.extractedAt,
                         t.confidence = r.confidence
                     WITH t, r
                     MATCH (a:Activity {id: r.activity}), (ro:Role {id: r.role})
                     CREATE (a)-[:HAS_TASK]->(t) CREATE (ro)-[:PERFORMS]->(t)""", rows=r["tasks"])
            s.run("UNWIND $rows AS r WITH r WHERE r.recipe IS NOT NULL MATCH (t:Task {id: r.id}), (x:Recipe {id: r.recipe}) "
                  "CREATE (t)-[:CRAFTED]->(x)", rows=r["tasks"])
            s.run("UNWIND $rows AS r WITH r WHERE r.harvest IS NOT NULL MATCH (t:Task {id: r.id}), (x:Harvest {id: r.harvest}) "
                  "CREATE (t)-[:HARVESTED]->(x)", rows=r["tasks"])
            s.run("""UNWIND $rows AS r MATCH (a:Task {id: r.from}), (b:Task {id: r.to})
                     CREATE (a)-[:NEXT {condition: r.condition, outcome: r.outcome, isDefault: r.isDefault}]->(b)""", rows=r["nexts"])
            s.run("UNWIND $rows AS r MATCH (a:Task {id: r.from}), (b:Task {id: r.to}) CREATE (a)-[:HANDOFF_TO]->(b)", rows=r["handoffs"])
            s.run("""UNWIND $rows AS r MATCH (a:Task {id: r.task}), (ro:Role {id: r.role})
                     CREATE (a)-[:ESCALATES_TO {reason: r.reason}]->(ro)""", rows=r["escalations"])
            s.run("""UNWIND $rows AS r MATCH (a:Task {id: r.task}), (ro:Role {id: r.role})
                     CREATE (a)-[:CONFIRMED_BY {at: date(r.at)}]->(ro)""", rows=r["confirmations"])
    return notes


def main() -> int:
    """Validate the YAML model against the graph, then load it."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    args = ap.parse_args()

    s = get_settings()
    graph = Graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    recipe_ids = {r["id"] for r in graph.read("MATCH (r:Recipe) RETURN r.id AS id")}
    harvest_ids = {r["id"] for r in graph.read("MATCH (h:Harvest) RETURN h.id AS id")}
    if not recipe_ids:
        sys.exit("No recipes in the graph. Run load_recipes.py --all and load_harvests.py first.")

    org, processes = read_model()
    errors = validate(org, processes, recipe_ids, harvest_ids)
    if errors:
        print(f"{len(errors)} problem(s) in the model; nothing written:")
        for e in errors:
            print("  -", e)
        return 1

    r = rows(org, processes)
    print(f"model OK: {len(r['units'])} org units, {len(r['roles'])} roles, {len(r['fills'])} role fills, "
          f"{len(r['processes'])} process(es), {len(r['activities'])} activities, {len(r['tasks'])} tasks, "
          f"{len(r['nexts'])} NEXT, {len(r['handoffs'])} handoffs, {len(r['escalations'])} escalations")
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
