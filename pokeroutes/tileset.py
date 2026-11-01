"""Load pokeemerald tilesets (tiles.png + palettes + metatiles.bin) and render metatiles.

Format notes (pokeemerald):
  * tiles.png       indexed 4bpp sheet, 8x8 tiles, 16 tiles per row
  * palettes/NN.pal JASC-PAL, 16 colours each. Primary owns slots 0-5, secondary 6-12.
  * metatiles.bin   8 u16 per metatile (4 bottom-layer tiles, then 4 top-layer tiles)
                    bits 0-9 tile index (>=512 -> secondary sheet), 10 hflip, 11 vflip, 12-15 palette
  * metatile_attributes.bin  u16 per metatile: bits 0-7 behaviour, 12-15 layer type
  * Secondary metatile ids start at 0x200.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

NUM_PRIMARY_TILES = 512
NUM_PRIMARY_METATILES = 512
NUM_PRIMARY_PALS = 6
NUM_PALS_TOTAL = 13


def _read_pal(path: Path) -> np.ndarray:
    lines = path.read_text().split()
    assert lines[0] == "JASC-PAL", path
    n = int(lines[2])
    vals = [int(v) for v in lines[3 : 3 + n * 3]]
    return np.array(vals, dtype=np.uint8).reshape(n, 3)


def _read_tiles(path: Path) -> np.ndarray:
    """Return (N, 8, 8) array of colour indices 0-15."""
    im = Image.open(path)
    if im.mode != "P":
        raise ValueError(f"{path} must be an indexed PNG")
    a = np.array(im, dtype=np.uint8) & 0x0F
    h, w = a.shape
    a = a.reshape(h // 8, 8, w // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 8, 8)
    return a


@dataclass
class Tileset:
    name: str
    path: Path
    tiles: np.ndarray
    palettes: np.ndarray  # (16, 16, 3)
    metatiles: np.ndarray  # (M, 8) uint16
    attributes: np.ndarray  # (M,) uint16

    @classmethod
    def load(cls, path: Path, name: Optional[str] = None) -> "Tileset":
        path = Path(path)
        tiles = _read_tiles(path / "tiles.png")
        pals = np.zeros((16, 16, 3), dtype=np.uint8)
        for i in range(16):
            p = path / "palettes" / f"{i:02d}.pal"
            if p.exists():
                pals[i] = _read_pal(p)[:16]
        raw = (path / "metatiles.bin").read_bytes()
        metatiles = np.frombuffer(raw, dtype="<u2").reshape(-1, 8).copy()
        araw = (path / "metatile_attributes.bin").read_bytes()
        attrs = np.frombuffer(araw, dtype="<u2").copy()
        return cls(name or path.name, path, tiles, pals, metatiles, attrs)


@dataclass
class TilesetPair:
    """A primary + secondary tileset as the game loads them for one map."""

    primary: Tileset
    secondary: Tileset
    _cache: Dict[int, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def palettes(self) -> np.ndarray:
        pals = np.zeros((16, 16, 3), dtype=np.uint8)
        pals[:NUM_PRIMARY_PALS] = self.primary.palettes[:NUM_PRIMARY_PALS]
        pals[NUM_PRIMARY_PALS:NUM_PALS_TOTAL] = self.secondary.palettes[NUM_PRIMARY_PALS:NUM_PALS_TOTAL]
        return pals

    def metatile_count(self) -> Tuple[int, int]:
        return len(self.primary.metatiles), len(self.secondary.metatiles)

    def _entries(self, mid: int) -> Optional[np.ndarray]:
        if mid < NUM_PRIMARY_METATILES:
            return self.primary.metatiles[mid] if mid < len(self.primary.metatiles) else None
        sid = mid - NUM_PRIMARY_METATILES
        return self.secondary.metatiles[sid] if sid < len(self.secondary.metatiles) else None

    def attribute(self, mid: int) -> int:
        if mid < NUM_PRIMARY_METATILES:
            a = self.primary.attributes
            return int(a[mid]) if mid < len(a) else 0
        a = self.secondary.attributes
        sid = mid - NUM_PRIMARY_METATILES
        return int(a[sid]) if sid < len(a) else 0

    def behavior(self, mid: int) -> int:
        return self.attribute(mid) & 0xFF

    def _tile(self, idx: int) -> np.ndarray:
        if idx < NUM_PRIMARY_TILES:
            t = self.primary.tiles
        else:
            t = self.secondary.tiles
            idx -= NUM_PRIMARY_TILES
        if idx >= len(t):
            return np.zeros((8, 8), dtype=np.uint8)
        return t[idx]

    def render_metatile(self, mid: int) -> np.ndarray:
        """Return a 16x16x3 RGB array for metatile id ``mid``."""
        if mid in self._cache:
            return self._cache[mid]
        out = np.zeros((16, 16, 3), dtype=np.uint8)
        entries = self._entries(mid)
        if entries is None:
            out[:] = (255, 0, 255)
            self._cache[mid] = out
            return out
        pals = self.palettes
        for layer in range(2):
            for q in range(4):
                e = int(entries[layer * 4 + q])
                t = self._tile(e & 0x3FF)
                if e & 0x400:
                    t = t[:, ::-1]
                if e & 0x800:
                    t = t[::-1, :]
                pal = pals[(e >> 12) & 0xF]
                y, x = (q // 2) * 8, (q % 2) * 8
                rgb = pal[t]
                if layer == 0:
                    out[y : y + 8, x : x + 8] = rgb
                else:
                    mask = t != 0
                    out[y : y + 8, x : x + 8][mask] = rgb[mask]
        self._cache[mid] = out
        return out

    def atlas(self, which: str = "primary", cols: int = 8, scale: int = 2, labels: bool = True) -> Image.Image:
        """Porymap-style sheet of every metatile, optionally labelled with hex ids."""
        from PIL import ImageDraw

        if which == "primary":
            ids = list(range(len(self.primary.metatiles)))
        else:
            ids = [NUM_PRIMARY_METATILES + i for i in range(len(self.secondary.metatiles))]
        rows = (len(ids) + cols - 1) // cols
        cell = 16 * scale + (10 if labels else 0)
        im = Image.new("RGB", (cols * 16 * scale, rows * cell), (30, 30, 30))
        d = ImageDraw.Draw(im)
        for n, mid in enumerate(ids):
            tile = Image.fromarray(self.render_metatile(mid)).resize((16 * scale, 16 * scale), Image.NEAREST)
            x, y = (n % cols) * 16 * scale, (n // cols) * cell
            im.paste(tile, (x, y))
            if labels:
                d.text((x + 1, y + 16 * scale), f"{mid:03X}", fill=(220, 220, 220))
        return im


def load_pair(root: Path, primary: str, secondary: str) -> TilesetPair:
    root = Path(root)
    return TilesetPair(
        Tileset.load(root / "primary" / primary),
        Tileset.load(root / "secondary" / secondary),
    )
