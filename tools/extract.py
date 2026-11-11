"""Pulls the quest and NPC facts OSMS doesn't publish out of a MapleStory Classic install.

Writes client/quests.json and client/npcs.json. Facts only: ids, NPC ids, job codes, levels, dates,
mesos and short NPC role labels. No dialogue, descriptions, names or images are copied; quest and
NPC names already come from OSMS, matched by the same ids.

Run on the developer's PC with the game fully closed. Leafline never opens game files.

    python tools/extract.py                       # default install path
    python tools/extract.py "D:/Games/maplestorycw"
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wz import WzFile  # noqa: E402

DEFAULT_INSTALL = Path("D:/Games/maplestorycw")
OUT = Path(__file__).resolve().parent.parent / "client"
QUEST_FILE = "Quest/QuestData/QuestData_000.wz"
STRING_FILE = "String/String_000.wz"


def game_running() -> bool:
    try:
        import subprocess

        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MapleStory.exe"], capture_output=True, text=True).stdout
        return "MapleStory.exe" in out
    except OSError:
        return False


def when(stamp):
    """`YYYYMMDDhhmm` as written in the client, as `YYYY-MM-DDThh:mm` (time zone not stated)."""
    if not isinstance(stamp, str) or len(stamp) != 12 or not stamp.isdigit():
        return None
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:]}"


def number(value):
    return value if isinstance(value, int) else None


def quest_facts(qid: int, img: dict) -> dict:
    start = img.get("Check", {}).get("0", {})
    finish = img.get("Check", {}).get("1", {})
    accept = img.get("Act", {}).get("0", {})
    info = img.get("QuestInfo", {})
    jobs = start.get("job")
    fact = {
        "id": qid,
        "giver": number(start.get("npc")),
        "turn_in": number(finish.get("npc")),
        "jobs": sorted(v for v in jobs.values() if isinstance(v, int)) if isinstance(jobs, dict) else None,
        "level_to_start": number(start.get("lvmin")),
        "level_to_finish": number(finish.get("lvmin")),
        "fame_to_start": number(start.get("pop")),
        "mesos_to_start": -accept["money"] if isinstance(accept.get("money"), int) and accept["money"] < 0 else None,
        "opens": when(start.get("start")),
        "closes": when(start.get("end")),
        "starts_itself": bool(info.get("autoStart") or info.get("selfStart") or start.get("normalAutoStart")) or None,
    }
    return {k: v for k, v in fact.items() if v is not None}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    install = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INSTALL
    data = install / "appdata" / "Data"
    if game_running():
        raise SystemExit("MapleStory is running. Close the game first.")
    quest_path, string_path = data / QUEST_FILE, data / STRING_FILE

    quests_wz = WzFile(quest_path)
    quests = []
    for entry in quests_wz.root.children:
        if entry.kind == "img" and entry.name.removesuffix(".img").isdigit():
            quests.append(quest_facts(int(entry.name.removesuffix(".img")), quests_wz.image(entry)))
    quests.sort(key=lambda q: q["id"])

    strings_wz = WzFile(string_path)
    npc_img = strings_wz.image(next(e for e in strings_wz.root.children if e.name == "Npc.img"))
    npcs = sorted(
        ({"id": int(k), "role": v["func"].strip()} for k, v in npc_img.items()
         if k.isdigit() and isinstance(v, dict) and isinstance(v.get("func"), str) and v["func"].strip()),
        key=lambda n: n["id"],
    )

    source = {
        "wz_version": quests_wz.version,
        "files": {QUEST_FILE: sha256(quest_path), STRING_FILE: sha256(string_path)},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    about_q = ("Quest facts from the MapleStory Classic client that OSMS does not publish, keyed by the quest id "
               "OSMS uses. giver and turn_in are NPC ids. jobs are job codes (0 Beginner, 100 Warrior, "
               "200 Magician, 300 Bowman, 400 Thief, and their branches). opens and closes are as written in "
               "the client; its time zone is not stated. Game data is Nexon's.")
    about_n = "NPC role labels from the MapleStory Classic client, keyed by the NPC id OSMS uses. Game data is Nexon's."
    write(OUT / "quests.json", {"schema": 1, "about": about_q, "source": source, "quests": quests})
    write(OUT / "npcs.json", {"schema": 1, "about": about_n, "source": source, "npcs": npcs})
    with_turn_in = sum(1 for q in quests if "turn_in" in q)
    print(f"{len(quests)} quests ({with_turn_in} with a turn-in NPC), {len(npcs)} NPC roles -> {OUT}")
    return 0


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
