"""Builds merged/v1/: OSMS's v1 files with the client's newer facts laid over them.

Same files, fields and meta.json as OSMS's v1, so Leafline reads it exactly as it reads OSMS. When
the client (client/full/, from tools/extract_full.py) has maps, quests, items or NPCs OSMS lacks, records
both have take the client's names and numbers and keep everything only OSMS has (descriptions,
picture addresses, monster stats, skills); records only the client has are added; quests and
recipes the client no longer has are dropped. When OSMS is as new or newer, merged/v1/ is a plain
copy of OSMS, and its meta.json says so, so Leafline goes back to OSMS.

Needs only the repository, not the game, so the daily workflow can run it after mirroring OSMS.

    python tools/build_merged.py
"""

import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OSMS = ROOT / "osms" / "v1"
CLIENT = ROOT / "client" / "full"
OUT = ROOT / "merged" / "v1"
FILES = ["maps.json", "monsters.json", "npcs.json", "quests.json", "skills.json", "items.json", "recipes.json"]
NO_MAP = 999999999
# Requirement kinds the client's quest files give; others (skills, citizenship) stay OSMS's.
QUEST_NEEDS = ("mob", "item", "quest")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def by_id(records: list) -> dict:
    return {r["id"]: r for r in records}


def put(record: dict, fields: dict) -> dict:
    out = dict(record)
    for key, value in fields.items():
        if value is None or value == "":
            continue
        out[key] = value
    return out


# ---------------------------------------------------------------- maps


def offset(theirs: dict, ours: dict):
    """How far OSMS's coordinates sit from the client's on this map: OSMS measures from its own
    picture's corner. Taken from the portals both list by name."""
    mine = {p["name"]: p for p in ours.get("portals", []) if p.get("x") is not None}
    dx, dy = [], []
    for p in theirs.get("portals", []):
        q = mine.get(p["name"])
        if q and p.get("x") is not None and p.get("y") is not None:
            dx.append(p["x"] - q["x"])
            dy.append(p["y"] - q["y"])
    if not dx:
        return None
    return round(statistics.median(dx)), round(statistics.median(dy))


def moved(points: list, shift) -> list:
    if shift is None:
        return points
    return [{**p, "x": p["x"] + shift[0] if p.get("x") is not None else None,
             "y": p["y"] + shift[1] if p.get("y") is not None else None} for p in points]


def same(a: list, b: list, keys: tuple) -> bool:
    pick = lambda items: Counter(tuple(i.get(k) for k in keys) for i in items)
    return pick(a) == pick(b)


def merge_maps(theirs: list, ours: list, report: Counter) -> list:
    t, o = by_id(theirs), by_id(ours)
    out = []
    for mid in sorted(set(t) | set(o)):
        if mid not in o:
            out.append(t[mid])
            continue
        mine = o[mid]
        if mid not in t:
            out.append(mine)
            report["maps added"] += 1
            continue
        rec = put(t[mid], {k: mine.get(k) for k in ("name", "street_name", "region", "bgm", "map_mark", "return_map_id")})
        rec["is_town"] = mine["is_town"]
        if set(rec.get("exits", [])) != set(mine["exits"]):
            rec["exits"] = mine["exits"]
        shift = offset(t[mid], mine)
        changed = False
        for field, keys in (("portals", ("name", "dest_map_id", "dest_portal")), ("npcs", ("npc_id",)), ("mobs", ("mob_id",))):
            if not same(t[mid].get(field, []), mine[field], keys):
                rec[field] = moved(mine[field], shift)
                changed = True
        if "return_map_id" in t[mid] and mine.get("return_map_id") is None:
            rec.pop("return_map_id", None)
        report["maps changed"] += changed or rec != t[mid]
        out.append(rec)
    return out


# ---------------------------------------------------------------- monsters and NPCs


