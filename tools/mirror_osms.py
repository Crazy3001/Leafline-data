"""Copies OSMS Data Explorer's v1 JSON files into osms/v1/, as a backup for Leafline.

OSMS publishes these under CC BY 4.0 (https://osmsdataexplorer.com). They are copied unchanged.
Follows OSMS's own advice: read meta.json, then fetch only the files whose checksum changed.
One request a second. Standard library only, so it runs anywhere Python 3.9+ does.

    python tools/mirror_osms.py          # update osms/v1/ in place
    python tools/mirror_osms.py --check  # say what would change, write nothing
"""

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://osmsdataexplorer.com/data/api/v1/"
OUT = Path(__file__).resolve().parent.parent / "osms" / "v1"
USER_AGENT = "Leafline-data/1 (backup mirror for the Leafline overlay; https://github.com/Crazy3001/Leafline)"
SCHEMA_VERSION = 1
MAX_BYTES = 32 * 1024 * 1024

_last = 0.0


def get(name: str) -> bytes:
    global _last
    wait = 1.0 - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    request = urllib.request.Request(BASE + name, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise SystemExit(f"{name} is over {MAX_BYTES} bytes; stopping")
    return body


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    check_only = "--check" in sys.argv
    meta_bytes = get("meta.json")
    meta = json.loads(meta_bytes)
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"OSMS is now schema {meta.get('schema_version')}; this mirror reads {SCHEMA_VERSION}")

    changed = []
    for name, info in sorted(meta["files"].items()):
        path = OUT / name
        if path.exists() and sha256(path.read_bytes()) == info["sha256"]:
            continue
        changed.append(name)
        if check_only:
            continue
        body = get(name)
        if sha256(body) != info["sha256"]:
            raise SystemExit(f"{name} did not match its checksum; OSMS may be mid-update, try later")
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    meta_path = OUT / "meta.json"
    if not check_only and (not meta_path.exists() or meta_path.read_bytes() != meta_bytes):
        OUT.mkdir(parents=True, exist_ok=True)
        meta_path.write_bytes(meta_bytes)
        changed.append("meta.json")

    print(("would update: " if check_only else "updated: ") + (", ".join(changed) if changed else "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
