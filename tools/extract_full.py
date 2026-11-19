"""Builds OSMS-shaped records straight from a MapleStory Classic install, into client/full/.

Same field names and shapes as OSMS Data Explorer's v1 files (maps, monsters, npcs, quests, items,
recipes), so tools/build_merged.py can lay them over OSMS's own records when the client is newer.
Names and numbers only: no descriptions, dialogue, quest text or images are copied, and no picture
addresses are made up. Labels OSMS gives in its own words (an equipment slot, a weapon type, the
job flags, a quest's region) are learned from the records OSMS and the client share, and only used
where every shared record agrees.

Run on the developer's PC with the game fully closed. Leafline never opens game files.

    python tools/extract_full.py                       # default install path
    python tools/extract_full.py "D:/Games/maplestorycw"
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import game_running, sha256, write  # noqa: E402
from wz import WzFile  # noqa: E402

DEFAULT_INSTALL = Path("D:/Games/maplestorycw")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "client" / "full"
OSMS = ROOT / "osms" / "v1"

STRING = "String/String_000.wz"
MAPS = ["Map/Map/Map0/Map0_000.wz", "Map/Map/Map9/Map9_000.wz"]
QUESTS = "Quest/QuestData/QuestData_000.wz"
ETC = "Etc/Etc_000.wz"
ITEMS = {
    "Consumable": "Item/Consume/Consume_000.wz",
    "Setup": "Item/Install/Install_000.wz",
    "Etc": "Item/Etc/Etc_000.wz",
}
# Equipment folders under Character/, by the folder's own name.
EQUIP_DIRS = ["Accessory", "Cap", "Cape", "Coat", "Glove", "Longcoat", "Pants", "Shield", "Shoes", "Weapon"]

NO_MAP = 999999999
# The client's names for item stats where OSMS uses another.
STAT_NAMES = {"incWAT": "incPAD", "incWDEF": "incPDD", "incMAT": "incMAD", "incMDEF": "incMDD", "incHP": "incMHP", "incMP": "incMMP"}
# Item stats OSMS carries, by its own names.
ITEM_STAT_KEYS = {
    "attackSpeed", "incACC", "incCRD", "incCRT", "incDEX", "incEVA", "incINT", "incJump", "incLUK", "incMAD", "incMDD",
    "incMHP", "incMMP", "incPAD", "incPDD", "incSTR", "incSpeed", "knockback", "notSale", "only", "pquest", "quest",
    "recovery", "reqDEX", "reqINT", "reqLUK", "reqLevel", "reqPOP", "reqSTR", "slotMax", "timeLimited", "tradeBlock",
    "tuc", "unitPrice", "useCooldown",
}


def children(wz: WzFile) -> dict:
    return {e.name: e for e in wz.root.children}


def num(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def ids_in(node) -> list:
    """The entries of a numbered list such as `0`, `1`, `2`, in order."""
    if not isinstance(node, dict):
        return []
    return [node[k] for k in sorted((k for k in node if k.isdigit()), key=int)]


def clean(name):
    """A name with its spacing tidied: the client has stray trailing and non-breaking spaces."""
    return " ".join(name.split()) if isinstance(name, str) else name


def number(v):
    """A whole or decimal number, as some item stats are."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def camel_words(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


# ---------------------------------------------------------------- learning OSMS's labels


class Labels:
    """A label OSMS gives that follows from something the client has, such as an item id's block.
    Used only for keys where every shared record gave the same label."""

    def __init__(self):
        self.seen = defaultdict(Counter)

    def learn(self, key, label):
        self.seen[key][json.dumps(label, sort_keys=True)] += 1

    def get(self, key):
        counts = self.seen.get(key)
        if not counts or len(counts) != 1:
            return None
        return json.loads(next(iter(counts)))


def osms(name):
    path = OSMS / name
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))} if path.exists() else {}


# ---------------------------------------------------------------- strings


