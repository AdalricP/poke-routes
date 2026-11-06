from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np

from .generate import DIRS4, WALKABLE, Event, Route

Cell = Tuple[int, int]

STORY_GATE_KIND = "story"
FIELD_MOVE_GATE_KINDS = ("cut", "surf", "strength", "rock_smash", "waterfall", "dive")
EVERY_GATE_KIND = frozenset(FIELD_MOVE_GATE_KINDS + (STORY_GATE_KIND,))
UNSUPPORTED_GATE_KINDS = ("dive",)
GATE_KIND_DISPLAY_NAME = {"cut": "Cut", "surf": "Surf", "strength": "Strength", "rock_smash": "Rock Smash",
                          "waterfall": "Waterfall", "dive": "Dive", STORY_GATE_KIND: "story"}

OBSTACLE_EVENT_KIND_FOR_GATE_KIND = {"cut": "cut_tree", "rock_smash": "rock_smash",
                                     "strength": "strength_boulder", STORY_GATE_KIND: "story_blocker"}
GATE_KIND_FOR_OBSTACLE_EVENT_KIND = {event_kind: gate_kind for gate_kind, event_kind in OBSTACLE_EVENT_KIND_FOR_GATE_KIND.items()}
GATE_KINDS_MADE_OF_WATER = ("surf", "waterfall")
GATE_KINDS_A_GATE_ALSO_TAKES_AWAY = {"surf": ("waterfall",)}
TREE_CLASS = "f"
ROCK_CLASS = "r"
BIOMES_WALLED_WITH_TREES = ("forest", "meadow", "riverland", "plains", "beach")

SURFABLE_CLASSES = ("w", "b")
WATERFALL_CLASS = "a"
WATER_CLASS = "w"
BLOCKED_STAND_IN_CLASS = "f"
OPEN_STAND_IN_CLASS = "p"
LEDGE_JUMP_DIRECTION_BY_CLASS = {"s": (1, 0), "e": (0, 1), "x": (0, -1)}
EVENT_KINDS_THAT_ARE_NOT_OBJECTS = ("hidden_item", "gate_switch", "sign")

NEAREST_GATE_DISTANCE_FROM_ENTRANCE_IN_CELLS = 4
FARTHEST_GATE_DISTANCE_FROM_ENTRANCE_IN_CELLS = 18
CLEARANCE_BETWEEN_GATE_AND_OTHER_EXITS_IN_CELLS = 4
MAXIMUM_CELLS_ACROSS_ONE_GATE_LAYER = 14
SEALED_BORDER_DEPTH_IN_CELLS = 2
MAXIMUM_STRENGTH_PUSHES_TO_CLEAR_THE_GAP = 6
NOT_REACHED_DISTANCE = -1
SURF_BAND_LAYER_CLASSES = (WATER_CLASS, WATER_CLASS, WATER_CLASS)
WATERFALL_BAND_LAYER_CLASSES = (WATER_CLASS, WATER_CLASS, WATERFALL_CLASS, WATERFALL_CLASS, WATER_CLASS)
GAP_GATE_DEPTH_IN_LAYERS = 2


@dataclass(frozen=True)
class GateLayer:
    distance_from_entrance_in_cells: int
    cells: Tuple[Cell, ...]
    gap_cell: Cell
    cell_after_gap: Cell
    is_gap_on_main_road: bool
    open_cells_beside_gap: int = 0


@dataclass
class GateDraft:
    layer: GateLayer
    new_class_by_cell: Dict[Cell, str]
    obstacle_event: Optional[Event] = None
    cells_kept_clear_of_objects: Tuple[Cell, ...] = ()
    strength_pushes_to_clear_the_gap: int = 0
    follows_the_layer_shape: bool = False


@dataclass
class GateSurroundings:
    entrance_cells: List[Cell]
    other_exit_cells: List[Cell]
    toward: str
    entrance_side: str
    spec_entrance_index: int
    reserved_cells: Set[Cell] = field(default_factory=set)


def requested_route_gate(spec) -> dict:
    region_block = spec.region if isinstance(spec.region, dict) else {}
    requested_gate = spec.gate or region_block.get("gate") or (spec.extra or {}).get("gate") or {}
    gate_request = dict(requested_gate) if isinstance(requested_gate, dict) else {}
    return gate_request


def gate_kinds_in_build_order(gate_request: dict) -> List[str]:
    field_move_needed = str(gate_request.get("needs") or "").strip().lower().replace(" ", "_")
    has_story_gate = gate_request.get("after") is not None or bool(gate_request.get("after_beat"))
    gate_kinds = ([STORY_GATE_KIND] if has_story_gate else []) + ([field_move_needed] if field_move_needed else [])
    return gate_kinds


