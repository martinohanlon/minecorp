"""Load a page of the Minecraft wiki as a lexical graph (docs/ontology.md v0.8).

    python loader/load_wiki.py --dry-run          # fetch, split, chunk and report; no OpenAI, no database
    python loader/load_wiki.py                    # embed (cache first) and load
    python loader/load_wiki.py --page "Tutorial:Beginner's guide" --size 300 --overlap 50 --wipe

The graph:
    (Wiki)-[:HAS_PAGE]->(Page)-[:HAS_SECTION]->(Section)-[:HAS_SUBSECTION]->(Section)      the section tree
    (Chunk {text, tokens, textHash, embedding, embeddingModel})-[:PART_OF]->(Section)
    (Chunk)-[:NEXT]->(Chunk)                                                               reading order, whole page

How the text becomes chunks:
- The page comes from the MediaWiki API (action=parse) and is split into sections by its headings.
  The text before the first heading is a section titled with the page title (dropped if it has no text).
- A chunk never crosses a heading. A section of `--size` tokens or fewer is one chunk; a longer one is cut
  at sentence boundaries (word boundaries for a very long sentence) into chunks of at most `--size` tokens,
  each starting with about `--overlap` tokens from the end of the one before. Tokens are counted with
  tiktoken (cl100k_base, the encoding of the OpenAI embedding models).
- The text that is embedded is the heading path ("Page > Section > Subsection"), a blank line, then the chunk.
  The stored `text` is the chunk alone.

Embeddings are cached in a local shelve file (loader/cache/, git-ignored) keyed by model and the sha256 of the
exact embedded text, so wiping the graph and loading again makes no OpenAI calls for text that has not changed.
Vectors are kept as float32 bytes, and a first load uses the same rounded values as a rebuild from the cache.

Wiki text is CC BY-NC-SA: every node keeps its source URL. `--wipe` removes only Wiki, Page, Section and Chunk nodes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shelve
import statistics
import sys
from array import array
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import httpx  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from minecorp_agent.config import get_settings  # noqa: E402

WIKI_URL = "https://minecraft.wiki"
WIKI_NAME = "Minecraft Wiki"
API_URL = f"{WIKI_URL}/api.php"
DEFAULT_PAGE = "Tutorial:Beginner's guide"
USER_AGENT = "MineCorpLoader/0.1 (+https://github.com/martinohanlon/minecorp)"
DEFAULT_MODEL = "text-embedding-3-small"
CHUNK_TOKENS = 300
OVERLAP_TOKENS = 50
EMBED_BATCH = 64
CACHE_PATH = ROOT / "loader" / "cache" / "embeddings"
VECTOR_INDEX = "chunk_embedding"
PRICE_PER_MILLION_TOKENS = {"text-embedding-3-small": 0.02, "text-embedding-3-large": 0.13}   # US dollars


# --------------------------------------------------------------------------- names and fetching

class WikiError(RuntimeError):
    """The wiki did not return the page."""


def page_url(title: str) -> str:
    """Canonical URL of a page: spaces become underscores and an apostrophe is written %27."""
    return f"{WIKI_URL}/w/" + quote(title.replace(" ", "_"), safe=":/(),!*")


def title_from_url(page: str) -> str:
    """Accept a wiki URL (/w/Title or index.php?title=Title) or a bare title; return the title with spaces."""
    if not page.startswith(("http://", "https://")):
        return page.replace("_", " ").strip()
    parsed = urlparse(page)
    if "title" in parse_qs(parsed.query):
        return parse_qs(parsed.query)["title"][0].replace("_", " ")
    path = unquote(parsed.path)
    return path.removeprefix("/w/").strip("/").replace("_", " ")


def fetch_page(title: str, client: httpx.Client | None = None) -> dict:
    """Fetch a page through the MediaWiki API. Returns {title, revisionId, html}."""
    params = {"action": "parse", "page": title, "prop": "text|revid", "format": "json", "formatversion": "2",
              "redirects": "1", "disableeditsection": "1", "disabletoc": "1", "disablelimitreport": "1"}
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
    try:
        response = client.get(API_URL, params=params)
        response.raise_for_status()
        body = response.json()
    finally:
        if own:
            client.close()
    if "error" in body:
        raise WikiError(f"{title!r}: {body['error'].get('info', body['error'])}")
    parsed = body["parse"]
    return {"title": parsed["title"], "revisionId": parsed["revid"], "html": parsed["text"]}


# --------------------------------------------------------------------------- HTML to sections

BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "tr", "table", "blockquote", "pre", "figure",
              "figcaption", "caption", "section", "center", "details", "summary", "tbody", "thead"}
HEADING_TAGS = {"h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
VOID_TAGS = {"br", "img", "hr", "wbr", "input", "meta", "link", "source", "col", "area", "base", "embed", "param", "track"}
SKIP_TAGS = {"script", "style", "noscript", "template"}
SKIP_CLASSES = {"mw-editsection", "reference", "references", "mw-references-wrap", "toc", "navbox", "noprint",
                "mw-cite-backlink", "mw-empty-elt"}
SKIP_CLASS_PREFIXES = ("mcui", "invslot")      # recipe grids and item slots: pictures whose only text is stack counts


ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")


def normalise_space(text: str) -> str:
    """Drop zero-width characters and collapse every run of whitespace (including non-breaking spaces) to one space."""
    return re.sub(r"\s+", " ", ZERO_WIDTH.sub("", text).replace("\xa0", " ")).strip()


def slug(title: str) -> str:
    """An anchor for a heading that has none: MediaWiki style (underscores for spaces)."""
    return normalise_space(title).replace(" ", "_")


class _SectionParser(HTMLParser):
    """Walks parsed wiki HTML and collects, per heading, the text blocks under it.

    Paragraphs, list items and table rows each become one line (list items start with "- ", table cells are
    separated by " | "). Edit links, references, navigation boxes, scripts and styles are left out.
    """

    def __init__(self, page_title: str):
        super().__init__(convert_charrefs=True)
        self.sections = [{"title": page_title, "level": 1, "anchor": "", "blocks": []}]
        self.current = self.sections[0]
        self.stack: list[tuple[str, bool]] = []       # (tag, started a skipped region)
        self.skip = 0
        self.buf: list[str] = []
        self.prefix = ""
        self.heading: dict | None = None

    def flush(self) -> None:
        text = normalise_space("".join(self.buf))
        self.buf = []
        if text:
            self.current["blocks"].append(self.prefix + text)
            self.prefix = ""

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            if tag == "br" and not self.skip:
                (self.heading["parts"] if self.heading else self.buf).append(" ")
            return
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        hidden = "display:none" in (a.get("style") or "").replace(" ", "")
        picture = any(c.startswith(SKIP_CLASS_PREFIXES) for c in classes)
        starts_skip = not self.skip and (tag in SKIP_TAGS or bool(classes & SKIP_CLASSES) or picture or hidden)
        self.stack.append((tag, starts_skip))
        if starts_skip:
            self.skip += 1
        if self.skip:
            return
        if tag in HEADING_TAGS:
            self.flush()
            self.heading = {"level": HEADING_TAGS[tag], "anchor": a.get("id"), "parts": []}
        elif self.heading is not None:
            if a.get("id") and not self.heading["anchor"]:       # older markup: <h2><span class="mw-headline" id=...>
                self.heading["anchor"] = a["id"]
        elif tag in BLOCK_TAGS:
            self.flush()
            if tag == "li":
                self.prefix = "- "
        elif tag in ("td", "th") and "".join(self.buf).strip():
            self.buf.append(" | ")

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            top, starts_skip = self.stack.pop()
            if starts_skip:
                self.skip -= 1
            if top == tag:
                break
        if self.skip:
            return
        if tag in HEADING_TAGS and self.heading is not None:
            title = normalise_space("".join(self.heading["parts"]))
            if title:
                self.current = {"title": title, "level": self.heading["level"],
                                "anchor": self.heading["anchor"] or slug(title), "blocks": []}
                self.sections.append(self.current)
            self.heading = None
        elif tag in BLOCK_TAGS:
            self.flush()
            if tag == "li":
                self.prefix = ""

    def handle_data(self, data):
        if self.skip:
            return
        (self.heading["parts"] if self.heading is not None else self.buf).append(data)

    def close(self):
        super().close()
        self.flush()


def parse_sections(html_text: str, page_title: str) -> list[dict]:
    """Split page HTML into sections in document order: [{title, level, anchor, blocks}]. The first is the intro."""
    parser = _SectionParser(page_title)
    parser.feed(html_text)
    parser.close()
    return parser.sections


def build_tree(raw: list[dict], page_title: str, url: str) -> list[dict]:
    """Turn the flat list into the section tree.

    Each result has url (page URL plus #anchor), title, level, order (document order from 0), parent (url or None
    for a top-level section), path ("Page > Section > Subsection") and text. An intro with no text is dropped.
    """
    out: list[dict] = []
    stack: list[dict] = []
    seen: dict[str, int] = {}
    for section in raw:
        text = "\n".join(section["blocks"])
        if section["level"] == 1 and not text:
            continue
        anchor = section["anchor"]
        if anchor:                                           # MediaWiki makes repeated anchors unique with _2, _3...
            seen[anchor] = seen.get(anchor, 0) + 1
            if seen[anchor] > 1:
                anchor = f"{anchor}_{seen[anchor]}"
        node = {"url": f"{url}#{anchor}" if anchor else url, "title": section["title"], "level": section["level"],
                "order": len(out), "parent": None, "path": page_title, "text": text}
        if section["level"] > 1:
            while stack and stack[-1]["level"] >= section["level"]:
                stack.pop()
            if stack:
                node["parent"] = stack[-1]["url"]
            node["path"] = f"{stack[-1]['path'] if stack else page_title} > {section['title']}"
            stack.append(node)
        out.append(node)
    return out


# --------------------------------------------------------------------------- chunking

class TikTokenizer:
    """Counts tokens with tiktoken's cl100k_base, the encoding used by the OpenAI embedding models."""

    def __init__(self, encoding: str = "cl100k_base"):
        import tiktoken
        self._enc = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        return len(self._enc.encode(text, disallowed_special=()))


