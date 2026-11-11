# Leafline-data

Backup and supplementary data for [Leafline](https://github.com/Crazy3001/Leafline), the free read-only
overlay for MapleStory Classic. Leafline's main source stays [OSMS Data Explorer](https://osmsdataexplorer.com);
this repo is the fallback and fills in what OSMS does not publish.

## What is here

| Path | What | Source |
|---|---|---|
| `osms/v1/` | Unchanged copies of OSMS's v1 JSON files | OSMS Data Explorer, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `shops/shops.json` | NPC shop stock at launch prices | Leafline's own list (same file as `data/shops.json` in Leafline) |
| `client/quests.json` | Per quest: giver and turn-in NPC ids, job codes, level to start and to finish, fame and mesos to start, open and close dates | Extracted from the game client |
| `client/npcs.json` | NPC role labels ("Grocer", "Weapon Seller" ...) | Extracted from the game client |
| `client/maps.json` | Per map: size, walkable floor length, platforms, platforms monsters spawn on, spawn points, ropes, ladders | Extracted from the game client |
| `drops/signature.json` | The etc items each monster drops (presence, no rates), each with its sources | Game client item text, [StrategyWiki](https://strategywiki.org/wiki/MapleStory/Drops_(Victoria_Island)) (CC BY-SA 4.0), and player reports (`sources/player_reported_drops.json`, the only source for shared drops like Leather and ores) |
| `sources/` | Snapshots of outside sources the lists are built from | As named in each file |

Everything is keyed by the ids OSMS uses, so it joins directly onto OSMS's files.

## Rules

- `client/` holds facts only: ids, numbers, dates and short role labels. No dialogue, descriptions,
  names, images, sprites or sounds. Game data belongs to Nexon.
- Extraction runs on a developer's PC with the game fully closed (`tools/extract.py` refuses to run
  while `MapleStory.exe` is open). Leafline itself never opens game files.
- Never commit game files (`*.wz`, `*.ms`, `*.img`); `.gitignore` blocks them.

## Updating

```bash
python tools/mirror_osms.py      # copy any OSMS file whose checksum changed (also runs daily on GitHub)
python tools/extract.py          # after a game patch, game closed; default install D:/Games/maplestorycw
python tools/extract_maps.py     # same, for map layout; also lists maps whose spawns or portals differ from OSMS
python tools/build_drops.py      # rebuild drops/signature.json from osms/ and sources/
```

`tools/wz.py` is a small read-only reader for the client's split PKG1 `.wz` files. It reads directories
and property trees only; image and sound data are skipped, never decoded.

## Credits

Everything under `osms/`, copied without changes, in the form OSMS asks for:

> Data from OSMS Data Explorer (https://osmsdataexplorer.com), CC BY 4.0

`drops/signature.json` is CC BY-SA 4.0 and includes data from StrategyWiki contributors
(https://strategywiki.org/wiki/MapleStory/Drops_(Victoria_Island)), CC BY-SA 4.0. Anything that shows
it must credit StrategyWiki and keep the same licence.

MapleStory and its game content (names, text, stats, images) are property of Nexon and are not
covered by OSMS's licence or by anything in this repo.