def story_beat_named_by_gate(gate_request: dict) -> str:
    if gate_request.get("after_beat"):
        return str(gate_request["after_beat"])
    if gate_request.get("after") is not None:
        story_beat_label = f"story beat {int(gate_request['after']) + 1}"
        return story_beat_label
    return "a story beat"


def build_route_gates(route: Route) -> None:
    gate_request = requested_route_gate(route.spec)
    if not gate_request or not route.exits:
        return

    surroundings = gate_surroundings_for_entrance(route, int(gate_request.get("entrance") or 0), gate_request.get("entrance_toward") or "")
    for gate_kind in gate_kinds_in_build_order(gate_request):
        gate_record = build_single_gate(route, gate_kind, gate_request, surroundings)
        route.gates.append(gate_record)
        route.log.append(f"gate: {gate_record['note']}")
        surroundings.reserved_cells |= {tuple(cell) for cell in gate_record.get("cells", [])}

    route.snap("gate")


def gate_surroundings_for_entrance(route: Route, spec_entrance_index: int, place_name_toward_entrance: str) -> GateSurroundings:
    entrance_position = route_exit_position_for_spec_exit(route, spec_entrance_index)
    entrance_exit, entrance_cells = route.exits[entrance_position]
    other_exit_cells = [cell for position, (_, cells) in enumerate(route.exits) if position != entrance_position for cell in cells]
    surroundings = GateSurroundings(entrance_cells=list(entrance_cells), other_exit_cells=other_exit_cells,
                                    toward=place_name_toward_entrance or entrance_exit.to or f"the {entrance_exit.side} edge",
                                    entrance_side=entrance_exit.side,
                                    spec_entrance_index=spec_entrance_index if 0 <= spec_entrance_index < len(route.spec.exits) else 0)
    return surroundings


def route_exit_position_for_spec_exit(route: Route, spec_entrance_index: int) -> int:
    if not (0 <= spec_entrance_index < len(route.spec.exits)):
        return 0
    spec_exit = route.spec.exits[spec_entrance_index]
    matching_positions = [position for position, (exit_object, _) in enumerate(route.exits) if exit_object is spec_exit]
    entrance_position = matching_positions[0] if matching_positions else 0
    return entrance_position


def build_single_gate(route: Route, gate_kind: str, gate_request: dict, surroundings: GateSurroundings) -> dict:
    if gate_kind in UNSUPPORTED_GATE_KINDS:
        return unsupported_gate_record(gate_kind, surroundings)
    if gate_kind not in EVERY_GATE_KIND:
        return unknown_gate_record(gate_kind, surroundings)

    open_grid = class_grid_with_gate_kinds(route.cls, route.events, EVERY_GATE_KIND)
    distance_grid = walking_distances_from_cells(open_grid, surroundings.entrance_cells)
    layers = candidate_gate_layers(route, distance_grid, gate_kind, surroundings)
    drafts = [draft for layer in layers
              for draft in drafts_for_gate_layer(route, layer, distance_grid, gate_kind, gate_request, surroundings)]
    for draft in sorted(drafts, key=lambda draft: draft_untidiness(route, draft, gate_kind)):
        if not is_gate_draft_sound(route, draft, gate_kind, surroundings):
            continue
        apply_gate_draft(route, draft)
        return built_gate_record(gate_kind, gate_request, draft, surroundings)

    failed_record = {"kind": gate_kind, "built": False, "entrance": surroundings.spec_entrance_index,
                     "toward": surroundings.toward,
                     "note": f"needs {GATE_KIND_DISPLAY_NAME[gate_kind]}, but no narrow spot near the "
                             f"{surroundings.entrance_side} exit closes the main path; the route is left open"}
    return failed_record


def unsupported_gate_record(gate_kind: str, surroundings: GateSurroundings) -> dict:
    unsupported_record = {"kind": gate_kind, "built": False, "supported": False,
                          "entrance": surroundings.spec_entrance_index, "toward": surroundings.toward,
                          "note": f"needs {GATE_KIND_DISPLAY_NAME[gate_kind]}: not supported yet (no underwater "
                                  f"maps), so nothing is placed and the route is left open"}
    return unsupported_record


def unknown_gate_record(gate_kind: str, surroundings: GateSurroundings) -> dict:
    unknown_record = {"kind": gate_kind, "built": False, "supported": False,
                      "entrance": surroundings.spec_entrance_index, "toward": surroundings.toward,
                      "note": f"needs '{gate_kind}', which isn't a field move the generator knows; nothing placed"}
    return unknown_record


