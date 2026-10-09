"""The full-text index over names, shared by the loaders.

One index covers every kind of thing a player can name (items and categories, mobs, blocks, biomes,
structures, and mob and biome classes), so the agent's `find` tool and name resolution search them all at once.
Creating it is idempotent and works before any of the labels has a node, so every loader that creates named
nodes can call `ensure_name_index` and the order the loaders run in does not matter.
"""
from __future__ import annotations

INDEX_NAME = "names"
LABELS = ("Item", "Mob", "Block", "Biome", "Structure", "MobClass", "BiomeClass", "Profession")

CREATE = (f"CREATE FULLTEXT INDEX {INDEX_NAME} IF NOT EXISTS "
          f"FOR (n:{'|'.join(LABELS)}) ON EACH [n.name]")


def ensure_name_index(session) -> None:
    """Create the names index if it is missing, and make it again if it was made for other labels.

    `CREATE ... IF NOT EXISTS` keeps an index of the same name whatever labels it covers, so a database made before a label was
    added (Profession, with the trades) would keep the old index and never find the new kind. Schema changes cannot share a
    transaction with data writes."""
    found = session.run("SHOW INDEXES YIELD name, labelsOrTypes WHERE name = $name RETURN labelsOrTypes AS labels", name=INDEX_NAME).data()
    if found and set(found[0]["labels"] or []) != set(LABELS):
        session.run(f"DROP INDEX {INDEX_NAME}").consume()
    session.run(CREATE).consume()
