"""Search and reading of the Minecraft wiki lexical graph (docs/ontology.md v0.8). Read-only, like every tool.

    search_wiki   embed the question, vector-search the chunks, return the best passages with page and section
    read_section  the whole text of one section (and, if asked, the chunks just before and after it)

The wiki is community-written advice and explanation, so results carry a `source` note and the system prompt tells the
model to say "according to the wiki". Recipes, drops and routes still come from the game-data tools.

The question is embedded with the same model the chunks were embedded with (EMBEDDING_MODEL, default
text-embedding-3-small); each chunk records its `embeddingModel` and a mismatch is reported rather than searched.
"""
from __future__ import annotations

import os
import threading

from neo4j.exceptions import ClientError

from .graph import Graph

VECTOR_INDEX = "chunk_embedding"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_HIT_CHARS = 1200        # one search hit, so five fit in a tool result
MAX_SECTION_CHARS = 6000    # one whole section
MIN_OVERLAP = 8             # characters; shorter shared endings are not treated as the overlap between chunks
SOURCE_NOTE = ("Minecraft Wiki: community-written advice, not the game data. Say 'according to the wiki'. "
               "The wiki also covers other editions (Bedrock); this server is Java Edition.")

_client = None
_client_lock = threading.Lock()


# --------------------------------------------------------------------------- embedding the question

def embedding_model() -> str:
    """The model the chunks were embedded with."""
    return os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def embed_query(text: str) -> list[float]:
    """Embed one question with the OpenAI embeddings API (the client is built on first use)."""
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI
            _client = OpenAI(timeout=20, max_retries=1)
    return _client.embeddings.create(model=embedding_model(), input=[text]).data[0].embedding


# --------------------------------------------------------------------------- text helpers

def join_overlap(a: str, b: str) -> str:
    """Join two consecutive chunks of one section: b starts with the end of a, so that stretch is not repeated."""
    for k in range(min(len(a), len(b)), MIN_OVERLAP - 1, -1):
        if a.endswith(b[:k]):
            return a + b[k:]
    return a + "\n" + b


def stitch(texts: list[str]) -> str:
    """Rebuild the text of consecutive chunks of one section."""
    out = ""
    for i, text in enumerate(texts):
        out = text if i == 0 else join_overlap(out, text)
    return out


def clip(text: str, limit: int) -> tuple[str, bool]:
    """Cut text to `limit` characters at a word boundary. Returns (text, was it cut)."""
    if len(text) <= limit:
        return text, False
    cut = text[:limit].rsplit(" ", 1)[0].rstrip()
    return cut + " ...", True