def is_inside_grid(grid: np.ndarray, cell: Cell) -> bool:
    cell_is_inside = 0 <= cell[0] < grid.shape[0] and 0 <= cell[1] < grid.shape[1]
    return cell_is_inside


def cells_one_move_away(grid: np.ndarray, cell: Cell) -> Iterator[Cell]:
    for step_y, step_x in DIRS4:
        neighbour_cell = (cell[0] + step_y, cell[1] + step_x)
        if not is_inside_grid(grid, neighbour_cell):
            continue
        neighbour_class = grid[neighbour_cell]
        if neighbour_class in WALKABLE:
            yield neighbour_cell
            continue
        if LEDGE_JUMP_DIRECTION_BY_CLASS.get(neighbour_class) != (step_y, step_x):
            continue
        landing_cell = (neighbour_cell[0] + step_y, neighbour_cell[1] + step_x)
        if is_inside_grid(grid, landing_cell) and grid[landing_cell] in WALKABLE:
            yield landing_cell


def walking_distances_from_cells(grid: np.ndarray, start_cells: Sequence[Cell]) -> np.ndarray:
    distance_grid = np.full(grid.shape, NOT_REACHED_DISTANCE, dtype=np.int32)
    queue = deque()
    for start_cell in start_cells:
        if grid[start_cell] in WALKABLE and distance_grid[start_cell] == NOT_REACHED_DISTANCE:
            distance_grid[start_cell] = 0
            queue.append(start_cell)
    while queue:
        cell = queue.popleft()
        for next_cell in cells_one_move_away(grid, cell):
            if distance_grid[next_cell] != NOT_REACHED_DISTANCE:
                continue
            distance_grid[next_cell] = distance_grid[cell] + 1
            queue.append(next_cell)
    return distance_grid


def reached_cells_mask(grid: np.ndarray, start_cells: Sequence[Cell]) -> np.ndarray:
    reached_mask = walking_distances_from_cells(grid, start_cells) != NOT_REACHED_DISTANCE
    return reached_mask


def class_grid_with_gate_kinds(class_grid: np.ndarray, events: Sequence[Event], allowed_gate_kinds) -> np.ndarray:
    grid = np.array(class_grid, copy=True)
    for event in events:
        gate_kind_needed = GATE_KIND_FOR_OBSTACLE_EVENT_KIND.get(event.kind)
        if gate_kind_needed and gate_kind_needed not in allowed_gate_kinds:
            grid[event.y, event.x] = BLOCKED_STAND_IN_CLASS
    if "surf" in allowed_gate_kinds and "waterfall" in allowed_gate_kinds:
        grid[grid == WATERFALL_CLASS] = OPEN_STAND_IN_CLASS
    if "surf" in allowed_gate_kinds:
        grid[np.isin(grid, SURFABLE_CLASSES)] = OPEN_STAND_IN_CLASS
    return grid


def draft_untidiness(route: Route, draft: GateDraft, gate_kind: str) -> int:
    climbs_sideways = gate_kind == "waterfall" and push_direction(draft.layer)[0] == 0
    sideways_waterfall_penalty = 4 if climbs_sideways else 0
    ragged_band_penalty = 1000 if draft.follows_the_layer_shape else 0
    untidiness = untileable_water_cell_count(route.cls, draft) + sideways_waterfall_penalty + ragged_band_penalty
    return untidiness


def untileable_water_cell_count(class_grid: np.ndarray, draft: GateDraft) -> int:
    if not any(new_class in (WATER_CLASS, WATERFALL_CLASS) for new_class in draft.new_class_by_cell.values()):
        return 0
    wet_mask = np.isin(classes_with_draft(class_grid, draft), [WATER_CLASS, WATERFALL_CLASS, "b", "q"])
    covered_mask = cells_covered_by_two_by_two_blocks(wet_mask)
    untileable_count = sum(1 for cell in draft.new_class_by_cell if not covered_mask[cell])
    return untileable_count


def cells_covered_by_two_by_two_blocks(mask: np.ndarray) -> np.ndarray:
    block_mask = mask[:-1, :-1] & mask[1:, :-1] & mask[:-1, 1:] & mask[1:, 1:]
    covered_mask = np.zeros_like(mask)
    for offset_y in (0, 1):
        for offset_x in (0, 1):
            covered_mask[offset_y:offset_y + block_mask.shape[0], offset_x:offset_x + block_mask.shape[1]] |= block_mask
    return covered_mask


def gate_kinds_allowed_without(gate_kind: str) -> frozenset:
    taken_away_gate_kinds = {gate_kind, *GATE_KINDS_A_GATE_ALSO_TAKES_AWAY.get(gate_kind, ())}
    allowed_gate_kinds = EVERY_GATE_KIND - taken_away_gate_kinds
    return allowed_gate_kinds


