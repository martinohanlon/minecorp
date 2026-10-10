"""A stand-in for the Minecraft plugin, so the agent can be tried from a terminal without starting Minecraft.

    python scripts/chat.py                              # a chat: type a question, read the reply
    python scripts/chat.py "how do I make a bed?"       # ask once and exit
    python scripts/chat.py --trace                      # also show what the agent did (tools, time, tokens)
    python scripts/chat.py --direct                     # no agent service: run the agent in this process

It does what the plugin does. The same request goes to POST /ask with the shared secret in X-MineCorp-Key; the same rules
apply (a question is required, at most 500 characters, one at a time); the same messages are shown ("MineCorp agent:
thinking...", the answer as gold lines, "The agent is not available right now. Try again later." when it fails). Because it
is a developer's tool it adds what a player never sees, in grey: the reason a request failed, how long it took, whether the
answer follows the chat style (at most 6 lines, about 90 characters each, plain text; it only flags real overruns), and with --trace the tools the
agent called, read from agent/logs/traces.jsonl.

In the chat: /explain shows what the plugin's /agent-explain book will contain (the last five questions, answers and sources: see
docs/explain-book.md), /trace turns the developer trace on or off, /name Alex changes the player, /help lists these, /quit leaves.
A leading "/agent " is accepted, as in the game.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

import httpx  # noqa: E402

from minecorp_agent.config import load_dotenv  # noqa: E402

MAX_QUESTION_LENGTH = 500            # AskGuard.MAX_QUESTION_LENGTH in the plugin
DEFAULT_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 30.0               # timeout-seconds in the plugin's config.yml
MAX_LINES, MAX_LINE_CHARS = 6, 100   # the prompt asks for about 90 characters a line; a few more is fine, so only flag real overruns
NAMESPACE = uuid.UUID("6f1d0c4e-5a7b-4f0e-9a52-0c0b6d3a1e11")

USAGE = "Usage: /agent <question>"
TOO_LONG = f"That question is too long (max {MAX_QUESTION_LENGTH} characters)."
NOT_CONFIGURED = "The agent is not configured yet. Ask an admin."
UNAVAILABLE = "The agent is not available right now. Try again later."
THINKING = "MineCorp agent: thinking..."

COLOURS = {"gold": "33", "grey": "90", "red": "31", "yellow": "93", "reset": "0"}
MAX_HISTORY = 5                      # exchanges kept for the book (the owner's choice)
MAX_PAGE_CHARS = 700                 # a written book holds about 1,024 characters a page; stay well under it
BOOK_TITLE, BOOK_AUTHOR = "Agent notes", "MineCorp Agent"
NO_SOURCES = "No sources: I answered without looking anything up."
MARKDOWN = re.compile(r"\*\*|__|`|^\s*#+\s|^\s*\|.*\|\s*$|\[[^\]]+\]\([^)]+\)", re.M)


# --------------------------------------------------------------------------- the plugin's rules

def player_uuid(name: str) -> str:
    """A stable id per player name (the game gives each player a UUID; the stub makes one up from the name)."""
    return str(uuid.uuid5(NAMESPACE, name))


def question_text(raw: str) -> str:
    """The question as the plugin sees it: "/agent how do I..." and "how do I..." are the same; trimmed."""
    raw = raw.strip()
    return re.sub(r"^/agent\b\s*", "", raw, flags=re.I).strip() if raw.lower().startswith("/agent") else raw


def check_question(question: str, configured: bool = True) -> tuple[str, str] | None:
    """The plugin's AskGuard, in the order it checks: (message, colour) when the question is refused, else None."""
    if not question:
        return USAGE, "yellow"
    if len(question) > MAX_QUESTION_LENGTH:
        return TOO_LONG, "red"
    if not configured:
        return NOT_CONFIGURED, "red"
    return None


def answer_lines(answer: str) -> list[str]:
    """The answer split on any line break, trailing empty lines dropped (Replies.lines in the plugin)."""
    lines = re.split(r"\r\n|\r|\n", answer)
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines


