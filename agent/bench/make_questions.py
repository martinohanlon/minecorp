"""Draft the widened question set from templates, with every gold answer computed from the live graph.

    cd agent
    python -m bench.make_questions            # print each candidate with the gold the graph gives (nothing is written)
    python -m bench.make_questions --write    # append the candidates that pass to bench/questions.json

A candidate passes when its gold query returns exactly one distinct value. The 20 questions already in questions.json (the pilot, and the fixed
regression set) are never changed; new ids are added after them. The owner reviews the file before it is used.
Free: no model calls.
"""
from __future__ import annotations

import argparse
import json
import sys

from minecorp_agent.config import get_settings
from minecorp_agent.graph import Graph

from bench.run_bench import QUESTIONS, is_correct

S1, S2, S3, S4 = "S1-recall", "S2-exact-data", "S3-multi-hop", "S4-version-specific"


def recipe_count(item: str, needs: str | None = None) -> str:
    """How many of an ingredient a recipe takes (the item's only recipe, or the only ingredient when none is named)."""
    where = f"WHERE x.name = '{needs}'" if needs else ""
    return f"MATCH (:Item {{name:'{item}'}})<-[:CREATES]-(:Recipe)-[q:REQUIRES]->(x) {where} RETURN q.count AS gold"


def enchant_max(name: str) -> str:
    return f"MATCH (e:Enchantment {{name:'{name}'}}) RETURN e.maxLevel AS gold"


def ore_peak(block: str, band: str = "") -> str:
    return (f"MATCH (:Block {{name:'{block}'}})<-[:CREATES]-(:Feature)<-[:PLACES]-(p:PlacedFeature)-[:IN_DIMENSION]->(:Dimension {{name:'Overworld'}}) "
            f"WHERE p.peakY IS NOT NULL {band} RETURN DISTINCT p.peakY AS gold")


def ore_extreme(block: str, fn: str, prop: str) -> str:
    return (f"MATCH (:Block {{name:'{block}'}})<-[:CREATES]-(:Feature)<-[:PLACES]-(p:PlacedFeature)-[:IN_DIMENSION]->(:Dimension {{name:'Overworld'}}) "
            f"RETURN {fn}(p.{prop}) AS gold")


def trade_uses(prof: str, item: str) -> str:
    return (f"MATCH (:Profession {{name:'{prof}'}})-[:OFFERS]->(:TradeSet)-[:INCLUDES]->(t:Trade)-[:CREATES]->(:Item {{name:'{item}'}}) "
            f"RETURN DISTINCT t.maxUses AS gold")


def trade_price(prof: str, item: str) -> str:
    return (f"MATCH (:Profession {{name:'{prof}'}})-[:OFFERS]->(:TradeSet)-[:INCLUDES]->(t:Trade)-[:CREATES]->(:Item {{name:'{item}'}}) "
            f"MATCH (t)-[q:REQUIRES]->(:Item {{name:'Emerald'}}) RETURN DISTINCT q.count AS gold")


def spawn_biomes(mob: str) -> str:
    return f"MATCH (b:Biome)-[:SPAWNS]->(:Mob {{name:'{mob}'}}) RETURN count(DISTINCT b) AS gold"


def biome_mobs(biome: str) -> str:
    return f"MATCH (:Biome {{name:'{biome}'}})-[:SPAWNS]->(m:Mob) RETURN count(DISTINCT m) AS gold"


NUM = lambda n, word: f"{n}|{word}"      # noqa: E731  an accepted digit or word form

