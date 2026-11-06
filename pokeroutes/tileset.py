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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

NUM_PRIMARY_TILES = 512
NUM_PRIMARY_METATILES = 512
NUM_PRIMARY_PALS = 6
NUM_PALS_TOTAL = 13
METATILE_LAYER_TYPE_SHIFT = 12
METATILE_LAYER_TYPE_MASK = 0xF
METATILE_LAYER_TYPE_COVERED = 1


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


def _load_anim(path: Path, tiles: np.ndarray) -> Dict[int, List[np.ndarray]]:
    """Animated-tile replacement frames (water, flowers, waterfalls...) from
    data/tilesets/*/anim/<name>/N.png: the same raw 8x8 frame strips the game copies into
    VRAM over the static tile at the same slot. Returns tile index -> ordered frame list,
    frame 0 first; only indices whose frame-0 content matches the static sheet exactly are
    included, so an unmatched/unknown animation (e.g. this tileset's water, which isn't
    baked into the static sheet at all) is skipped rather than guessed at."""
    anim_dir = path / "anim"
    if not anim_dir.is_dir():
        return {}
    out: Dict[int, List[np.ndarray]] = {}
    for group in sorted(p for p in anim_dir.iterdir() if p.is_dir()):
        frame_files = sorted(group.glob("*.png"), key=lambda f: int(f.stem))
        if not frame_files:
            continue
        frames = [_read_tiles(f) for f in frame_files]
        n = frames[0].shape[0]
        if any(f.shape[0] != n for f in frames):
            continue
        votes: Counter = Counter()
        for i in range(n):
            for j in range(len(tiles)):
                if np.array_equal(tiles[j], frames[0][i]):
                    votes[j - i] += 1
        if not votes:
            continue
        base = votes.most_common(1)[0][0]
        for i in range(n):
            idx = base + i
            if 0 <= idx < len(tiles) and np.array_equal(tiles[idx], frames[0][i]):
                out[idx] = [fr[i] for fr in frames]
    return out


@dataclass
class Tileset:
    name: str
    path: Path
    tiles: np.ndarray
    palettes: np.ndarray  # (16, 16, 3)
    metatiles: np.ndarray  # (M, 8) uint16
    attributes: np.ndarray  # (M,) uint16
    tile_frames: Dict[int, List[np.ndarray]] = field(default_factory=dict, repr=False)

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
        tile_frames = _load_anim(path, tiles)
        return cls(name or path.name, path, tiles, pals, metatiles, attrs, tile_frames)


@dataclass
class TilesetPair:
    """A primary + secondary tileset as the game loads them for one map."""

    primary: Tileset
    secondary: Tileset
    _cache: Dict[Tuple[int, int], np.ndarray] = field(default_factory=dict, repr=False)
    _anim_cache: Optional[Dict[int, int]] = field(default=None, repr=False)

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

    def _tile(self, idx: int, frame: int = 0) -> np.ndarray:
        if idx < NUM_PRIMARY_TILES:
            t = self.primary
        else:
            t = self.secondary
            idx -= NUM_PRIMARY_TILES
        frames = t.tile_frames.get(idx)
        if frames:
            return frames[frame % len(frames)]
        if idx >= len(t.tiles):
            return np.zeros((8, 8), dtype=np.uint8)
        return t.tiles[idx]

    def render_metatile(self, mid: int, frame: int = 0) -> np.ndarray:
        """Return a 16x16x3 RGB array for metatile id ``mid``. ``frame`` picks the animation
        frame (water ripples, swaying flowers, falling waterfalls...) for any of its tiles
        that animate; frame 0 is always identical to the metatile's static look."""
        key = (mid, frame)
        if key in self._cache:
            return self._cache[key]
        out = np.zeros((16, 16, 3), dtype=np.uint8)
        entries = self._entries(mid)
        if entries is None:
            out[:] = (255, 0, 255)
            self._cache[key] = out
            return out
        pals = self.palettes
        for layer in range(2):
            for q in range(4):
                e = int(entries[layer * 4 + q])
                t = self._tile(e & 0x3FF, frame)
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
        self._cache[key] = out
        return out

    def layer_type(self, mid: int) -> int:
        layer_type_of_metatile = (self.attribute(mid) >> METATILE_LAYER_TYPE_SHIFT) & METATILE_LAYER_TYPE_MASK
        return layer_type_of_metatile

    def top_layer_mask(self, mid: int) -> np.ndarray:
        top_layer_pixels = np.zeros((16, 16), dtype=bool)
        entries = self._entries(mid)
        if entries is None or self.layer_type(mid) == METATILE_LAYER_TYPE_COVERED:
            return top_layer_pixels
        for quarter_index in range(4):
            tile_entry = int(entries[4 + quarter_index])
            tile_pixels = self._tile(tile_entry & 0x3FF)
            tile_pixels = tile_pixels[:, ::-1] if tile_entry & 0x400 else tile_pixels
            tile_pixels = tile_pixels[::-1, :] if tile_entry & 0x800 else tile_pixels
            top_y, top_x = (quarter_index // 2) * 8, (quarter_index % 2) * 8
            top_layer_pixels[top_y:top_y + 8, top_x:top_x + 8] = tile_pixels != 0
        return top_layer_pixels

    def animated_metatiles(self) -> Dict[int, int]:
        """metatile id -> animation frame count, for every metatile that uses at least one
        tile with more than one frame (see Tileset.tile_frames)."""
        if self._anim_cache is not None:
            return self._anim_cache
        out: Dict[int, int] = {}
        n_pri, n_sec = self.metatile_count()
        ids = list(range(n_pri)) + [NUM_PRIMARY_METATILES + i for i in range(n_sec)]
        for mid in ids:
            entries = self._entries(mid)
            if entries is None:
                continue
            nf = 1
            for e in entries:
                idx = int(e) & 0x3FF
                t, local = (self.primary, idx) if idx < NUM_PRIMARY_TILES else (self.secondary, idx - NUM_PRIMARY_TILES)
                frames = t.tile_frames.get(local)
                if frames:
                    nf = max(nf, len(frames))
            if nf > 1:
                out[mid] = nf
        self._anim_cache = out
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