def gate_depth_in_layers(gate_kind: str) -> int:
    if gate_kind == "surf":
        return len(SURF_BAND_LAYER_CLASSES)
    if gate_kind == "waterfall":
        return len(WATERFALL_BAND_LAYER_CLASSES)
    if gate_kind == "strength":
        return GAP_GATE_DEPTH_IN_LAYERS + MAXIMUM_STRENGTH_PUSHES_TO_CLEAR_THE_GAP
    return GAP_GATE_DEPTH_IN_LAYERS


def nearest_other_exit_distance(distance_grid: np.ndarray, other_exit_cells: Sequence[Cell]) -> int:
    reached_distances = [int(distance_grid[cell]) for cell in other_exit_cells if distance_grid[cell] != NOT_REACHED_DISTANCE]
    nearest_distance = min(reached_distances) if reached_distances else FARTHEST_GATE_DISTANCE_FROM_ENTRANCE_IN_CELLS * 4
    return nearest_distance


def candidate_gate_layers(route: Route, distance_grid: np.ndarray, gate_kind: str,
                          surroundings: GateSurroundings) -> List[GateLayer]:
    other_exit_distance = nearest_other_exit_distance(distance_grid, surroundings.other_exit_cells)
    farthest_distance = min(FARTHEST_GATE_DISTANCE_FROM_ENTRANCE_IN_CELLS,
                            other_exit_distance - CLEARANCE_BETWEEN_GATE_AND_OTHER_EXITS_IN_CELLS - gate_depth_in_layers(gate_kind))
    main_road_cells = set(route.critical_path)

    layers = []
    for distance in range(NEAREST_GATE_DISTANCE_FROM_ENTRANCE_IN_CELLS, farthest_distance + 1):
        layer = gate_layer_at_distance(route, distance_grid, distance, main_road_cells)
        if layer is None or not is_gate_layer_usable(route, layer, distance_grid, gate_kind, surroundings):
            continue
        layers.append(layer)

    preferred_layers = sorted(layers, key=gate_layer_preference)
    return preferred_layers


def gate_layer_preference(layer: GateLayer) -> Tuple[int, int]:
    off_main_road_penalty_in_cells = 0 if layer.is_gap_on_main_road else 4
    open_side_penalty_in_cells = 6 * layer.open_cells_beside_gap
    preference = (len(layer.cells) + off_main_road_penalty_in_cells + open_side_penalty_in_cells,
                  layer.distance_from_entrance_in_cells)
    return preference


def cells_at_distance(distance_grid: np.ndarray, distance: int) -> Tuple[Cell, ...]:
    cells = tuple((int(cell_y), int(cell_x)) for cell_y, cell_x in zip(*np.nonzero(distance_grid == distance)))
    return cells


def is_in_sealed_border(route: Route, cell: Cell) -> bool:
    cell_is_in_border = not route.inside(cell[0], cell[1], SEALED_BORDER_DEPTH_IN_CELLS)
    return cell_is_in_border


def straight_through_gap_candidates(route: Route, distance_grid: np.ndarray, layer_cells: Sequence[Cell],
                                    distance: int) -> List[Tuple[Cell, Cell]]:
    candidates = []
    for gap_cell in layer_cells:
        for step_y, step_x in DIRS4:
            cell_before_gap = (gap_cell[0] - step_y, gap_cell[1] - step_x)
            cell_after_gap = (gap_cell[0] + step_y, gap_cell[1] + step_x)
            if not (is_inside_grid(distance_grid, cell_before_gap) and is_inside_grid(distance_grid, cell_after_gap)):
                continue
            if distance_grid[cell_before_gap] != distance - 1 or distance_grid[cell_after_gap] != distance + 1:
                continue
            if route.cls[gap_cell] not in WALKABLE or route.cls[cell_after_gap] not in WALKABLE:
                continue
            candidates.append((gap_cell, cell_after_gap))
    return candidates


def gate_layer_at_distance(route: Route, distance_grid: np.ndarray, distance: int,
                           main_road_cells: Set[Cell]) -> Optional[GateLayer]:
    layer_cells = cells_at_distance(distance_grid, distance)
    if not layer_cells or len(layer_cells) > MAXIMUM_CELLS_ACROSS_ONE_GATE_LAYER:
        return None
    gap_candidates = [candidate for candidate in straight_through_gap_candidates(route, distance_grid, layer_cells, distance)
                      if not is_in_sealed_border(route, candidate[0])]
    if not gap_candidates:
        return None

    gap_cell, cell_after_gap = min(gap_candidates, key=lambda candidate: (
        open_cell_count_beside_gap(route, distance_grid, distance, *candidate), candidate[0] not in main_road_cells))
    layer = GateLayer(distance_from_entrance_in_cells=distance, cells=layer_cells, gap_cell=gap_cell,
                      cell_after_gap=cell_after_gap, is_gap_on_main_road=gap_cell in main_road_cells,
                      open_cells_beside_gap=open_cell_count_beside_gap(route, distance_grid, distance, gap_cell, cell_after_gap))
    return layer


