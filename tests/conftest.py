"""Shared test setup: import paths, the Paper jar, and the throwaway graph used by the integration tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT / "loader", ROOT / "agent"):
    sys.path.insert(0, str(path))

JAR = ROOT / "server" / "versions" / "26.2" / "paper-26.2.jar"


@pytest.fixture(scope="session")
def jar_path() -> Path:
    """The real Paper jar. Tests that need it are skipped when it has not been downloaded."""
    if not JAR.exists():
        pytest.skip(f"Paper jar not found: {JAR} (run scripts/start.ps1 once)")
    return JAR


@pytest.fixture(scope="session")
def built(jar_path):
    """The whole graph rebuilt from source in the throwaway database (built once per test run, dropped at the end).

    Only the tests marked `integration` ask for it, so the fast run never connects to Neo4j.
    """
    from graphdb import build_test_graph
    yield from build_test_graph(jar_path)


@pytest.fixture(scope="session")
def db(built):
    return built["db"]


@pytest.fixture(scope="session")
def agent_graph(built):
    """The agent's read-only Graph wrapper, pointed at the throwaway database."""
    from graphdb import TEST_DB
    from minecorp_agent.graph import Graph
    g = Graph(built["uri"], built["user"], built["password"], TEST_DB)
    yield g
    g.close()