def strings(data: Path) -> dict:
    wz = WzFile(data / STRING)
    idx = children(wz)
    s = {}
    maps = {}
    for group, entries in wz.image(idx["Map.img"]).items():
        if isinstance(entries, dict):
            for mid, v in entries.items():
                if mid.isdigit() and isinstance(v, dict):
                    maps[int(mid)] = (group, clean(v.get("mapName")), clean(v.get("streetName")))
    s["maps"] = maps
    s["mobs"] = {int(k): clean(v.get("name")) for k, v in wz.image(idx["Mob.img"]).items() if k.isdigit() and isinstance(v, dict)}
    s["npcs"] = {int(k): clean(v.get("name")) for k, v in wz.image(idx["Npc.img"]).items() if k.isdigit() and isinstance(v, dict)}
    items = {}
    for img in ("Consume.img", "Ins.img"):
        for k, v in wz.image(idx[img]).items():
            if k.isdigit() and isinstance(v, dict):
                items[int(k)] = v.get("name")
    for k, v in (wz.image(idx["Etc.img"]).get("Etc") or {}).items():
        if k.isdigit() and isinstance(v, dict):
            items[int(k)] = v.get("name")
    for group, entries in (wz.image(idx["Eqp.img"]).get("ClassicWorld") or {}).items():
        for k, v in (entries or {}).items():
            if k.isdigit() and isinstance(v, dict):
                items[int(k)] = v.get("name")
    s["items"] = {k: clean(v) for k, v in items.items()}
    s["skills"] = {int(k): v.get("name") for k, v in wz.image(idx["Skill.img"]).items() if k.isdigit() and isinstance(v, dict) and v.get("name")}
    return s


# ---------------------------------------------------------------- maps


def map_records(data: Path, names: dict, theirs: dict) -> tuple[list, dict]:
    """Maps, and where each monster spawns (for monsters.json)."""
    regions = Labels()
    for mid, r in theirs.items():
        if mid in names["maps"]:
            regions.learn(names["maps"][mid][0], r.get("region"))
    out, spawns = [], defaultdict(Counter)
    for rel in MAPS:
        path = data / rel
        if not path.exists():
            continue
        wz = WzFile(path)
        for entry in wz.root.children:
            name = entry.name.removesuffix(".img")
            if entry.kind != "img" or not name.isdigit():
                continue
            mid = int(name)
            if mid not in names["maps"]:
                continue
            group, map_name, street = names["maps"][mid]
            img = wz.image(entry)
            info = img.get("info") or {}
            portals, mobs, npcs = [], [], []
            for p in ids_in(img.get("portal")):
                if p.get("pt") == 0:
                    continue  # a spawn point, which OSMS leaves out
                dest = num(p.get("tm"))
                portals.append({"name": p.get("pn"), "type": num(p.get("pt")), "x": num(p.get("x")), "y": num(p.get("y")),
                                "dest_map_id": dest, "dest_portal": p.get("tn") or None,
                                "_scripted": bool(p.get("script"))})
            for life in ids_in(img.get("life")):
                if not str(life.get("id", "")).isdigit() or life.get("hide"):
                    continue
                point = {"x": num(life.get("x")), "y": num(life.get("y"))}
                if life.get("type") == "m":
                    mobs.append({"mob_id": int(life["id"]), **point})
                    spawns[int(life["id"])][mid] += 1
                elif life.get("type") == "n":
                    npcs.append({"npc_id": int(life["id"]), **point})
            exits = sorted({p["dest_map_id"] for p in portals if p["dest_map_id"] not in (None, NO_MAP, mid)})
            ret = num(info.get("returnMap"))
            record = {
                "id": mid,
                "name": map_name or "",
                "street_name": street,
                "region": regions.get(group) or camel_words(group),
                "is_town": bool(info.get("town")),
                "bgm": info.get("bgm"),
                "map_mark": info.get("mapMark") or None,
                "mob_rate": info.get("mobRate"),
                "return_map_id": None if ret in (None, NO_MAP, mid) else ret,
                "exits": exits,
                "portals": portals,
                "npcs": npcs,
                "mobs": mobs,
            }
            out.append(record)
    infer_scripted_exits(out)
    return out, spawns


