"""Pulls each map's layout numbers out of a MapleStory Classic install into client/maps.json.

Per map, keyed by OSMS's map id: its size, how much of it can be walked, how many platforms it
has, how many of those monsters spawn on, how many spawn points there are, and its ropes and
ladders. Numbers only: no images, names or text. Run with the game fully closed.

Also compares spawn points and portals with OSMS's maps.json and lists maps that differ, which
shows whether a game patch changed maps OSMS has not caught up with.

    python tools/extract_maps.py                       # default install path
    python tools/extract_maps.py "D:/Games/maplestorycw"
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import game_running, sha256, write  # noqa: E402
from wz import WzFile  # noqa: E402

DEFAULT_INSTALL = Path("D:/Games/maplestorycw")
ROOT = Path(__file__).resolve().parent.parent
MAP_FILES = ["Map/Map/Map0/Map0_000.wz", "Map/Map/Map9/Map9_000.wz"]
NO_MAP = 999999999


def layout(img: dict) -> dict:
    info = img.get("info") or {}
    groups = defaultdict(list)  # (layer, group) -> segments
    group_of = {}  # foothold id -> (layer, group)
    for layer, layer_groups in (img.get("foothold") or {}).items():
        if not isinstance(layer_groups, dict):
            continue
        for group, segments in layer_groups.items():
            if not isinstance(segments, dict):
                continue
            for fid, seg in segments.items():
                if isinstance(seg, dict) and all(k in seg for k in ("x1", "y1", "x2", "y2")):
                    groups[(layer, group)].append(seg)
                    if fid.isdigit():
                        group_of[int(fid)] = (layer, group)

    def is_floor(seg):  # walls are vertical footholds
        return seg["x1"] != seg["x2"]

    floors = {key for key, segs in groups.items() if any(is_floor(s) for s in segs)}
    walkable = sum(abs(s["x2"] - s["x1"]) for segs in groups.values() for s in segs if is_floor(s))
    xs = [x for segs in groups.values() for s in segs for x in (s["x1"], s["x2"])]
    ys = [y for segs in groups.values() for s in segs for y in (s["y1"], s["y2"])]
    if all(k in info for k in ("VRLeft", "VRRight", "VRTop", "VRBottom")):
        width, height = info["VRRight"] - info["VRLeft"], info["VRBottom"] - info["VRTop"]
    elif xs:
        width, height = max(xs) - min(xs), max(ys) - min(ys)
    else:
        width = height = None

    spawns = [v for v in (img.get("life") or {}).values() if isinstance(v, dict) and v.get("type") == "m"]
    spawn_groups = {group_of.get(v.get("fh")) for v in spawns} - {None}
    ropes = [v for v in (img.get("ladderRope") or {}).values() if isinstance(v, dict)]
    # Only portals that lead somewhere; start points and script triggers have no target map.
    portals = [v for v in (img.get("portal") or {}).values() if isinstance(v, dict) and v.get("tm", NO_MAP) != NO_MAP]
    return {
        "width": width,
        "height": height,
        "walkable": walkable,
        "platforms": len(floors),
        "spawn_points": len(spawns),
        "spawn_platforms": len(spawn_groups),
        "ropes": sum(1 for r in ropes if not r.get("l")),
        "ladders": sum(1 for r in ropes if r.get("l")),
        "_portals": len(portals),
    }


def main() -> int:
    install = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INSTALL
    data = install / "appdata" / "Data"
    if game_running():
        raise SystemExit("MapleStory is running. Close the game first.")
    osms = {m["id"]: m for m in json.loads((ROOT / "osms/v1/maps.json").read_text(encoding="utf-8"))}

    maps, files = {}, {}
    for rel in MAP_FILES:
        path = data / rel
        if not path.exists():
            continue
        wz = WzFile(path)
        files[rel] = sha256(path)
        for entry in wz.root.children:
            name = entry.name.removesuffix(".img")
            if entry.kind == "img" and name.isdigit() and int(name) in osms:
                maps[int(name)] = layout(wz.image(entry))

    differ = []
    for mid, m in sorted(maps.items()):
        theirs = osms[mid]
        o_spawns = len(theirs.get("mobs") or [])
        o_portals = sum(1 for x in theirs.get("portals") or [] if x.get("dest_map_id") not in (None, NO_MAP))
        if (m["spawn_points"], m["_portals"]) != (o_spawns, o_portals):
            differ.append(f"{mid} {theirs['name']}: spawns {o_spawns}->{m['spawn_points']}, portals {o_portals}->{m['_portals']}")
    for m in maps.values():
        del m["_portals"]

    missing = sorted(set(osms) - set(maps))
    out = [{"id": mid, **m} for mid, m in sorted(maps.items())]
    about = ("Map layout numbers from the MapleStory Classic client, keyed by the map id OSMS uses. width and "
             "height are the map's camera bounds in pixels; walkable is the summed horizontal length of every "
             "floor; platforms counts connected floor pieces; spawn_platforms how many of them monsters spawn on. "
             "Game data is Nexon's.")
    write(ROOT / "client" / "maps.json", {"schema": 1, "about": about, "source": {"files": files}, "maps": out})
    print(f"{len(maps)} of {len(osms)} OSMS maps -> client/maps.json; not in the client files read: {missing}")
    print(f"{len(differ)} maps differ from OSMS in spawn points or portals:")
    for line in differ[:40]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
