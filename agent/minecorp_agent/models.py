"""Plain data types shared by the agent modules."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    """A question from a player."""
    player_uuid: str
    player_name: str
    session_id: str
    text: str


@dataclass
class Step:
    """One tool call made while answering (part of the decision trace)."""
    turn: int
    tool: str
    args: dict
    ms: int
    result_chars: int
    error: str | None = None


@dataclass
class Result:
    """The outcome of one question: the answer plus how it was reached."""
    answer: str
    status: str                       # ok | turn_limit | error
    model: str
    ms: int
    steps: list[Step] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    error: str | None = None
