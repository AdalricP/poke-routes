"""Read and write pokeemerald layout blockdata (map.bin / border.bin)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .tileset import TilesetPair

METATILE_MASK = 0x03FF
COLLISION_SHIFT = 10
ELEVATION_SHIFT = 12


@dataclass
class Layout:
    metatile: np.ndarray  # (H, W) uint16
    collision: np.ndarray  # (H, W) uint8, 0 passable / 1 blocked
    elevation: np.ndarray  # (H, W) uint8, 0-15

    @property
    def height(self) -> int:
        return self.metatile.shape[0]

    @property
    def width(self) -> int:
        return self.metatile.shape[1]

    @classmethod
    def blank(cls, w: int, h: int, fill: int = 1) -> "Layout":
        return cls(
            np.full((h, w), fill, dtype=np.uint16),
            np.zeros((h, w), dtype=np.uint8),
            np.full((h, w), 3, dtype=np.uint8),
        )

    def to_blockdata(self) -> bytes:
        v = (
            (self.metatile.astype(np.uint16) & METATILE_MASK)
            | ((self.collision.astype(np.uint16) & 3) << COLLISION_SHIFT)
            | ((self.elevation.astype(np.uint16) & 0xF) << ELEVATION_SHIFT)
        )
        return v.astype("<u2").tobytes()

    @classmethod
    def from_blockdata(cls, data: bytes, width: int) -> "Layout":
        v = np.frombuffer(data, dtype="<u2").reshape(-1, width)
        return cls(
            (v & METATILE_MASK).astype(np.uint16),
            ((v >> COLLISION_SHIFT) & 3).astype(np.uint8),
            ((v >> ELEVATION_SHIFT) & 0xF).astype(np.uint8),
        )

    @classmethod
    def read(cls, path: Path, width: int) -> "Layout":
        return cls.from_blockdata(Path(path).read_bytes(), width)

    def render(self, tiles: TilesetPair, scale: int = 1) -> Image.Image:
        h, w = self.metatile.shape
        out = np.zeros((h * 16, w * 16, 3), dtype=np.uint8)
        for y in range(h):
            for x in range(w):
                out[y * 16 : y * 16 + 16, x * 16 : x * 16 + 16] = tiles.render_metatile(int(self.metatile[y, x]))
        im = Image.fromarray(out)
        if scale != 1:
            im = im.resize((w * 16 * scale, h * 16 * scale), Image.NEAREST)
        return im
