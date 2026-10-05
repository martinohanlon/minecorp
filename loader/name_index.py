"""The full-text index over names, shared by the loaders.

One index covers every kind of thing a player can name (items and categories, mobs, blocks, biomes,
structures, and mob and biome classes), so the agent's `find` tool and name resolution search them all at once.
Creating it is idempotent and works before any of the labels has a node, so every loader that creates named
nodes can call `ensure_name_index` and the order the loaders run in does not matter.
"""
from __future__ import annotations

INDEX_NAME = "names"
LABELS = ("Item", "Mob", "Block", "Biome", "Structure", "MobClass", "BiomeClass")

CREATE = (f"CREATE FULLTEXT INDEX {INDEX_NAME} IF NOT EXISTS "
          f"FOR (n:{'|'.join(LABELS)}) ON EACH [n.name]")


def ensure_name_index(session) -> None:
    """Create the names index if it is missing. Schema changes cannot share a transaction with data writes."""
    session.run(CREATE).consume()