SENTENCE_END = re.compile(r"""(?<=[.!?])\s+(?=[A-Z0-9"'(\[])""")


def split_units(text: str) -> list[tuple[str, str]]:
    """Cut text into sentences: [(separator before, sentence)]. The separator is a newline between lines."""
    units: list[tuple[str, str]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for i, sentence in enumerate(SENTENCE_END.split(line)):
            units.append(("" if not units else "\n" if i == 0 else " ", sentence))
    return units


def render(units: list[tuple[str, str]]) -> str:
    """Join units, ignoring the separator before the first."""
    return "".join((sep if i else "") + text for i, (sep, text) in enumerate(units))


def split_long(text: str, size: int, tok) -> list[str]:
    """Cut one over-long sentence at word boundaries into pieces of at most `size` tokens (a single huge word stays whole)."""
    pieces: list[str] = []
    current: list[str] = []
    for word in text.split(" "):
        if current and tok.count(" ".join(current + [word])) > size:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def overlap_tail(units: list[tuple[str, str]], overlap: int, tok) -> list[tuple[str, str]]:
    """The end of a finished chunk to repeat at the start of the next: whole sentences up to `overlap` tokens,
    or, when even the last sentence is longer, its last words up to `overlap` tokens."""
    tail: list[tuple[str, str]] = []
    for unit in reversed(units):
        if tok.count(render([unit] + tail)) <= overlap:
            tail = [unit] + tail
        else:
            break
    if not tail and overlap > 0:
        words = units[-1][1].split(" ")
        take: list[str] = []
        for word in reversed(words):
            if tok.count(" ".join([word] + take)) <= overlap:
                take = [word] + take
            else:
                break
        if take and len(take) < len(words):
            tail = [(" ", " ".join(take))]
    return tail


def chunk_text(text: str, tok, size: int = CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[str]:
    """Cut one section's text into chunks of at most `size` tokens (a section that fits is one chunk)."""
    units: list[tuple[str, str]] = []
    for sep, sentence in split_units(text):
        if tok.count(sentence) > size:
            units += [(sep if i == 0 else " ", p) for i, p in enumerate(split_long(sentence, size, tok))]
        else:
            units.append((sep, sentence))
    if not units:
        return []
    chunks: list[str] = []
    current: list[tuple[str, str]] = []
    for unit in units:
        if current and tok.count(render(current + [unit])) > size:
            chunks.append(render(current))
            current = overlap_tail(current, overlap, tok)
            while current and tok.count(render(current + [unit])) > size:
                current = current[1:]                       # make room: give up the oldest overlap first
        current = current + [unit]
    chunks.append(render(current))
    return chunks


def build(title: str, revision_id: int, html_text: str, fetched_at: str, tok, size: int = CHUNK_TOKENS,
          overlap: int = OVERLAP_TOKENS) -> dict:
    """Page HTML to the rows of the graph: wiki, page, sections (tree) and chunks (reading order, no embeddings yet)."""
    url = page_url(title)
    sections = build_tree(parse_sections(html_text, title), title, url)
    chunks: list[dict] = []
    for section in sections:
        for n, text in enumerate(chunk_text(section["text"], tok, size, overlap)):
            embed_text = f"{section['path']}\n\n{text}"
            chunks.append({"id": f"{section['url']}~{n}", "section": section["url"], "index": len(chunks), "text": text,
                           "tokens": tok.count(text), "embedText": embed_text,
                           "textHash": hashlib.sha256(embed_text.encode("utf-8")).hexdigest()})
    return {"wiki": {"url": WIKI_URL, "name": WIKI_NAME},
            "page": {"url": url, "title": title, "revisionId": revision_id, "fetchedAt": fetched_at},
            "sections": sections, "chunks": chunks, "size": size, "overlap": overlap}


# --------------------------------------------------------------------------- embeddings and their cache

def encode_vector(vector) -> bytes:
    """Floats to float32 bytes (what the cache and the graph both hold)."""
    return array("f", vector).tobytes()


def decode_vector(raw: bytes) -> list[float]:
    values = array("f")
    values.frombytes(raw)
    return list(values)


class EmbeddingCache:
    """A shelve file of {model|sha256 of the embedded text: float32 bytes}; survives a wipe of the graph."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = shelve.open(str(path))

    @staticmethod
    def key(model: str, text_hash: str) -> str:
        return f"{model}|{text_hash}"

    def get(self, model: str, text_hash: str) -> list[float] | None:
        raw = self.db.get(self.key(model, text_hash))
        return None if raw is None else decode_vector(raw)

    def has(self, model: str, text_hash: str) -> bool:
        return self.key(model, text_hash) in self.db

    def put(self, model: str, text_hash: str, vector) -> None:
        self.db[self.key(model, text_hash)] = encode_vector(vector)

    def sync(self) -> None:
        self.db.sync()

    def __len__(self) -> int:
        return len(self.db)

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def cache_exists(path: Path) -> bool:
    """Whether a shelve file is already there (its extension depends on the platform's dbm)."""
    return path.parent.exists() and any(path.parent.glob(path.name + "*"))


def openai_embedder(model: str, client=None):
    """A function from a list of texts to a list of vectors, using the OpenAI embeddings API."""
    if client is None:
        from openai import OpenAI
        client = OpenAI()

    def embed(texts: list[str]) -> list[list[float]]:
        response = client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]

    return embed


def embed_chunks(chunks: list[dict], cache: EmbeddingCache, model: str, embedder, batch: int = EMBED_BATCH) -> dict:
    """Put an `embedding` on every chunk, calling `embedder` only for text the cache has not seen.

    Each batch is saved as soon as it arrives, so a failure part-way loses nothing already paid for.
    Returns {"chunks", "fromCache" (chunks needing no call), "embedded" (distinct texts sent)}.
    """
    missing: dict[str, str] = {}
    for c in chunks:
        if c["textHash"] not in missing and not cache.has(model, c["textHash"]):
            missing[c["textHash"]] = c["embedText"]
    items = list(missing.items())
    for i in range(0, len(items), batch):
        part = items[i:i + batch]
        vectors = embedder([text for _, text in part])
        if len(vectors) != len(part):
            raise RuntimeError(f"asked for {len(part)} embeddings, got {len(vectors)}")
        for (text_hash, _), vector in zip(part, vectors):
            cache.put(model, text_hash, vector)
        cache.sync()
    for c in chunks:
        c["embedding"] = cache.get(model, c["textHash"])
    return {"chunks": len(chunks), "fromCache": sum(1 for c in chunks if c["textHash"] not in missing), "embedded": len(items)}


# --------------------------------------------------------------------------- Neo4j

def write(g: dict, model: str, uri: str, user: str, password: str, database: str, wipe: bool = False) -> None:
    """Write the page. Loading a page again replaces its sections and chunks; `wipe` first removes every wiki node."""
    chunks = g["chunks"]
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session(database=database) as s:
            for label, prop in (("Wiki", "url"), ("Page", "url"), ("Section", "url"), ("Chunk", "id")):
                s.run(f"CREATE CONSTRAINT {label.lower()}_{prop} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE")
            if chunks:
                dims = len(chunks[0]["embedding"])
                s.run(f"""CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS FOR (c:Chunk) ON c.embedding
                          OPTIONS {{indexConfig: {{`vector.dimensions`: {dims}, `vector.similarity_function`: 'cosine'}}}}""")
            if wipe:
                s.run("MATCH (n) WHERE n:Chunk OR n:Section OR n:Page OR n:Wiki DETACH DELETE n")
            s.run("""MATCH (p:Page {url: $url})-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION*0..]->(s:Section)
                     OPTIONAL MATCH (s)<-[:PART_OF]-(c:Chunk)
                     DETACH DELETE c, s""", url=g["page"]["url"])
            s.run("""MERGE (w:Wiki {url: $wiki.url}) SET w.name = $wiki.name
                     MERGE (p:Page {url: $page.url})
                     SET p.title = $page.title, p.revisionId = $page.revisionId, p.fetchedAt = $page.fetchedAt
                     MERGE (w)-[:HAS_PAGE]->(p)""", wiki=g["wiki"], page=g["page"])
            s.run("""UNWIND $rows AS r
                     CREATE (:Section {url: r.url, title: r.title, level: r.level, order: r.order})""", rows=g["sections"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.parent IS NULL
                     MATCH (p:Page {url: $page}), (s:Section {url: r.url}) CREATE (p)-[:HAS_SECTION]->(s)""",
                  rows=g["sections"], page=g["page"]["url"])
            s.run("""UNWIND $rows AS r WITH r WHERE r.parent IS NOT NULL
                     MATCH (a:Section {url: r.parent}), (b:Section {url: r.url}) CREATE (a)-[:HAS_SUBSECTION]->(b)""",
                  rows=g["sections"])
            for i in range(0, len(chunks), 100):
                rows = [{"id": c["id"], "section": c["section"], "index": c["index"], "text": c["text"], "tokens": c["tokens"],
                         "textHash": c["textHash"], "embedding": c["embedding"]} for c in chunks[i:i + 100]]
                s.run("""UNWIND $rows AS r
                         CREATE (c:Chunk {id: r.id, index: r.index, text: r.text, tokens: r.tokens,
                                          textHash: r.textHash, embeddingModel: $model})
                         WITH c, r CALL db.create.setNodeVectorProperty(c, 'embedding', r.embedding)
                         WITH c, r MATCH (s:Section {url: r.section}) CREATE (c)-[:PART_OF]->(s)""", rows=rows, model=model)
            s.run("""UNWIND $pairs AS p MATCH (a:Chunk {id: p[0]}), (b:Chunk {id: p[1]}) CREATE (a)-[:NEXT]->(b)""",
                  pairs=[[a["id"], b["id"]] for a, b in zip(chunks, chunks[1:])])


# --------------------------------------------------------------------------- report

def report(g: dict, model: str) -> list[str]:
    """The section tree with chunk counts, the chunk-size spread and a cost estimate, as lines of text."""
    per_section: dict[str, list[dict]] = {}
    for c in g["chunks"]:
        per_section.setdefault(c["section"], []).append(c)
    depth: dict[str, int] = {}
    lines = [f"{g['page']['title']}  (revision {g['page']['revisionId']}, chunks of {g['size']} tokens, overlap {g['overlap']})"]
    for s in g["sections"]:
        depth[s["url"]] = 0 if s["parent"] is None else depth[s["parent"]] + 1
        mine = per_section.get(s["url"], [])
        lines.append(f"  {'  ' * depth[s['url']]}{s['title']}  [h{s['level']}]  {sum(c['tokens'] for c in mine)} tokens, {len(mine)} chunks")
    tokens = [c["tokens"] for c in g["chunks"]]
    embed_tokens = sum(c["tokens"] for c in g["chunks"])
    lines.append(f"{len(g['sections'])} sections, {len(g['chunks'])} chunks")
    if tokens:
        lines.append(f"chunk tokens: min {min(tokens)}, median {int(statistics.median(tokens))}, max {max(tokens)}; "
                     f"sections with more than one chunk: {sum(1 for v in per_section.values() if len(v) > 1)}")
        price = PRICE_PER_MILLION_TOKENS.get(model)
        if price is not None:
            lines.append(f"embedding {embed_tokens} tokens (plus headings) with {model} costs about ${embed_tokens * price / 1e6:.5f} if nothing is cached")
    return lines


# --------------------------------------------------------------------------- command line

def main() -> int:
    """Fetch, split, chunk, report and (unless --dry-run) embed and load."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", default=DEFAULT_PAGE, help="page title or wiki URL")
    ap.add_argument("--size", type=int, default=CHUNK_TOKENS, help="maximum tokens per chunk")
    ap.add_argument("--overlap", type=int, default=OVERLAP_TOKENS, help="tokens repeated from the previous chunk")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI embedding model")
    ap.add_argument("--cache", type=Path, default=CACHE_PATH, help="embedding cache (shelve file)")
    ap.add_argument("--dry-run", action="store_true", help="report only; no OpenAI calls, no database")
    ap.add_argument("--wipe", action="store_true", help="remove every Wiki, Page, Section and Chunk node first")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")                # wiki titles can hold characters the Windows console cannot show
    if not 0 <= args.overlap < args.size:
        sys.exit("--overlap must be at least 0 and less than --size")

    s = get_settings()                                      # also reads .env (OPENAI_API_KEY, Neo4j)
    title = title_from_url(args.page)
    try:
        fetched = fetch_page(title)
    except (WikiError, httpx.HTTPError) as exc:
        sys.exit(f"could not fetch the page: {exc}")
    g = build(fetched["title"], fetched["revisionId"], fetched["html"],
              datetime.now(timezone.utc).isoformat(timespec="seconds"), TikTokenizer(), args.size, args.overlap)
    print("\n".join(report(g, args.model)))
    if args.dry_run:
        if cache_exists(args.cache) and g["chunks"]:
            with EmbeddingCache(args.cache) as cache:
                have = sum(1 for c in g["chunks"] if cache.has(args.model, c["textHash"]))
            print(f"{have} of {len(g['chunks'])} chunks are already in the embedding cache")
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (put it in .env)")
    with EmbeddingCache(args.cache) as cache:
        stats = embed_chunks(g["chunks"], cache, args.model, openai_embedder(args.model))
    print(f"embeddings: {stats['fromCache']} chunks from the cache, {stats['embedded']} texts sent to OpenAI")
    write(g, args.model, s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database, wipe=args.wipe)
    print(f"loaded {len(g['sections'])} sections and {len(g['chunks'])} chunks into {s.neo4j_database}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
