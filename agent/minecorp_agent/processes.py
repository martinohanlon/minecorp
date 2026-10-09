"""The process tool: `get_process`, plans for what the game's data does not spell out.

A process is a goal a player works towards (get to the Nether, defeat the ender dragon), made of activities (stages) and tasks
(steps). The graph holds it as

    (Process)-[:HAS_ACTIVITY {seq}]->(Activity)-[:HAS_TASK {seq}]->(Task)-[:NEXT {condition, isDefault}]->(Task)
    (Task)-[:HAS_ACTION {order, quantity}]->(Action)        a step that is something the game can do (mine, craft, trade...)
    (Process)-[:BUILDS_ON]->(Process)                       a bigger goal that needs a smaller one first

A task with no action is a manual step (build the frame). Every process is a candidate drafted from the wiki until a player
confirms it, so the tool says so and the model gives it as a plan, not as a fact.

Called with no name it lists the processes. With a name it gives the usual route (following the default NEXT from the first
task) and the alternatives (the other NEXT edges). With a step too it gives that one step in full, because the descriptions are
long and the whole of a process would not fit in one result.
"""
from __future__ import annotations

from .graph import Graph

MAX_CANDIDATES = 4
MAX_STEP_CANDIDATES = 6

CANDIDATE_NOTE = ("This plan is a candidate: it was drafted from the Minecraft Wiki and the game data and has not been confirmed "
                  "by the player. Give it as a plan, not as a fact.")
HOW_TO_READ = ("'usualRoute' is the normal way through, in order; 'alternatives' says where it can branch and why. A step in "
               "'by hand' has no game action. Call get_process again with 'step' for a step in full, and use how_to_get, "
               "where_to_find or get_recipes for the items and places a step names.")


# --------------------------------------------------------------------------- reading the graph

def load_processes(graph: Graph) -> dict[str, dict]:
    """Every process with its tasks (in order), the actions of each task and the next-step edges, keyed by process id."""
    processes = {p["id"]: {**p, "tasks": [], "next": {}, "buildsOn": []} for p in graph.read(
        "MATCH (p:Process) RETURN p.id AS id, p.name AS name, p.description AS about ORDER BY p.name")}
    if not processes:
        return {}
    for r in graph.read("""
            MATCH (p:Process)-[ha:HAS_ACTIVITY]->(a:Activity)-[ht:HAS_TASK]->(t:Task)
            OPTIONAL MATCH (t)-[h:HAS_ACTION]->(x:Action)
            WITH p, a, ha, t, ht, x, h ORDER BY h.order
            RETURN p.id AS process, a.name AS activity, ha.seq AS activitySeq, t.id AS id, t.name AS name, t.description AS description,
                   ht.seq AS seq, t.status AS status, t.source AS source, t.confidence AS confidence,
                   collect(CASE WHEN x IS NULL THEN null ELSE {name: x.name, quantity: h.quantity} END) AS actions
            ORDER BY process, activitySeq, seq"""):
        processes[r["process"]]["tasks"].append({**r, "actions": [a for a in r["actions"] if a]})
    for r in graph.read("""
            MATCH (a:Task)-[n:NEXT]->(b:Task)
            RETURN a.id AS fromId, b.id AS toId, b.name AS toName, n.condition AS condition, n.isDefault AS usual"""):
        for p in processes.values():
            if any(t["id"] == r["fromId"] for t in p["tasks"]):
                p["next"].setdefault(r["fromId"], []).append(r)
                break
    for r in graph.read("MATCH (a:Process)-[:BUILDS_ON]->(b:Process) RETURN a.id AS id, b.name AS name ORDER BY name"):
        processes[r["id"]]["buildsOn"].append(r["name"])
    return processes


def action_text(a: dict) -> str:
    """'Mine Obsidian x10' (a quantity of one is not written)."""
    q = a.get("quantity")
    return a["name"] + (f" x{q}" if q and q > 1 else "")


def status_of(process: dict) -> str:
    return next((t["status"] for t in process["tasks"] if t.get("status")), "candidate")


# --------------------------------------------------------------------------- finding a process or a step

def words_of(text: str) -> list[str]:
    """The words of a search text, lowercase, with a plural 's' removed from longer words so 'diamonds' finds 'diamond'."""
    out = []
    for w in text.lower().replace("-", " ").replace("_", " ").replace("/", " ").split():
        w = w.strip(".,?!'\"")
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w:
            out.append(w)
    return out


def score(words: list[str], name: str, id_: str, about: str) -> int | None:
    """Lower is better: 0 the name or id as written, 1 every word in the name or id, 2 every word in the text, None no match."""
    if not words:
        return None
    n, i, a = " ".join(words_of(name)), " ".join(words_of(id_)), " ".join(words_of(about or ""))
    if " ".join(words) in (n, i):
        return 0
    if all(w in n or w in i for w in words):
        return 1
    if all(w in n or w in i or w in a for w in words):
        return 2
    return None


def find_process(processes: dict[str, dict], text: str) -> tuple[dict | None, dict | None]:
    """The process a name means, or (None, error for the model)."""
    words = words_of(text)
    ranked = sorted(((score(words, p["name"], p["id"], p["about"]), p["name"], p) for p in processes.values()
                     if score(words, p["name"], p["id"], p["about"]) is not None), key=lambda x: (x[0], x[1]))
    if not ranked:
        return None, {"error": f"No process matches '{text}'.", "type": "bad_argument",
                      "processes": [p["name"] for p in processes.values()]}
    best = [p for s, _n, p in ranked if s == ranked[0][0]]
    if len(best) > 1:
        return None, {"error": f"'{text}' fits several processes. Pick one and call again with its name.", "type": "bad_argument",
                      "candidates": [p["name"] for p in best[:MAX_CANDIDATES]]}
    return best[0], None


