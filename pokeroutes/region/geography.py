"""Generic land and sea for any region.

The seed picks a kind of geography (an archetype) and then shapes it: where the
land sits, how much of the map it covers, how ragged the coast is, the bays,
headlands and offshore islets. Nothing here belongs to one region's lore; the
Arklarna map is a separate preset (``terrain.arklarna_coastline``).

The shape of each archetype is a "land field" over the map: positive on land,
negative at sea, zero on the coastline. Domain warp and fBm noise roughen it
before it is cut at zero.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage as ndi

from .terrain import Fbm, norm01, shore_distance
from .world import World

ARCHETYPES = ("continent", "coast", "peninsula", "islands", "archipelago", "inland", "crescent")
ARCHETYPE_WEIGHTS = (2.0, 2.0, 1.5, 1.5, 1.5, 1.0, 1.0)
SEA_RING_CELLS = 3
SMALLEST_ISLET_CELLS = 8
LARGEST_FILLED_HOLE_CELLS = 6
LAND_FRACTION_LIMITS = (0.12, 0.82)


@dataclass
class MapFrame:
    rng: np.random.Generator
    cell_x: np.ndarray
    cell_y: np.ndarray
    warped_x: np.ndarray
    warped_y: np.ndarray
    coast_noise: np.ndarray
    width: float

    def uniform(self, low: float, high: float) -> float:
        return float(self.rng.uniform(low, high))

    def integer(self, low: int, high_inclusive: int) -> int:
        return int(self.rng.integers(low, high_inclusive + 1))


@dataclass
class LandShape:
    field: np.ndarray
    coast_roughness: float
    sea_ring: bool = True
    lake_seeds: Optional[np.ndarray] = None
    land_fraction_limits: Tuple[float, float] = LAND_FRACTION_LIMITS


def make_map_frame(w: World) -> MapFrame:
    rng = w.rng
    rows, columns = np.mgrid[0:w.H, 0:w.W].astype(float)
    map_x, map_y = (columns + 0.5) / w.H, (rows + 0.5) / w.H
    warp_noise = Fbm(rng, 4)
    warp_amount = float(rng.uniform(0.03, 0.12))
    warp_scale = float(rng.uniform(10, 28))
    warp_x = norm01(warp_noise(columns / warp_scale, rows / warp_scale)) - 0.5
    warp_y = norm01(warp_noise(columns / warp_scale + 7.3, rows / warp_scale + 3.1)) - 0.5
    coast_scale = float(rng.uniform(4, 11))
    coast_noise = norm01(Fbm(rng, 5, gain=float(rng.uniform(0.5, 0.75)))(columns / coast_scale, rows / coast_scale)) - 0.5
    return MapFrame(rng, columns, rows, map_x + 2 * warp_amount * warp_x, map_y + 2 * warp_amount * warp_y,
                    coast_noise, w.W / w.H)


def lobed_blob(frame: MapFrame, centre_x: float, centre_y: float, radius: float, wobble: float = 0.3,
               harmonics: int = 5) -> np.ndarray:
    offset_x = (frame.warped_x - centre_x) / radius
    offset_y = (frame.warped_y - centre_y) / radius
    angle = np.arctan2(offset_y, offset_x)
    radius_scale = np.ones_like(angle)
    for harmonic in range(2, harmonics + 1):
        strength = frame.uniform(0.0, wobble) / (harmonic - 1) ** 0.7
        radius_scale += strength * np.cos(harmonic * angle + frame.uniform(0, 2 * math.pi))
    return 1.0 - np.hypot(offset_x, offset_y) / np.maximum(radius_scale, 0.35)


def segment_distance(frame: MapFrame, start: Tuple[float, float], end: Tuple[float, float]) -> np.ndarray:
    (start_x, start_y), (end_x, end_y) = start, end
    along_x, along_y = end_x - start_x, end_y - start_y
    length_squared = max(1e-9, along_x * along_x + along_y * along_y)
    t = np.clip(((frame.warped_x - start_x) * along_x + (frame.warped_y - start_y) * along_y) / length_squared, 0, 1)
    return np.hypot(frame.warped_x - (start_x + t * along_x), frame.warped_y - (start_y + t * along_y))


def curved_arm(frame: MapFrame, points: List[Tuple[float, float]], half_width: float) -> np.ndarray:
    nearest = np.full(frame.warped_x.shape, np.inf)
    for start, end in zip(points, points[1:]):
        nearest = np.minimum(nearest, segment_distance(frame, start, end))
    return 1.0 - nearest / half_width


def bezier_points(start, control, end, count: int = 12) -> List[Tuple[float, float]]:
    points = []
    for t in np.linspace(0, 1, count):
        x = (1 - t) ** 2 * start[0] + 2 * t * (1 - t) * control[0] + t * t * end[0]
        y = (1 - t) ** 2 * start[1] + 2 * t * (1 - t) * control[1] + t * t * end[1]
        points.append((float(x), float(y)))
    return points


def map_centre(frame: MapFrame) -> Tuple[float, float]:
    return frame.width / 2, 0.5


def random_direction(frame: MapFrame) -> Tuple[float, float]:
    angle = frame.rng.integers(8) * math.pi / 4 + frame.uniform(-0.3, 0.3)
    return math.cos(angle), math.sin(angle)


def edge_point(frame: MapFrame, direction: Tuple[float, float]) -> Tuple[float, float]:
    centre_x, centre_y = map_centre(frame)
    reach = min(abs(frame.width / 2 / direction[0]) if direction[0] else np.inf,
                abs(0.5 / direction[1]) if direction[1] else np.inf)
    return centre_x + direction[0] * reach, centre_y + direction[1] * reach


# ------------------------------------------------------------------ archetypes

def continent(frame: MapFrame) -> LandShape:
    centre_x = frame.width / 2 + frame.uniform(-0.15, 0.15)
    centre_y = 0.5 + frame.uniform(-0.08, 0.08)
    core_radius = frame.uniform(0.30, 0.42)
    field = lobed_blob(frame, centre_x, centre_y, core_radius, wobble=0.35)
    for _ in range(frame.integer(1, 4)):
        angle = frame.uniform(0, 2 * math.pi)
        reach = core_radius * frame.uniform(0.7, 1.05)
        lobe = lobed_blob(frame, centre_x + math.cos(angle) * reach, centre_y + math.sin(angle) * reach,
                          frame.uniform(0.08, 0.18), wobble=0.4, harmonics=3)
        field = np.maximum(field, lobe)
    return LandShape(field, frame.uniform(0.25, 0.6))


def coast(frame: MapFrame) -> LandShape:
    inland_x, inland_y = random_direction(frame)
    centre_x, centre_y = map_centre(frame)
    shoreline_offset = frame.uniform(-0.2, 0.15)
    along = (frame.warped_x - centre_x) * inland_x + (frame.warped_y - centre_y) * inland_y
    across = -(frame.warped_x - centre_x) * inland_y + (frame.warped_y - centre_y) * inland_x
    wave_count, wave_height = frame.uniform(1.0, 3.5), frame.uniform(0.03, 0.12)
    wave = wave_height * np.sin(across * wave_count * 2 * math.pi + frame.uniform(0, 2 * math.pi))
    field = (along - shoreline_offset + wave) / 0.25
    return LandShape(field, frame.uniform(0.3, 0.7), sea_ring=False)


def peninsula(frame: MapFrame) -> LandShape:
    outward_x, outward_y = random_direction(frame)
    root_x, root_y = edge_point(frame, (-outward_x, -outward_y))
    mainland_depth = frame.uniform(0.26, 0.40)
    along = (frame.warped_x - root_x) * outward_x + (frame.warped_y - root_y) * outward_y
    mainland = (mainland_depth - along) / 0.25
    reach = frame.uniform(0.55, 0.85) * min(frame.width, 1.0)
    tip = (root_x + outward_x * reach, root_y + outward_y * reach)
    bend = frame.uniform(-0.25, 0.25)
    middle = (root_x + outward_x * reach / 2 - outward_y * bend, root_y + outward_y * reach / 2 + outward_x * bend)
    arm = curved_arm(frame, bezier_points((root_x, root_y), middle, tip), frame.uniform(0.09, 0.16))
    head = lobed_blob(frame, tip[0], tip[1], frame.uniform(0.08, 0.16), wobble=0.4, harmonics=3)
    field = np.maximum(np.maximum(mainland, arm), head)
    return LandShape(field, frame.uniform(0.25, 0.55), sea_ring=False)


def scattered_centres(frame: MapFrame, count: int, spacing: float, margin: float) -> List[Tuple[float, float]]:
    centres: List[Tuple[float, float]] = []
    for _ in range(count * 60):
        if len(centres) == count:
            break
        x, y = frame.uniform(margin, frame.width - margin), frame.uniform(margin, 1 - margin)
        if all(math.hypot(x - other_x, y - other_y) >= spacing for other_x, other_y in centres):
            centres.append((x, y))
    return centres


def islands(frame: MapFrame) -> LandShape:
    island_count = frame.integer(2, 4)
    spacing = {2: 0.55, 3: 0.45, 4: 0.38}[island_count]
    field = np.full(frame.warped_x.shape, -1.0)
    for centre_x, centre_y in scattered_centres(frame, island_count, spacing, 0.2):
        radius = frame.uniform(0.16, 0.27) * (1.15 if island_count == 2 else 1.0)
        field = np.maximum(field, lobed_blob(frame, centre_x, centre_y, radius, wobble=0.35))
    return LandShape(field, frame.uniform(0.2, 0.5), land_fraction_limits=(0.2, 0.5))


def archipelago(frame: MapFrame) -> LandShape:
    field = np.full(frame.warped_x.shape, -1.0)
    island_count = frame.integer(10, 22)
    for centre_x, centre_y in scattered_centres(frame, island_count, 0.13, 0.08):
        radius = frame.uniform(0.035, 0.10)
        field = np.maximum(field, lobed_blob(frame, centre_x, centre_y, radius, wobble=0.5, harmonics=4))
    return LandShape(field, frame.uniform(0.35, 0.8), land_fraction_limits=(0.12, 0.35))


def inland(frame: MapFrame) -> LandShape:
    lake_seeds = np.zeros(frame.warped_x.shape, dtype=bool)
    for centre_x, centre_y in scattered_centres(frame, frame.integer(2, 5), 0.3, 0.15):
        radius = frame.uniform(0.04, 0.09)
        lake_seeds |= lobed_blob(frame, centre_x, centre_y, radius, wobble=0.4, harmonics=4) > 0
    field = np.ones(frame.warped_x.shape)
    return LandShape(field, 0.0, sea_ring=False, lake_seeds=lake_seeds, land_fraction_limits=(0.0, 1.0))


def crescent(frame: MapFrame) -> LandShape:
    centre_x = frame.width / 2 + frame.uniform(-0.15, 0.15)
    centre_y = 0.5 + frame.uniform(-0.06, 0.06)
    ring_radius = frame.uniform(0.30, 0.42)
    ring_half_width = frame.uniform(0.10, 0.17)
    distance = np.hypot(frame.warped_x - centre_x, frame.warped_y - centre_y)
    ring = 1.0 - np.abs(distance - ring_radius) / ring_half_width
    opening_angle = frame.uniform(0, 2 * math.pi)
    opening_half_width = frame.uniform(0.4, 1.1)
    angle = np.arctan2(frame.warped_y - centre_y, frame.warped_x - centre_x)
    off_opening = np.abs(np.angle(np.exp(1j * (angle - opening_angle))))
    closing = np.clip((off_opening - opening_half_width) / 0.35, -1.0, 1.0)
    return LandShape(np.minimum(ring, closing), frame.uniform(0.2, 0.45))


SHAPERS: Dict[str, Callable[[MapFrame], LandShape]] = {
    "continent": continent, "coast": coast, "peninsula": peninsula, "islands": islands,
    "archipelago": archipelago, "inland": inland, "crescent": crescent,
}


# ------------------------------------------------------------------ coast detail

def coastline_cells(land: np.ndarray) -> np.ndarray:
    return land & ndi.binary_dilation(~land)


def random_cells(frame: MapFrame, mask: np.ndarray, count: int) -> List[Tuple[int, int]]:
    rows, columns = np.nonzero(mask)
    if rows.size == 0:
        return []
    picks = frame.rng.choice(rows.size, size=min(count, rows.size), replace=False)
    return [(int(columns[i]), int(rows[i])) for i in picks]


def cell_to_map(frame: MapFrame, cell: Tuple[int, int]) -> Tuple[float, float]:
    column, row = cell
    return float(frame.warped_x[row, column]), float(frame.warped_y[row, column])


def bays_and_headlands(frame: MapFrame, field: np.ndarray) -> np.ndarray:
    shore = coastline_cells(field > 0)
    for cell in random_cells(frame, shore, frame.integer(0, 4)):
        centre_x, centre_y = cell_to_map(frame, cell)
        bay = lobed_blob(frame, centre_x, centre_y, frame.uniform(0.04, 0.10), wobble=0.3, harmonics=3)
        field = np.minimum(field, -bay)
    shore = coastline_cells(field > 0)
    for cell in random_cells(frame, shore, frame.integer(0, 4)):
        centre_x, centre_y = cell_to_map(frame, cell)
        headland = lobed_blob(frame, centre_x, centre_y, frame.uniform(0.04, 0.09), wobble=0.4, harmonics=3)
        field = np.maximum(field, headland)
    return field


def offshore_islets(frame: MapFrame, land: np.ndarray) -> np.ndarray:
    open_sea = ndi.distance_transform_edt(~land) > 4
    islets = np.zeros_like(land)
    for cell in random_cells(frame, open_sea, frame.integer(0, 6)):
        centre_x, centre_y = cell_to_map(frame, cell)
        blob = lobed_blob(frame, centre_x, centre_y, frame.uniform(0.012, 0.03), wobble=0.5, harmonics=3)
        islets |= (blob + frame.coast_noise * 0.4) > 0
    return islets & open_sea


def cut_at_land_fraction(rough_field: np.ndarray, limits: Tuple[float, float]) -> np.ndarray:
    land = rough_field > 0
    low, high = limits
    fraction = float(land.mean())
    if fraction < low:
        return rough_field > np.quantile(rough_field, 1 - low)
    if fraction > high:
        return rough_field > np.quantile(rough_field, 1 - high)
    return land


def add_sea_ring(land: np.ndarray) -> np.ndarray:
    ringed = land.copy()
    ringed[:SEA_RING_CELLS, :] = ringed[-SEA_RING_CELLS:, :] = False
    ringed[:, :SEA_RING_CELLS] = ringed[:, -SEA_RING_CELLS:] = False
    return ringed


def tidy_land(land: np.ndarray) -> np.ndarray:
    labels, count = ndi.label(land)
    if count == 0:
        return land
    sizes = ndi.sum(land, labels, range(1, count + 1))
    kept = np.isin(labels, [i + 1 for i, size in enumerate(sizes) if size >= SMALLEST_ISLET_CELLS])
    holes, hole_count = ndi.label(ndi.binary_fill_holes(kept) & ~kept)
    hole_sizes = ndi.sum(holes > 0, holes, range(1, hole_count + 1))
    small_holes = np.isin(holes, [i + 1 for i, size in enumerate(hole_sizes) if size < LARGEST_FILLED_HOLE_CELLS])
    return kept | small_holes


def label_landmasses(land: np.ndarray) -> np.ndarray:
    labels, count = ndi.label(land)
    landmass = np.zeros(land.shape, dtype=np.int16)
    if count == 0:
        return landmass
    sizes = ndi.sum(land, labels, range(1, count + 1))
    for rank, label in enumerate(np.argsort(-sizes) + 1, start=1):
        landmass[labels == label] = rank
    return landmass


# ------------------------------------------------------------------ the step

def pick_archetype(w: World, archetype: Optional[str]) -> str:
    if archetype is None or archetype in ("", "any", "random"):
        weights = np.array(ARCHETYPE_WEIGHTS) / sum(ARCHETYPE_WEIGHTS)
        return ARCHETYPES[int(w.rng.choice(len(ARCHETYPES), p=weights))]
    if archetype not in ARCHETYPES:
        raise ValueError(f"unknown geography {archetype!r}; pick one of {', '.join(ARCHETYPES)}")
    return archetype


def land_and_sea(w: World, archetype: Optional[str] = None) -> None:
    kind = pick_archetype(w, archetype)
    frame = make_map_frame(w)
    shape = SHAPERS[kind](frame)
    field = shape.field if kind == "inland" else bays_and_headlands(frame, shape.field)
    rough_field = field + frame.coast_noise * shape.coast_roughness
    land = cut_at_land_fraction(rough_field, shape.land_fraction_limits)
    if kind != "inland":
        land |= offshore_islets(frame, land)
    if shape.sea_ring:
        land = add_sea_ring(land)
    land = tidy_land(land) if kind != "inland" else land
    w.land = land
    w.geography = kind
    w.landmass = label_landmasses(land)
    w.lagoon = np.zeros_like(land)
    w.strait = None
    if shape.lake_seeds is not None:
        w.overrides["lake_seed"] = shape.lake_seeds & land
    w.log.append(f"geography: {kind}, {land.mean() * 100:.0f}% land, {int(w.landmass.max())} landmasses")


# ------------------------------------------------------------------ height

def inland_points(w: World, count: int, rng: np.random.Generator) -> List[Tuple[int, int]]:
    shore = shore_distance(w.land)
    deep = w.land & (shore >= np.quantile(shore[w.land], 0.5))
    rows, columns = np.nonzero(deep)
    if rows.size == 0:
        return []
    picks = rng.choice(rows.size, size=min(count, rows.size), replace=False)
    return [(int(columns[i]), int(rows[i])) for i in picks]


def mountain_ranges(w: World, rng: np.random.Generator) -> np.ndarray:
    rows, columns = np.mgrid[0:w.H, 0:w.W].astype(float)
    ranges = np.zeros((w.H, w.W))
    for column, row in inland_points(w, int(rng.integers(1, 4)), rng):
        angle = float(rng.uniform(0, math.pi))
        length, width = float(rng.uniform(10, 34)), float(rng.uniform(6, 13))
        along = (columns - column) * math.cos(angle) + (rows - row) * math.sin(angle)
        across = -(columns - column) * math.sin(angle) + (rows - row) * math.cos(angle)
        ranges = np.maximum(ranges, np.exp(-((along / length) ** 2 + (across / width) ** 2)))
    return ranges


def hill_belt(w: World, rng: np.random.Generator) -> np.ndarray:
    rows, columns = np.mgrid[0:w.H, 0:w.W].astype(float)
    angle = float(rng.uniform(0, math.pi))
    offset = float(rng.uniform(-0.2, 0.2)) * w.H
    across = -(columns - w.W / 2) * math.sin(angle) + (rows - w.H / 2) * math.cos(angle) - offset
    return np.exp(-((across / (0.10 * w.H)) ** 2))


def heightmap(w: World) -> None:
    """Hills and ridges by domain-warped fBm, rising away from the shore, with one to
    three mountain ranges and a belt of lower hills wherever the seed puts them."""
    rng = w.rng
    rows, columns = np.mgrid[0:w.H, 0:w.W].astype(float)
    land = w.land
    lake_seeds = w.overrides.get("lake_seed")
    open_water = ~land if lake_seeds is None else (~land | lake_seeds)
    shore = shore_distance(~open_water)
    sea_distance = ndi.distance_transform_edt(~land)
    warp = Fbm(rng, 4)
    warp_x = warp(columns / 30, rows / 30)
    warp_y = warp(columns / 30 + 5.2, rows / 30 + 1.3)
    hills = norm01(Fbm(rng, 5)(columns / 26 + 3.5 * warp_x, rows / 26 + 3.5 * warp_y)) - 0.5
    ridges = Fbm(rng, 5, gain=0.55)(columns / 20 + 2.0 * warp_x, rows / 20 + 2.0 * warp_y, ridged=True)
    ranges = mountain_ranges(w, rng)
    belt = hill_belt(w, rng)
    rise = 1 - np.exp(-shore / 7.0)
    height = 0.06 + 0.24 * rise + hills * 0.34 * rise
    height += ridges * 0.55 * ranges * rise
    height += ridges * 0.20 * belt * rise
    if lake_seeds is not None:
        height -= 0.10 * ndi.gaussian_filter(lake_seeds.astype(float), 1.5)
    height = np.where(land, np.maximum(height, 0.012), 0.0)
    sea = -(0.04 + 0.42 * (1 - np.exp(-sea_distance / 7.0))) - (hills - 0.5) * 0.05
    w.height = np.where(land, height, sea)
    w.overrides["upland"] = ranges
    if lake_seeds is not None:
        w.overrides["lake_extra"] = lake_seeds
