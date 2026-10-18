"""Each player's current conversation: the id that agent memory files their questions under.

A conversation belongs to a session, not to a player: it starts when the player joins and again when they use /agent-clear, and the
service gives it an id (`<player uuid>:<random>`). A player has many conversations over time; the Neo4j `User` (the player's uuid) links
them. The id is kept in this process; after a restart the player carries on in their newest stored conversation (see `core._session`).
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

MAX_PLAYERS = 500            # players remembered at once; the longest-quiet one is forgotten first


class Sessions:
    """Safe to share between threads (the service answers several players at once)."""

    def __init__(self, max_players: int = MAX_PLAYERS) -> None:
        self._max_players = max_players
        self._current: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def current(self, player: str) -> str | None:
        with self._lock:
            return self._current.get(player)

    def start(self, player: str) -> str:
        """A new conversation for the player; it replaces the current one."""
        with self._lock:
            return self._set(player, f"{player}:{uuid.uuid4().hex[:12]}")

    def adopt(self, player: str, session_id: str | None = None) -> str:
        """Make `session_id` (or, with none, a new conversation) the player's current one, unless they already have one: that one is returned."""
        with self._lock:
            existing = self._current.get(player)
            if existing is not None:
                return existing
            return self._set(player, session_id or f"{player}:{uuid.uuid4().hex[:12]}")

    def _set(self, player: str, session_id: str) -> str:
        self._current[player] = session_id
        self._current.move_to_end(player)
        while len(self._current) > self._max_players:
            self._current.popitem(last=False)
        return session_id
