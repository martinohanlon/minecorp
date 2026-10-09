"""Tests for what the agent tools say about trades, against the real graph in the throwaway database.

No model is involved. Run with:  pytest -m integration"""
from __future__ import annotations

import json

import pytest

from minecorp_agent import tools
from minecorp_agent.budget import fit

pytestmark = [pytest.mark.integration, pytest.mark.jar]


def call(graph, tool, **args):
    return tools.TOOLS_BY_NAME[tool].fn(graph, args)


def result_for(out, kind):
    return next(r for r in out["results"] if r["kind"] == kind)


def trade_ways(out):
    return [w for w in out["ways"] if w["how"] == "trade"]


class TestHowToGet:
    def test_a_villager_sells_bread_for_an_emerald(self, agent_graph):
        out = call(agent_graph, "how_to_get", name="bread")
        assert trade_ways(out) == [{"how": "trade", "with": "Farmer", "level": 1, "pay": "Emerald", "gets": "6 Bread"}]

    def test_emeralds_can_be_mined_and_got_by_selling_goods(self, agent_graph):
        out = call(agent_graph, "how_to_get", name="emerald")
        assert {"mine"} <= {w["how"] for w in out["ways"]}
        trades = trade_ways(out)
        assert trades and all({"with", "pay"} <= set(t) for t in trades)
        assert {"how": "trade", "with": "Farmer", "level": 1, "pay": "22 Carrot"} in trades

    def test_the_trades_listed_are_the_lowest_levels_first_and_the_rest_are_counted(self, agent_graph):
        out = call(agent_graph, "how_to_get", name="emerald")
        trades = trade_ways(out)
        assert len(trades) == 8 and out["moreTrades"] >= 70        # 87 trades give an emerald, a few shared by several professions
        assert [t["level"] for t in trades] == sorted(t["level"] for t in trades)

    def test_an_enchanted_item_says_so(self, agent_graph):
        trades = trade_ways(call(agent_graph, "how_to_get", name="iron pickaxe"))
        assert trades and all(t["note"] == "the item comes enchanted" for t in trades)
        assert {"Toolsmith"} <= {t["with"] for t in trades}

    def test_the_wandering_trader_has_a_pool_not_a_level(self, agent_graph):
        trades = trade_ways(call(agent_graph, "how_to_get", name="iron pickaxe"))
        trader = [t for t in trades if t["with"] == "Wandering Trader"]
        assert trader and all("level" not in t and t["pool"] in {"common", "uncommon"} for t in trader)

    def test_the_how_to_read_note_explains_a_trade(self, agent_graph):
        assert "trade (a villager" in call(agent_graph, "how_to_get", name="bread")["howToRead"]

    def test_a_category_has_no_trade_ways(self, agent_graph):
        out = call(agent_graph, "how_to_get", name="planks")
        assert not trade_ways(out) and "moreTrades" not in out

    def test_an_item_nobody_trades_has_none(self, agent_graph):
        out = call(agent_graph, "how_to_get", name="diamond")
        assert not trade_ways(out) and "moreTrades" not in out

    def test_a_price_that_varies_says_so(self, agent_graph):
        pays = [w["pay"] for w in trade_ways(call(agent_graph, "how_to_get", name="enchanted book"))]
        assert pays and all("depends on what you buy" in p for p in pays)

    def test_a_trade_only_some_villagers_offer_says_which(self, agent_graph):
        trades = trade_ways(call(agent_graph, "how_to_get", name="map"))
        assert any(t.get("onlyVillagersOf") == "jungle, snow, taiga villages" for t in trades)

    def test_the_answer_fits_its_budget_without_losing_anything(self, agent_graph):
        out = call(agent_graph, "how_to_get", name="emerald")
        assert "omitted" not in fit(out, tools.TOOLS_BY_NAME["how_to_get"].budget)


