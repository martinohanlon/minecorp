"""What each player has asked the agent recently, so the player can ask how the answers were made (docs/explain-book.md).

The agent keeps it, not the plugin: it already knows the player (the uuid on every request), it is where neo4j-agent-memory will live,
and the page layout is then written once. For now it is in memory, bounded, and lost when the service restarts. `History` is the one
place to replace when the memory comes.
"""
from __future__ import annotations

import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field

MAX_EXCHANGES = 5            # per player: the owner's choice
MAX_PLAYERS = 500            # players remembered at once; the longest-quiet one is forgotten first


@dataclass(frozen=True)
class Exchange:
    """One question, the answer the player was shown (one entry per line), and where its facts came from."""
    question: str
    lines: tuple[str, ...]
    sources: tuple[dict, ...]
    # when it was answered, seconds since the epoch; 0 when not known. It decides where a conversation ends, and is not part of what an
    # exchange IS: one read back from memory equals the one that was written.
    at: float = field(default=0.0, compare=False)


class History:
    """Each player's last few exchanges. Safe to share between threads (the service answers several players at once)."""

    def __init__(self, limit: int = MAX_EXCHANGES, max_players: int = MAX_PLAYERS) -> None:
        self._limit, self._max_players = limit, max_players
        self._players: OrderedDict[str, deque[Exchange]] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, player: str, exchange: Exchange) -> None:
        with self._lock:
            items = self._players.setdefault(player, deque(maxlen=self._limit))
            items.append(exchange)
            self._players.move_to_end(player)
            while len(self._players) > self._max_players:
                self._players.popitem(last=False)

    def recent(self, player: str) -> list[Exchange]:
        """The player's last exchanges, oldest first (as a notebook reads); empty for a player not seen. A copy."""
        with self._lock:
            return list(self._players.get(player, ()))

    def forget(self, player: str) -> None:
        with self._lock:
            self._players.pop(player, None)

