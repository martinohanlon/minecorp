"""Finding things by name: one search over every kind of named node.

Candidates come from the full-text index `names` (made by the loaders): the words as written, as a prefix
(so "pick" finds a pickaxe) and with typo tolerance (so "creepers" or "dimond" still find something). A fixed
ranking then orders them: id, exact name, name starts with the text, name contains it, all the words, some of the
words, and last the typo matches. A match that is only a typo is shown only when nothing else matched.
"""
from __future__ import annotations

import re

from .graph import Graph

INDEX = "names"

# the label that says what a node is, most specific first
KINDS = (("ItemClass", "category"), ("MobClass", "mob class"), ("BiomeClass", "biome class"),
         ("Item", "item"), ("Mob", "mob"), ("Block", "block"), ("Biome", "biome"), ("Structure", "structure"))
KIND_NAMES = tuple(k for _, k in KINDS)

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/&|])')


def lucene_words(text: str) -> list[str]:
    """The words of a search text, safe to put in a Lucene query (no operators, no wildcards)."""
    cleaned = _LUCENE_SPECIAL.sub(" ", text.replace("_", " ").replace("#", " "))
    return [w for w in cleaned.lower().split() if w and w not in ("and", "or", "not")]


def lucene_query(words: list[str]) -> str:
    """Each word as written, as a prefix and (from four letters) with typo tolerance, joined by OR."""
    parts = []
    for w in words:
        parts += [w, f"{w}*"]
        if len(w) >= 4:
            parts.append(f"{w}~")
    return " OR ".join(parts)


def search_names(graph: Graph, text: str, kinds: tuple[str, ...] | None = None, limit: int = 10) -> list[dict]:
    """Things whose name matches the text, best first. Each row has id, name, kind, options and rank.

    `kinds` limits the search to those kinds (from KIND_NAMES). Rank 0 is an id, 1 an exact name, 2 a name
    starting with the text, 3 a name containing it, 4 all the words, 5 some of the words, 6 a typo match.
    """
    t = " ".join(re.sub(r"^\s*#?minecraft:", "", text.lower()).replace("_", " ").split())
    words = lucene_words(t)
    if not words:
        return []
    slug = "_".join(words)
    rows = graph.read(
        """
        CALL () {
            CALL db.index.fulltext.queryNodes($index, $lucene, {limit: 200}) YIELD node, score
            RETURN node, score
          UNION
            MATCH (node) WHERE node.id IN [$slug, 'minecraft:' + $slug, '#minecraft:' + $slug]
            RETURN node, 0.0 AS score
        }
        WITH node, max(score) AS score
        WITH node, score, toLower(node.name) AS lname,
             size([w IN $words WHERE toLower(node.name) CONTAINS w OR node.id CONTAINS w]) AS hits
        WITH node, score, hits,
             CASE WHEN node.id IN [$slug, 'minecraft:' + $slug, '#minecraft:' + $slug] THEN 0
                  WHEN lname = $t THEN 1
                  WHEN lname STARTS WITH $t THEN 2
                  WHEN lname CONTAINS $t THEN 3
                  WHEN hits = size($words) THEN 4
                  WHEN hits >= $need THEN 5
                  ELSE 6 END AS rank
        RETURN node.id AS id, node.name AS name, labels(node) AS labels, node.size AS options, rank, hits
        ORDER BY rank, hits DESC, char_length(name), name
        LIMIT 60
        """, index=INDEX, lucene=lucene_query(words), slug=slug, words=words, t=t, need=min(2, len(words)))
    out = []
    for r in rows:
        found = next((k for label, k in KINDS if label in r["labels"]), None)
        if found is None or (kinds and found not in kinds):
            continue
        out.append({"id": r["id"], "name": r["name"], "kind": found, "options": r["options"], "rank": r["rank"],
                    "isCategory": found == "category"})
    if any(r["rank"] <= 5 for r in out):
        out = [r for r in out if r["rank"] <= 5]
    return out[:limit]