def style_warnings(lines: list[str]) -> list[str]:
    """Where an answer breaks the chat style the agent is asked to follow."""
    out = []
    if len(lines) > MAX_LINES:
        out.append(f"{len(lines)} lines (asked for at most {MAX_LINES})")
    long = [n for n, line in enumerate(lines, 1) if len(line) > MAX_LINE_CHARS]
    if long:
        out.append(f"{len(long)} line(s) over {MAX_LINE_CHARS} characters (longest {max(len(lines[n - 1]) for n in long)})")
    if MARKDOWN.search("\n".join(lines)):
        out.append("markdown the chat cannot show")
    return out


# --------------------------------------------------------------------------- talking to the agent

@dataclass
class Reply:
    """What came back: the answer lines, or the reason there are none."""
    lines: list[str] = field(default_factory=list)
    request_id: str | None = None
    seconds: float = 0.0
    error: str | None = None
    sources: list[dict] = field(default_factory=list)       # where the answer's facts came from (kind, label, url?)


def clean_sources(raw) -> list[dict]:
    """The sources in a reply, keeping only well-formed ones: a kind and a label, and a link only if it is a web address."""
    out = []
    for s in raw if isinstance(raw, list) else []:
        if isinstance(s, dict) and isinstance(s.get("kind"), str) and isinstance(s.get("label"), str) and s["label"].strip():
            url = s.get("url")
            out.append({"kind": s["kind"], "label": s["label"].strip(), **({"url": url} if isinstance(url, str) and url.startswith(("http://", "https://")) else {})})
    return out


# --------------------------------------------------------------------------- the notes (what /agent-explain will put in a book)

@dataclass(frozen=True)
class Exchange:
    """One question, its answer and its sources."""
    question: str
    lines: list[str]
    sources: list[dict]


class History:
    """A player's last few exchanges, in memory. The plugin keeps the same; neo4j-agent-memory can replace it later."""

    def __init__(self, limit: int = MAX_HISTORY):
        self._items: deque[Exchange] = deque(maxlen=limit)

    def add(self, exchange: Exchange) -> None:
        self._items.append(exchange)

    def recent(self) -> list[Exchange]:
        """Oldest first, as a notebook reads."""
        return list(self._items)


def source_line(s: dict) -> str:
    return f"- {s['kind']}: {s['label']}"


def split_pages(lines: list[str], limit: int = MAX_PAGE_CHARS) -> list[str]:
    """Lines packed into pages of at most `limit` characters, breaking between lines (a longer line is cut at a space)."""
    pages, current = [], ""
    for line in lines:
        pieces = []
        while len(line) > limit:                                    # one line longer than a page
            cut = line.rfind(" ", 0, limit + 1)                     # a space just after the limit still lets `limit` characters through
            cut = cut if cut > 0 else limit
            pieces.append(line[:cut])
            line = line[cut:].lstrip()
        pieces.append(line)
        for piece in pieces:
            candidate = f"{current}\n{piece}" if current else piece
            if current and len(candidate) > limit:
                pages.append(current)
                current = piece
            else:
                current = candidate
    if current:
        pages.append(current)
    return pages or [""]


def book_pages(history: History | list[Exchange]) -> list[str]:
    """The pages of the notebook: a contents page, then for each exchange its question and answer, and its sources.

    The Minecraft plugin builds the same pages (docs/explain-book.md); the links in the sources are clickable there and are not
    shown here, since a terminal cannot click them. Returns no pages when there is nothing to explain."""
    items = history.recent() if isinstance(history, History) else list(history)
    if not items:
        return []
    contents = [f"{BOOK_TITLE}", "", f"Last {len(items)} question(s):"] + [f"{n}. {e.question[:40]}" + ("..." if len(e.question) > 40 else "") for n, e in enumerate(items, 1)]
    pages = ["\n".join(contents)]
    for n, e in enumerate(items, 1):
        pages += split_pages([f"{n}. {e.question}", ""] + e.lines)
        sources = [source_line(s) for s in e.sources] or [NO_SOURCES]
        pages += split_pages([f"Sources for {n}:", ""] + sources)
    return pages


