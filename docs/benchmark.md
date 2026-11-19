# Benchmark: does a knowledge layer let a small model match a large one?

Status: v1.0 (2026-11-19). Built and run twice: a 20-question pilot (240 runs, $1.94) and a 60-question run (720 runs, $5.74). On the 60 questions, a small model with the knowledge layer answered 180 of 180 correctly against 165 of 180 for the large model alone, at about a fifth of the cost per right answer. Read the caveats before quoting that: the questions are about this server's data and are graded against the same graph the agent reads. The options for what comes next are at the end.

## The short answer

| | A small, model only | C large, model only | B small + KL | D large + KL |
|---|---|---|---|---|
| Right (of 180) | 124 (69%) | 165 (92%) | **180 (100%)** | 180 (100%) |
| Cost per right answer, list price | $0.00025 | $0.0048 | **$0.0009** | $0.060 |
| Cost per right answer, with the prompt cache | $0.00025 | $0.0048 | $0.0002 | $0.027 |

`gpt-6-luna` is the small model, `gpt-6.2-astra` the large one, reasoning effort medium for both; KL means the MineCorp knowledge layer (the agent and its graph tools). Run 20261118-170033, shuffled, on the agent with the `maxUses` fix.

- **H1, quality:** small + KL against the large model alone is **+8.3 points, 95% interval [+2.2, +16.1]** (resampling the 60 questions). The interval excludes zero: here the small model with the layer is better, not just as good.
- **H2, cost:** small + KL is **5.3x cheaper per right answer at list price, 22.7x with the prompt cache.** Quote 5.3x first. It is about 0.8 s slower per question (3.8 s against 3.0 s).
- **The layer removes the gap between the models:** small + KL and large + KL are equal (180 of 180), and the layer lifts the small model by 31 points.

What this does and does not show is in "Caveats". In one line: it shows the agent retrieves and states this server's data faithfully and cheaply; it does not show the data is right, and it does not say a large model is a worse general Minecraft helper.

## How to run it

```
./scripts/run-benchmark.ps1                     # the full run: 720 runs, about 6 dollars; asks first
./scripts/run-benchmark.ps1 -Yes -Repeats 1     # no prompt, a third of the cost
./scripts/run-benchmark.ps1 s1 s2 -Repeats 1    # only questions whose id contains s1 or s2
./scripts/run-benchmark.ps1 -Arms bare -Models gpt-6-luna -Repeats 1
./scripts/run-benchmark.ps1 -VerifyOnly         # free: check every gold answer against the graph
./scripts/run-benchmark.ps1 -ReportOnly [-Run 20261118-170033]   # free: report on a finished run
./scripts/run-benchmark.ps1 -DryRun -Repeats 1 s1                # free: print the command it would run
```

There is a bash twin, `run-benchmark.sh`, with `--yes`, `--repeats` and so on. It needs the graph (the Neo4j in `.env`) and `OPENAI_API_KEY`.

- It checks all 60 gold answers against the graph first and stops if one no longer matches, then asks before spending (it refuses to run without `-Yes` when nobody can answer), runs every question on the four arms in shuffled order, and prints the report.
- `-MaxCost` is a hard cap (default 10 dollars): a run stops before passing it. The cost counted is the cached estimate; list price can be about twice that.
- Results go to `agent/bench/results/<run>.jsonl` (git-ignored), one line per run: question, arm, model, answer, outcome, tokens (including cached and reasoning), the exact model string the API returned, tools called, latency and cost.
- `python -m bench.report [run]` (from `agent/`) re-scores the stored answers against the current `questions.json` and prints accuracy by question type, cost, the confidence intervals and a per-question table. A fix to a question applies to a finished run without calling a model again. `python -m bench.verify_gold` and `python -m bench.make_questions` are the other two programs.
- The logic of the script is in `scripts/run_benchmark.py`; the wrappers are thin, as for the other scripts (`scripts/README.md`).

## Design

### Hypothesis

H1 (quality): MineCorp's agent with the knowledge layer and a **small** model answers as well as a **large** model without it.
H2 (cost): it does so at a fraction of the cost per right answer. Both must hold: a tie on accuracy at 7x the price proves nothing, and a cheaper agent that is worse proves nothing either.

