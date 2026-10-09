"""Tests for loader/name_index.py: the full-text index over names is made once, and made again when its labels change."""
from __future__ import annotations

import pytest
from neo4j import GraphDatabase

import name_index as ni
from graphdb import TEST_DB


class FakeSession:
    """Records the statements and answers the SHOW INDEXES question with the labels it was given."""

    def __init__(self, labels=None):
        self.labels = labels
        self.statements: list[str] = []

    def run(self, statement, **params):
        self.statements.append(statement.split("\n")[0][:40])
        outer = self

        class Result:
            def data(self):
                return [] if outer.labels is None else [{"labels": outer.labels}]

            def consume(self):
                return None
        return Result()


def drops(session):
    return [s for s in session.statements if s.startswith("DROP")]


def creates(session):
    return [s for s in session.statements if s.startswith("CREATE")]


class TestEnsure:
    def test_a_missing_index_is_created(self):
        s = FakeSession(labels=None)
        ni.ensure_name_index(s)
        assert len(creates(s)) == 1 and not drops(s)

    def test_an_index_with_the_right_labels_is_left_alone_so_the_create_is_a_no_op(self):
        s = FakeSession(labels=list(ni.LABELS))
        ni.ensure_name_index(s)
        assert not drops(s) and len(creates(s)) == 1       # `IF NOT EXISTS` makes it harmless

    def test_the_order_of_the_labels_does_not_matter(self):
        s = FakeSession(labels=list(reversed(ni.LABELS)))
        ni.ensure_name_index(s)
        assert not drops(s)

    def test_an_index_made_before_a_label_was_added_is_dropped_and_made_again(self):
        s = FakeSession(labels=[x for x in ni.LABELS if x != "Profession"])
        ni.ensure_name_index(s)
        assert len(drops(s)) == 1 and len(creates(s)) == 1
        assert s.statements.index(drops(s)[0]) < s.statements.index(creates(s)[0])

    def test_an_index_with_a_label_that_is_no_longer_wanted_is_remade(self):
        s = FakeSession(labels=[*ni.LABELS, "Villager"])
        ni.ensure_name_index(s)
        assert len(drops(s)) == 1

    def test_an_index_with_no_labels_listed_is_remade(self):
        s = FakeSession(labels=[])
        ni.ensure_name_index(s)
        assert len(drops(s)) == 1


class TestWhatTheIndexCovers:
    def test_professions_are_in_it(self):
        assert "Profession" in ni.LABELS and "Profession" in ni.CREATE

    def test_the_labels_are_the_kinds_the_agent_can_search(self):
        from minecorp_agent.names import KINDS
        agent_labels = {label for label, _kind in KINDS}
        assert agent_labels - {"ItemClass"} <= set(ni.LABELS), "a kind the agent searches must be in the index"


@pytest.mark.integration
@pytest.mark.jar
class TestInTheDatabase:
    def test_an_index_made_for_fewer_labels_is_rebuilt_and_online(self, built, db):
        old = "CREATE FULLTEXT INDEX names IF NOT EXISTS FOR (n:Item|Mob|Block|Biome|Structure|MobClass|BiomeClass) ON EACH [n.name]"
        with GraphDatabase.driver(built["uri"], auth=(built["user"], built["password"])) as driver:
            with driver.session(database=TEST_DB) as session:
                try:
                    session.run("DROP INDEX names IF EXISTS").consume()
                    session.run(old).consume()
                    before = session.run("SHOW INDEXES YIELD name, labelsOrTypes WHERE name = 'names' RETURN labelsOrTypes AS l").data()[0]["l"]
                    assert "Profession" not in before
                    ni.ensure_name_index(session)
                    after = session.run("SHOW INDEXES YIELD name, labelsOrTypes WHERE name = 'names' RETURN labelsOrTypes AS l").data()[0]["l"]
                    assert set(after) == set(ni.LABELS)
                finally:
                    ni.ensure_name_index(session)
                    session.run("CALL db.awaitIndexes(60)").consume()
        assert db.one("SHOW INDEXES YIELD name, state WHERE name = 'names' RETURN state") == "ONLINE"

    def test_a_profession_is_found_through_the_rebuilt_index(self, db):
        rows = db.q("CALL db.index.fulltext.queryNodes('names', 'farmer') YIELD node RETURN labels(node) AS l, node.name AS name")
        assert any("Profession" in r["l"] and r["name"] == "Farmer" for r in rows)
