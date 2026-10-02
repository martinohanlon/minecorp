"""Read-only access to the MineCorp knowledge graph in Neo4j."""
from __future__ import annotations

from neo4j import READ_ACCESS, GraphDatabase


class Graph:
    """Thin wrapper over the Neo4j driver. Every query runs in a read transaction."""

    def __init__(self, uri: str, user: str, password: str, database: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def read(self, query: str, **params) -> list[dict]:
        """Run a read query and return rows as plain dicts."""
        with self._driver.session(database=self._database, default_access_mode=READ_ACCESS) as session:
            return session.execute_read(lambda tx: [r.data() for r in tx.run(query, **params)])

    def verify(self) -> None:
        """Raise if the database cannot be reached."""
        self._driver.verify_connectivity()

    def close(self) -> None:
        """Close the driver."""
        self._driver.close()