class TestWhereToFind:
    def test_a_map_to_a_mansion_is_sold_by_a_cartographer(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="mansion"), "structure")
        assert out["mapToFindIt"] == [{"buyFrom": "Cartographer villager", "level": 5, "pay": "14 Emerald and Compass"}]
        assert out["generatesIn"]

    def test_a_swamp_hut_map_is_only_sold_in_some_villages(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="swamp hut"), "structure")
        assert out["mapToFindIt"][0]["onlyVillagersOf"] == "jungle, snow, taiga villages"

    def test_a_structure_with_no_map_has_no_map_entry(self, agent_graph):
        assert "mapToFindIt" not in result_for(call(agent_graph, "where_to_find", name="fortress"), "structure")

    def test_an_item_a_villager_sells_says_so_next_to_where_it_is_mined(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="emerald"), "item")
        assert out["found"][0]["how"] == "mine" and out["canBeTradedFor"] and out["moreTrades"] > 0

    def test_an_item_that_is_only_traded_is_not_called_unfindable(self, agent_graph):
        out = result_for(call(agent_graph, "where_to_find", name="bread"), "item")
        assert "found" not in out and out["canBeTradedFor"][0]["with"] == "Farmer" and "note" not in out

    def test_an_item_nobody_drops_or_trades_still_says_so(self, agent_graph, db):
        item = db.one("""MATCH (i:Item)<-[:CREATES]-(:Recipe) WHERE NOT EXISTS { (i)<-[:CREATES]-(a:Action) WHERE a:Mine OR a:Kill OR a:Shear OR a:Trade } AND NOT i:ItemClass
                         RETURN i.id ORDER BY i.id LIMIT 1""")
        out = result_for(call(agent_graph, "where_to_find", name=item), "item")
        assert "found" not in out and "canBeTradedFor" not in out and "no villager trades it" in out["note"]


class TestDescribe:
    def test_a_profession_lists_what_it_trades_at_each_level(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="farmer"), "profession")
        assert [lv["level"] for lv in out["levels"]] == [1, 2, 3, 4, 5]
        first = out["levels"][0]
        assert "20 Wheat for Emerald" in first["trades"] and "Emerald for 6 Bread" in first["trades"] and first["offersAtRandom"] == 2
        assert "offersAtRandom" in out["next"]

    def test_a_long_list_is_cut_with_a_count(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="shepherd"), "profession")
        long = [lv for lv in out["levels"] if "moreTrades" in lv]
        assert long and all(len(lv["trades"]) == 8 for lv in long)

    def test_the_villager_has_the_professions(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="villager"), "mob")
        assert len(out["professions"]) == 13 and {"Farmer", "Librarian"} <= set(out["professions"])
        assert "profession" in out["next"]

    def test_the_wandering_trader_has_its_pools(self, agent_graph):
        out = call(agent_graph, "describe", name="wandering trader")["results"][0]
        assert out["offers"] == [{"picks": 2, "pool": "buying", "trades": 6}, {"picks": 5, "pool": "common", "trades": 76},
                                 {"picks": 2, "pool": "uncommon", "trades": 15}]

    def test_a_structure_says_who_sells_a_map_to_it(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="mansion"), "structure")
        assert out["mapSoldBy"][0]["buyFrom"] == "Cartographer villager"

    def test_an_item_counts_the_trades_that_give_and_want_it(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="emerald"), "item")
        assert out["tradesGivingIt"] == 87 and out["tradesWantingIt"] > 100

    def test_an_item_with_no_trades_has_no_counts(self, agent_graph):
        out = result_for(call(agent_graph, "describe", name="Iron Pickaxe"), "item")
        assert "tradesWantingIt" not in out or out["tradesWantingIt"] == 0

    def test_everything_is_json_and_fits(self, agent_graph):
        for name in ("farmer", "shepherd", "villager", "mansion", "emerald"):
            out = call(agent_graph, "describe", name=name)
            json.dumps(out)
            assert "omitted" not in fit(out, tools.TOOLS_BY_NAME["describe"].budget), name


class TestFind:
    def test_a_profession_is_found_and_labelled(self, agent_graph):
        assert {"id": "minecraft:farmer", "name": "Farmer", "kind": "profession"} in call(agent_graph, "find", query="farmer")["matches"]

    def test_the_kind_filter_accepts_profession(self, agent_graph):
        matches = call(agent_graph, "find", query="tool", kind="profession")["matches"]
        assert [m["name"] for m in matches] == ["Toolsmith"] and {m["kind"] for m in matches} == {"profession"}

    def test_an_unknown_kind_lists_profession_among_the_known(self, agent_graph):
        assert "profession" in call(agent_graph, "find", query="x", kind="trader")["error"]

    def test_a_profession_is_not_an_item_so_item_tools_do_not_resolve_it(self, agent_graph):
        assert "error" in call(agent_graph, "get_recipes", name="farmer")

    def test_a_profession_is_not_a_place_for_where_to_find(self, agent_graph):
        assert "error" in call(agent_graph, "where_to_find", name="farmer")