### Arms

| Arm | Model | Knowledge layer | Role |
|---|---|---|---|
| A | small | none (model only) | floor |
| B | small | full agent | **the claim** |
| C | large | none (model only) | what you would otherwise buy |
| D | large | full agent | ceiling |

"Model only" means no tools and a minimal prompt that keeps the same role and chat style (short plain text), so the style does not decide the score. The agent's prompt names no tool in that mode. A "model plus wiki retrieval, no graph" arm is a later ablation.

The same endpoint is used for every arm: the gpt-6 models reject function tools together with a reasoning effort on Chat Completions (a 400 that says to use `/v1/responses`), and Sol and Astra have no `none` setting. So the benchmark calls the **Responses API** (`Settings.api = "responses"`, `--api`), and the service stays on Chat Completions.

### Models and prices

US dollars per million tokens, input / output, from the price list of 2026-11-18, kept with the date in `agent/bench/prices.json`. All support reasoning effort low to max, and the knowledge cutoffs are May to June 2026.

| Model | Price | vs Luna | Role |
|---|---|---|---|
| `gpt-6-luna` | $0.10 / $0.50 | 1x | **small** |
| `gpt-6.1-terra` | $1 / $5 | 10x | ladder rung (deferred) |
| `gpt-6.2-sol` | $2 / $10 | 20x | ladder rung (deferred) |
| `gpt-6.2-astra` | $8 / $40 | 80x | **large** |

Luna against Astra is the widest gap in one family, so it makes the cost half of the claim as strong as it can be while keeping the training-data vintage the same. `gpt-5.4-mini` (what the agent was tuned on) and `gpt-5.4-nano` are in `prices.json` for later. The same reasoning effort (medium, `--effort`) is sent to every model and recorded.

### Cost and caching

Cost per run is tokens times the price table. The knowledge-layer arms send about 8,500 input tokens a run (a long system prompt and the tool list, plus the tool results each later call re-sends) against about 220 for the model-only arms, and make 3.6 tool calls a run for the small model and 1.7 for the large one.

Cached input is cheaper: the price list has no cached rate, so the estimate assumes 10 percent of the input price and always shows the list-price figure too. About 90 percent of the knowledge layer's input was cached in the 60-question run, in the first half of the run and in the second alike. What is cached is mostly the prompt and tool list (identical on every question) and the earlier messages of the same run, and both happen in production. What a benchmark flatters is a cache that never goes cold: these runs sent a call every few seconds, a real server may leave minutes between questions, and then the prefix costs the full price. So the cached figure is an upper bound that needs steady traffic, the list-price figure is what no caching gives, and a real server sits between them. A cold-cache run has not been done.

### Scoring

Deterministic. A question is right when every entry in its `expect` list appears in the answer (`a|b` means either). Numbers must stand alone (232 is not in 2320 or 232.5), words must start a word ("one" is not in "stone"), a roman numeral is a whole word ("v" is not in "level"), and a plural is accepted. A miss is **abstained** (the answer says it does not know or could not find it) or **wrong** (a confident wrong answer); both are failures and are reported separately, because a confident wrong answer is the worse one. An answer from a model that cannot answer because the game is newer than its training counts as a failure for that arm, and the version-specific questions are scored on purpose.

Each question is run 3 times per arm. The three repeats are not independent, so every confidence interval resamples the **questions**, not the runs; with 60 questions the intervals are still wide, and they are printed for that reason.

Scorer bugs found by reading the answers (all mine, all fixed and the runs re-scored): "III" was not accepted for the Lunge level, "Endermen" not for "enderman", and a bare "v" or "one" would have matched inside other words.

## The question set

60 questions in `agent/bench/questions.json`, 15 in each of four types, so each part tests something different:

