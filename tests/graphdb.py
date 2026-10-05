"""Builds the whole graph from source in a THROWAWAY database, for the integration tests.

The database is called `minecorptest`. It is created at the start and dropped at the end; the real database is
never touched. Needs the local Neo4j (Enterprise Edition, for a second database) and the Paper jar.
"""
from __future__ import annotations

import os
import sys

import pytest
from neo4j import GraphDatabase

import compute_routes
import load_actions as la
import load_model as lm
import load_world as lwd
import load_recipes as lr
from minecorp_agent.config import load_dotenv

TEST_DB = "minecorptest"


class Db:
    """Tiny helper for queries against the test database."""

    def __init__(self, driver, database: str = TEST_DB):
        self.driver = driver
        self.database = database

    def q(self, cypher: str, **params) -> list[dict]:
        return [r.data() for r in self.driver.execute_query(cypher, database_=self.database, **params).records]

    def one(self, cypher: str, **params):
        rows = self.q(cypher, **params)
        assert len(rows) == 1, rows
        return next(iter(rows[0].values())) if len(rows[0]) == 1 else rows[0]


def empty_test_database(name: str):
    """Generator for a fixture: an EMPTY throwaway database called `name` (created, yielded, dropped).

    Yields {"db", "uri", "user", "password", "database"}. For tests that need no jar. Skips without Neo4j Enterprise.
    """
    load_dotenv()
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    assert name != os.environ.get("NEO4J_DATABASE", "neo4j"), "the test database must not be the real one"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        driver.close()
        pytest.skip(f"Neo4j is not reachable: {exc}")
    try:
        driver.execute_query(f"DROP DATABASE {name} IF EXISTS WAIT", database_="system")
        driver.execute_query(f"CREATE DATABASE {name} WAIT", database_="system")
    except Exception as exc:
        driver.close()
        pytest.skip(f"cannot create a test database (needs Enterprise Edition): {exc}")
    try:
        yield {"db": Db(driver, name), "uri": uri, "user": user, "password": password, "database": name}
    finally:
        try:
            driver.execute_query(f"DROP DATABASE {name} IF EXISTS WAIT", database_="system")
        finally:
            driver.close()


def build_test_graph(jar_path):
    """Generator for a session fixture: build, yield what was built, then drop the database."""
    load_dotenv()
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    assert TEST_DB != os.environ.get("NEO4J_DATABASE", "neo4j"), "the test database must not be the real one"

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except Exception as exc:  # not running, wrong password...
        driver.close()
        pytest.skip(f"Neo4j is not reachable: {exc}")
    try:
        driver.execute_query(f"CREATE DATABASE {TEST_DB} IF NOT EXISTS WAIT", database_="system")
    except Exception as exc:  # Community Edition has a single database
        driver.close()
        pytest.skip(f"cannot create a test database (needs Enterprise Edition): {exc}")

    try:
        recipes, tags = lr.read_jar(jar_path)
        normalised = {rid: lr.normalise(rid, raw) for rid, raw in recipes.items()}
        recipe_graph = lr.to_graph(normalised, tags, set(tags), [])
        lr.load_into_neo4j(recipe_graph, uri, user, password, TEST_DB, wipe=True)

        action_graph = la.build(jar_path)
        la.write(action_graph, uri, user, password, TEST_DB)

        world_graph = lwd.build(jar_path)                              # needs the Block and Mob nodes the actions made
        lwd.write(world_graph, uri, user, password, TEST_DB)

        org, processes = lm.read_model()
        action_ids = {rec["id"] for rec in driver.execute_query("MATCH (a:Action) RETURN a.id AS id", database_=TEST_DB).records}
        assert lm.validate(org, processes, action_ids) == []
        model_rows = lm.rows(org, processes)
        lm.write(model_rows, uri, user, password, TEST_DB)

        with pytest.MonkeyPatch.context() as mp:                       # compute_routes reads its settings from the environment
            mp.setenv("NEO4J_DATABASE", TEST_DB)
            mp.setenv("NEO4J_URI", uri)
            mp.setenv("NEO4J_USER", user)
            mp.setenv("NEO4J_PASSWORD", password)
            if not os.environ.get("AGENT_SHARED_SECRET"):
                mp.setenv("AGENT_SHARED_SECRET", "x" * 16)
            mp.setattr(sys, "argv", ["compute_routes.py"])
            assert compute_routes.main() == 0

        yield {"db": Db(driver), "uri": uri, "user": user, "password": password, "normalised": normalised,
               "recipe_graph": recipe_graph, "action_graph": action_graph, "world_graph": world_graph, "model_rows": model_rows}
    finally:
        try:
            driver.execute_query(f"DROP DATABASE {TEST_DB} IF EXISTS WAIT", database_="system")
        finally:
            driver.close()