class Chat:
    """One player's conversation with the agent: over HTTP like the plugin, or directly in this process."""

    def __init__(self, name: str = "Steve", url: str = DEFAULT_URL, secret: str = "", client=None, direct=None, timeout: float = DEFAULT_TIMEOUT):
        self.name, self.url, self.secret, self.timeout = name, url.rstrip("/"), secret, timeout
        self.client, self.direct = client, direct          # direct: a function (Question, request_id) -> answer text, or (text, sources)
        self.history = History()

    @property
    def configured(self) -> bool:
        return bool(self.secret) or self.direct is not None

    def body(self, question: str) -> dict:
        """The JSON the plugin sends: playerUuid, playerName, sessionId (the same uuid) and question."""
        pid = player_uuid(self.name)
        return {"playerUuid": pid, "playerName": self.name, "sessionId": pid, "question": question}

    def ask(self, question: str) -> Reply:
        """Ask once. Never raises: a failure becomes a Reply with an error."""
        started = time.monotonic()
        try:
            if self.direct is not None:
                request_id = uuid.uuid4().hex[:12]
                from minecorp_agent.models import Question
                pid = player_uuid(self.name)
                result = self.direct(Question(pid, self.name, pid, question), request_id)
                text, sources = result if isinstance(result, tuple) else (result, [])
            else:
                client = self.client or httpx.Client(timeout=self.timeout)
                response = client.post(f"{self.url}/ask", json=self.body(question), headers={"X-MineCorp-Key": self.secret})
                if response.status_code != 200:
                    return Reply(error=f"HTTP {response.status_code}: {response.text[:160]}", seconds=time.monotonic() - started)
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
                    return Reply(error="the reply has no answer", seconds=time.monotonic() - started)
                request_id, text, sources = data.get("requestId"), data["answer"], data.get("sources")
        except Exception as exc:                            # timeout, connection refused, bad JSON, a failure inside the agent
            return Reply(error=f"{type(exc).__name__}: {exc}", seconds=time.monotonic() - started)
        reply = Reply(lines=answer_lines(text), request_id=request_id, seconds=time.monotonic() - started, sources=clean_sources(sources))
        self.history.add(Exchange(question, reply.lines, reply.sources))
        return reply

    def healthy(self) -> str | None:
        """None when the service answers /health, else the reason it does not (HTTP mode only)."""
        try:
            client = self.client or httpx.Client(timeout=5.0)
            r = client.get(f"{self.url}/health")
            return None if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- the trace

def trace_path() -> Path:
    return Path(os.environ.get("AGENT_TRACE_DIR", str(ROOT / "agent" / "logs"))) / "traces.jsonl"


def trace_for(request_id: str | None, path: Path | None = None) -> dict | None:
    """The agent's decision trace for a request id (the last one written), or None."""
    path = path or trace_path()
    if not request_id or not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("requestId") == request_id:
            return entry
    return None


def format_trace(entry: dict) -> list[str]:
    """A short account of what the agent did, one line per tool call."""
    usage = entry.get("usage") or {}
    lines = [f"agent: {entry.get('status')}, {entry.get('model')}, {entry.get('ms')} ms, {usage.get('model_calls', 0)} model call(s), "
             f"{usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)} tokens"]
    for step in entry.get("steps", []):
        args = ", ".join(f"{k}={v!r}" for k, v in (step.get("args") or {}).items())
        lines.append(f"  tool {step.get('tool')}({args})  {step.get('ms')} ms, {step.get('result_chars')} chars" + (f"  ERROR {step['error']}" if step.get("error") else ""))
    if not entry.get("steps"):
        lines.append("  no tools used")
    if entry.get("error"):
        lines.append(f"  error: {entry['error']}")
    return lines


# --------------------------------------------------------------------------- the terminal

class Terminal:
    """Prints coloured lines; colour is off when the output is not a terminal or NO_COLOR is set."""

    def __init__(self, colour: bool, write=print):
        self.colour, self.write = colour, write

    def say(self, text: str, colour: str = "reset") -> None:
        self.write(f"\x1b[{COLOURS[colour]}m{text}\x1b[0m" if self.colour else text)


