"""A small read-only reader for MapleStory's split PKG1 .wz files (Data/<Category>/<Category>_000.wz).

Reads the directory and the property trees of .img entries: numbers, strings, nested properties and
vectors. Images, sounds and other binary blobs are skipped, never decoded. Nothing is ever written
to the game folder; files are opened read-only and read whole into memory.

Developer-side only. Leafline itself never opens game files.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MASK32 = 0xFFFFFFFF
OFFSET_KEY = 0x581C3F6D


def version_hash(version: int) -> int:
    h = 0
    for ch in str(version):
        h = ((h * 32) + ord(ch) + 1) & MASK32
    return h


def rotl32(value: int, shift: int) -> int:
    shift &= 31
    return ((value << shift) | (value >> (32 - shift))) & MASK32


@dataclass
class Entry:
    name: str
    kind: str  # "dir" or "img"
    size: int
    offset: int
    children: list["Entry"] = field(default_factory=list)


class WzFile:
    """One .wz file. `key` is the string key stream (all zeros when the client doesn't encrypt names)."""

    def __init__(self, path: Path, key: bytes = b"", version: int | None = None):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        if self.data[:4] != b"PKG1":
            raise ValueError(f"{self.path.name}: not a PKG1 file")
        self.size, self.start = struct.unpack_from("<QI", self.data, 4)
        self.key = key
        self.root: Entry | None = None
        self.version = None
        self._open(version)

    # --- primitive reads -------------------------------------------------------------------------

    def _byte(self, pos: int) -> tuple[int, int]:
        return self.data[pos], pos + 1

    def _sbyte(self, pos: int) -> tuple[int, int]:
        return struct.unpack_from("<b", self.data, pos)[0], pos + 1

    def _cint(self, pos: int) -> tuple[int, int]:
        v, pos = self._sbyte(pos)
        if v == -128:
            return struct.unpack_from("<i", self.data, pos)[0], pos + 4
        return v, pos

    def _clong(self, pos: int) -> tuple[int, int]:
        v, pos = self._sbyte(pos)
        if v == -128:
            return struct.unpack_from("<q", self.data, pos)[0], pos + 8
        return v, pos

    def _keybyte(self, i: int) -> int:
        return self.key[i] if i < len(self.key) else 0

    def _string(self, pos: int) -> tuple[str, int]:
        """An encrypted string: positive length = UTF-16, negative = single-byte."""
        n, pos = self._sbyte(pos)
        if n == 0:
            return "", pos
        if n > 0:
            if n == 127:
                n, pos = struct.unpack_from("<i", self.data, pos)[0], pos + 4
            chars = []
            mask = 0xAAAA
            for i in range(n):
                c = struct.unpack_from("<H", self.data, pos + i * 2)[0]
                k = self._keybyte(i * 2) | (self._keybyte(i * 2 + 1) << 8)
                chars.append(chr((c ^ mask ^ k) & 0xFFFF))
                mask = (mask + 1) & 0xFFFF
            return "".join(chars), pos + n * 2
        if n == -128:
            n, pos = struct.unpack_from("<i", self.data, pos)[0], pos + 4
        else:
            n = -n
        out = bytearray()
        mask = 0xAA
        for i in range(n):
            out.append((self.data[pos + i] ^ mask ^ self._keybyte(i)) & 0xFF)
            mask = (mask + 1) & 0xFF
        return out.decode("latin-1"), pos + n

    # --- directory -------------------------------------------------------------------------------

    def _offset(self, pos: int, vhash: int) -> tuple[int, int]:
        off = ((pos - self.start) ^ MASK32) & MASK32
        off = (off * vhash) & MASK32
        off = (off - OFFSET_KEY) & MASK32
        off = rotl32(off, off & 0x1F)
        enc = struct.unpack_from("<I", self.data, pos)[0]
        off = (off ^ enc) & MASK32
        off = (off + self.start * 2) & MASK32
        return off, pos + 4

    def _directory(self, pos: int, vhash: int, depth: int = 0) -> list[Entry]:
        if depth > 16:
            raise ValueError("directory nests too deep")
        count, pos = self._cint(pos)
        if not 0 <= count <= 100_000:
            raise ValueError(f"implausible entry count {count}")
        entries = []
        for _ in range(count):
            kind, pos = self._byte(pos)
            if kind == 1:
                pos += 4 + 2 + 4
                continue
            if kind == 2:
                ref = struct.unpack_from("<i", self.data, pos)[0]
                pos += 4
                kind, p = self._byte(self.start + ref)
                name, _ = self._string(p)
            elif kind in (3, 4):
                name, pos = self._string(pos)
            else:
                raise ValueError(f"unknown entry type {kind}")
            size, pos = self._cint(pos)
            _checksum, pos = self._cint(pos)
            offset, pos = self._offset(pos, vhash)
            if not self.start <= offset < len(self.data):
                raise ValueError("offset outside the file")
            entries.append(Entry(name, "dir" if kind == 3 else "img", size, offset))
        for e in entries:
            if e.kind == "dir":
                e.children = self._directory(e.offset, vhash, depth + 1)
        return entries

    def _plausible(self, entries: list[Entry]) -> bool:
        imgs = [e for e in walk(entries) if e.kind == "img"]
        if not imgs:
            return bool(entries) or True
        return all(self.data[e.offset] in (0x73, 0x1B) for e in imgs[:20])

    def _open(self, version: int | None) -> None:
        """Older files carry an encrypted version after the header; 64-bit ones don't, so the
        version is found by trying candidates until every offset lands on an image."""
        layouts = [self.start, self.start + 2]
        versions = [version] if version is not None else range(0, 3000)
        if len(self.data) <= self.start + 2:
            self.root, self.version = Entry("", "dir", 0, self.start), version
            return
        for v in versions:
            vhash = version_hash(v)
            for pos in layouts:
                try:
                    entries = self._directory(pos, vhash)
                except (ValueError, struct.error, IndexError):
                    continue
                if self._plausible(entries):
                    self.root = Entry("", "dir", 0, pos, entries)
                    self.version = v
                    return
        raise ValueError(f"{self.path.name}: no version made the directory readable")

    # --- image property trees --------------------------------------------------------------------

    def _block_string(self, pos: int, img: int) -> tuple[str, int]:
        t, pos = self._byte(pos)
        if t in (0x00, 0x73):
            return self._string(pos)
        if t in (0x01, 0x1B):
            ref = struct.unpack_from("<i", self.data, pos)[0]
            return self._string(img + ref)[0], pos + 4
        raise ValueError(f"unknown string block {t:#x} at {pos}")

    def _properties(self, pos: int, img: int) -> tuple[dict, int]:
        count, pos = self._cint(pos)
        out = {}
        for _ in range(count):
            name, pos = self._block_string(pos, img)
            t, pos = self._byte(pos)
            if t == 0:
                value = None
            elif t in (2, 11):
                value, pos = struct.unpack_from("<h", self.data, pos)[0], pos + 2
            elif t in (3, 19):
                value, pos = self._cint(pos)
            elif t == 20:
                value, pos = self._clong(pos)
            elif t == 4:
                flag, pos = self._byte(pos)
                if flag == 0x80:
                    value, pos = struct.unpack_from("<f", self.data, pos)[0], pos + 4
                else:
                    value = 0.0
            elif t == 5:
                value, pos = struct.unpack_from("<d", self.data, pos)[0], pos + 8
            elif t == 8:
                value, pos = self._block_string(pos, img)
            elif t == 9:
                size = struct.unpack_from("<I", self.data, pos)[0]
                pos += 4
                end = pos + size
                value = self._extended(pos, img)
                pos = end
            else:
                raise ValueError(f"unknown property type {t} for {name!r}")
            out[name] = value
        return out, pos

    def _extended(self, pos: int, img: int):
        kind, pos = self._block_string(pos, img)
        if kind == "Property":
            return self._properties(pos + 2, img)[0]
        if kind == "Shape2D#Vector2D":
            x, pos = self._cint(pos)
            y, pos = self._cint(pos)
            return {"x": x, "y": y}
        if kind == "UOL":
            return {"uol": self._block_string(pos + 1, img)[0]}
        # Canvas, Sound_DX8, Convex2D and the like: binary or not needed. Skipped, not decoded.
        return {"<skipped>": kind}

    def image(self, entry: Entry) -> dict:
        kind, pos = self._block_string(entry.offset, entry.offset)
        if kind != "Property":
            raise ValueError(f"{entry.name} starts with {kind!r}, not Property")
        return self._properties(pos + 2, entry.offset)[0]


def walk(entries: list[Entry], prefix: str = ""):
    for e in entries:
        yield e if not prefix else Entry(f"{prefix}/{e.name}", e.kind, e.size, e.offset, e.children)
        if e.kind == "dir":
            yield from walk(e.children, f"{prefix}/{e.name}" if prefix else e.name)
