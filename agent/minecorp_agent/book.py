"""The explain book: the pages a player is shown when they ask how their recent answers were made (docs/explain-book.md).

The service builds it and the plugin only draws it, so the layout is written once. A book is plain data:

    {"title": "Agent notes", "author": "MineCorp Agent",
     "pages": [{"lines": [{"text": "Agent notes"}, {"text": "1. How do I...", "goto": 2}, {"text": "- wiki: Tutorial:Mining", "url": "https://..."}]}]}

A line is one line of text. `url` makes it a link to open; `goto` makes it jump to that page (counted from 1). Page 1 is the contents.
No page holds more than MAX_PAGE_CHARS characters (a written book holds about 1,024), so nothing is cut by the game.
"""
from __future__ import annotations

from .history import Exchange

MAX_PAGE_CHARS = 700
TITLE, AUTHOR = "Agent notes", "MineCorp Agent"
NO_SOURCES = "No sources: I answered without looking anything up."
CONTENTS_QUESTION_CHARS = 40


def cut_long_lines(lines: list[dict], limit: int) -> list[dict]:
    """Lines no longer than `limit`: a longer one is cut at a space (or at the limit when it has none), keeping its link."""
    out = []
    for line in lines:
        text = line["text"]
        while len(text) > limit:
            cut = text.rfind(" ", 0, limit + 1)                 # a space just after the limit still lets `limit` characters through
            cut = cut if cut > 0 else limit
            out.append({**line, "text": text[:cut]})
            text = text[cut:].lstrip()
        out.append({**line, "text": text})
    return out


def paginate(lines: list[dict], limit: int = MAX_PAGE_CHARS) -> list[list[dict]]:
    """Lines packed into pages of at most `limit` characters (a newline counts as one), breaking between lines."""
    pages: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for line in cut_long_lines(lines, limit):
        cost = len(line["text"]) + (1 if current else 0)
        if current and size + cost > limit:
            pages.append(current)
            current, size = [line], len(line["text"])
        else:
            current.append(line)
            size += cost
    if current:
        pages.append(current)
    return pages or [[{"text": ""}]]


def source_lines(sources) -> list[dict]:
    """'- wiki: Tutorial:Mining > Mining layers' for each source, a link when it has an address."""
    return [{"text": f"- {s['kind']}: {s['label']}", **({"url": s["url"]} if s.get("url") else {})} for s in sources]


def build_book(exchanges: list[Exchange]) -> dict | None:
    """The book for a player's exchanges (oldest first): a contents page, then each question with its answer and its sources.

    None when there is nothing to explain."""
    if not exchanges:
        return None
    bodies: list[list[list[dict]]] = []
    for n, e in enumerate(exchanges, 1):
        answer = [{"text": f"{n}. {e.question}"}, {"text": ""}] + [{"text": line} for line in e.lines]
        sources = [{"text": f"Sources for {n}:"}, {"text": ""}] + (source_lines(e.sources) or [{"text": NO_SOURCES}])
        bodies.append(paginate(answer) + paginate(sources))
    first, page = [], 2                                          # page 1 is the contents
    for body in bodies:
        first.append(page)
        page += len(body)
    contents = [{"text": TITLE}, {"text": ""}, {"text": f"Last {len(exchanges)} question(s):"}]
    for n, e in enumerate(exchanges, 1):
        q = e.question[:CONTENTS_QUESTION_CHARS] + ("..." if len(e.question) > CONTENTS_QUESTION_CHARS else "")
        contents.append({"text": f"{n}. {q}", "goto": first[n - 1]})
    pages = [contents] + [p for body in bodies for p in body]
    return {"title": TITLE, "author": AUTHOR, "pages": [{"lines": p} for p in pages]}
