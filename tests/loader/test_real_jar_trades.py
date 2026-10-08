"""The trade loader against the REAL Paper jar: what must hold for any version, and known vanilla facts that act as canaries.

Skipped when the jar has not been downloaded. If a fact fails after a version upgrade, check the game first: it may have changed.
The facts are ones commonly quoted for the game (every trade involves emeralds, a farmer buys wheat, a cartographer sells maps to
a woodland mansion), a sanity check: the rows are read from the data, not typed in."""
from __future__ import annotations

import pytest

import load_trades as lt

pytestmark = pytest.mark.jar


@pytest.fixture(scope="module")
def g(jar_path):
    return lt.build(jar_path)


def trades_in(g, set_id: str) -> list[dict]:
    by_id = {t["id"]: t for t in g["trades"]}
    return [by_id[i["trade"]] for i in g["includes"] if i["set"] == set_id]


class TestWhatMustHold:
    def test_something_was_read(self, g):
        assert len(g["trades"]) > 300 and len(g["sets"]) > 60 and len(g["professions"]) >= 13

    def test_every_trade_is_in_a_trade_set(self, g):
        in_a_set = {i["trade"] for i in g["includes"]}
        assert {t["id"] for t in g["trades"]} <= in_a_set

    def test_every_set_has_trades_and_is_offered_by_someone(self, g):
        offered = {o["set"] for o in g["offers"]}
        with_trades = {i["set"] for i in g["includes"]}
        assert {s["id"] for s in g["sets"]} == offered == with_trades

    def test_a_set_picks_at_least_one_trade_and_may_ask_for_more_than_it_holds(self, g):
        # eight sets ask for one more than they hold (the butcher's level 4 asks for two and holds one): the villager then
        # offers what there is
        assert all(s["amount"] >= 1 for s in g["sets"])
        asks_too_many = [s for s in g["sets"] if s["amount"] > len(trades_in(g, s["id"]))]
        assert asks_too_many and all(s["amount"] == len(trades_in(g, s["id"])) + 1 for s in asks_too_many)

    def test_every_trade_pays_with_something_and_gives_something(self, g):
        for t in g["trades"]:
            assert t["pay"] and t["pay"][0]["role"] == "price" and t["gives"]["item"].startswith("minecraft:"), t["id"]
            assert t["gives"]["count"] >= 1

    def test_a_price_has_a_count_unless_it_varies(self, g):
        for t in g["trades"]:
            assert (t["pay"][0]["count"] is None) == t["variablePrice"], t["id"]

    def test_levels_run_from_one_to_five_for_every_profession(self, g):
        by = {}
        for o in g["offers"]:
            if o["level"] is not None:
                by.setdefault(o["from"], set()).add(o["level"])
        assert by and all(levels == {1, 2, 3, 4, 5} for levels in by.values())

    def test_the_trader_has_pools_and_no_levels(self, g):
        trader = [o for o in g["offers"] if o["from"] == lt.TRADER]
        assert {o["pool"] for o in trader} == {"buying", "common", "uncommon"} and all(o["level"] is None for o in trader)

    def test_a_trade_that_only_some_villagers_offer_says_which(self, g):
        known = {"desert", "jungle", "plains", "savanna", "snow", "swamp", "taiga"}
        for t in g["trades"]:
            assert set(t["villagerTypes"]) <= known, t["id"]

    def test_a_map_leads_to_at_least_one_structure(self, g):
        assert g["stats"]["maps that lead to a structure"] >= 5
        for t in g["trades"]:
            for x in t["locates"]:
                assert x["structure"].startswith("minecraft:") and x["radius"] > 0


class TestVanillaCanaries:
    def test_every_trade_involves_emeralds(self, g):
        for t in g["trades"]:
            assert "minecraft:emerald" in [p["item"] for p in t["pay"]] + [t["gives"]["item"]], t["id"]

    def test_the_professions(self, g):
        names = {p["name"] for p in g["professions"]}
        assert {"Farmer", "Librarian", "Cleric", "Cartographer", "Armorer", "Toolsmith", "Weaponsmith", "Butcher", "Fisherman",
                "Fletcher", "Leatherworker", "Mason", "Shepherd"} <= names
        assert "Wandering Trader" not in names and "Smith" not in names

    def test_a_novice_farmer_buys_wheat_for_an_emerald(self, g):
        wheat = [t for t in trades_in(g, "minecraft:farmer/level_1") if t["pay"][0]["item"] == "minecraft:wheat"]
        assert len(wheat) == 1 and wheat[0]["gives"]["item"] == "minecraft:emerald" and wheat[0]["pay"][0]["count"] == 20

    def test_a_farmer_sells_bread_for_an_emerald(self, g):
        bread = [t for t in trades_in(g, "minecraft:farmer/level_1") if t["gives"]["item"] == "minecraft:bread"]
        assert len(bread) == 1 and bread[0]["pay"][0]["item"] == "minecraft:emerald" and bread[0]["gives"]["count"] == 6

    def test_a_cartographer_sells_a_map_to_a_woodland_mansion(self, g):
        located = {x["structure"] for t in g["trades"] for x in t["locates"]}
        assert {"minecraft:mansion", "minecraft:monument", "minecraft:trial_chambers"} <= located

    def test_the_mansion_map_is_not_limited_to_a_village_type(self, g):
        mansion = [t for t in g["trades"] if any(x["structure"] == "minecraft:mansion" for x in t["locates"])]
        assert len(mansion) == 1 and mansion[0]["villagerTypes"] == []

    def test_the_librarian_sells_enchanted_books_for_a_price_that_varies(self, g):
        books = [t for t in g["trades"] if t["gives"]["item"] == "minecraft:enchanted_book"]
        assert books and all(t["variablePrice"] and t["enchanted"] for t in books)

    def test_the_wandering_trader_buys_goods_for_emeralds(self, g):
        buying = trades_in(g, "minecraft:wandering_trader/buying")
        assert buying and all(t["gives"]["item"] == "minecraft:emerald" for t in buying)

    def test_nothing_trades_diamonds_for_free_and_no_villager_gives_one(self, g):
        assert not [t for t in g["trades"] if t["gives"]["item"] == "minecraft:diamond"]

    def test_smiths_share_trades(self, g):
        assert g["stats"]["trades in more than one set"] >= 1

    def test_the_trade_rebalance_datapack_is_not_loaded(self, g):
        assert not [t for t in g["trades"] if "rebalance" in t["id"]]
