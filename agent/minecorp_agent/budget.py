"""Size budgets and error shapes for tool results.

A result that is too big loses whole list entries from the end (the best come first), never characters from the
middle of the JSON, and says what it dropped. A failed tool call gives the model one of three errors, so it
tells the player the right thing: the arguments were wrong (fix them and call again), a service the tool needs
is down (tell the player), or something unexpected broke.
"""
from __future__ import annotations

import json

from neo4j.exceptions import DriverError, Neo4jError

try:                                   # the embedding call in search_wiki can fail on its own
    from openai import OpenAIError
except ImportError:                    # pragma: no cover
    OpenAIError = ()

DEFAULT_BUDGET = 6000                  # characters of JSON a tool result may use
BAD_ARGUMENT, UNAVAILABLE, FAILED = "bad_argument", "unavailable", "failed"


def size(result) -> int:
    """Characters the result takes once sent to the model."""
    return len(json.dumps(result, separators=(",", ":")))


def fit(result, budget: int):
    """The result cut to the budget by dropping entries from the end of its longest list, or as is if it fits.

    Dropped entries are counted in `omitted` (list name to count) with a `note`. A result that is not an object,
    or still too big with no list left to shorten, becomes an error asking for a narrower question.
    """
    if size(result) <= budget:
        return result
    if not isinstance(result, dict):
        return too_big()
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in result.items()}
    omitted: dict[str, int] = {}
    out["omitted"] = omitted
    out["note"] = (str(result["note"]) + " " if result.get("note") else "") + \
        "Some entries were left out to keep this short; ask a narrower question to see them."
    while size(out) > budget:
        lists = {k: v for k, v in out.items() if isinstance(v, list) and len(v) > 1}
        if not lists:
            return too_big()
        key = max(lists, key=lambda k: size(lists[k]))
        lists[key].pop()
        omitted[key] = omitted.get(key, 0) + 1
    return out


def too_big() -> dict:
    return {"error": "The answer is too large to show. Ask about something narrower.", "type": BAD_ARGUMENT}


def classify(exc: Exception) -> tuple[str, str]:
    """(type, message for the model) for an exception raised while running a tool."""
    if isinstance(exc, (Neo4jError, DriverError, OpenAIError, ConnectionError, TimeoutError)):
        return UNAVAILABLE, "A service this tool needs is not available right now. Tell the player the knowledge graph is not available."
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return BAD_ARGUMENT, f"The arguments were not usable ({exc}). Check them and call the tool again."
    return FAILED, "That tool failed unexpectedly. Try another approach or tell the player you could not find out."