def infer_scripted_exits(maps: list) -> None:
    """A scripted portal (the server picks where it goes, such as the way into Forgotten Hollow)
    names no destination. When exactly one portal elsewhere leads back to it by name, that is
    where it goes: the link is filled in and marked `dest_inferred`."""
    back = defaultdict(list)
    for m in maps:
        for p in m["portals"]:
            if p["dest_map_id"] not in (None, NO_MAP, m["id"]) and p["dest_portal"]:
                back[(p["dest_map_id"], p["dest_portal"])].append((m["id"], p["name"]))
    for m in maps:
        for p in m["portals"]:
            scripted = p.pop("_scripted")
            if not scripted or p["dest_map_id"] not in (None, NO_MAP):
                continue
            sources = back.get((m["id"], p["name"]), [])
            if len(sources) == 1:
                p["dest_map_id"], p["dest_portal"] = sources[0]
                p["dest_inferred"] = True
                m["exits"] = sorted(set(m["exits"]) | {sources[0][0]})


# ---------------------------------------------------------------- monsters


def mob_records(names: dict, spawns: dict) -> list:
    """Each monster's name and where it spawns, which come from the up-to-date String and Map files.
    Their stats don't: the client keeps live monster data in an encrypted pack, and the plain
    Mob_000.wz beside it is an old copy (2026-08), so levels, HP and EXP stay OSMS's."""
    ids = set(names["mobs"]) | set(spawns)
    return [{"id": mid, "name": names["mobs"].get(mid) or "",
             "spawns": [{"map_id": m, "count": c} for m, c in sorted(spawns.get(mid, {}).items())]}
            for mid in sorted(ids)]


# ---------------------------------------------------------------- NPCs


def npc_records(names: dict, maps: list) -> list:
    where = defaultdict(set)
    for m in maps:
        for n in m["npcs"]:
            where[n["npc_id"]].add(m["id"])
    return [{"id": nid, "name": name, "maps": sorted(where.get(nid, ()))} for nid, name in sorted(names["npcs"].items()) if name]


# ---------------------------------------------------------------- quests


def quest_records(data: Path, theirs: dict, classes: "Labels") -> list:
    wz = WzFile(data / QUESTS)
    regions = Labels()
    for r in theirs.values():
        if r.get("area") is not None:
            regions.learn(r["area"], r.get("region"))
    out = []
    for entry in wz.root.children:
        name = entry.name.removesuffix(".img")
        if entry.kind != "img" or not name.isdigit():
            continue
        qid = int(name)
        img = wz.image(entry)
        info = img.get("QuestInfo") or {}
        if not info.get("name"):
            continue
        start = (img.get("Check") or {}).get("0") or {}
        finish = (img.get("Check") or {}).get("1") or {}
        accept = (img.get("Act") or {}).get("0") or {}
        done = (img.get("Act") or {}).get("1") or {}
        requirements = []
        for q in ids_in(start.get("quest")):
            if num(q.get("id")) is not None:
                requirements.append({"type": "quest", "id": q["id"], "state": num(q.get("state"))})
        for m in ids_in(finish.get("mob")):
            if num(m.get("id")) is not None:
                requirements.append({"type": "mob", "id": m["id"], "count": num(m.get("count")) or 1})
        for i in ids_in(finish.get("item")):
            count = num(i.get("count"))
            if num(i.get("id")) is not None and (count is None or count > 0):
                requirements.append({"type": "item", "id": i["id"], "count": count or 1})
        rewards = []
        if num(done.get("exp")):
            rewards.append({"type": "exp", "value": done["exp"]})
        if num(done.get("money")) and done["money"] > 0:
            rewards.append({"type": "money", "value": done["money"]})
        for i in ids_in(done.get("item")):
            count = num(i.get("count")) or 1
            if num(i.get("id")) is None or count <= 0:
                continue
            reward = {"type": "item", "guaranteed": not i.get("prop"), "id": i["id"], "count": count}
            if num(i.get("job")) and classes.get(i["job"]):
                reward["classes"] = classes.get(i["job"])
            if i.get("prop"):
                reward["prop"] = i["prop"]
            rewards.append(reward)
        start_items = [{"type": "item", "id": i["id"], "count": i["count"]} for i in ids_in(accept.get("item"))
                       if num(i.get("id")) is not None and (num(i.get("count")) or 0) > 0]
        area = num(info.get("area"))
        record = {
            "id": qid,
            "name": info["name"],
            "region": regions.get(area) if area is not None else None,
            "area": area,
            "npc_id": num(start.get("npc")),
            "level_min": num(start.get("lvmin")),
            "chain": info.get("parent") or None,
            "next_quest_id": num(done.get("nextQuest")),
            "requirements": requirements,
            "rewards": rewards,
            "start_items": start_items,
        }
        out.append({k: v for k, v in record.items() if v not in (None, [])} | {"id": qid, "name": clean(info["name"])})
    return out