def open_cell_count_beside_gap(route: Route, distance_grid: np.ndarray, distance: int, gap_cell: Cell,
                               cell_after_gap: Cell) -> int:
    lateral_step = (abs(cell_after_gap[1] - gap_cell[1]), abs(cell_after_gap[0] - gap_cell[0]))
    side_cells = [(gap_cell[0] + lateral_step[0], gap_cell[1] + lateral_step[1]),
                  (gap_cell[0] - lateral_step[0], gap_cell[1] - lateral_step[1])]
    open_side_cells = [cell for cell in side_cells
                       if is_inside_grid(route.cls, cell) and route.cls[cell] in WALKABLE and distance_grid[cell] != distance]
    open_cell_count = len(open_side_cells)
    return open_cell_count


def is_gate_layer_usable(route: Route, layer: GateLayer, distance_grid: np.ndarray, gate_kind: str,
                         surroundings: GateSurroundings) -> bool:
    gate_depth = gate_depth_in_layers(gate_kind)
    nearby_cells = {cell for depth in range(-1, gate_depth + 1)
                    for cell in cells_at_distance(distance_grid, layer.distance_from_entrance_in_cells + depth)}
    exit_cells = set(surroundings.entrance_cells) | set(surroundings.other_exit_cells)
    is_clear_of_earlier_gates = not (nearby_cells & surroundings.reserved_cells)
    is_clear_of_exits = not (set(layer.cells) & exit_cells)
    layer_is_usable = is_clear_of_earlier_gates and is_clear_of_exits
    return layer_is_usable


def wall_class_beside_gap(route: Route, gate_kind: str) -> str:
    if gate_kind == "cut" or route.spec.biome in BIOMES_WALLED_WITH_TREES:
        return TREE_CLASS
    return ROCK_CLASS


def obstacle_event_for_gap(gate_kind: str, gap_cell: Cell, gate_request: dict, toward: str) -> Event:
    display_name = GATE_KIND_DISPLAY_NAME[gate_kind]
    if gate_kind != STORY_GATE_KIND:
        field_move_obstacle = Event(OBSTACLE_EVENT_KIND_FOR_GATE_KIND[gate_kind], gap_cell[0], gap_cell[1],
                                    {"why": f"route gate: needs {display_name} to get in from {toward}",
                                     "route_gate": True, "needs": gate_kind, "toward": toward})
        return field_move_obstacle
    story_beat = story_beat_named_by_gate(gate_request)
    story_blocker = Event(OBSTACLE_EVENT_KIND_FOR_GATE_KIND[STORY_GATE_KIND], gap_cell[0], gap_cell[1],
                          {"why": f"route gate: stands in the way from {toward} until {story_beat}",
                           "route_gate": True, "role": "story_blocker", "after_beat": story_beat,
                           "script_note": f"moves after {story_beat}", "toward": toward, "text": ""})
    return story_blocker


def drafts_for_gate_layer(route: Route, layer: GateLayer, distance_grid: np.ndarray, gate_kind: str,
                          gate_request: dict, surroundings: GateSurroundings) -> Iterator[GateDraft]:
    if gate_kind in GATE_KINDS_MADE_OF_WATER:
        band_layer_classes = SURF_BAND_LAYER_CLASSES if gate_kind == "surf" else WATERFALL_BAND_LAYER_CLASSES
        yield straight_water_band_draft(route, layer, band_layer_classes)
        yield water_band_draft(layer, distance_grid, band_layer_classes)
        return
    yield from gap_drafts(route, layer, distance_grid, gate_kind, gate_request, surroundings)


def lateral_offsets_of_layer(layer: GateLayer) -> Tuple[int, int]:
    step_y, step_x = push_direction(layer)
    lateral_offsets = [(cell[0] - layer.gap_cell[0]) * abs(step_x) + (cell[1] - layer.gap_cell[1]) * abs(step_y)
                       for cell in layer.cells]
    lateral_range = (min(lateral_offsets), max(lateral_offsets))
    return lateral_range