| Type | What it tests | Expected |
|---|---|---|
| S1 recall | well-known facts (a recipe, what a creeper drops, a classic enchantment level) | the control: all four arms do well |
| S2 exact data | numbers a model cannot recall exactly (ore heights, trade prices and uses) | the layer wins |
| S3 multi-hop and counts | joins and counts across the graph (biomes where a mob spawns, enchantments of a level, a profession's trades) | the layer wins |
| S4 version-specific | content this server has that older training data lacks (the Sulfur Cube, Parched, spears, Lunge, copper tools) | the layer wins against the small model, maybe not the large one |

Every gold answer is the result of a Cypher query stored with its question. `python -m bench.verify_gold` re-runs all 60 against the live graph (60 of 60 match); a gold that no longer matches stops the run. The first 20 questions are the pilot and a fixed regression set; the other 40 were generated from templates by `python -m bench.make_questions`, which computes each gold from the graph and checks that the gold passes its own `expect`. That check caught two counts I had typed from memory (enchantments with maximum level 3 and 1: I wrote 13 and 10, the graph says 16 and 9), so counts are computed, never written by hand.

**The 20 pilot questions** (gold in brackets):
- S1: iron ingots for a bucket (3); what a creeper drops (gunpowder); oak planks from one log (4); the Efficiency maximum level (5); what to smelt for an iron ingot (raw iron or iron ore).
- S2: where upper-band iron ore peaks (y 232); where copper ore peaks (48); the highest y for coal ore (319); how many times a farmer's wheat trade can be made before a restock (16); what a farmer charges for a cake (3 emeralds).
- S3: enchantments with a maximum level of 5 (7); biomes where wolves spawn (9); professions that sell bells (3); the mob that spawns in the most biomes (Enderman, 61, next 54); a farmer's trades in total (14).
- S4: the Lunge maximum level (3); what a copper spear is crafted from (2 sticks and a copper ingot); the tiers of spear (7); where the Sulfur Cube spawns (Sulfur Caves); where the Parched spawns (Desert).

**The 40 generated questions:**

| Type | Questions |
|---|---|
| S1 | recipe counts: furnace (8 cobblestone), chest (8 planks), crafting table (4 planks), bread (3 wheat), ladder (7 sticks), wooden pickaxe (2 sticks); maximum levels of Sharpness (5), Protection (4), Fortune (3), Unbreaking (3) |
| S2 | ore heights: emerald peak (232), gold peak (-16), coal peak in the 0 to 192 band (96), redstone highest y (15), lapis highest y (64), copper lowest y (-16); trades: a farmer's cake uses (12), a bell's price (36 emeralds), a saddle's price (6), pumpkin pies per emerald (4) |
| S3 | enchantments with maximum level 3 (16) and 1 (9); biomes where sheep (26), pigs (25) and bats (54) spawn; mobs that spawn in the Desert (14) and in Mushroom Fields (3); a level 1 farmer's trades (5); which profession sells saddles (Leatherworker); how many professions buy wheat (1) |
| S4 | maximum levels of Wind Burst (3), Breach (4), Density (5); biomes for the Bogged (2) and the Nautilus (9); copper ingots for a copper pickaxe (3), sword (2), helmet (5) and chestplate (8); sticks for an iron spear (2) |

Choices and exclusions:
- **Counts use definitions the data fixes:** "spawn naturally" is any biome spawn entry; a profession "sells" an item if any of its trades gives it; "trades in total" counts every trade in every level's pool. The question text does not spell this out, as a player would not.
- **Left out on purpose:** structures per dimension (the graph links the Nether to only 2 structures while a Fortress and a Nether ruined portal exist, so the gold would reward a gap in the graph); the Nautilus armor and the Harness (no recipe in the data); route-length questions (a number only the layer's own metric defines); and one question removed during drafting because it named the three ore blocks it asked to count.
- `s4-copper-spear` accepts any answer naming a stick and a copper ingot, including a wrong count; the answers were read.

## Results

### Second run: 60 questions, 720 runs, shuffled (run 20261118-170033)

Same models and effort, on the agent with the `maxUses` fix, order shuffled with seed 1. Cost $5.74 at the cached rate ($11.82 at list price), under the $10 cap.

| Type (45 runs per cell) | A small, model only | C large, model only | B small + KL | D large + KL |
|---|---|---|---|---|
| S1 recall | 43 | 45 | 45 | 45 |
| S2 exact data | 35 | 42 | 45 | 45 |
| S3 multi-hop | 22 | 33 | 45 | 45 |
| S4 version-specific | 24 | 45 | 45 | 45 |
| **All (of 180)** | **124 (69%)** | **165 (92%)** | **180 (100%)** | **180 (100%)** |
| abstained / confidently wrong | 5 / 51 | 0 / 15 | 0 / 0 | 0 / 0 |

| | per run (cached) | per run (list price) | per right answer (cached) | per right answer (list price) | median latency | tool calls per run |
|---|---|---|---|---|---|---|
| A small, model only | 0.00017 | 0.00017 | 0.00025 | 0.00025 | 2.8 s | 0 |
| C large, model only | 0.00439 | 0.00439 | 0.00479 | 0.00479 | 3.0 s | 0 |
| B small + KL | 0.00021 | 0.00091 | 0.00021 | 0.00091 | 3.8 s | 3.6 |
| D large + KL | 0.02710 | 0.06022 | 0.02710 | 0.06022 | 5.8 s | 1.7 |

Differences in accuracy over the questions, with 95% intervals: B against C **+8.3 points [+2.2, +16.1]**; B against D 0.0 [0.0, 0.0]; B against A +31.1 [+21.1, +41.7]; D against C +8.3 [+2.2, +16.1]. Cost per right answer, B against C: 5.3x cheaper at list price, 22.7x cached; B against D: 66x cheaper at list price.

Where each arm fails (the answers were read, not only counted):
- **Small, model only (A), 56 misses:** the S3 counts are worst (22 of 45), and on S4 it says copper spears, Sulfur Cubes and the copper armor "aren't in vanilla Minecraft" or asks which mod is in use (24 of 45); 51 of its 56 misses are confident wrong answers.
- **Large, model only (C), 15 misses, all in S2 and S3:** a farmer's cake costs 1 emerald (the data says 3; 0 of 6 across both runs); a farmer has 10 trades (14); sheep spawn in 23 biomes (26), pigs in 22 (25), bats in 53 (54); the desert has 12 kinds of mob (14). Each is close to the truth and a reasonable approximation, but not this server's data. It scored 45 of 45 on S4: **the large model already knows the new version**, so S4 separates the small model from the large one, not the layer from the large model.
- **Small + KL (B):** 180 of 180 once the scorer was fixed. It sometimes needs many calls (10 for "which mob spawns in the most biomes"), the tail behind the 3.6 average.

### The agent fix the pilot found

The pilot's `s2-farmer-wheat-uses` ("how many times can that trade be used before a restock?") failed for both knowledge-layer arms every time (0 of 6) while the model alone answered 16 every time. The graph held `Trade.maxUses` but no tool returned it, and the prompt says to state only what the tools returned: a gap in the tools, not in the graph or the models. Fixed on main as `1c0268d` (2026-11-18): `describe` of a profession writes each trade as "20 Wheat for Emerald (16 uses before restock)" and `how_to_get` trade entries carry `maxUses`. The first wording, "(16 uses)", made the model answer "20 times" (the wheat count), so it is spelt out. After the fix the question is 3 of 3 for both layer arms, and the new `s2-cake-uses` is 3 of 3.

### The pilot (run 20261118-155734): 20 questions, 240 runs, before the fix

Cost $1.94 cached ($4.06 list). Right of 60: A 45 (75%), C 54 (90%), B 55 (92%), D 57 (95%). B against C +1.7 points [-13.3, +16.7]; B against A +16.7 [-1.7, +35.0]; D against C +5.0 [-10, +20]. Cost per right answer, B against C: 3.3x cheaper at list price, 14x cached. With 20 questions the intervals were too wide to say anything but that the result was consistent with the hypothesis, which is why the set was widened. Its other findings are in the section above (the cake, the tool gap, the large model knowing the new version) and in the caveats.

## Caveats

These matter more than the headline.

1. **The knowledge layer is graded on its own data.** Every gold answer is a query on the same graph the agent reads. 180 of 180 therefore means the agent retrieves and states the graph faithfully, which is the point of the layer, but it is close to a ceiling by construction and cannot show the graph is right. Where the graph and the real game differ, the layer would be confidently wrong and this benchmark would score it right. (One check against a blind spot: the farmer's cake, 3 emeralds, was read in the raw jar, `villager_trade/farmer/4/emerald_cake.json`; older versions charge 1.)
2. **The questions are about this server's data, which the layer is built for.** The model alone is at a disadvantage on S2 and S3 by design. S1 is the neutral control and the arms tie there (43 to 45 of 45). The result says a large model is worse at reproducing the exact figures of this server, not that it is a worse general Minecraft helper.
3. **The agent was built for these tools.** Ore heights, spawns, trades and enchantments are what its tools cover; a question on something they lack fails, as the trade uses did before the fix. The 60 questions contain no such gap left, so the benchmark does not measure how often a real player would hit one.
4. **The agent was tuned on `gpt-5.4-mini`**, and the questions were written after reading the graph, though not tuned against the agent. The tuning may favour B; a different small model would test it.
5. **One pair of models and one effort.** `gpt-6-luna` and `gpt-6.2-astra` at medium effort; another pair or effort may differ. Model versions change under an alias, so the exact model string the API returned is stored with every run. Prices change; the cost figures are approximate and dated 2026-11-18.
6. **Cache behaviour:** see "Cost and caching"; a cold cache was not measured.

## What is built

On the `benchmark` branch (committed and pushed to origin on 2026-11-19; not merged into main):
- `agent/bench/run_bench.py` (the runner: arms, models, shuffled order with a seed, repeats, cost cap), `report.py`, `verify_gold.py`, `make_questions.py`, `prices.json`, `questions.json`; results in `agent/bench/results/` (git-ignored).
- `scripts/run-benchmark.ps1`, `.sh` and `run_benchmark.py`, and a row in `scripts/README.md`.
- Agent changes, defaults unchanged for the service: `Settings.reasoning_effort`, `Settings.use_tools` (False = no tools and the bare prompt), `Settings.api` (`chat` or `responses`, the second a separate code path in `agent.py` used only by the benchmark), and usage that also records cached tokens, reasoning tokens and the model string.
- Tests: `tests/agent/test_bench.py` (cost, scoring, outcomes, the shuffle, the question file, the report) and `tests/scripts/test_run_benchmark.py` (the script's steps and its refusals, including a regression test for a PowerShell bug: positional binding once sent a bare question id to `-Run` and started a full 240-run set; fixed). The fast Python suite passes (925).

On main: the `maxUses` fix, two evals (`trade-max-uses`, `trade-max-uses-cake`) and the extended tests, commit `1c0268d`.

Spend to date: about $8 (the two runs, and a few small test runs).

## What comes next

The hypothesis holds on questions about this server's data. Options, none started:
1. **Questions the layer does not cover,** to measure the gap rate a real player would hit.
2. **Questions whose gold is not from the graph** (the wiki, or the game itself), to test the layer's data and not just its retrieval.
3. **Harder, free-form questions scored by a judge model,** since 100 percent on exact answers leaves no headroom. A judge needs a human reading a sample and a fixed, logged prompt.
4. **A cold-cache cost run,** to replace the range between 5.3x and 22.7x with a measured figure for a quiet server.
5. **The deferred items:** the Terra and Sol rungs (a cost-against-accuracy curve), the "model plus wiki retrieval, no graph" arm, and a modified world (facts changed in the graph) to prove the layer, not training data, is answering.

Also open: whether to merge the `benchmark` branch into main, and the margin for "as good as" (with 60 questions the interval for B against C already excludes zero in B's favour, so it did not have to be set).

## History

- 2026-11-18: design agreed (four arms, the gpt-6 family, same effort, a $10 budget, model-only means no tools). The runner was built and run on one question; the Responses API was needed for the gpt-6 models. A 20-question pilot ran ($1.94); reading it found the `maxUses` gap and three scorer bugs. The gap was fixed on main; the run order was shuffled and the set widened to 60; the second run ($5.74) followed.
- 2026-11-19: the run script (`run-benchmark`) added; this document rewritten as a description of the current state.
