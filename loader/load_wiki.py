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


LINK_START, LINK_END = "", ""        # private-use markers around link text while a page is parsed
NON_ARTICLE_NAMESPACES = {"file", "category", "special", "template", "help", "user", "user talk", "talk", "mediawiki",
                          "module", "draft"}


def link_title(href: str, classes) -> str | None:
    """The wiki page a link points to, as a title (spaces, no fragment), or None for links that are not to an article:
    red links (no such page), external links, same-page anchors, icons, and file, category or special pages."""
    if not href or {"new", "external", "image"} & set(classes) or href.startswith(("#", "http://", "https://", "//", "mailto:")):
        return None
    parsed = urlparse(href)
    if parsed.path.startswith("/w/"):
        title = unquote(parsed.path[3:])
    elif parsed.path.endswith("index.php") and "title" in parse_qs(parsed.query):
        title = parse_qs(parsed.query)["title"][0]
    else:
        return None
    title = normalise_space(title.replace("_", " "))
    if not title or title.split(":", 1)[0].lower() in NON_ARTICLE_NAMESPACES and ":" in title:
        return None
    return title


def strip_link_markers(blocks: list[str], records: list[dict]) -> tuple[list[str], list[dict]]:
    """Remove the link markers from a section's blocks and turn the recorded links into
    [{title, text, start, end}], where start and end are character positions in "\\n".join(blocks) (markers gone)."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    start = 0
    for ch in "\n".join(blocks):
        if ch == LINK_START:
            start = len(out)
        elif ch == LINK_END:
            spans.append((start, len(out)))
        else:
            out.append(ch)
    clean = "".join(out)
    links = []
    for record, (s, e) in zip(records, spans):
        title = link_title(record["href"], record["classes"])
        if title and e > s:
            links.append({"title": title, "text": clean[s:e], "start": s, "end": e})
    return (clean.split("\n") if clean else []), links


class _SectionParser(HTMLParser):
    """Walks parsed wiki HTML and collects, per heading, the text blocks under it.

    Paragraphs, list items and table rows each become one line (list items start with "- ", table cells are
    separated by " | "). Edit links, references, navigation boxes, scripts and styles are left out.
    Links are kept as markers around their text, resolved to positions by `strip_link_markers`.
    """

    def __init__(self, page_title: str):
        super().__init__(convert_charrefs=True)
        self.sections = [{"title": page_title, "level": 1, "anchor": "", "blocks": [], "records": []}]
        self.current = self.sections[0]
        self.stack: list[tuple[str, bool]] = []       # (tag, started a skipped region)
        self.skip = 0
        self.buf: list[str] = []
        self.prefix = ""
        self.heading: dict | None = None
        self.link: dict | None = None                 # the link being read: {href, classes}

    def flush(self) -> None:
        self.end_link()                                        # a link never spans two lines
        text = normalise_space("".join(self.buf))
        self.buf = []
        if text.replace(LINK_START, "").replace(LINK_END, "").strip():
            self.current["blocks"].append(self.prefix + text)
            self.prefix = ""

    def end_link(self) -> None:
        """Close the link being read, if any. The end marker goes right after the last visible character, so
        whitespace inside the anchor but around its text stays outside the link (and collapses normally)."""
        link, self.link = self.link, None
        if link is None or not link["started"]:                # no visible text (an icon): nothing to record
            return
        tail = ""
        while self.buf and not self.buf[-1].strip():
            tail = self.buf.pop() + tail
        last = self.buf.pop()
        tail = last[len(last.rstrip()):] + tail
        self.buf.append(last.rstrip())
        self.buf.append(LINK_END)
        if tail:
            self.buf.append(tail)
        self.current["records"].append(link)

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
        if tag == "a" and self.heading is None:
            self.end_link()                                    # a link inside a link is invalid; keep the outer one whole
            self.link = {"href": a.get("href", ""), "classes": classes, "started": False}
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
        if tag == "a":
            self.end_link()
        if tag in HEADING_TAGS and self.heading is not None:
            title = normalise_space("".join(self.heading["parts"]))
            if title:
                self.current = {"title": title, "level": self.heading["level"],
                                "anchor": self.heading["anchor"] or slug(title), "blocks": [], "records": []}
                self.sections.append(self.current)
            self.heading = None
        elif tag in BLOCK_TAGS:
            self.end_link()                                    # a link never spans two lines
            self.flush()
            if tag == "li":
                self.prefix = ""

    def handle_data(self, data):
        if self.skip:
            return
        if self.heading is not None:
            self.heading["parts"].append(data)
            return
        if self.link is not None and not self.link["started"] and data.strip():
            lead = data[:len(data) - len(data.lstrip())]       # the start marker goes before the first visible character
            self.buf += [lead, LINK_START, data[len(lead):]]
            self.link["started"] = True
            return
        self.buf.append(data)

    def close(self):
        super().close()
        self.end_link()
        self.flush()


def parse_sections(html_text: str, page_title: str) -> list[dict]:
    """Split page HTML into sections in document order: [{title, level, anchor, blocks, links}]. The first is the intro.

    `links` are the article links in the section as [{title, text, start, end}] (positions in "\\n".join(blocks)).
    """
    parser = _SectionParser(page_title)
    parser.feed(html_text)
    parser.close()
    for section in parser.sections:
        section["blocks"], section["links"] = strip_link_markers(section["blocks"], section.pop("records"))
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
                "order": len(out), "parent": None, "path": page_title, "text": text, "links": section.get("links", [])}
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


def locate_chunks(text: str, chunks: list[str]) -> list[int | None]:
    """Where each chunk starts in the section text it was cut from (chunks are exact substrings, in order)."""
    starts: list[int | None] = []
    pos = 0
    for chunk in chunks:
        start = text.find(chunk, pos)
        starts.append(start if start >= 0 else None)
        if start >= 0:
            pos = start + 1
    return starts


def build(title: str, revision_id: int, html_text: str, fetched_at: str, tok, size: int = CHUNK_TOKENS,
          overlap: int = OVERLAP_TOKENS) -> dict:
    """Page HTML to the rows of the graph: wiki, page, sections (tree) and chunks (reading order, no embeddings yet).

    Each chunk carries `rawLinks`: the article links whose text starts inside it, as [{title, text}] with the titles as
    written in the page. A link in the overlap between two chunks belongs to both. `apply_links` turns them into pages.
    """
    url = page_url(title)
    sections = build_tree(parse_sections(html_text, title), title, url)
    chunks: list[dict] = []
    for section in sections:
        texts = chunk_text(section["text"], tok, size, overlap)
        for n, (text, start) in enumerate(zip(texts, locate_chunks(section["text"], texts))):
            embed_text = f"{section['path']}\n\n{text}"
            raw = [] if start is None else [{"title": l["title"], "text": l["text"]} for l in section["links"]
                                            if start <= l["start"] < start + len(text)]
            chunks.append({"id": f"{section['url']}~{n}", "section": section["url"], "index": len(chunks), "text": text,
                           "tokens": tok.count(text), "embedText": embed_text, "rawLinks": raw,
                           "textHash": hashlib.sha256(embed_text.encode("utf-8")).hexdigest()})
    return {"wiki": {"url": WIKI_URL, "name": WIKI_NAME},
            "page": {"url": url, "title": title, "revisionId": revision_id, "fetchedAt": fetched_at},
            "sections": sections, "chunks": chunks, "size": size, "overlap": overlap}


def resolve_titles(titles: list[str], client: httpx.Client | None = None) -> dict[str, dict | None]:
    """What each link target is, through the MediaWiki API: {title as linked: {page, target}}.

    `page` is the page that was linked (first letter capitalised by the wiki); `target` is the page it redirects to, or
    None when it is not a redirect. The value is None for a page that does not exist. A redirect is a fact about the
    wiki, not something to hide: "Black Bed" is a page that redirects to "Bed", and the link meant Black Bed.
    Asks 50 titles at a time.
    """
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
    out: dict[str, dict | None] = {}
    try:
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            response = client.get(API_URL, params={"action": "query", "titles": "|".join(batch), "redirects": "1",
                                                   "format": "json", "formatversion": "2"})
            response.raise_for_status()
            query = response.json().get("query", {})
            normalised = {n["from"]: n["to"] for n in query.get("normalized", [])}
            redirected = {r["from"]: r["to"] for r in query.get("redirects", [])}
            exists = {p["title"]: not (p.get("missing") or p.get("invalid")) for p in query.get("pages", [])}
            for title in batch:
                page = normalised.get(title, title)
                if page in redirected:
                    target = redirected[page]
                    out[title] = {"page": page, "target": target if exists.get(target, False) else None}
                else:
                    out[title] = {"page": page, "target": None} if exists.get(page, False) else None
    finally:
        if own:
            client.close()
    return out


def apply_links(g: dict, resolved: dict[str, dict | None]) -> dict:
    """Turn each chunk's `rawLinks` into `links` and list the pages and redirects they involve.

    A link goes to the page that was linked, as written ("Black Bed"), never silently to where it redirects. A chunk gets
    one entry per page: {url, title, count, texts}. If the page is a redirect, `g["redirects"]` has {url, title, to, toTitle}
    so the graph can hold (Page)-[:REDIRECTS_TO]->(Page). `g["linkedPages"]` is every page involved, as [{url, title}].
    Links to pages that do not exist and to the page itself (or a redirect to it) are dropped; a title the resolver was
    not asked about is taken as it is.
    """
    own = g["page"]["title"]
    pages: dict[str, str] = {}
    redirects: dict[str, dict] = {}
    for chunk in g["chunks"]:
        grouped: dict[str, dict] = {}
        for link in chunk.get("rawLinks", []):
            info = resolved.get(link["title"], {"page": link["title"], "target": None})
            if info is None or own in (info["page"], info["target"]):
                continue
            entry = grouped.setdefault(info["page"].lower(), {"url": page_url(info["page"]), "title": info["page"], "count": 0, "texts": []})
            entry["count"] += 1
            if link["text"] not in entry["texts"]:
                entry["texts"].append(link["text"])
            pages[entry["url"]] = entry["title"]
            if info["target"]:
                pages[page_url(info["target"])] = info["target"]
                redirects[entry["url"]] = {"url": entry["url"], "title": entry["title"], "to": page_url(info["target"]), "toTitle": info["target"]}
        chunk["links"] = list(grouped.values())
    g["linkedPages"] = [{"url": u, "title": t} for u, t in sorted(pages.items())]
    g["redirects"] = [redirects[u] for u in sorted(redirects)]
    return g


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
            # Pages that are only linked to become stubs (url and title, nothing fetched). A page that is already
            # loaded keeps all its properties; loading a stub later fills in the same node.
            s.run("""UNWIND $rows AS r
                     MERGE (p:Page {url: r.url}) ON CREATE SET p.title = r.title
                     WITH p MATCH (w:Wiki {url: $wiki}) MERGE (w)-[:HAS_PAGE]->(p)""",
                  rows=g.get("linkedPages", []), wiki=g["wiki"]["url"])
            links = [{"chunk": c["id"], **link} for c in chunks for link in c.get("links", [])]
            for i in range(0, len(links), 500):
                s.run("""UNWIND $rows AS r MATCH (c:Chunk {id: r.chunk}), (p:Page {url: r.url})
                         CREATE (c)-[:LINKS_TO {count: r.count, texts: r.texts}]->(p)""", rows=links[i:i + 500])
            s.run("""UNWIND $rows AS r MATCH (a:Page {url: r.url}), (b:Page {url: r.to}) MERGE (a)-[:REDIRECTS_TO]->(b)""",
                  rows=g.get("redirects", []))


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
    if "linkedPages" in g:
        total = sum(link["count"] for c in g["chunks"] for link in c.get("links", []))
        lines.append(f"links: {total} in chunks, to {len(g['linkedPages'])} other pages (stubs unless loaded)")
        if g.get("redirects"):
            lines.append(f"{len(g['redirects'])} of those pages are redirects (kept as REDIRECTS_TO): "
                         f"{[f'{r['title']} -> {r['toTitle']}' for r in g['redirects'][:6]]}")
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
    raw_titles = sorted({link["title"] for c in g["chunks"] for link in c["rawLinks"]})
    try:
        apply_links(g, resolve_titles(raw_titles) if raw_titles else {})      # redirects followed, missing pages dropped
    except httpx.HTTPError as exc:
        sys.exit(f"could not resolve the links: {exc}")
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
    print(f"loaded {len(g['sections'])} sections, {len(g['chunks'])} chunks and links to {len(g['linkedPages'])} pages into {s.neo4j_database}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