def merge_monsters(theirs: list, ours: list, maps: list, report: Counter) -> list:
    """OSMS's monsters with the client's names and spawns. Monsters only the client names are left
    out: their stats are in a pack the client keeps encrypted, so there'd be nothing to show."""
    o = by_id(ours)
    known = {m["id"] for m in maps}
    out = []
    for rec in theirs:
        mine = o.get(rec["id"])
        if not mine:
            out.append(rec)
            continue
        new = put(rec, {"name": mine["name"]})
        times = {s["map_id"]: s.get("mob_time") for s in rec.get("spawns", [])}
        spawns = []
        for s in mine["spawns"]:
            if s["map_id"] in known:
                spawn = {"map_id": s["map_id"], "count": s["count"]}
                if times.get(s["map_id"]) is not None:
                    spawn["mob_time"] = times[s["map_id"]]
                spawns.append(spawn)
        if not same(rec.get("spawns", []), spawns, ("map_id", "count")):
            new["spawns"] = spawns
        report["monsters changed"] += new != rec
        out.append(new)
    spawning = {m for m in o if o[m]["spawns"]} - {r["id"] for r in theirs}
    if spawning:
        report["monsters spawning that OSMS lacks (left out)"] += len(spawning)
    return out


def merge_npcs(theirs: list, ours: list, quests: list, report: Counter) -> list:
    t, o = by_id(theirs), by_id(ours)
    wanted = {q.get("npc_id") for q in quests}
    out = []
    for nid in sorted(set(t) | set(o)):
        mine = o.get(nid)
        if nid not in o:
            out.append(t[nid])
        elif nid in t:
            rec = put(t[nid], {"name": mine["name"]})
            if mine["maps"] and set(mine["maps"]) != set(t[nid].get("maps", [])):
                rec["maps"] = mine["maps"]
            report["npcs changed"] += rec != t[nid]
            out.append(rec)
        elif mine["maps"] or nid in wanted:
            out.append(mine)
            report["npcs added"] += 1
    return out


# ---------------------------------------------------------------- quests


def item_rewards(rec: dict) -> Counter:
    return Counter((r["id"], r.get("count", 1), bool(r.get("guaranteed", True)))
                   for r in rec.get("rewards", []) if r.get("type") == "item")


def merge_quests(theirs: list, ours: list, report: Counter) -> list:
    t, o = by_id(theirs), by_id(ours)
    out = []
    for qid in sorted(o):
        mine = o[qid]
        if qid not in t:
            out.append(mine)
            report["quests added"] += 1
            continue
        rec = put(t[qid], {k: mine.get(k) for k in ("name", "region", "area", "npc_id", "level_min", "chain", "next_quest_id")})
        others = [r for r in rec.get("requirements", []) if r.get("type") not in QUEST_NEEDS]
        needs = [r for r in mine.get("requirements", []) if r["type"] in QUEST_NEEDS]
        if not same([r for r in rec.get("requirements", []) if r.get("type") in QUEST_NEEDS], needs, ("type", "id", "count", "state")):
            rec["requirements"] = others + needs
        amounts = {r["type"]: r["value"] for r in mine.get("rewards", []) if r["type"] in ("exp", "money")}
        rewards = [r for r in rec.get("rewards", []) if r.get("type") not in ("exp", "money")]
        if item_rewards(rec) != item_rewards(mine):
            rewards = [r for r in rewards if r.get("type") != "item"] + [r for r in mine.get("rewards", []) if r["type"] == "item"]
            rec.pop("reward_choices", None)
            rec.pop("reward_random", None)
        rec["rewards"] = [{"type": k, "value": v} for k, v in amounts.items()] + rewards
        if mine.get("start_items") and not same(rec.get("start_items", []), mine["start_items"], ("id", "count")):
            rec["start_items"] = mine["start_items"]
        if not rec["rewards"]:
            rec.pop("rewards")
        report["quests changed"] += rec != t[qid]
        out.append(rec)
    report["quests dropped (gone from the client)"] += len(set(t) - set(o))
    return out


# ---------------------------------------------------------------- items and recipes


def merge_items(theirs: list, ours: list, report: Counter) -> list:
    t, o = by_id(theirs), by_id(ours)
    out = []
    for iid in sorted(set(t) | set(o)):
        if iid not in o:
            out.append(t[iid])
            continue
        mine = o[iid]
        if iid not in t:
            out.append(mine)
            report["items added"] += 1
            continue
        rec = put(t[iid], {k: mine.get(k) for k in ("name", "category", "sub_category", "price", "gender", "req_classes", "spec")})
        if "stats" in mine or "stats" in rec:
            rec["stats"] = mine.get("stats", {})
            if not rec["stats"]:
                rec.pop("stats")
        if mine.get("weapon") and rec.get("weapon"):
            rec["weapon"] = put(rec["weapon"], mine["weapon"])
        if mine.get("scroll"):
            rec["scroll"] = put(rec.get("scroll") or {}, mine["scroll"])
        report["items changed"] += rec != t[iid]
        out.append(rec)
    return out