def straight_water_band_draft(route: Route, layer: GateLayer, band_layer_classes: Sequence[str]) -> GateDraft:
    step_y, step_x = push_direction(layer)
    lateral_step_y, lateral_step_x = abs(step_x), abs(step_y)
    nearest_lateral_offset, farthest_lateral_offset = lateral_offsets_of_layer(layer)
    new_class_by_cell = {}
    for depth, band_class in enumerate(band_layer_classes):
        for lateral_offset in range(nearest_lateral_offset, farthest_lateral_offset + 1):
            cell = (layer.gap_cell[0] + step_y * depth + lateral_step_y * lateral_offset,
                    layer.gap_cell[1] + step_x * depth + lateral_step_x * lateral_offset)
            if is_in_sealed_border(route, cell):
                continue
            new_class_by_cell[cell] = band_class
    band_draft = GateDraft(layer=layer, new_class_by_cell=new_class_by_cell)
    return band_draft


def water_band_draft(layer: GateLayer, distance_grid: np.ndarray, band_layer_classes: Sequence[str]) -> GateDraft:
    new_class_by_cell = {}
    for depth, band_class in enumerate(band_layer_classes):
        for cell in cells_at_distance(distance_grid, layer.distance_from_entrance_in_cells + depth):
            new_class_by_cell[cell] = band_class
    band_draft = GateDraft(layer=layer, new_class_by_cell=new_class_by_cell,
                           follows_the_layer_shape=True)
    return band_draft


def gap_drafts(route: Route, layer: GateLayer, distance_grid: np.ndarray, gate_kind: str,
               gate_request: dict, surroundings: GateSurroundings) -> Iterator[GateDraft]:
    wall_class = wall_class_beside_gap(route, gate_kind)
    obstacle_event = obstacle_event_for_gap(gate_kind, layer.gap_cell, gate_request, surroundings.toward)
    open_grid = class_grid_with_gate_kinds(route.cls, route.events, EVERY_GATE_KIND)
    layer_cells_on_the_way_through = cells_on_the_way_to_cells(
        open_grid, distance_grid, layer.distance_from_entrance_in_cells, surroundings.other_exit_cells)
    next_layer_cells_on_the_way_through = cells_on_the_way_to_cells(
        open_grid, distance_grid, layer.distance_from_entrance_in_cells + 1, surroundings.other_exit_cells)
    thin_walls = {cell: wall_class for cell in layer_cells_on_the_way_through if cell != layer.gap_cell}
    thick_walls = thin_walls | {cell: wall_class for cell in next_layer_cells_on_the_way_through if cell != layer.cell_after_gap}
    nook_walls = {cell: wall_class for cell in layer.cells if cell != layer.gap_cell}
    kept_clear_cells = straight_push_lane(route, layer) if gate_kind == "strength" else (layer.gap_cell, layer.cell_after_gap)
    for walls in (nook_walls | thick_walls, nook_walls | thin_walls, thick_walls, thin_walls):
        yield GateDraft(layer=layer, new_class_by_cell=dict(walls), obstacle_event=obstacle_event,
                        cells_kept_clear_of_objects=(layer.gap_cell,) + tuple(kept_clear_cells))


def cells_one_move_before(grid: np.ndarray, cell: Cell) -> Iterator[Cell]:
    for step_y, step_x in DIRS4:
        previous_cell = (cell[0] - step_y, cell[1] - step_x)
        if not is_inside_grid(grid, previous_cell):
            continue
        if grid[previous_cell] in WALKABLE:
            yield previous_cell
            continue
        if LEDGE_JUMP_DIRECTION_BY_CLASS.get(grid[previous_cell]) != (step_y, step_x):
            continue
        launch_cell = (previous_cell[0] - step_y, previous_cell[1] - step_x)
        if is_inside_grid(grid, launch_cell) and grid[launch_cell] in WALKABLE:
            yield launch_cell


def cells_on_the_way_to_cells(grid: np.ndarray, distance_grid: np.ndarray, distance: int,
                              target_cells: Sequence[Cell]) -> Set[Cell]:
    beyond_cells = {cell for cell in target_cells if distance_grid[cell] > distance}
    stack = list(beyond_cells)
    layer_cells_on_the_way = set()
    while stack:
        for previous_cell in cells_one_move_before(grid, stack.pop()):
            if distance_grid[previous_cell] == distance:
                layer_cells_on_the_way.add(previous_cell)
                continue
            if distance_grid[previous_cell] < distance or previous_cell in beyond_cells:
                continue
            beyond_cells.add(previous_cell)
            stack.append(previous_cell)
    return layer_cells_on_the_way


def push_direction(layer: GateLayer) -> Tuple[int, int]:
    direction = (layer.cell_after_gap[0] - layer.gap_cell[0], layer.cell_after_gap[1] - layer.gap_cell[1])
    return direction


