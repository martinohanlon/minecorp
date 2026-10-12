"""Unit tests for agent/minecorp_agent/history.py: each player's last few exchanges, in memory."""
from __future__ import annotations

import threading

import pytest

from minecorp_agent import history as hist
from minecorp_agent.history import Exchange, History


def ex(n):
    return Exchange(f"question {n}", (f"answer {n}",), ({"kind": "game data", "label": f"Recipes for {n}"},))


class TestPerPlayer:
    def test_a_player_not_seen_has_nothing(self):
        assert History().recent("alex") == []

    def test_exchanges_are_kept_oldest_first(self):
        h = History()
        h.add("alex", ex(1))
        h.add("alex", ex(2))
        assert [e.question for e in h.recent("alex")] == ["question 1", "question 2"]

    def test_players_do_not_see_each_others_questions(self):
        h = History()
        h.add("alex", ex(1))
        h.add("sam", ex(2))
        assert [e.question for e in h.recent("alex")] == ["question 1"] and [e.question for e in h.recent("sam")] == ["question 2"]

    def test_only_the_last_five_are_kept(self):
        h = History()
        for n in range(8):
            h.add("alex", ex(n))
        assert [e.question for e in h.recent("alex")] == [f"question {n}" for n in range(3, 8)]

    def test_the_limit_is_five(self):
        assert hist.MAX_EXCHANGES == 5

    def test_recent_gives_a_copy(self):
        h = History()
        h.add("alex", ex(1))
        h.recent("alex").clear()
        assert len(h.recent("alex")) == 1

    def test_a_player_can_be_forgotten(self):
        h = History()
        h.add("alex", ex(1))
        h.forget("alex")
        h.forget("nobody")
        assert h.recent("alex") == []


class TestMemoryIsBounded:
    def test_the_longest_quiet_player_is_forgotten_first(self):
        h = History(max_players=3)
        for name in ("a", "b", "c"):
            h.add(name, ex(1))
        h.add("a", ex(2))                  # a is active again, so b is now the quietest
        h.add("d", ex(1))
        assert h.recent("b") == [] and h.recent("a") and h.recent("c") and h.recent("d")

    def test_the_number_of_players_never_passes_the_limit(self):
        h = History(max_players=10)
        for n in range(100):
            h.add(f"p{n}", ex(n))
        assert sum(1 for n in range(100) if h.recent(f"p{n}")) == 10


class TestThreads:
    def test_many_threads_adding_do_not_lose_or_break_anything(self):
        h = History(limit=1000)
        errors = []

        def work(player):
            try:
                for n in range(200):
                    h.add(player, ex(n))
                    h.recent(player)
            except Exception as exc:        # pragma: no cover - the failure being tested for
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(f"p{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [] and all(len(h.recent(f"p{i}")) == 200 for i in range(8))


def test_an_exchange_cannot_be_changed_after_it_is_stored():
    e = ex(1)
    with pytest.raises(Exception):
        e.question = "other"