# ---------------------------------------------------------------- items


def item_info(data: Path) -> dict:
    """Every item's `info` (and `spec`) block, by id, with the category its file says."""
    found = {}
    for category, rel in ITEMS.items():
        wz = WzFile(data / rel)
        for entry in wz.root.children:
            for key, node in wz.image(entry).items():
                if key.isdigit() and isinstance(node, dict):
                    found[int(key)] = (category, None, node.get("info") or {}, node.get("spec"))
    for folder in EQUIP_DIRS:
        path = data / "Character" / folder / f"{folder}_000.wz"
        if not path.exists():
            continue
        wz = WzFile(path)
        for entry in wz.root.children:
            name = entry.name.removesuffix(".img")
            if entry.kind == "img" and name.isdigit():
                found[int(name)] = ("Equipment", folder, wz.image(entry).get("info") or {}, None)
    return found


def class_labels(found: dict, theirs: dict) -> "Labels":
    """OSMS's job names for each of the client's job bit sets."""
    classes = Labels()
    for iid, r in theirs.items():
        if iid in found and "reqJob" in found[iid][2]:
            classes.learn(found[iid][2]["reqJob"], r.get("req_classes") or [])
    return classes


SCROLL_TIERS = ("Lesser", "Intermediate", "Greater", "Chaos")