def straight_push_lane(route: Route, layer: GateLayer) -> Tuple[Cell, ...]:
    step_y, step_x = push_direction(layer)
    lane_cells = tuple((layer.gap_cell[0] + step_y * pushes, layer.gap_cell[1] + step_x * pushes)
                       for pushes in range(1, MAXIMUM_STRENGTH_PUSHES_TO_CLEAR_THE_GAP + 1))
    inside_lane_cells = tuple(cell for cell in lane_cells if is_inside_grid(route.cls, cell))
    return inside_lane_cells


def classes_with_draft(class_grid: np.ndarray, draft: GateDraft) -> np.ndarray:
    drafted_classes = np.array(class_grid, copy=True)
    for cell, new_class in draft.new_class_by_cell.items():
        drafted_classes[cell] = new_class
    return drafted_classes


def is_event_displaced_by_draft(event: Event, draft: GateDraft) -> bool:
    event_cell = (event.y, event.x)
    is_under_new_terrain = event_cell in draft.new_class_by_cell
    is_object_in_the_way = event_cell in draft.cells_kept_clear_of_objects and event.kind not in EVENT_KINDS_THAT_ARE_NOT_OBJECTS
    event_is_displaced = is_under_new_terrain or is_object_in_the_way
    return event_is_displaced


def events_with_draft(events: Sequence[Event], draft: GateDraft) -> List[Event]:
    kept_events = [event for event in events if not is_event_displaced_by_draft(event, draft)]
    drafted_events = kept_events + ([draft.obstacle_event] if draft.obstacle_event else [])
    return drafted_events


def reaches_any_cell(grid: np.ndarray, start_cells: Sequence[Cell], target_cells: Sequence[Cell]) -> bool:
    reached_mask = reached_cells_mask(grid, start_cells)
    reaches_a_target = any(reached_mask[cell] for cell in target_cells)
    return reaches_a_target


def every_exit_cell(route: Route) -> List[Cell]:
    exit_cells = [cell for _, cells in route.exits for cell in cells]
    return exit_cells


def keeps_every_reachable_cell(route: Route, open_grid_before: np.ndarray, open_grid_after: np.ndarray,
                               draft: GateDraft, surroundings: GateSurroundings) -> bool:
    exit_cells = every_exit_cell(route)
    reached_before = reached_cells_mask(open_grid_before, exit_cells)
    reached_after = reached_cells_mask(open_grid_after, exit_cells)
    for cell in draft.new_class_by_cell:
        reached_before[cell] = False
    lost_cells_count = int((reached_before & ~reached_after).sum())

    other_exits_reached_before = [cell for cell in surroundings.other_exit_cells
                                  if reached_cells_mask(open_grid_before, surroundings.entrance_cells)[cell]]
    reached_from_entrance_after = reached_cells_mask(open_grid_after, surroundings.entrance_cells)
    keeps_through_route = all(reached_from_entrance_after[cell] for cell in other_exits_reached_before)
    keeps_everything = lost_cells_count == 0 and keeps_through_route
    return keeps_everything


def is_gate_draft_sound(route: Route, draft: GateDraft, gate_kind: str, surroundings: GateSurroundings) -> bool:
    drafted_classes = classes_with_draft(route.cls, draft)
    drafted_events = events_with_draft(route.events, draft)

    blocked_grid = class_grid_with_gate_kinds(drafted_classes, drafted_events, gate_kinds_allowed_without(gate_kind))
    if reaches_any_cell(blocked_grid, surroundings.entrance_cells, surroundings.other_exit_cells):
        return False
    open_grid_before = class_grid_with_gate_kinds(route.cls, route.events, EVERY_GATE_KIND)
    open_grid_after = class_grid_with_gate_kinds(drafted_classes, drafted_events, EVERY_GATE_KIND)
    if not keeps_every_reachable_cell(route, open_grid_before, open_grid_after, draft, surroundings):
        return False
    if gate_kind != "strength":
        return True

    object_cells = {(event.y, event.x) for event in drafted_events
                    if event.kind not in EVENT_KINDS_THAT_ARE_NOT_OBJECTS and event is not draft.obstacle_event}
    draft.strength_pushes_to_clear_the_gap = strength_pushes_that_clear_the_gap(open_grid_after, draft.layer, object_cells, surroundings)
    boulder_can_be_cleared = draft.strength_pushes_to_clear_the_gap > 0
    return boulder_can_be_cleared


