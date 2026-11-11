"""Saves StrategyWiki's MapleStory Victoria Island drop table as sources/strategywiki_victoria_drops.json.

StrategyWiki's text is CC BY-SA 4.0 (https://strategywiki.org). The page is read once through the
MediaWiki API (the HTML page refuses programs), and only each row's item name and the monsters it
lists are kept, with the revision id so the snapshot can be traced. The table reflects later global
MapleStory, so it is a cross-check for Classic, not a final word.

    python tools/fetch_strategywiki.py
"""

import datetime
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

PAGE = "MapleStory/Drops (Victoria Island)"
API = "https://strategywiki.org/w/api.php"
OUT = Path(__file__).resolve().parent.parent / "sources" / "strategywiki_victoria_drops.json"
USER_AGENT = "Leafline-data/1 (compiling a credited common-drops list; https://github.com/Crazy3001/Leafline-data)"


def main() -> None:
    query = urllib.parse.urlencode(
        {"action": "parse", "page": PAGE, "prop": "wikitext|revid", "format": "json", "formatversion": "2"}
    )
    request = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        parsed = json.load(response)["parse"]
    rows = []
    for block in parsed["wikitext"].split("\n|-"):
        lines = [line for line in block.split("\n") if line.strip()]
        head = next((line for line in lines if line.startswith("!") and "<br/>" in line), None)
        if not head:
            continue
        item = re.sub(r"\[\[.*?\]\]", "", head.split("<br/>", 1)[1]).strip(" !")
        cells = [line[1:].strip() for line in lines if line.startswith("|") and not line.startswith(("|}", "{|"))]
        if len(cells) < 4:
            continue
        monsters = re.findall(r"\[\[MapleStory/Monsters/[^|\]]*\|([^\]]+)\]\]", cells[3])
        if monsters:
            rows.append({"item": item, "monsters": monsters})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "source": f"https://strategywiki.org/wiki/{PAGE.replace(' ', '_')}",
        "revision": parsed["revid"],
        "fetched": datetime.date.today().isoformat(),
        "licence": "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/), StrategyWiki contributors",
        "rows": rows,
    }
    OUT.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} rows from revision {parsed['revid']} -> {OUT}")


if __name__ == "__main__":
    main()