def item_records(found: dict, names: dict, theirs: dict, classes: "Labels") -> list:
    slots, weapons, scrolls, scroll_stats, genders, blocks = Labels(), Labels(), Labels(), Labels(), Labels(), Counter()
    for iid, r in theirs.items():
        if iid not in found:
            continue
        category, folder, info, _ = found[iid]
        blocks[iid // 10000] += 1
        slots.learn((category, iid // 10000), r.get("sub_category"))
        if r.get("weapon"):
            weapons.learn(iid // 10000, (r["weapon"] or {}).get("type"))
        if category == "Equipment":
            genders.learn((iid // 1000) % 10, r.get("gender"))
        if r.get("scroll"):
            s = r["scroll"]
            scrolls.learn(iid // 100, s.get("slot"))
            for key in s.get("stats") or {}:
                scroll_stats.learn(key, s.get("stat"))
    out = []
    for iid, (category, folder, info, spec) in sorted(found.items()):
        name = names["items"].get(iid)
        if not name or info.get("cash") or blocks[iid // 10000] == 0 and iid not in theirs and not new_block_ok(iid, category):
            continue
        stats = {}
        for key, value in info.items():
            key = STAT_NAMES.get(key, key)
            if key in ITEM_STAT_KEYS and number(value):
                stats[key] = value
        record = {"id": iid, "name": name, "category": category,
                  "sub_category": slots.get((category, iid // 10000))}
        if stats:
            record["stats"] = stats
        if num(info.get("price")):
            record["price"] = info["price"]
        if isinstance(spec, dict):
            record["spec"] = {k: v for k, v in spec.items() if isinstance(v, (int, float, str))}
        if "reqJob" in info:
            jobs = classes.get(info["reqJob"])
            if jobs:
                record["req_classes"] = jobs
        if category == "Equipment" and folder == "Weapon":
            kind = weapons.get(iid // 10000)
            record["weapon"] = {"type": kind, "after_image": info.get("afterImage")} if kind else None
        if category == "Equipment" and genders.get((iid // 1000) % 10):
            record["gender"] = genders.get((iid // 1000) % 10)
        if record["sub_category"] == "Scroll" and num(info.get("success")) is not None:
            effect = {k: stats.pop(k) for k in list(stats) if k.startswith("inc")}
            tier = name.rsplit(":", 1)[-1].strip() if ":" in name else None
            record["scroll"] = {
                "slot": scrolls.get(iid // 100),
                "stat": next((scroll_stats.get(k) for k in effect if scroll_stats.get(k)), None),
                "tier": tier if tier in SCROLL_TIERS else None,
                "success_rate": info["success"],
                "destroy_rate": num(info.get("cursed")),
                "stats": effect,
            }
            record["scroll"] = {k: v for k, v in record["scroll"].items() if v is not None}
            if not stats:
                record.pop("stats", None)
        out.append({k: v for k, v in record.items() if v is not None})
    return out


def new_block_ok(iid: int, category: str) -> bool:
    """Items in a block OSMS has nothing of: kept only for the kinds OSMS lists in every block."""
    return category in ("Consumable", "Setup", "Etc")


# ---------------------------------------------------------------- recipes


def recipe_records(data: Path, theirs: list) -> list:
    """Recipes as OSMS lists them: an optional additive (a catalyst some recipes take) is left out
    of the ingredients, as OSMS does. The discipline's name is OSMS's for that skill id."""
    disciplines = Labels()
    for r in theirs:
        disciplines.learn(r.get("discipline_skill_id"), r.get("discipline"))
    wz = WzFile(data / ETC)
    craft = wz.image(children(wz)["CraftRecipe.img"])
    out = []
    for discipline in sorted((k for k in craft if k.isdigit()), key=int):
        for level in sorted((k for k in craft[discipline] if k.isdigit()), key=int):
            for r in ids_in(craft[discipline][level]):
                check = ((r.get("Conditions") or {}).get("SkillCheck") or {})
                result = r.get("Results") or {}
                skill = num(check.get("SkillID"))
                if num(result.get("item")) is None:
                    continue
                ingredients = [{"item_id": i["item"], "count": num(i.get("count")) or 1}
                               for i in ids_in(r.get("Ingredients")) if num(i.get("item")) is not None]
                out.append({
                    "discipline": disciplines.get(skill) or "",
                    "discipline_skill_id": skill,
                    "output_item_id": result["item"],
                    "output_count": num(result.get("count")) or 1,
                    "req_skill_level": num(check.get("reqSkillLevel")) or int(level),
                    "craft_exp": num(result.get("exp")) or 0,
                    "meso_cost": num(r.get("Meso")) or 0,
                    "ingredients": ingredients,
                })
    return out


# ---------------------------------------------------------------- main


def main() -> int:
    install = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INSTALL
    data = install / "appdata" / "Data"
    if game_running():
        raise SystemExit("MapleStory is running. Close the game first.")
    names = strings(data)
    maps, spawns = map_records(data, names, osms("maps.json"))
    found = item_info(data)
    their_items = osms("items.json")
    classes = class_labels(found, their_items)
    files = {
        "maps.json": maps,
        "monsters.json": mob_records(names, spawns),
        "npcs.json": npc_records(names, maps),
        "quests.json": quest_records(data, osms("quests.json"), classes),
        "items.json": item_records(found, names, their_items, classes),
        "recipes.json": recipe_records(data, list(osms("recipes.json").values())),
    }
    read = [STRING, QUESTS, ETC, *MAPS, *ITEMS.values(), *(f"Character/{d}/{d}_000.wz" for d in EQUIP_DIRS)]
    source = {
        "schema": 1,
        "about": "OSMS-shaped records built from the MapleStory Classic client by tools/extract_full.py: names and "
                 "numbers only, no descriptions, dialogue or images. Game data is Nexon's.",
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {rel: sha256(data / rel) for rel in read if (data / rel).exists()},
        "records": {name: len(records) for name, records in files.items()},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, records in files.items():
        write(OUT / name, records)
    write(OUT / "source.json", source)
    print(json.dumps(source["records"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
