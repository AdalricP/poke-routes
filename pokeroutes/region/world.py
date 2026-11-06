"""Shared state for the region pipeline.

The region is a grid of cells (``World.W`` x ``World.H``). One cell is ``CELL``
metatiles on a side, so a route rectangle of 16 x 20 cells becomes a 64 x 80
metatile route map. Arrays are indexed ``[y, x]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

CELL = 4  # metatiles per region cell

# land biome codes (one char each, so grids export as short strings)
BIOME_NAMES = {
    "~": "sea", "=": "deep sea", "o": "lake",
    "b": "beach", "d": "desert", "m": "meadow", "f": "forest", "F": "deep forest",
    "w": "marsh", "M": "mountain", "S": "snow peaks", "v": "volcanic", "l": "lava field",
    "a": "ash badlands", "g": "dragon grove", "B": "regrown burn",
}
# what each region biome becomes in a poke-routes spec (spec.BIOMES)
SPEC_BIOME = {
    "~": "beach", "=": "beach", "o": "riverland",
    "b": "beach", "d": "desert", "m": "meadow", "f": "forest", "F": "forest",
    "w": "riverland", "M": "mountain", "S": "mountain", "v": "volcanic", "l": "volcanic",
    "a": "desert", "g": "forest", "B": "forest",
}
WATER = set("~=o")


@dataclass
class Landmark:
    id: str
    legendary: str
    name: str
    kind: str
    x: int
    y: int
    footprint: List[int]  # x0, y0, x1, y1 (cells, exclusive)
    story: str
    route_goal: bool = True  # a route should lead here
    mode: str = "land"  # land | sea: how a route reaches it

    def json(self) -> dict:
        return dict(vars(self))


@dataclass
class City:
    id: str
    name: str
    rect: List[int]  # x0, y0, x1, y1
    gym: str
    honours: str
    half: str
    size: str
    note: str = ""

    @property
    def cx(self) -> float:
        return (self.rect[0] + self.rect[2]) / 2

    @property
    def cy(self) -> float:
        return (self.rect[1] + self.rect[3]) / 2

    def json(self) -> dict:
        d = dict(vars(self))
        d["map"] = "MAP_" + self.id.upper()
        return d


@dataclass
class World:
    seed: int
    W: int = 168
    H: int = 112
    rng: np.random.Generator = None
    land: np.ndarray = None
    height: np.ndarray = None  # sea < 0 <= land, land roughly 0..1
    preset: str = "generic"  # generic | arklarna
    geography: str = ""  # the kind of land the generic generator drew (continent, islands, ...)
    landmass: np.ndarray = None  # 0 sea, else a land label (arklarna: 1 Oberland, 2 Nehrung, 3 isles)
    lagoon: np.ndarray = None
    strait: Optional[Tuple[int, int]] = None  # (x, y); only the arklarna preset has one
    sea: np.ndarray = None
    lake: np.ndarray = None
    river: np.ndarray = None
    flow: np.ndarray = None
    recv: np.ndarray = None  # flat index of the downhill neighbour, -1 = sink
    temp: np.ndarray = None
    moist: np.ndarray = None
    biome: np.ndarray = None
    tiers: np.ndarray = None
    wind: Tuple[float, float] = (1.0, 0.0)  # prevailing wind (dx, dy): westerlies
    landmarks: List[Landmark] = field(default_factory=list)
    cities: List[City] = field(default_factory=list)
    links: List[dict] = field(default_factory=list)
    routes: List[dict] = field(default_factory=list)
    overrides: Dict[str, np.ndarray] = field(default_factory=dict)  # biome forced by legendaries
    stages: List[Tuple[str, object]] = field(default_factory=list)  # (title, PIL image)
    log: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.rng is None:
            self.rng = np.random.default_rng(self.seed)

    def landmark(self, lid: str) -> Optional[Landmark]:
        return next((l for l in self.landmarks if l.id == lid), None)

    def city(self, cid: str) -> Optional[City]:
        return next((c for c in self.cities if c.id == cid), None)

    @property
    def water(self) -> np.ndarray:
        return ~self.land | self.lake
