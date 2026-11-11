"""Builds drops/signature.json: the etc items each monster drops.

Three sources, each named on every entry:
- "client": the item's own description (or, failing that, its name) in the game client names the
  monster ("A rib from Skeleton Soldier"). Read from OSMS's items.json; the text itself is not copied.
- "strategywiki": StrategyWiki's Victoria Island drop table (CC BY-SA 4.0), saved by
  tools/fetch_strategywiki.py. It describes later global MapleStory, so it is kept only where both
  the item and the monster exist in Classic.
- "player": drops players report from playing Classic, in sources/player_reported_drops.json. This
  is where shared drops (Leather, ores, feathers...) come from; no open database lists them.

This is presence, not rates.

    python tools/build_drops.py
"""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "drops" / "signature.json"

# Words in item descriptions that mean a monster under another name.
ALIASES = {
    "pig with a ribbon": "Ribbon Pig",
    "humongous Zombie Mushroom": "Zombie Mushmom",
    "Skeleton Officer": "Officer Skeleton",
    "Skeleton Commander": "Commander Skeleton",
    "Stone Mask": "Rocky Mask",
}
NOT_MONSTERS = re.compile(r"^(Test\(|Straw )")


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main() -> None:
    monsters = [m for m in load("osms/v1/monsters.json") if not NOT_MONSTERS.match(m["name"])]
    items = {i["id"]: i for i in load("osms/v1/items.json")}
    etc = {i: it for i, it in items.items() if 4000000 <= i < 4010000}

    # A name means every monster called that, and the numbered forms ("Fairy 2", "Jr. Boogie 1").
    ids_named = defaultdict(set)
    for m in monsters:
        ids_named[m["name"]].add(m["id"])
        base = re.sub(r" \d+$", "", m["name"])
        if base != m["name"]:
            ids_named[base].add(m["id"])
    names = sorted(ids_named, key=len, reverse=True)

    def monsters_in(text):
        found, rest = [], text
        for phrase, real in ALIASES.items():
            if re.search(re.escape(phrase), rest, re.I):
                found.append(real)
                rest = re.sub(re.escape(phrase), " ", rest, flags=re.I)
        # Case matters: the game writes a monster's name capitalised ("from Skeleton Soldier"), while
        # "a slime" or "an octopus" in lower case is just a word (Plateon's Helmet, Bloctopus Key Chain).
        for name in names:
            pattern = r"\b" + re.escape(name) + r"(s|es)?\b"
            if re.search(pattern, rest):
                found.append(name)
                rest = re.sub(pattern, " ", rest)
        return found

    pairs = defaultdict(set)  # (monster id, item id) -> sources
    for item_id, item in etc.items():
        named = monsters_in(item.get("description") or "")
        # A name alone ("Raffle Petal") counts only among monster drops; 4003xxx are crafting
        # materials, where "Fairy Wing" says nothing about who drops it.
        if not named and item_id < 4003000:
            named = monsters_in(item["name"])
        for name in named:
            for mid in ids_named.get(name, ()):
                pairs[(mid, item_id)].add("client")

    wiki = load("sources/strategywiki_victoria_drops.json")
    item_ids = defaultdict(list)
    for item_id, item in etc.items():
        item_ids[item["name"].lower()].append(item_id)
    for row in wiki["rows"]:
        for item_id in item_ids.get(row["item"].lower(), []):
            for name in row["monsters"]:
                for mid in ids_named.get(name, ()):
                    pairs[(mid, item_id)].add("strategywiki")

    # Drops players report, matched by name to any etc item (ores and Omok pieces sit above 4010000).
    reported = load("sources/player_reported_drops.json")
    any_etc = defaultdict(list)
    for item_id, item in items.items():
        if 4000000 <= item_id < 5000000:
            any_etc[item["name"].lower()].append(item_id)
    unmatched = []
    for row in reported["rows"]:
        mids = ids_named.get(row["monster"], set())
        if not mids:
            unmatched.append(row["monster"])
        for name in row["items"]:
            # "Name #4000000" picks one of several items that share a name.
            pinned = re.fullmatch(r"(.+?) #(\d+)", name)
            if pinned:
                name, wanted = pinned.group(1), int(pinned.group(2))
                found = [i for i in any_etc.get(name.lower(), []) if i == wanted]
            else:
                found = any_etc.get(name.lower(), [])
            if not found:
                unmatched.append(f"{row['monster']}: {name}")
            for item_id in found:
                for mid in mids:
                    pairs[(mid, item_id)].add("player")
    if unmatched:
        raise SystemExit(f"player-reported names not in the game data: {unmatched}")

    by_monster = defaultdict(list)
    for (mid, item_id), sources in pairs.items():
        drop = {"item": item_id, "name": items[item_id]["name"], "sources": sorted(sources)}
        # The client marks items that only drop while a quest wants them.
        if (items[item_id].get("stats") or {}).get("quest"):
            drop["quest"] = True
        by_monster[mid].append(drop)
    names_by_id = {m["id"]: m["name"] for m in monsters}
    out = [
        {"id": mid, "name": names_by_id[mid], "drops": sorted(drops, key=lambda d: d["item"])}
        for mid, drops in sorted(by_monster.items())
    ]
    document = {
        "schema": 1,
        "about": (
            "The etc items each monster drops. Presence only, no rates. Keyed by OSMS's monster and item "
            "ids. Each drop names its sources: client (the game's own item text names the monster), "
            "strategywiki (StrategyWiki's Victoria Island drop table, from later global MapleStory) and "
            "player (reported by players of Classic, unverified; the only source for shared drops such as "
            "Leather, ores and feathers). quest: true marks items that only drop while a quest wants them."
        ),
        "licence": (
            "CC BY-SA 4.0. Includes data from StrategyWiki (https://strategywiki.org/wiki/MapleStory/"
            f"Drops_(Victoria_Island), revision {wiki['revision']}), CC BY-SA 4.0. Game data is Nexon's."
        ),
        "monsters": out,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    spawned = {m["id"] for m in monsters if m.get("spawns")}
    covered = spawned & set(by_monster)
    both = sum(1 for s in pairs.values() if len(s) >= 2)
    only_player = sum(1 for s in pairs.values() if s == {"player"})
    print(f"{len(pairs)} monster-item pairs ({both} in two or more sources, {only_player} player-reported only) "
          f"for {len(by_monster)} monsters; "
          f"{len(covered)} of {len(spawned)} monsters that spawn have at least one -> {OUT}")
    print("spawning monsters with none:", sorted({names_by_id[m] for m in spawned - covered}))


if __name__ == "__main__":
    main()