def strength_pushes_that_clear_the_gap(open_grid: np.ndarray, layer: GateLayer, object_cells: Set[Cell],
                                       surroundings: GateSurroundings) -> int:
    step_y, step_x = push_direction(layer)
    other_exits_reached = [cell for cell in surroundings.other_exit_cells
                           if reached_cells_mask(open_grid, surroundings.entrance_cells)[cell]]
    for pushes in range(1, MAXIMUM_STRENGTH_PUSHES_TO_CLEAR_THE_GAP + 1):
        boulder_resting_cell = (layer.gap_cell[0] + step_y * pushes, layer.gap_cell[1] + step_x * pushes)
        player_cell = (boulder_resting_cell[0] - step_y, boulder_resting_cell[1] - step_x)
        if not is_inside_grid(open_grid, boulder_resting_cell) or open_grid[boulder_resting_cell] not in WALKABLE:
            return 0
        if boulder_resting_cell in object_cells:
            return 0
        grid_with_resting_boulder = np.array(open_grid, copy=True)
        grid_with_resting_boulder[boulder_resting_cell] = BLOCKED_STAND_IN_CLASS
        reached_mask = reached_cells_mask(grid_with_resting_boulder, [player_cell])
        if all(reached_mask[cell] for cell in other_exits_reached):
            return pushes
    return 0


def apply_gate_draft(route: Route, draft: GateDraft) -> None:
    for cell, new_class in draft.new_class_by_cell.items():
        route.cls[cell] = new_class
    route.events[:] = events_with_draft(route.events, draft)
    route.props[:] = [prop for prop in route.props
                      if (int(prop.get("y", -1)), int(prop.get("x", -1))) not in draft.new_class_by_cell]


def gate_record_cells(draft: GateDraft, gate_kind: str) -> List[List[int]]:
    obstacle_cells = [draft.layer.gap_cell, draft.layer.cell_after_gap]
    push_cells = list(straight_push_lane_cells_used(draft)) if gate_kind == "strength" else []
    record_cells = [list(cell) for cell in dict.fromkeys(obstacle_cells + push_cells + list(draft.new_class_by_cell))]
    return record_cells


def straight_push_lane_cells_used(draft: GateDraft) -> Iterator[Cell]:
    step_y, step_x = push_direction(draft.layer)
    for pushes in range(1, draft.strength_pushes_to_clear_the_gap + 1):
        yield (draft.layer.gap_cell[0] + step_y * pushes, draft.layer.gap_cell[1] + step_x * pushes)


def gate_note(gate_kind: str, gate_request: dict, draft: GateDraft, surroundings: GateSurroundings) -> str:
    where = f"{draft.layer.distance_from_entrance_in_cells} steps in from the {surroundings.entrance_side} exit (from {surroundings.toward})"
    gap_material = "trees" if TREE_CLASS in draft.new_class_by_cell.values() else "rock"
    if gate_kind == "cut":
        return f"a Cut tree in a 1-wide gap of {gap_material}, {where}"
    if gate_kind == "rock_smash":
        return f"a smashable rock in a 1-wide gap of {gap_material}, {where}"
    if gate_kind == "strength":
        return (f"a Strength boulder in a 1-wide gap of {gap_material}, {where}; "
                f"{draft.strength_pushes_to_clear_the_gap} push(es) clears it")
    if gate_kind == "surf":
        return f"a water band across the path you have to Surf, {where}"
    if gate_kind == "waterfall":
        return f"a waterfall step across the path (Surf + Waterfall), {where}"
    story_note = f"someone stands in a 1-wide gap of {gap_material} until {story_beat_named_by_gate(gate_request)}, {where}"
    return story_note


def built_gate_record(gate_kind: str, gate_request: dict, draft: GateDraft, surroundings: GateSurroundings) -> dict:
    gate_record = {"kind": gate_kind, "built": True, "entrance": surroundings.spec_entrance_index,
                   "toward": surroundings.toward, "side": surroundings.entrance_side,
                   "distance_from_entrance": draft.layer.distance_from_entrance_in_cells,
                   "cell": list(draft.layer.gap_cell), "cells": gate_record_cells(draft, gate_kind),
                   "note": gate_note(gate_kind, gate_request, draft, surroundings)}
    if gate_kind == STORY_GATE_KIND:
        gate_record["after_beat"] = story_beat_named_by_gate(gate_request)
    if gate_kind == "strength":
        gate_record["pushes"] = draft.strength_pushes_to_clear_the_gap
    return gate_record


def class_grid_with_route_gates_open(route: Route) -> np.ndarray:
    from .surf_crossing import route_lies_on_open_sea

    has_built_gate = any(gate_record.get("built") for gate_record in route.gates)
    if not has_built_gate and not route_lies_on_open_sea(route):
        return route.cls
    class_grid_with_gates_open = class_grid_with_gate_kinds(route.cls, route.events, EVERY_GATE_KIND)
    return class_grid_with_gates_open