def respond(chat: Chat, question: str, term: Terminal, trace: bool) -> bool:
    """One question, as the game would show it. Returns True when the agent answered."""
    question = question_text(question)
    refused = check_question(question, chat.configured)
    if refused:
        term.say(*refused)
        return False
    term.say(THINKING, "grey")
    reply = chat.ask(question)
    if reply.error:
        term.say(UNAVAILABLE, "red")
        term.say(f"(stub) {reply.error}", "grey")
        return False
    for line in reply.lines:
        term.say(line, "gold")
    notes = [f"{reply.seconds:.1f} s"] + [f"style: {w}" for w in style_warnings(reply.lines)]
    term.say("(stub) " + "; ".join(notes), "grey")
    if trace:
        entry = trace_for(reply.request_id)
        for line in (format_trace(entry) if entry else [f"no trace found for request {reply.request_id} (is the agent writing to {trace_path()}?)"]):
            term.say("(stub) " + line, "grey")
    return True


NOTHING_TO_EXPLAIN = "Nothing to explain yet: ask a question first."
HELP = "Type a question. /explain shows the notes the book would hold, /trace shows what the agent did, /name <player> changes the player, /quit leaves."


def explain(chat: Chat, term: Terminal) -> None:
    """What /agent-explain gives in the game: the pages of the book. Printed page by page, in grey, as the stub's own output."""
    pages = book_pages(chat.history)
    if not pages:
        term.say(NOTHING_TO_EXPLAIN, "yellow")
        return
    term.say(f"(stub) book \"{BOOK_TITLE}\" by {BOOK_AUTHOR}, {len(pages)} page(s)", "grey")
    for n, page in enumerate(pages, 1):
        term.say(f"(stub) --- page {n} ---", "grey")
        for line in page.split("\n"):
            term.say("  " + line, "reset")
    urls = [s["url"] for e in chat.history.recent() for s in e.sources if s.get("url")]
    if urls:
        term.say("(stub) links in the book (clickable in the game): " + ", ".join(dict.fromkeys(urls)), "grey")


def repl(chat: Chat, term: Terminal, read=None, trace: bool = False) -> None:
    """The chat loop. `read` is where lines come from (the keyboard unless a test gives its own)."""
    read = read or input
    term.say(f"{chat.name} is in the MineCorp stub chat. {HELP}", "grey")
    while True:
        try:
            line = read("> ")
        except (EOFError, KeyboardInterrupt):
            term.say("", "reset")
            return
        text = line.strip()
        command = text.lower()
        if command in ("/quit", "/exit", "/q"):
            return
        if command == "/help":
            term.say(HELP, "grey")
        elif command in ("/explain", "/agent-explain"):
            explain(chat, term)
        elif command == "/trace":
            trace = not trace
            term.say(f"(stub) trace {'on' if trace else 'off'}", "grey")
        elif command.startswith("/name"):
            name = text[5:].strip()
            if name:
                chat.name = name
            term.say(f"(stub) you are {chat.name}" if name else "Usage: /name <player>", "grey" if name else "yellow")
        else:
            respond(chat, text, term, trace)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*", help="ask once and exit (no question starts the chat)")
    ap.add_argument("--name", default="Steve", help="the player's name (default Steve)")
    ap.add_argument("--url", default=None, help=f"the agent service (default AGENT_URL or {DEFAULT_URL})")
    ap.add_argument("--direct", action="store_true", help="run the agent in this process instead of calling the service")
    ap.add_argument("--trace", action="store_true", help="show what the agent did after each answer")
    ap.add_argument("--explain", action="store_true", help="after the answer, show the notes the book would hold")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds to wait for the agent")
    ap.add_argument("--no-color", action="store_true", help="plain text")
    args = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    colour = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    if colour and os.name == "nt":
        os.system("")                                       # switch the Windows console to ANSI colours
    term = Terminal(colour)

    if args.direct:
        from minecorp_agent.core import answer_with_sources
        chat = Chat(args.name, direct=answer_with_sources, timeout=args.timeout)
    else:
        chat = Chat(args.name, args.url or os.environ.get("AGENT_URL", DEFAULT_URL), os.environ.get("AGENT_SHARED_SECRET", ""), timeout=args.timeout)
        problem = chat.healthy() if chat.configured else None
        if problem:
            term.say(f"(stub) the agent service at {chat.url} is not answering ({problem}).", "red")
            term.say("(stub) start it with ./scripts/run-agent.ps1, or add --direct to run the agent in this process.", "grey")
            return 1
    if args.question:
        ok = respond(chat, " ".join(args.question), term, args.trace)
        if ok and args.explain:
            explain(chat, term)
        return 0 if ok else 1
    repl(chat, term, trace=args.trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
