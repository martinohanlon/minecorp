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
    sources: list = field(default_factory=list)      # what the tool result gave the answer (see sources.py)


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
    context_turns: int = 0            # earlier exchanges of the conversation that were sent to the model with the question

    @property
    def sources(self) -> list:
        """The sources of every tool call, each once, in order: what the player may ask to see."""
        from .sources import merge_sources
        return merge_sources(*(s.sources for s in self.steps))
