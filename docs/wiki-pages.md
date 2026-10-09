# Which tutorial pages to put in the lexical graph

2026-10-05. A review of the pages listed under **Introductory** and **General** on https://minecraft.wiki/w/Tutorials (87 pages; the other sections were left out on purpose), and a proposal for which belong in the lexical graph. **Tier 1 was loaded on 2026-10-05** from `model/wiki_pages.yaml` (see "After loading" at the end).

## How the pages were judged

Each page was fetched through the MediaWiki API and run through the real loader: split into sections, cut into chunks of 300 tokens with 50 overlap, and scanned for the names of items, mobs, blocks, biomes and structures already in the domain graph. The measures are chunks, tokens, how many domain nodes the page talks about, how many wiki links it has, and signs of what it is for (Bedrock, redstone and command mentions, and the wiki's own banners: stub, work in progress, needs cleanup, Java-only, removed content).

The 87 pages together are 3,364 chunks and 558 thousand tokens, about a cent to embed. **Cost is not the constraint. Relevance, accuracy and noise are:** more text means more passages competing in a search, and a page about an exploit or a Bedrock menu would answer a player's question wrongly with full confidence.

The criteria: written for the player the game is running for (Java Edition, survival), explains something a question could be about (starting out, food, mining, shelter), mentions many nodes the domain graph already has (so the bridge is rich), a reasonable size, and not flagged as unreliable. Pages that belong to a model that does not exist yet are held back until it does.

## Tier 1: add now (17 pages, one already loaded)

16 new pages, 677 chunks, about 116 thousand tokens (a quarter of a cent). Together with the Beginner's guide they cover starting out, the first days, food, mining (including where ores are, which meets the ore placements in the world graph), shelter and the Nether portal.

- **The first days:** Beginner's guide (loaded), Your first 10 minutes, The second day, The third day, Tips and tricks.
- **Food:** Hunger management, Gathering food.
- **Mining and finding things:** Mining (the largest, 135 chunks, and the one that links most to the ore data), Mining/Diamonds, Mining/Ore veins, Mining/Ancient debris, Exploring caves, Smelting.
- **Building and staying safe:** Shelters (183 chunks, heavy but the Beginner's guide points players to it), Best building materials, Phantom-proofing.
- **The Nether:** Nether portals.

The Beginner's guide already links to five of these (Mining four times, then Exploring caves, Hunger management, Shelters and The second day), and to two tier 2 pages (Navigation and Desert survival). Those pages are stubs in the graph today, so loading them turns links that exist into paths from one chunk to another.

## Tier 2: hold (40 pages, 1,764 chunks, 299 thousand tokens)

Worth having, but each waits for something:

- **Until enchantments are modelled (4):** Enchanting table mechanics, Anvil mechanics, Best enchantments guide, Durability management. Their value is in the nodes they would link to.
- **Until villager trades are modelled (2 held):** Creating a village, Village mechanics. Trading was loaded once trades were modelled (v0.12), the first tier 2 page to be.
- **Until advancements are modelled (6):** the Java-only advancement guide tabs (Minecraft, Nether, The End, Husbandry, Adventure, How Did We Get Here?). They explain the game's own progression, which also lines up with MineCorp's tasks.
- **Until mob statistics are modelled (4):** Combat (218 chunks), Getting max gear, Dual wielding, Horses.
- **Moved to tier 3 (2026-10-09):** End survival (listed below under "after tier 1 has been judged" in the original review).
- **After tier 1 has been judged (16):** Best biomes to play, Best biomes for homes (188 chunks), the biome survival pages (Desert, Snowy, Island, Mushroom fields, Thunderstorm), Nether survival (104 chunks), End survival, Complete main adventure, Adventuring, Transportation methods, Navigation, Spawn-proofing (work in progress), Pillar jumping, Mining/Fossils.
- **General advice, large or in list form (7):** Things to do, Things not to do (204 chunks), Time-saving tips, Organization, Shelter types (needs cleanup), Construction, Glossary.

## Excluded (30 pages)

- **Another platform or edition (6):** both Menu screen pages, Playing with a controller, Achievement guide and its Legacy Console version, Minecraft in education.
- **Exploits and glitches (6):** X-ray glitches, Headless pistons, Indestructible End crystals, Breaking bedrock, Accessing the void, Chunk loader. The wiki marks these as using a bug.
- **Commands, creative and superflat (3):** Summoning jockeys, Managing slimes in superflat mode, Things to do when bored.
- **Redstone contraptions (2):** Automatic smelting, Nether hub.
- **Reference, removed content or stubs (8):** Units of measure, Measuring distance, Mapping (describes removed content), Hitboxes, Parkour, Bridging, Breaking a fall, and the Advancement guide index (no content of its own).
- **Flagged unreliable or hypothetical (5):** Zombie siege defense (accuracy disputed), Surviving in a single area for a long time, Survival in an infinite desert, Survival in an infinite ice spikes biome, Gathering resources on Peaceful difficulty (all marked as needing cleanup or built on a hypothetical).

## Tier 3: the dragon and mob farms (added 2026-10-09)

The first review stopped at Introductory and General. The Tutorial namespace has 539 pages, and the processes the owner wants (defeat the dragon, build a mob farm) have pages of their own. Five were measured the same way and added: 163 chunks, about 29 thousand tokens, well under a tenth of a cent.

| Page | Chunks | Tokens | Why |
|---|---|---|---|
| Defeating the ender dragon | 50 | 9.3k | The core: equipment, finding the stronghold (triangulation, the portal room), the fight, the crystals. There is no separate stronghold or End portal tutorial. |
| Exploring a Nether fortress | 49 | 7.5k | Where blaze rods come from, for eyes of ender. |
| Enderman farming | 11 | 1.6k | Ender pearls, the other ingredient. |
| End survival | 20 | 4.0k | Held as tier 2; relevant once the dragon is a goal. |
| Mob farm | 33 | 6.8k | The general page for a farm: locations, designs, drops, transporting mobs, grinding. It mixes Java and Bedrock designs (13 and 20 mentions), so the process must say which. |

**Held:** Nether survival (104 chunks, 23k tokens: as large as all five together, so it would dominate Nether questions; add it when a "survive the Nether" process is drafted), Monster spawner traps (43 chunks; for trapping at an existing dungeon spawner), and the single-mob farms (Creeper, Blaze, Zombified piglin) until a process needs them.

**Excluded:** Experience farming (marked work in progress), Obsidian farming and End of light mob farms (marked as needing cleanup), Indestructible End crystals (an exploit), Exploring an End city (for after the dragon), and Combat (152 kB, mob statistics).

## What this suggests for the loader

- **A reviewed page list.** Done: `model/wiki_pages.yaml`, read by `load_wiki.py --pages`, in the same spirit as `model/wiki_links.yaml`: the choice of what is in the graph is a human decision and is a file, not a command line.
- **Remember what the wiki says about a page.** Keep the banner on the `Page` as a property (`edition`, `quality`: stub, work in progress, needs cleanup), so the agent can say "this page is marked as needing cleanup" and so Bedrock-only pages cannot slip in unnoticed.
- **Check retrieval before tier 2.** Load tier 1 and ask the 29 evals plus a set of new questions (where to mine, what to eat, how to build a shelter) before adding more. The tier 2 pages are the ones most likely to make search worse, not better.

## After loading (2026-10-05)

All 17 pages loaded (720 chunks, 677 newly embedded; the Beginner's guide came from the cache). The lexical graph is now 17 pages, 549 sections, 720 chunks and 1,133 stub pages, linked by 3,191 `LINKS_TO` and 423 `REDIRECTS_TO`. After the link job there are 673 `ABOUT` and 4,743 `MENTIONS` over 535 nodes (409 items, 64 mobs, 17 blocks, 31 biomes, 14 structures).

**Retrieval.** Six questions asked of the live search each returned the right page and section first:

- *What y level should I mine diamonds at?* Mining/Diamonds, Branch mining.
- *How do I get food early on?* the Beginner's guide's Obtaining food.
- *How do I build a safe shelter on the first night?* Shelters for the first night.
- *What is an ore vein?* Mining/Ore veins.
- *How do I stop phantoms attacking me?* Phantom-proofing.
- *What do I do in the first ten minutes?* Your first 10 minutes.

**For you to review in `model/wiki_links.yaml`.** More text means more ordinary words that are also names. The name-only mentions the pages never link, by size:

- **Wall** (94 mentions in 62 chunks): the shelter pages use the plain word, not the item category.
- **Meat** (46), **Fire** (34), **Copper** (24), **Lead** (14): common words that are also nodes.
- **Real ones, probably keep:** Villager, Spawner, Chest, Stone, Piston, Ocean, Iron Pickaxe, Beef, Porkchop.
- **Ambiguous pages:** 41, mostly an item and its category of the same name (Diamond ore, Iron ore, Anvil, Bucket...), and the fish that are both an item and a mob (Cod, Salmon, Pufferfish, Tropical fish, Rabbit, Chicken).
- **Concepts the graph does not model:** 430 linked pages, led by Java Edition, Bedrock Edition, Lava, Village, Crafting, Water, Food, Hunger and The Nether. Lava, Water and the Nether are candidates for the domain graph.
