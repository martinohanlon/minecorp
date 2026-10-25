"""model/ontology.yaml against the graph the loaders build: the spec and the graph must not drift apart.

The spec (a Neo4j Ontology Graph Specification) is read as plain YAML. The graph is built from source in the throwaway database
(tests/graphdb.py) and read back with the schema procedures. Checked in both directions: everything in the graph is in the spec (labels, label
combinations, relationship types and endpoint pairs, properties and their types, uniqueness constraints) and everything in the spec is in the
graph (except the lexical graph and the player's history, which the throwaway database does not build), plus the endpoint cardinality and the
allowed values the spec states. The agent memory graph is the library's, not this spec's, so it is left out. Needs Neo4j Enterprise and the
Paper jar.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.jar]

SPEC_PATH = Path(__file__).resolve().parent.parent.parent / "model" / "ontology.yaml"
MEMORY_LABELS = {"Conversation", "Message", "ReasoningTrace", "ReasoningStep", "ToolCall", "Tool", "User", "Entity", "Fact", "Preference",
                 "ConsolidationRun", "MemoryReadAudit", "Ontology", "OntologyVersion", "OntologyLock", "SchemaMigration"}
# in the spec but not built in the throwaway database: the lexical graph (its own tests build it) and what a player does over time
NOT_BUILT_HERE = {"nodes": {"wiki", "page", "section", "chunk"},
                  "relationships": {"enchantment_applies_to_item",   # the loader supports it; no enchantment in 26.2 names items directly
                                    "wiki_has_page", "page_has_section", "section_has_subsection", "chunk_part_of_section", "chunk_next",
                                    "chunk_links_to_page", "page_redirects_to", "player_completed_task", "task_confirmed_by"}
                  | {f"page_about_{k}" for k in ("item", "mob", "block", "biome", "structure")}
                  | {f"chunk_mentions_{k}" for k in ("item", "mob", "block", "biome", "structure")}}


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def base_labels(node: dict) -> frozenset:
    """The labels a node of this spec node always has."""
    labels = node.get("labels") or {"identifier": node["label"]}
    return frozenset([labels["identifier"], *labels.get("implied", [])])


def optional_labels(node: dict) -> frozenset:
    return frozenset((node.get("labels") or {}).get("optional", []))


def node_matches(node: dict, labels: frozenset) -> bool:
    """A graph node with exactly these labels is this spec node: all its base labels, and nothing but optional ones besides."""
    return base_labels(node) <= labels and labels - base_labels(node) <= optional_labels(node)


def end_matches(spec, end: dict, labels: frozenset) -> bool:
    if "label" in end:
        return end["label"] in labels
    return node_matches(spec["nodes"][end["node"]], labels)


def only_game(labels) -> frozenset | None:
    """The labels of a node, or None when it is one the agent memory library owns."""
    s = frozenset(labels)
    return None if s & MEMORY_LABELS else s


@pytest.fixture(scope="module")
def graph(built, db):
    node_props = db.q("CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName, propertyTypes RETURN nodeLabels, propertyName, propertyTypes")
    triples = db.q("MATCH (a)-[r]->(b) RETURN DISTINCT labels(a) AS f, type(r) AS t, labels(b) AS to")
    rel_props = db.q("CALL db.schema.relTypeProperties() YIELD relType, propertyName, propertyTypes RETURN relType, propertyName, propertyTypes")
    constraints = db.q("SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties WHERE type CONTAINS 'UNIQUENESS' RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
    return {"node_props": node_props, "triples": triples, "rel_props": rel_props,
            "unique": {(c["label"], c["prop"]) for c in constraints}}


def normal_type(t: str) -> str:
    """A type from the schema procedures as the spec writes it: 'STRING NOT NULL' is STRING, 'LIST<STRING NOT NULL> NOT NULL' is LIST<STRING>."""
    t = t.replace(" NOT NULL", "")
    return {"DATE": "DATE", "ZONED_DATETIME": "ZONED DATETIME"}.get(t, t)


def type_ok(declared: str, found: list[str]) -> bool:
    if declared == "ANY":
        return True
    seen = {normal_type(t) for t in found}
    seen.discard("LIST<NOTHING>")                                  # an empty list says nothing about its element type
    if declared.startswith("VECTOR<"):
        return "LIST<FLOAT>" in seen or "VECTOR<FLOAT32>" in seen or "LIST<NOTHING>" in found or declared in seen
    return declared in seen or (declared == "FLOAT" and seen == {"INTEGER"}) or (not seen and declared.startswith("LIST<"))


class TestTheSpecIsComplete:
    def test_every_label_combination_in_the_graph_is_one_spec_node(self, spec, graph):
        found = {only_game(r["nodeLabels"]) for r in graph["node_props"]} - {None}
        unmatched = [sorted(f) for f in found if sum(node_matches(n, f) for n in spec["nodes"].values()) != 1]
        assert not unmatched, f"graph nodes the spec does not describe (or describes twice): {unmatched}"

    def test_every_relationship_in_the_graph_is_a_spec_relationship_between_those_nodes(self, spec, graph):
        missing = []
        for r in graph["triples"]:
            f, to = only_game(r["f"]), only_game(r["to"])
            if f is None or to is None:
                continue
            if not any(rel["type"] == r["t"] and end_matches(spec, rel["from"], f) and end_matches(spec, rel["to"], to) for rel in spec["relationships"].values()):
                missing.append(f"({':'.join(sorted(f))})-[{r['t']}]->({':'.join(sorted(to))})")
        assert not missing, f"graph relationships the spec does not describe: {missing}"

    def test_every_node_property_in_the_graph_is_declared_and_has_the_declared_type(self, spec, graph):
        problems = []
        for r in graph["node_props"]:
            labels = only_game(r["nodeLabels"])
            if labels is None or r["propertyName"] is None:
                continue
            node = next((n for n in spec["nodes"].values() if node_matches(n, labels)), None)
            if node is None:
                continue
            declared = (node.get("properties") or {}).get(r["propertyName"])
            if declared is None:
                problems.append(f"{':'.join(sorted(labels))}.{r['propertyName']} is not in the spec")
            elif not type_ok(declared["type"], r["propertyTypes"]):
                problems.append(f"{':'.join(sorted(labels))}.{r['propertyName']} is {r['propertyTypes']} but the spec says {declared['type']}")
        assert not problems, problems

    def test_every_relationship_property_in_the_graph_is_declared_for_its_type(self, spec, graph):
        declared: dict[str, dict] = {}
        for rel in spec["relationships"].values():
            for name, p in (rel.get("properties") or {}).items():
                declared.setdefault(rel["type"], {}).setdefault(name, []).append(p)
        problems = []
        for r in graph["rel_props"]:
            t = re.sub(r"[:`]", "", r["relType"])
            if r["propertyName"] is None or t in {"IS_PLAYER"}:
                continue
            options = declared.get(t, {}).get(r["propertyName"])
            if not options:
                problems.append(f"{t}.{r['propertyName']} is not in the spec")
            elif not any(type_ok(p["type"], r["propertyTypes"]) for p in options):
                problems.append(f"{t}.{r['propertyName']} is {r['propertyTypes']} but the spec says {[p['type'] for p in options]}")
        assert not problems, problems

    def test_every_uniqueness_constraint_in_the_graph_is_in_the_spec(self, spec, graph):
        wanted = set()
        for n in spec["nodes"].values():
            for label in base_labels(n):
                wanted |= {(label, name) for name, p in (n.get("properties") or {}).items() if p.get("unique")}
        found = {(label, prop) for label, prop in graph["unique"] if label not in MEMORY_LABELS}
        found -= {("Station", "id")}                                # a marker label that has its own constraint; the spec states it on Item
        extra = found - wanted
        assert not extra, f"constraints in the graph the spec does not state: {sorted(extra)}"


class TestTheGraphHasWhatTheSpecSays:
    def test_every_spec_node_is_in_the_graph(self, spec, graph):
        found = {only_game(r["nodeLabels"]) for r in graph["node_props"]} - {None}
        gone = [k for k, n in spec["nodes"].items() if k not in NOT_BUILT_HERE["nodes"] and not any(node_matches(n, f) for f in found)]
        assert not gone, f"spec nodes with no node in the graph: {gone}"

    def test_every_spec_relationship_is_in_the_graph(self, spec, graph):
        triples = [(only_game(r["f"]), r["t"], only_game(r["to"])) for r in graph["triples"]]
        gone = []
        for key, rel in spec["relationships"].items():
            if key in NOT_BUILT_HERE["relationships"]:
                continue
            if not any(t == rel["type"] and f and to and end_matches(spec, rel["from"], f) and end_matches(spec, rel["to"], to) for f, t, to in triples):
                gone.append(key)
        assert not gone, f"spec relationships with none in the graph: {gone}"

    def test_every_spec_node_property_is_in_the_graph(self, spec, graph):
        found = {}
        for r in graph["node_props"]:
            labels = only_game(r["nodeLabels"])
            if labels and r["propertyName"]:
                found.setdefault(labels, set()).add(r["propertyName"])
        gone = []
        for key, n in spec["nodes"].items():
            if key in NOT_BUILT_HERE["nodes"]:
                continue
            have = set().union(*[props for labels, props in found.items() if node_matches(n, labels)] or [set()])
            gone += [f"{key}.{p}" for p in (n.get("properties") or {}) if p not in have]
        assert not gone, f"spec properties no node has: {gone}"

    def test_every_uniqueness_flag_in_the_spec_is_a_constraint_in_the_graph(self, spec, graph):
        gone = []
        for key, n in spec["nodes"].items():
            if key in NOT_BUILT_HERE["nodes"]:
                continue
            for name, p in (n.get("properties") or {}).items():
                if p.get("unique") and not any((label, name) in graph["unique"] for label in base_labels(n)):
                    gone.append(f"{key}.{name}")
        assert not gone, f"unique in the spec but not constrained in the graph: {gone}"


def label_pattern(spec, end: dict) -> str:
    """A Cypher label expression for an endpoint: ':Item:ItemClass'."""
    if "label" in end:
        return ":" + end["label"]
    return "".join(":" + label for label in sorted(base_labels(spec["nodes"][end["node"]])))


class TestEndpointCardinality:
    def test_the_counts_the_spec_states_hold_for_every_node(self, spec, db):
        checked, problems = 0, []
        for key, rel in spec["relationships"].items():
            if key in NOT_BUILT_HERE["relationships"]:
                continue
            frm, to = label_pattern(spec, rel["from"]), label_pattern(spec, rel["to"])
            for end, outer, inner, arrow in (("to", frm, to, f"(a)-[:{rel['type']}]->(b{{inner}})"), ("from", to, frm, f"(a)<-[:{rel['type']}]-(b{{inner}})")):
                c = rel[end]
                for field, op in (("count", "<>"), ("min_count", "<"), ("max_count", ">")):
                    if c.get(field) is None:
                        continue
                    pattern = arrow.format(inner=inner)
                    n = db.one(f"MATCH (a{outer}) WITH a, COUNT {{ {pattern} }} AS n WHERE n {op} {c[field]} RETURN count(a)")
                    checked += 1
                    if n:
                        problems.append(f"{key}: {n} {outer} node(s) break {end} {field} {c[field]}")
        assert checked >= 4 and not problems, problems

    def test_the_values_the_spec_allows_are_the_only_ones_in_the_graph(self, spec, db):
        problems = []
        for key, rel in spec["relationships"].items():
            if key in NOT_BUILT_HERE["relationships"]:
                continue
            for name, p in (rel.get("properties") or {}).items():
                if p.get("one_of"):
                    found = db.q(f"MATCH (a{label_pattern(spec, rel['from'])})-[r:{rel['type']}]->(b{label_pattern(spec, rel['to'])}) "
                                 f"WHERE r.{name} IS NOT NULL RETURN DISTINCT r.{name} AS v")
                    bad = sorted(str(x["v"]) for x in found if x["v"] not in p["one_of"])
                    if bad:
                        problems.append(f"{key}.{name} has {bad}, the spec allows {p['one_of']}")
        for key, node in spec["nodes"].items():
            if key in NOT_BUILT_HERE["nodes"]:
                continue
            for name, p in (node.get("properties") or {}).items():
                if p.get("one_of"):
                    found = db.q(f"MATCH (n{label_pattern(spec, {'node': key})}) WHERE n.{name} IS NOT NULL RETURN DISTINCT n.{name} AS v")
                    bad = sorted(str(x["v"]) for x in found if x["v"] not in p["one_of"])
                    if bad:
                        problems.append(f"{key}.{name} has {bad}, the spec allows {p['one_of']}")
        assert not problems, problems


class TestTheSpecItself:
    def test_it_validates_against_the_published_schema(self, spec):
        jsonschema = pytest.importorskip("jsonschema")
        import json
        import urllib.request
        try:
            request = urllib.request.Request(spec["schema"], headers={"User-Agent": "minecorp-tests"})      # the host refuses the default agent
            with urllib.request.urlopen(request, timeout=20) as response:
                schema = json.loads(response.read())
        except OSError as exc:
            pytest.skip(f"the published schema could not be fetched: {exc}")
        errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(spec), key=lambda e: list(e.absolute_path))
        assert not errors, [f"{'.'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]

    def test_every_relationship_points_at_nodes_the_spec_defines(self, spec):
        for key, rel in spec["relationships"].items():
            for end in ("from", "to"):
                node = rel[end].get("node")
                assert node is None or node in spec["nodes"], f"{key}.{end} names {node}"

    def test_no_two_spec_nodes_have_the_same_labels(self, spec):
        seen = [(base_labels(n), optional_labels(n)) for n in spec["nodes"].values()]
        assert len({b for b, _ in seen}) == len(seen)

    def test_the_documented_version_matches_the_ontology_doc(self, spec):
        doc = (SPEC_PATH.parent.parent / "docs" / "ontology.md").read_text(encoding="utf-8")
        assert f"v{spec['version']}" in doc, "model/ontology.yaml says a version docs/ontology.md does not mention"
