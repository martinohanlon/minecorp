"""Link wiki pages and chunks to the game graph (docs/ontology.md v0.9).

    python loader/link_wiki.py --dry-run     # do it all, print the review report, then roll back
    python loader/link_wiki.py               # write the links

Run it after load_wiki.py and after the game graph is loaded (load_recipes, load_actions). It needs no OpenAI and is
deterministic. It replaces the links it made earlier, so it can be run again at any time.

The work is done by the graph. Python only scans the chunk text for names, which a graph cannot do, and hands plain
strings to Cypher; everything that is a relationship is found by following relationships:

    (Page)-[:ABOUT]->(node)
        A wiki page is about the nodes whose name is its title: the page "Torch" is about the item Torch. The node can be
        an Item (including an item category or a station), a Mob, or a Block that is not also an Item. Plural and singular
        forms count ("Log" for the category "Logs"), but an exact name beats them.

    (Chunk)-[:MENTIONS]->(node)
        The chunk is about the node. Two kinds of evidence, merged on the relationship:
        - the wiki's own links: (Chunk)-[:LINKS_TO]->(Page)-[:ABOUT]->(node). If the page is a redirect it is followed
          only when the page itself is about nothing: (Page)-[:REDIRECTS_TO]->(Page). So a link to "Black Bed" (a page
          that redirects to "Bed") is about the item Black Bed, and a link to "Wood" (redirecting to "Logs") is about
          the category. An editor said what the words mean, so "iron" linking to Iron Ingot counts (linkCount).
        - names found in the text (count): longest name first, whole words, plurals allowed, stoplist and aliases applied.

Ambiguity stays in the graph. When a title or a name can mean several nodes of the same quality (the page "Chicken" is the
mob and the item; "dirt" is the item and the category of dirt blocks) there is a relationship to EACH of them, with
ambiguous = true and candidates = how many; whoever reads the graph next, with the chunk as context, decides.

Relationship properties: ABOUT {method (title|override), match (exact|variant), ambiguous, candidates, status},
MENTIONS {method (link|name|link+name), count, linkCount, surfaces, corroborated, ambiguous, candidates, status}.
Both carry origin = 'link_wiki' (that is what the job replaces; links added by hand are left alone), and status is
'candidate' (or 'confirmed' for a fix from model/wiki_links.yaml). corroborated means the same wiki page links the node
somewhere. A link is evidence for a mention; no link says nothing (editors link the first mention only), so nothing is
dropped for lacking one. Count distinct nodes per section, not sums: chunks overlap.

--dry-run runs everything in a transaction, reads the result for the report, and rolls back.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import yaml  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402,F401

from quiet_driver import open_driver  # noqa: E402

from minecorp_agent.config import get_settings  # noqa: E402

CONFIG_PATH = ROOT / "model" / "wiki_links.yaml"
ORIGIN = "link_wiki"
TOP = 25
KINDS = ("Item", "Mob", "Block")

# The nodes a name can mean. A block that is also an item (the torch) is covered by the item, not counted twice. The classes
# (item categories aside) are not named: "Can Equip Saddle" or "Has Structure Village Plains" is not something a page is about.
NODE_LABELS = "(n:Item OR n:Mob OR n:Block OR n:Biome OR n:Structure)"
NOT_A_TWIN = "NOT (n:Block AND EXISTS { MATCH (i:Item {id: n.id}) })"
NAMED = f"{NODE_LABELS} AND {NOT_A_TWIN}"
# A node named in the config as {kind, id}: the kind is one of its labels.
CONFIG_NODE = "any(t IN {targets} WHERE n.id = t.id AND t.kind IN labels(n))"


# --------------------------------------------------------------------------- names in text

def normalise(text: str) -> str:
    """Lower case with single spaces."""
    return " ".join(str(text).lower().split())


def plurals(key: str) -> set[str]:
    """The name and its likely plural forms (text says "torches", "arrows", "berries")."""
    out = {key, key + "s", key + "es"}
    if key.endswith("y") and len(key) > 1:
        out.add(key[:-1] + "ies")
    return out


def surface_map(names: list[str], aliases: dict[str, list[tuple[str, str]]]) -> dict[str, dict]:
    """Every written form of every name: {surface: {names: [...], targets: [...], match}}.

    An exact name beats a plural form of another name ("arrows" is the exact name of one node and the plural of
    another). An alias replaces whatever the words would otherwise mean."""
    forms: dict[str, dict[str, set[str]]] = {}
    for name in sorted({normalise(n) for n in names if n}):
        for surface in plurals(name):
            forms.setdefault(surface, {"exact": set(), "variant": set()})["exact" if surface == name else "variant"].add(name)
    out = {}
    for surface, tiers in forms.items():
        match = "exact" if tiers["exact"] else "variant"
        out[surface] = {"names": sorted(tiers[match]), "targets": [], "match": match}
    for surface, wanted in aliases.items():
        out[surface] = {"names": [], "targets": [{"kind": k, "id": i} for k, i in wanted], "match": "alias"}
    return out


def scan(chunks: list[dict], surfaces: dict[str, dict], stoplist: set[str]) -> tuple[list[dict], Counter]:
    """Find names in chunk text. Returns ([{chunk, surface, count, names, targets}], how often each stoplisted word was skipped).

    Longest name first, whole words, any case. A stoplisted word is skipped (an alias is never stoplisted).
    """
    if not surfaces:
        return [], Counter()
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(s) for s in sorted(surfaces, key=len, reverse=True)) + r")(?!\w)", re.I)
    found, stopped = [], Counter()
    for chunk in chunks:
        counts = Counter(normalise(m.group(1)) for m in pattern.finditer(chunk["text"] or ""))
        for surface, count in sorted(counts.items()):
            entry = surfaces[surface]
            if entry["match"] != "alias" and (surface in stoplist or any(n in stoplist for n in entry["names"])):
                stopped[surface] += count
                continue
            found.append({"chunk": chunk["id"], "surface": surface, "count": count, "names": entry["names"], "targets": entry["targets"]})
    return found, stopped


# --------------------------------------------------------------------------- configuration

def targets(value, where: str) -> list[tuple[str, str]]:
    """Targets named in the config as [(kind, id)]. None is nobody; a string is an Item id; a mapping names
    {item|mob|block: id}; a list holds any of these."""
    if value is None:
        return []
    if isinstance(value, str):
        return [("Item", value)]
    if isinstance(value, dict):
        out = []
        for key, id_ in value.items():
            if str(key).lower() not in ("item", "mob", "block", "biome", "structure") or not isinstance(id_, str):
                raise ValueError(f"{where}: expected item, mob, block, biome or structure with an id, got {key!r}: {id_!r}")
            out.append((str(key).capitalize(), id_))
        return out
    if isinstance(value, list):
        return [t for v in value for t in targets(v, where)]
    raise ValueError(f"{where}: cannot read {value!r}")


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Read model/wiki_links.yaml: {stoplist: set, aliases: {surface: [(kind, id)]}, pages: {title: [(kind, id)]}}.
    A page mapped to null (an empty list) is one a person said is not about anything in the game."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    unknown = set(raw) - {"stoplist", "aliases", "pages"}
    if unknown:
        raise ValueError(f"{path.name}: unknown keys {sorted(unknown)}")
    return {"stoplist": {normalise(s) for s in (raw.get("stoplist") or [])},
            "aliases": {normalise(k): targets(v, f"aliases.{k}") for k, v in (raw.get("aliases") or {}).items()},
            "pages": {normalise(k): targets(v, f"pages.{k}") for k, v in (raw.get("pages") or {}).items()}}


def as_param(wanted: list[tuple[str, str]]) -> list[dict]:
    return [{"kind": k, "id": i} for k, i in wanted]


def check_config(tx, config: dict) -> list[str]:
    """Problems with the config: a fix that names a node that is not in the graph."""
    problems = []
    for section in ("aliases", "pages"):
        for key, wanted in config[section].items():
            for kind, id_ in wanted:
                if not tx.run(f"MATCH (n:{kind} {{id: $id}}) RETURN n LIMIT 1", id=id_).single():
                    problems.append(f"{section}: {key!r} points at unknown {kind.lower()} {id_!r}")
    return problems


# --------------------------------------------------------------------------- the graph work

def about_pages(tx, config: dict, at: str) -> None:
    """(Page)-[:ABOUT]->(node): from the fixes in the config first, then from the page titles."""
    overrides = [{"title": t, "targets": as_param(w)} for t, w in config["pages"].items()]
    tx.run(f"""UNWIND $overrides AS o
               MATCH (p:Page) WHERE toLower(p.title) = o.title
               MATCH (n) WHERE {CONFIG_NODE.format(targets='o.targets')}
               WITH p, collect(n) AS ns
               UNWIND ns AS n
               CREATE (p)-[:ABOUT {{origin: $origin, method: 'override', match: 'exact', ambiguous: size(ns) > 1,
                                   candidates: size(ns), status: 'confirmed', extractedAt: $at}}]->(n)""",
           overrides=overrides, origin=ORIGIN, at=at).consume()
    tx.run(f"""MATCH (p:Page) WHERE NOT toLower(p.title) IN $overridden
               MATCH (n) WHERE {NAMED}
               WITH p, n, toLower(p.title) AS t, toLower(n.name) AS k
               WITH p, n, CASE
                          WHEN t = k THEN 0
                          WHEN t IN [k + 's', k + 'es'] OR k IN [t + 's', t + 'es'] THEN 1
                          WHEN k ENDS WITH 'y' AND t = left(k, size(k) - 1) + 'ies' THEN 1
                          WHEN t ENDS WITH 'y' AND k = left(t, size(t) - 1) + 'ies' THEN 1 END AS rank
               WHERE rank IS NOT NULL
               WITH p, min(rank) AS best, collect({{n: n, rank: rank}}) AS found
               WITH p, best, [f IN found WHERE f.rank = best | f.n] AS ns
               UNWIND ns AS n
               CREATE (p)-[:ABOUT {{origin: $origin, method: 'title', match: CASE best WHEN 0 THEN 'exact' ELSE 'variant' END,
                                   ambiguous: size(ns) > 1, candidates: size(ns), status: 'candidate', extractedAt: $at}}]->(n)""",
           overridden=list(config["pages"]), origin=ORIGIN, at=at).consume()


def mention_names(tx, found: list[dict], at: str) -> None:
    """(Chunk)-[:MENTIONS]->(node) from names found in the text. The graph says which nodes carry each name."""
    for i in range(0, len(found), 500):
        tx.run(f"""UNWIND $found AS f
                   MATCH (c:Chunk {{id: f.chunk}})
                   MATCH (n) WHERE {NODE_LABELS}
                         AND ((toLower(n.name) IN f.names AND {NOT_A_TWIN}) OR {CONFIG_NODE.format(targets='f.targets')})
                   WITH f, c, collect(n) AS ns
                   UNWIND ns AS n
                   MERGE (c)-[m:MENTIONS {{origin: $origin}}]->(n)
                   ON CREATE SET m.method = 'name', m.count = f.count, m.linkCount = 0, m.surfaces = [f.surface],
                                 m.ambiguous = size(ns) > 1, m.candidates = size(ns), m.status = 'candidate', m.extractedAt = $at
                   ON MATCH SET m.count = m.count + f.count,
                                m.surfaces = CASE WHEN f.surface IN m.surfaces THEN m.surfaces ELSE m.surfaces + f.surface END,
                                m.ambiguous = m.ambiguous OR size(ns) > 1,
                                m.candidates = CASE WHEN size(ns) > m.candidates THEN size(ns) ELSE m.candidates END""",
               found=found[i:i + 500], origin=ORIGIN, at=at).consume()


def mention_links(tx, at: str) -> None:
    """(Chunk)-[:MENTIONS]->(node) from the wiki's links: Chunk-LINKS_TO->Page-ABOUT->node, following a redirect only when
    the linked page is itself about nothing (the nearest page that is about something wins)."""
    tx.run("""MATCH (c:Chunk)-[l:LINKS_TO]->(p:Page)
              MATCH path = (p)-[:REDIRECTS_TO*0..1]->(:Page)-[a:ABOUT]->(n)
              WITH c, l, collect({n: n, a: a, hops: length(path)}) AS found
              WITH c, l, found, reduce(h = 99, x IN found | CASE WHEN x.hops < h THEN x.hops ELSE h END) AS nearest
              UNWIND [x IN found WHERE x.hops = nearest] AS x
              WITH c, l, x.n AS n, coalesce(x.a.ambiguous, false) AS ambiguous, coalesce(x.a.candidates, 1) AS candidates,
                   [t IN l.texts | toLower(t)] AS texts
              MERGE (c)-[m:MENTIONS {origin: $origin}]->(n)
              ON CREATE SET m.method = 'link', m.count = 0, m.linkCount = l.count, m.surfaces = texts,
                            m.ambiguous = ambiguous, m.candidates = candidates, m.status = 'candidate', m.extractedAt = $at
              ON MATCH SET m.method = CASE WHEN m.count > 0 THEN 'link+name' ELSE 'link' END,
                           m.linkCount = m.linkCount + l.count,
                           m.surfaces = m.surfaces + [t IN texts WHERE NOT t IN m.surfaces],
                           m.ambiguous = m.ambiguous OR ambiguous,
                           m.candidates = CASE WHEN candidates > m.candidates THEN candidates ELSE m.candidates END""",
           origin=ORIGIN, at=at).consume()
    tx.run("""MATCH (c:Chunk)-[m:MENTIONS {origin: $origin}]->(n)
              OPTIONAL MATCH (c)-[:PART_OF]->(:Section)<-[:HAS_SUBSECTION*0..]-(:Section)<-[:HAS_SECTION]-(pg:Page)
              SET m.corroborated = m.linkCount > 0 OR EXISTS {
                  MATCH (pg)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION*0..]->(:Section)<-[:PART_OF]-(:Chunk)-[o:MENTIONS {origin: $origin}]->(n)
                  WHERE o.linkCount > 0 }""", origin=ORIGIN).consume()


def sort_surfaces(tx) -> None:
    """Put the wordings on every mention in alphabetical order, so the graph reads the same on every run."""
    tx.run("""MATCH ()-[m:MENTIONS {origin: $origin}]->()
              UNWIND m.surfaces AS s
              WITH m, s ORDER BY s
              WITH m, collect(s) AS sorted
              SET m.surfaces = sorted""", origin=ORIGIN).consume()


def clear(tx) -> None:
    """Remove the links this job made earlier. Links added by hand (another origin) stay."""
    tx.run("MATCH ()-[r:ABOUT|MENTIONS {origin: $origin}]->() DELETE r", origin=ORIGIN).consume()


def link(tx, config: dict, at: str) -> Counter:
    """Replace the job's links with new ones, inside the given transaction. Returns the stoplist's effect."""
    clear(tx)
    about_pages(tx, config, at)
    names = [r["name"] for r in tx.run(f"MATCH (n) WHERE {NAMED} RETURN DISTINCT toLower(n.name) AS name")]
    chunks = [r.data() for r in tx.run("MATCH (c:Chunk) RETURN c.id AS id, c.text AS text ORDER BY c.index")]
    found, stopped = scan(chunks, surface_map(names, config["aliases"]), config["stoplist"])
    mention_names(tx, found, at)
    mention_links(tx, at)
    sort_surfaces(tx)
    return stopped


# --------------------------------------------------------------------------- report

def read_results(tx) -> dict:
    """What the report needs, read back from the graph the job just wrote (or is about to roll back)."""
    def rows(query: str, **params) -> list[dict]:
        return [r.data() for r in tx.run(query, **params)]

    return {
        "pages": rows("MATCH (p:Page) RETURN count(p) AS n")[0]["n"],
        "about": rows("MATCH (p:Page)-[a:ABOUT {origin: $o}]->(n) RETURN p.title AS page, a.method AS method, a.match AS match, "
                      "a.ambiguous AS ambiguous, n.name AS name, n.id AS id, labels(n) AS labels", o=ORIGIN),
        "unmapped": rows("""MATCH (:Chunk)-[l:LINKS_TO]->(p:Page)
                            WHERE NOT EXISTS { (p)-[:REDIRECTS_TO*0..1]->(:Page)-[:ABOUT]->() }
                            RETURN p.title AS page, sum(l.count) AS links ORDER BY links DESC, page"""),
        "mentions": rows("MATCH (c:Chunk)-[m:MENTIONS {origin: $o}]->(n) RETURN c.id AS chunk, m.method AS method, m.count AS count, "
                         "m.linkCount AS linkCount, m.surfaces AS surfaces, m.corroborated AS corroborated, m.ambiguous AS ambiguous, "
                         "n.name AS name, n.id AS id, labels(n) AS labels", o=ORIGIN),
    }


def kind_of(labels: list[str]) -> str:
    """Item, Category (an item category), Station, Mob, Block, Biome or Structure: what the node says about itself."""
    for label in ("Mob", "Block", "Biome", "Structure", "ItemClass", "Station"):
        if label in labels:
            return {"ItemClass": "Category"}.get(label, label)
    return "Item"


def report(data: dict, stopped: Counter, problems: list[str], top: int = TOP) -> list[str]:
    """The review report, as lines of text, from `read_results`."""
    about, mentions = data["about"], data["mentions"]
    pages_about = {a["page"] for a in about}
    lines = [f"pages: {data['pages']}  about something in the game: {len(pages_about)}  "
             f"(ABOUT links {len(about)}: exact {sum(a['match'] == 'exact' for a in about)}, variant {sum(a['match'] == 'variant' for a in about)}, "
             f"overrides {sum(a['method'] == 'override' for a in about)})"]
    ambiguous: dict[str, list[str]] = {}
    for a in about:
        if a["ambiguous"]:
            ambiguous.setdefault(a["page"], []).append(f"{kind_of(a['labels'])} {a['name']} ({a['id']})")
    if ambiguous:
        lines.append(f"ambiguous pages, linked to every candidate ({len(ambiguous)}); fix one in model/wiki_links.yaml if it is never needed:")
        lines += [f"   {page} -> {'; '.join(sorted(found))}" for page, found in sorted(ambiguous.items())]
    lines.append(f"pages linked to that are about nothing in the game, redirects followed (concepts the graph does not model): {len(data['unmapped'])}")
    lines += [f"   {u['links']:3}  {u['page']}" for u in data["unmapped"][:top]]
    by_method = Counter(m["method"] for m in mentions)
    nodes = {(m["id"], tuple(sorted(m["labels"]))) for m in mentions}
    lines.append(f"mentions: {len(mentions)}  (link {by_method['link']}, name {by_method['name']}, link+name {by_method['link+name']})  "
                 f"distinct nodes {len(nodes)} (Item {sum('Item' in n[1] for n in nodes)}, Mob {sum('Mob' in n[1] for n in nodes)}, "
                 f"Block {sum('Block' in n[1] for n in nodes)}, Biome {sum('Biome' in n[1] for n in nodes)}, "
                 f"Structure {sum('Structure' in n[1] for n in nodes)})  ambiguous {sum(bool(m['ambiguous']) for m in mentions)}")
    weak: dict[tuple, dict] = {}
    for m in mentions:
        if m["method"] == "name" and not m["corroborated"]:
            w = weak.setdefault((m["id"], tuple(sorted(m["labels"])), m["name"]), {"count": 0, "chunks": 0, "ambiguous": False, "surfaces": set()})
            w["count"] += m["count"]
            w["chunks"] += 1
            w["ambiguous"] = w["ambiguous"] or bool(m["ambiguous"])
            w["surfaces"] |= set(m["surfaces"])
    lines.append(f"name-only mentions the page never links ({len(weak)} nodes): review these; add generic words to the stoplist")
    for (id_, labels, name), w in sorted(weak.items(), key=lambda kv: (-kv[1]["count"], kv[0][2], kv[0][0]))[:top]:
        kind = kind_of(list(labels))
        lines.append(f"   {w['count']:3}  {name}" + (f" [{kind}]" if kind != "Item" else "") + f"  in {w['chunks']} chunks  {sorted(w['surfaces'])}" +
                     ("  [ambiguous]" if w["ambiguous"] else ""))
    only_links = sorted({m["name"] for m in mentions if m["method"] == "link"})
    lines.append(f"found only through the wiki's links ({len(only_links)} nodes, wording the text does not give): {only_links[:top]}")
    if stopped:
        lines.append(f"ignored by the stoplist: {dict(stopped)}")
    lines += [f"PROBLEM: {p}" for p in problems]
    return lines


# --------------------------------------------------------------------------- run

def run(session, config: dict, at: str, dry_run: bool = False) -> dict:
    """Make the links in one transaction. A dry run (or a problem in the config) rolls back after the report is read.

    Returns {data, stopped, problems, written}: the report data, what the stoplist ignored, config problems, and
    whether the links were kept."""
    tx = session.begin_transaction()
    try:
        problems = check_config(tx, config)
        if problems:
            tx.rollback()
            return {"data": None, "stopped": Counter(), "problems": problems, "written": False}
        stopped = link(tx, config, at)
        data = read_results(tx)
        if dry_run:
            tx.rollback()
        else:
            tx.commit()
        return {"data": data, "stopped": stopped, "problems": [], "written": not dry_run}
    except BaseException:
        tx.rollback()
        raise
    finally:
        tx.close()


def main() -> int:
    """Make the links, print the report, and (unless --dry-run) keep them."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="do it all, print the report, then roll back")
    ap.add_argument("--config", type=Path, default=CONFIG_PATH, help="stoplist, aliases and page fixes (YAML)")
    ap.add_argument("--top", type=int, default=TOP, help="rows per list in the report")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    try:
        config = load_config(args.config)
    except (ValueError, yaml.YAMLError) as exc:
        sys.exit(f"{args.config}: {exc}")
    s = get_settings()
    with open_driver(s.neo4j_uri, s.neo4j_user, s.neo4j_password) as driver:
        with driver.session(database=s.neo4j_database) as session:
            result = run(session, config, datetime.now(timezone.utc).isoformat(timespec="seconds"), args.dry_run)
    if result["problems"]:
        print("\n".join(f"PROBLEM: {p}" for p in result["problems"]))
        sys.exit("nothing written: fix the problems above in the config")
    print("\n".join(report(result["data"], result["stopped"], [], args.top)))
    print("dry run: nothing written." if args.dry_run else
          f"written: {len(result['data']['about'])} ABOUT and {len(result['data']['mentions'])} MENTIONS relationships.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