def clamp(value, default: int, low: int, high: int) -> int:
    """An integer argument from the model, forced into [low, high]; a missing or odd value gives the default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(number, high))


def path_text(page: str, trail: list[str]) -> str:
    """'Page > Section > Subsection'; the intro section (titled like the page) is just the page."""
    return page if not trail or trail == [page] else " > ".join([page] + trail)


# --------------------------------------------------------------------------- search_wiki

def tool_search_wiki(graph: Graph, args: dict) -> dict:
    """Vector search over the chunks; hits in the same section are merged into one passage."""
    question = str(args.get("question") or "").strip()
    if not question:
        return {"error": "Give the question or topic to search for."}
    limit = clamp(args.get("limit"), 5, 1, 8)
    model = embedding_model()
    try:
        rows = graph.read(f"""
            MATCH (c:Chunk)
            SEARCH c IN (VECTOR INDEX {VECTOR_INDEX} FOR $vector LIMIT $k) SCORE AS score
            MATCH (c)-[:PART_OF]->(s:Section)
            MATCH path = (p:Page)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION*0..]->(s)
            RETURN c.index AS idx, c.text AS text, c.embeddingModel AS model, score,
                   s.url AS section, p.title AS page, [n IN nodes(path) WHERE n:Section | n.title] AS trail
            ORDER BY score DESC
            """, k=limit * 4, vector=embed_query(question))
    except ClientError as exc:
        if VECTOR_INDEX in str(exc):
            return {"error": "The wiki search is not available: the wiki has not been loaded into the graph."}
        raise
    if not rows:
        return {"question": question, "source": SOURCE_NOTE, "hits": [],
                "note": "Nothing in the wiki matched. Say you could not find it."}
    wrong = sorted({r["model"] for r in rows if r["model"] != model})
    if wrong:
        return {"error": f"The wiki was embedded with {wrong[0]} but searches use {model}; reload the wiki with {model}."}

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["section"], []).append(r)
    hits = []
    for url, rs in groups.items():
        rs.sort(key=lambda r: r["idx"])
        runs: list[list[dict]] = []
        for r in rs:                                          # chunks next to each other read as one passage
            if runs and r["idx"] == runs[-1][-1]["idx"] + 1:
                runs[-1].append(r)
            else:
                runs.append([r])
        text, _ = clip("\n[...]\n".join(stitch([r["text"] for r in run]) for run in runs), MAX_HIT_CHARS)
        hits.append({"page": rs[0]["page"], "section": path_text(rs[0]["page"], rs[0]["trail"]), "sectionUrl": url,
                     "text": text, "score": round(max(r["score"] for r in rs), 3)})
    hits.sort(key=lambda h: -h["score"])
    return {"question": question, "source": SOURCE_NOTE, "hits": hits[:limit],
            "note": "A passage may be cut short or be part of a longer section: call read_section with its sectionUrl to read more."}


# --------------------------------------------------------------------------- read_section

def _trail(graph: Graph, url: str) -> dict | None:
    """Page title and the section titles from the top of the tree down to this section."""
    rows = graph.read("""
        MATCH path = (p:Page)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION*0..]->(s:Section {url: $url})
        RETURN p.title AS page, [n IN nodes(path) WHERE n:Section | n.title] AS trail LIMIT 1
        """, url=url)
    return rows[0] if rows else None


def _neighbours(graph: Graph, chunk_id: str, count: int, before: bool) -> list[dict]:
    """The `count` chunks just before (or after) a chunk in reading order, nearest last (or first), with their section."""
    pattern = "(b:Chunk)-[:NEXT*1..3]->(:Chunk {id: $id})" if before else "(:Chunk {id: $id})-[:NEXT*1..3]->(b:Chunk)"
    order = "DESC" if before else "ASC"
    rows = graph.read(f"""
        MATCH p = {pattern} WHERE length(p) <= $count
        MATCH (b)-[:PART_OF]->(s:Section)
        RETURN s.title AS section, b.text AS text, length(p) AS d ORDER BY d {order}
        """, id=chunk_id, count=count)
    return [{"section": r["section"], "text": clip(r["text"], MAX_HIT_CHARS)[0]} for r in rows]


def tool_read_section(graph: Graph, args: dict) -> dict:
    """The full text of one section (by URL, or by its title when that is unambiguous), plus optional neighbours."""
    ref = str(args.get("section") or "").strip()
    if not ref:
        return {"error": "Give the sectionUrl from a search_wiki hit (or the section title)."}
    before = clamp(args.get("before"), 0, 0, 3)
    after = clamp(args.get("after"), 0, 0, 3)

    info = _trail(graph, ref)
    url = ref
    if info is None:                                          # not a URL: try the title
        urls = [r["url"] for r in graph.read(
            "MATCH (s:Section) WHERE toLower(s.title) = toLower($t) RETURN s.url AS url ORDER BY s.url LIMIT 6", t=ref)]
        if not urls:
            return {"error": f"No wiki section matches '{ref}'. Use the sectionUrl from a search_wiki hit."}
        if len(urls) > 1:
            return {"error": f"'{ref}' is ambiguous: give one of these sectionUrls.",
                    "candidates": [{"section": path_text(i["page"], i["trail"]), "sectionUrl": u}
                                   for u in urls if (i := _trail(graph, u))]}
        url, info = urls[0], _trail(graph, urls[0])

    chunks = graph.read("MATCH (c:Chunk)-[:PART_OF]->(:Section {url: $url}) RETURN c.id AS id, c.text AS text ORDER BY c.index", url=url)
    subsections = graph.read("MATCH (:Section {url: $url})-[:HAS_SUBSECTION]->(c:Section) RETURN c.title AS title, c.url AS url ORDER BY c.order", url=url)
    out: dict = {"page": info["page"], "section": path_text(info["page"], info["trail"]), "sectionUrl": url, "source": SOURCE_NOTE}
    if chunks:
        out["text"], cut = clip(stitch([c["text"] for c in chunks]), MAX_SECTION_CHARS)
        if cut:
            out["truncated"] = True
        if before:
            out["before"] = _neighbours(graph, chunks[0]["id"], before, before=True)
        if after:
            out["after"] = _neighbours(graph, chunks[-1]["id"], after, before=False)
    else:
        out["text"] = ""
        out["note"] = "This section has no text of its own; read one of its subsections."
    if subsections:
        out["subsections"] = [{"title": s["title"], "sectionUrl": s["url"]} for s in subsections]
    return out
