"""Conversation context: the player's last few exchanges, sent to the model ahead of the new question (docs/agent-memory.md).

Without it every question is a conversation of one, and "and how many of those do I need?" has nothing to refer to. The exchanges come from
the in-process history (and from agent memory after a restart), so this works with `AGENT_MEMORY=local` too. Only the questions and the
answers are replayed, not the tool results: a follow-up that needs facts calls the tools again.

A conversation ends when the player goes quiet: exchanges separated by more than the idle time are not one conversation, so yesterday's
question does not colour today's.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .history import Exchange

DEFAULT_TURNS = 3                # the owner's choice
DEFAULT_IDLE_MINUTES = 15.0      # the owner's choice
MAX_TURNS = 10                   # more is a lot of tokens for little


class ConversationConfigError(ValueError):
    """A conversation setting is wrong; the message says which."""


@dataclass(frozen=True)
class ConversationOptions:
    turns: int = DEFAULT_TURNS                       # earlier exchanges sent to the model; 0 turns the context off
    idle_minutes: float = DEFAULT_IDLE_MINUTES       # a quiet gap longer than this ends the conversation

    @property
    def idle_seconds(self) -> float:
        return self.idle_minutes * 60.0


def options_from_env(env: Mapping[str, str]) -> ConversationOptions:
    """Read AGENT_CONVERSATION_TURNS (0 to 10) and AGENT_CONVERSATION_IDLE_MINUTES (above 0); a wrong value raises ConversationConfigError."""
    raw_turns = env.get("AGENT_CONVERSATION_TURNS", "").strip()
    raw_idle = env.get("AGENT_CONVERSATION_IDLE_MINUTES", "").strip()
    try:
        turns = int(raw_turns) if raw_turns else DEFAULT_TURNS
    except ValueError:
        raise ConversationConfigError(f"AGENT_CONVERSATION_TURNS must be a whole number from 0 to {MAX_TURNS}, not {raw_turns!r}") from None
    if not 0 <= turns <= MAX_TURNS:
        raise ConversationConfigError(f"AGENT_CONVERSATION_TURNS must be from 0 to {MAX_TURNS}, not {turns}")
    try:
        idle = float(raw_idle) if raw_idle else DEFAULT_IDLE_MINUTES
    except ValueError:
        raise ConversationConfigError(f"AGENT_CONVERSATION_IDLE_MINUTES must be a number of minutes, not {raw_idle!r}") from None
    if not idle > 0:
        raise ConversationConfigError(f"AGENT_CONVERSATION_IDLE_MINUTES must be above 0, not {idle}")
    return ConversationOptions(turns, idle)


def recent_context(exchanges: Sequence[Exchange], *, now: float, options: ConversationOptions) -> list[Exchange]:
    """The exchanges to send ahead of a new question, oldest first: the current conversation's last `turns` exchanges.

    Walking back from the newest, an exchange belongs to the conversation while the quiet gap after it is at most the idle time (for the
    newest, the gap up to `now`). An exchange with no time (`at` is 0) is taken to be continuous with its neighbours.
    """
    if options.turns <= 0:
        return []
    kept: list[Exchange] = []
    later = now                                      # the time of what came after the exchange being considered
    for exchange in reversed(exchanges):
        if exchange.at and later - exchange.at > options.idle_seconds:
            break
        kept.append(exchange)
        if len(kept) == options.turns:
            break
        later = exchange.at or later
    return kept[::-1]
