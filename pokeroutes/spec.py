"""Route specification: what the designer asks for."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

import yaml


BIOMES = ("forest", "meadow", "plains", "riverland", "beach", "desert", "mountain", "snow", "volcanic")
# Emerald routes that look like each biome: General + their secondary tileset
BIOME_TILESET = {
    "forest": "general_fortree", "meadow": "general_fortree", "plains": "general_fortree",
    "riverland": "general_fortree",
    "beach": "general_slateport",  # Route 109
    "desert": "general_mauville",  # Route 111
    "snow": "general_fallarbor",  # no snow in Emerald: Route 113's ash-covered ground, grass and trees are the nearest
    "mountain": "general_lavaridge",  # Route 112
    "volcanic": "general_lavaridge",  # Mt. Chimney / Jagged Pass rock (the Route 113 model draws cliff interiors as brick)
}
ROUTE_EFFECTS = ("none", "leaves", "snow", "rain", "ash")
TREE_SHADES = ("auto", "leaves", "meadow", "green", "yellow", "red")


@dataclass
class Exit:
    side: str  # north | south | east | west
    at: float = 0.5  # 0..1 along that side
    width: int = 3
    to: str = ""  # map constant this exit connects to
    offset: Optional[int] = None


@dataclass
class RouteSpec:
    name: str
    id: str = ""
    size: List[int] = field(default_factory=lambda: [48, 80])  # width, height in metatiles
    seed: int = 0
    biome: str = "forest"
    tileset: str = ""  # "" = picked from the biome (BIOME_TILESET)
    palette: str = ""  # optional palette grade, e.g. "arklarna"
    exits: List[Exit] = field(default_factory=list)
    meander: float = 0.6  # 0 straight .. 1 wild
    road: bool = True  # dirt road along the main path
    branches: int = 2
    loops: int = 1
    dead_ends: int = 1
    clearings: int = 3
    lakes: int = 1
    tall_grass: float = 0.35
    hidden_paths: int = 2
    hidden_items: int = 4
    special_areas: List[str] = field(default_factory=list)  # species names, archetypes, or "auto"
    # special areas are rare: each "auto" entry becomes an area only this often (named
    # entries always count); `pokeroutes region` also caps the total across the region
    special_area_chance: float = 0.35
    special_budget: int = -1  # areas this route may still have (-1 = no cap; set by the region)
    species_file: str = ""
    music: str = "MUS_ROUTE104"
    weather: str = "WEATHER_SUNNY"
    effect: str = "none"
    tree_shade: str = "auto"  # ground under trees in the preview: auto | leaves | meadow | green | yellow | red
    exclude_areas: List[str] = field(default_factory=list)  # special areas already used elsewhere
    # braided-linear skeleton (see braid.py): the route is a line of sections; in each,
    # the main lane may split into 2-3 parallel lanes that rejoin at the next node
    sections: int = 0  # 0 = from the route length
    max_lanes: int = 3  # lanes side by side in one section (1 = no braids)
    merge_chance: float = 0.35  # a side lane merges into its neighbour early
    dead_end_chance: float = 0.3  # a side lane stops short (item, lookout or pocket)
    ledge_chance: float = 0.35  # one-way ledge drops between / on lanes
    extra_exits: int = 0  # extra unconnected exits a side lane runs to (rare)
    # extra endings: [{kind: exit|special|secret, side, at, to, width, area}]
    endings: List[dict] = field(default_factory=list)
    min_run: int = 5  # shortest straight run between two turns (d-pad: no jaggies)
    max_walkable: float = 0.40  # density budget: share of the map you can walk on
    lore_spots: int = 2  # hidden little clearings with a sign / grave / ruin for writers
    coast_side: str = ""  # north | south | east | west: the sea lies along this edge (lanes keep off it)
    sea_share: float = 0.0  # how much of the map across that edge is sea (0..0.5)
    role: str = ""  # region role: connector | turn | crossroads | dead_end | ... ("" = from the exits)
    world: List[int] = field(default_factory=list)  # [x, y] of the NW corner on the shared height map (pokeroutes.height)
    region: dict = field(default_factory=dict)  # region-generator block (role, goal, elevation, water, ...)
    gate: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)  # unknown spec keys (e.g. a `region` block), kept, not used

    def __post_init__(self) -> None:
        if not self.tileset:
            self.tileset = BIOME_TILESET.get(self.biome, "general_fortree")
        if self.effect not in ROUTE_EFFECTS:
            raise ValueError(f"route {self.name!r}: effect {self.effect!r} is not one of {', '.join(ROUTE_EFFECTS)}")
        if self.tree_shade not in TREE_SHADES:
            raise ValueError(f"route {self.name!r}: tree_shade {self.tree_shade!r} is not one of {', '.join(TREE_SHADES)}")

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @classmethod
    def load(cls, path: Path) -> "RouteSpec":
        raw = yaml.safe_load(Path(path).read_text())
        exit_keys = {f.name for f in fields(Exit)}
        raw["exits"] = [Exit(**{k: v for k, v in e.items() if k in exit_keys}) for e in raw.get("exits", [])]
        known = {f.name for f in fields(cls)}
        extra = {k: raw.pop(k) for k in list(raw) if k not in known}
        spec = cls(**raw)
        spec.extra.update(extra)
        if not spec.id:
            spec.id = "".join(ch for ch in spec.name.title() if ch.isalnum())
        if spec.species_file and not Path(spec.species_file).is_absolute():
            spec.species_file = str((Path(path).parent / spec.species_file).resolve())
        return spec