def merge_recipes(theirs: list, ours: list, report: Counter) -> list:
    key = lambda r: (r.get("discipline_skill_id"), r["output_item_id"], r.get("req_skill_level"))
    t = {key(r): r for r in theirs}
    next_id = max((r["id"] for r in theirs), default=0) + 1
    out = []
    for mine in ours:
        k = key(mine)
        if k in t:
            rec = put(t[k], {f: mine[f] for f in ("craft_exp", "meso_cost", "output_count", "ingredients")})
            report["recipes changed"] += rec != t[k]
        else:
            rec = {"id": next_id, **mine}
            next_id += 1
            report["recipes added"] += 1
        out.append(rec)
    report["recipes dropped (gone from the client)"] += len(set(t) - {key(r) for r in ours})
    return sorted(out, key=lambda r: r["id"])


# ---------------------------------------------------------------- main


def dump(records) -> bytes:
    return json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def ahead(meta: dict, source) -> list:
    """What the client has that OSMS doesn't yet: the sign OSMS hasn't caught up with a patch.
    Timestamps alone can't tell, since OSMS may rebuild from the same client minutes apart."""
    if source is None or source["extracted_at"] <= meta["data_updated_at"]:
        return []
    signs = []
    for name in ("maps.json", "quests.json", "items.json", "npcs.json"):
        theirs = {r["id"] for r in load(OSMS / name)}
        ours = {r["id"] for r in load(CLIENT / name)}
        if ours - theirs:
            signs.append(f"{len(ours - theirs)} {name.removesuffix('.json')} OSMS lacks")
        if name == "quests.json" and theirs - ours:
            signs.append(f"{len(theirs - ours)} quests gone from the client")
    return signs


def main() -> int:
    meta = load(OSMS / "meta.json")
    source = load(CLIENT / "source.json") if (CLIENT / "source.json").exists() else None
    signs = ahead(meta, source)
    newer = bool(signs)
    if signs:
        print("client is ahead of OSMS: " + "; ".join(signs))
    OUT.mkdir(parents=True, exist_ok=True)
    report = Counter()
    if newer:
        theirs = {name: load(OSMS / name) for name in FILES}
        ours = {name: load(CLIENT / name) for name in FILES if (CLIENT / name).exists()}
        maps = merge_maps(theirs["maps.json"], ours["maps.json"], report)
        quests = merge_quests(theirs["quests.json"], ours["quests.json"], report)
        files = {
            "maps.json": maps,
            "monsters.json": merge_monsters(theirs["monsters.json"], ours["monsters.json"], maps, report),
            "npcs.json": merge_npcs(theirs["npcs.json"], ours["npcs.json"], quests, report),
            "quests.json": quests,
            "skills.json": theirs["skills.json"],
            "items.json": merge_items(theirs["items.json"], ours["items.json"], report),
            "recipes.json": merge_recipes(theirs["recipes.json"], ours["recipes.json"], report),
        }
        bodies = {name: dump(records) for name, records in files.items()}
        stamp = source["extracted_at"]
        build = hashlib.sha256("".join(sorted(source["files"].values())).encode()).hexdigest()[:16]
        out_meta = {
            "schema_version": meta["schema_version"],
            "game": meta.get("game"),
            "client_build": f"client {build} + osms {meta['client_build'][:16]}",
            "data_updated_at": stamp,
            "based_on": {"osms_data_updated_at": meta["data_updated_at"], "client_extracted_at": stamp},
            "files": {name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                             "records": len(files[name]), "updated_at": stamp} for name, body in bodies.items()},
        }
    else:
        bodies = {name: (OSMS / name).read_bytes() for name in FILES}
        out_meta = {**meta, "files": {name: meta["files"][name] for name in FILES},
                    "based_on": {"osms_data_updated_at": meta["data_updated_at"],
                                 "client_extracted_at": source["extracted_at"] if source else None}}
    for name, body in bodies.items():
        (OUT / name).write_bytes(body)
    (OUT / "meta.json").write_text(json.dumps(out_meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("client newer than OSMS: laid over it" if newer else "OSMS is as new as the client: plain copy")
    for line, n in sorted(report.items()):
        print(f"  {line}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