def find_step(tasks: list[tuple[dict, dict]], text: str) -> tuple[tuple[dict, dict] | None, dict | None]:
    """The (process, task) a step name means, or (None, error for the model)."""
    words = words_of(text)
    ranked = sorted(((score(words, t["name"], t["id"].split("/", 1)[-1], t["description"]), t["name"], (p, t)) for p, t in tasks
                     if score(words, t["name"], t["id"].split("/", 1)[-1], t["description"]) is not None), key=lambda x: (x[0], x[1]))
    if not ranked:
        return None, {"error": f"No step matches '{text}'.", "type": "bad_argument", "steps": [t["name"] for _p, t in tasks][:30]}
    best = [pt for s, _n, pt in ranked if s == ranked[0][0]]
    if len(best) > 1:
        return None, {"error": f"'{text}' fits several steps. Pick one and call again.", "type": "bad_argument",
                      "candidates": [f"{p['name']}: {t['name']}" for p, t in best[:MAX_STEP_CANDIDATES]]}
    return best[0], None


# --------------------------------------------------------------------------- the views

def usual_route(process: dict) -> list[dict]:
    """The tasks in the normal order: from the first task, following the default next step each time."""
    by_id = {t["id"]: t for t in process["tasks"]}
    route, seen = [], set()
    current = process["tasks"][0]["id"] if process["tasks"] else None
    while current and current not in seen:
        seen.add(current)
        route.append(by_id[current])
        usual = [n for n in process["next"].get(current, []) if n["usual"]]
        current = usual[0]["toId"] if usual else None
    return route


def outline(process: dict) -> dict:
    """The whole process in short: the usual route, where it can branch, and every step by stage."""
    route = usual_route(process)
    steps = []
    for n, t in enumerate(route, 1):
        what = "; ".join(action_text(a) for a in t["actions"]) if t["actions"] else "by hand"
        steps.append(f"{n}. {t['name']} ({what})")
    alternatives = []
    for t in process["tasks"]:
        edges = process["next"].get(t["id"], [])
        if len(edges) > 1:
            usual = next((e for e in edges if e["usual"]), None)
            alt = {"after": t["name"]}
            if usual:
                alt["usually"] = {"go to": usual["toName"], "when": usual["condition"]}
            alt["or"] = [{"go to": e["toName"], "when": e["condition"]} for e in edges if not e["usual"]]
            alternatives.append(alt)
    stages: dict[str, list[str]] = {}
    for t in process["tasks"]:
        stages.setdefault(t["activity"], []).append(t["name"])
    out = {"process": process["name"], "about": process["about"], "status": status_of(process)}
    if process["buildsOn"]:
        out["buildsOn"] = process["buildsOn"]
    out["usualRoute"] = steps
    if alternatives:
        out["alternatives"] = alternatives
    out["stages"] = [{"stage": name, "steps": names} for name, names in stages.items()]
    first = process["tasks"][0] if process["tasks"] else None
    if first and first.get("source"):
        out["source"] = first["source"]
    out["note"] = ((CANDIDATE_NOTE + " " if out["status"] == "candidate" else "")
                   + ("Do the plans in 'buildsOn' first: this one needs them. " if process["buildsOn"] else "") + HOW_TO_READ)
    return out


def step_view(process: dict, task: dict) -> dict:
    """One step in full: what to do, the game actions it names, and where it can lead."""
    edges = process["next"].get(task["id"], [])
    out = {"process": process["name"], "stage": task["activity"], "step": task["name"], "details": task["description"]}
    if task["actions"]:
        out["gameActions"] = [action_text(a) for a in task["actions"]]
    else:
        out["byHand"] = "The game data has no action for this step: it is done by hand."
    if edges:
        out["next"] = [{"go to": e["toName"], "when": e["condition"], **({"usually": True} if e["usual"] else {})} for e in edges]
    else:
        out["next"] = "This is the last step."
    if task.get("source"):
        out["source"] = task["source"]
    status = task.get("status") or "candidate"
    out["status"] = status
    if status == "candidate":
        out["note"] = CANDIDATE_NOTE
    return out


def tool_get_process(graph: Graph, args: dict) -> dict:
    """get_process: plans for goals the game's data does not spell out; the usual route, alternatives and each step in full."""
    name = str(args.get("name") or "").strip()
    step = str(args.get("step") or "").strip()
    processes = load_processes(graph)
    if not processes:
        return {"processes": [], "note": "No plans are loaded."}
    if not name and not step:
        return {"processes": [{"name": p["name"], "about": p["about"], "status": status_of(p),
                               **({"buildsOn": p["buildsOn"]} if p["buildsOn"] else {})} for p in processes.values()],
                "note": "Plans for what the game's data does not spell out. Call get_process with a name for the route. "
                        + CANDIDATE_NOTE}
    if name:
        process, err = find_process(processes, name)
        if err:
            return err
        if not step:
            return outline(process)
        found, err = find_step([(process, t) for t in process["tasks"]], step)
    else:
        found, err = find_step([(p, t) for p in processes.values() for t in p["tasks"]], step)
    if err:
        return err
    return step_view(*found)