# (id, stratum, question, expect, gold query)
CANDIDATES: list[tuple[str, str, str, list[str], str]] = [
    # S1 recall: well-known crafting facts and classic enchantment levels (the control)
    ("s1-furnace", S1, "How many cobblestone do you need to craft a furnace?", ["8|eight"], recipe_count("Furnace")),
    ("s1-chest", S1, "How many planks do you need to craft a chest?", ["8|eight"], recipe_count("Chest")),
    ("s1-crafting-table", S1, "How many planks do you need to craft a crafting table?", ["4|four"], recipe_count("Crafting Table")),
    ("s1-bread", S1, "How many wheat do you need to craft one bread?", ["3|three"], recipe_count("Bread")),
    ("s1-ladder", S1, "How many sticks do you need for one crafting of ladders?", ["7|seven"], recipe_count("Ladder")),
    ("s1-wooden-pickaxe-sticks", S1, "How many sticks does a wooden pickaxe need?", ["2|two"], recipe_count("Wooden Pickaxe", "Stick")),
    ("s1-sharpness-max", S1, "What is the maximum level of the Sharpness enchantment?", ["5|five|v"], enchant_max("Sharpness")),
    ("s1-protection-max", S1, "What is the maximum level of the Protection enchantment?", ["4|four|iv"], enchant_max("Protection")),
    ("s1-fortune-max", S1, "What is the maximum level of the Fortune enchantment?", ["3|three|iii"], enchant_max("Fortune")),
    ("s1-unbreaking-max", S1, "What is the maximum level of the Unbreaking enchantment?", ["3|three|iii"], enchant_max("Unbreaking")),
    # S2 exact data: heights and trade numbers this server's data fixes
    ("s2-emerald-peak", S2, "At what y level does emerald ore generation peak in the Overworld?", ["232"], ore_peak("Emerald Ore")),
    ("s2-gold-peak", S2, "At what y level does gold ore generation peak in the Overworld?", ["-16"], ore_peak("Gold Ore")),
    ("s2-coal-peak", S2, "In the coal ore band that spans y 0 to 192, at what y level does coal generation peak?", ["96"],
     ore_peak("Coal Ore", "AND p.minY = 0")),
    ("s2-redstone-max-y", S2, "What is the highest y level at which redstone ore can generate in the Overworld?", ["15"], ore_extreme("Redstone Ore", "max", "maxY")),
    ("s2-lapis-max-y", S2, "What is the highest y level at which lapis lazuli ore can generate in the Overworld?", ["64"], ore_extreme("Lapis Ore", "max", "maxY")),
    ("s2-copper-min-y", S2, "What is the lowest y level at which copper ore can generate in the Overworld?", ["-16"], ore_extreme("Copper Ore", "min", "minY")),
    ("s2-cake-uses", S2, "How many times can a farmer villager's cake trade be made before the villager restocks?", ["12|twelve"], trade_uses("Farmer", "Cake")),
    ("s2-bell-price", S2, "How many emeralds does a villager charge for a bell?", ["36|thirty-six"], trade_price("Armorer", "Bell")),
    ("s2-saddle-price", S2, "How many emeralds does a leatherworker villager charge for a saddle?", ["6|six"], trade_price("Leatherworker", "Saddle")),
    ("s2-pumpkin-pie-price", S2, "How many pumpkin pies does a farmer villager give you for one emerald?", ["4|four"],
     "MATCH (:Profession {name:'Farmer'})-[:OFFERS]->(:TradeSet)-[:INCLUDES]->(t:Trade)-[c:CREATES]->(:Item {name:'Pumpkin Pie'}) RETURN DISTINCT c.count AS gold"),
    # S3 multi-hop: counts and joins across the graph
    ("s3-max-level-3", S3, "How many enchantments have a maximum level of 3?", ["__COMPUTED__"],
     "MATCH (e:Enchantment) WHERE e.maxLevel = 3 RETURN count(e) AS gold"),
    ("s3-max-level-1", S3, "How many enchantments have a maximum level of 1?", ["__COMPUTED__"],
     "MATCH (e:Enchantment) WHERE e.maxLevel = 1 RETURN count(e) AS gold"),
    ("s3-sheep-biomes", S3, "In how many different biomes can sheep spawn naturally?", ["26|twenty-six"], spawn_biomes("Sheep")),
    ("s3-pig-biomes", S3, "In how many different biomes can pigs spawn naturally?", ["25|twenty-five"], spawn_biomes("Pig")),
    ("s3-desert-mobs", S3, "How many different kinds of mob can spawn naturally in the Desert biome?", ["__COMPUTED__"], biome_mobs("Desert")),
    ("s3-mushroom-mobs", S3, "How many different kinds of mob can spawn naturally in the Mushroom Fields biome?", ["__COMPUTED__"], biome_mobs("Mushroom Fields")),
    ("s3-farmer-level1-trades", S3, "How many different trades can a level 1 farmer villager be offered?", ["5|five"],
     "MATCH (:Profession {name:'Farmer'})-[o:OFFERS]->(:TradeSet)-[:INCLUDES]->(t:Trade) WHERE o.level = 1 RETURN count(DISTINCT t) AS gold"),
    ("s3-saddle-profession", S3, "Which villager profession sells saddles?", ["leatherworker"],
     "MATCH (p:Profession)-[:OFFERS]->(:TradeSet)-[:INCLUDES]->(:Trade)-[:CREATES]->(:Item {name:'Saddle'}) RETURN DISTINCT p.name AS gold"),
    ("s3-wheat-buyers", S3, "How many different villager professions will buy wheat?", ["__COMPUTED__"],
     "MATCH (p:Profession)-[:OFFERS]->(:TradeSet)-[:INCLUDES]->(t:Trade) WHERE (t)-[:REQUIRES]->(:Item {name:'Wheat'}) RETURN count(DISTINCT p) AS gold"),
    ("s3-bat-biomes", S3, "In how many different biomes can bats spawn naturally?", ["__COMPUTED__"], spawn_biomes("Bat")),
    # S4 version-specific: newer enchantments, mobs and copper tools
    ("s4-wind-burst-max", S4, "What is the maximum level of the Wind Burst enchantment?", ["3|three|iii"], enchant_max("Wind Burst")),
    ("s4-breach-max", S4, "What is the maximum level of the Breach enchantment?", ["4|four|iv"], enchant_max("Breach")),
    ("s4-density-max", S4, "What is the maximum level of the Density enchantment?", ["5|five|v"], enchant_max("Density")),
    ("s4-bogged-biomes", S4, "In how many different biomes can the Bogged spawn naturally?", ["2|two"], spawn_biomes("Bogged")),
    ("s4-nautilus-biomes", S4, "In how many different biomes can the Nautilus spawn naturally?", ["9|nine"], spawn_biomes("Nautilus")),
    ("s4-copper-pickaxe", S4, "How many copper ingots do you need to craft a copper pickaxe?", ["3|three"], recipe_count("Copper Pickaxe", "Copper Tool Materials")),
    ("s4-copper-sword", S4, "How many copper ingots do you need to craft a copper sword?", ["2|two"], recipe_count("Copper Sword", "Copper Tool Materials")),
    ("s4-copper-chestplate", S4, "How many copper ingots do you need to craft a copper chestplate?", ["8|eight"], recipe_count("Copper Chestplate")),
    ("s4-copper-helmet", S4, "How many copper ingots do you need to craft a copper helmet?", ["5|five"], recipe_count("Copper Helmet")),
    ("s4-iron-spear-sticks", S4, "How many sticks do you need to craft an iron spear?", ["2|two"], recipe_count("Iron Spear", "Stick")),
]

WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
         12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="append the candidates that pass to bench/questions.json")
    args = ap.parse_args(argv)
    s = get_settings()
    graph = Graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    graph.verify()
    existing = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    have = {c["id"] for c in existing}
    new, skipped = [], []
    for qid, stratum, question, expect, query in CANDIDATES:
        if qid in have:
            continue
        values = sorted({str(r["gold"]) for r in graph.read(query)})
        if len(values) != 1:
            skipped.append((qid, values))
            print(f"SKIP  {qid:<28} gold query gave {values}")
            continue
        gold = values[0]
        if expect == ["__COMPUTED__"]:                 # an answer that is a count the graph decides: expect the number or its word
            n = int(gold)
            expect = [f"{n}|{WORDS[n]}" if n in WORDS else str(n)]
        sample = f"The answer is {gold}." if not gold.isdigit() and not gold.lstrip("-").isdigit() else f"It is {gold}."
        if not is_correct(sample, expect):                  # the gold must pass its own check, or the question would fail a perfect answer
            skipped.append((qid, f"gold {gold!r} does not match expect {expect}"))
            print(f"SKIP  {qid:<28} gold {gold!r} does not match expect {expect}")
            continue
        print(f"OK    {qid:<28} {stratum:<20} gold={gold!r:<10} expect={expect}")
        new.append({"id": qid, "stratum": stratum, "question": question, "expect": expect, "gold": gold, "gold_query": query})
    by = {}
    for c in new:
        by[c["stratum"]] = by.get(c["stratum"], 0) + 1
    print(f"\n{len(new)} candidates pass, {len(skipped)} skipped: {by}")
    if args.write and new:
        QUESTIONS.write_text(json.dumps(existing + new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"appended to {QUESTIONS}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
