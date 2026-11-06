from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Sequence, Set

SEA_ROUTE_FIELD_MOVE = "surf"


def gate_block_for_route_spec(world, gated_route: dict, exits_in_spec_order: Sequence[dict]) -> dict:
    story_beats = list(world.overrides.get("story") or [])
    start_place_name = story_start_place_name(story_beats, world.cities)
    gate_block = dict(gated_route["gate"])

    beat_name = story_beat_name(story_beats, gate_block.get("after"))
    if beat_name:
        gate_block["after_beat"] = beat_name
    gated_entrance = choose_gated_entrance(world.routes, story_beats, start_place_name, gated_route, exits_in_spec_order)
    gate_block.update(gated_entrance)

    return gate_block


def story_start_place_name(story_beats: Sequence[dict], cities: Sequence) -> str:
    first_beat_place_name = (story_beats[0].get("at") or "") if story_beats else ""
    if first_beat_place_name:
        return first_beat_place_name
    first_city_name = cities[0].name if cities else ""
    return first_city_name


def story_beat_name(story_beats: Sequence[dict], beat_index: Optional[int]) -> str:
    if beat_index is None or not (0 <= int(beat_index) < len(story_beats)):
        return ""
    beat = story_beats[int(beat_index)]
    beat_name = beat.get("name") or f"story beat {int(beat_index) + 1}"
    return beat_name


def choose_gated_entrance(routes: Sequence[dict], story_beats: Sequence[dict], start_place_name: str,
                          gated_route: dict, exits_in_spec_order: Sequence[dict]) -> dict:
    if not exits_in_spec_order:
        return {"entrance": 0, "entrance_rule": "the route has no exits; the first one is gated"}
    neighbour_names = [exit_record.get("to") or "" for exit_record in exits_in_spec_order]
    hops_from_start = hop_counts_from_place(routes, start_place_name, gated_route["name"])

    entrance_from_story = entrance_reached_first_through_story(
        routes, story_beats, start_place_name, gated_route["name"], neighbour_names, hops_from_start)
    if entrance_from_story:
        return entrance_from_story
    entrance_from_roads = entrance_closest_by_road(neighbour_names, hops_from_start, start_place_name)
    if entrance_from_roads:
        return entrance_from_roads

    unreachable_entrance = {"entrance": 0, "entrance_toward": neighbour_names[0],
                            "entrance_rule": f"no exit is reachable from {start_place_name or 'the start'}; the first one is gated"}
    return unreachable_entrance


def entrance_reached_first_through_story(routes, story_beats, start_place_name, gated_route_name,
                                         neighbour_names, hops_from_start) -> Optional[dict]:
    for story_step in range(-1, len(story_beats)):
        reachable_place_names = places_reachable_at_story_step(routes, story_beats, start_place_name, story_step, gated_route_name)
        candidate_exit_indices = [exit_index for exit_index, name in enumerate(neighbour_names) if name in reachable_place_names]
        if not candidate_exit_indices:
            continue
        chosen_exit_index = min(candidate_exit_indices, key=lambda exit_index: hops_from_start.get(neighbour_names[exit_index], 10 ** 6))
        step_label = "at a new game" if story_step < 0 else f"after beat {story_step + 1}"
        entrance = {"entrance": chosen_exit_index, "entrance_toward": neighbour_names[chosen_exit_index],
                    "entrance_rule": f"faces {neighbour_names[chosen_exit_index]}, the side you can walk up to first ({step_label})"}
        return entrance
    return None


def entrance_closest_by_road(neighbour_names, hops_from_start, start_place_name) -> Optional[dict]:
    connected_exit_indices = [exit_index for exit_index, name in enumerate(neighbour_names) if name in hops_from_start]
    if not connected_exit_indices:
        return None
    chosen_exit_index = min(connected_exit_indices, key=lambda exit_index: hops_from_start[neighbour_names[exit_index]])
    entrance = {"entrance": chosen_exit_index, "entrance_toward": neighbour_names[chosen_exit_index],
                "entrance_rule": f"faces {neighbour_names[chosen_exit_index]}, the side closest to {start_place_name} by road"}
    return entrance


def place_adjacency(routes: Sequence[dict], skipped_route_name: str = "") -> Dict[str, List[str]]:
    adjacency: Dict[str, List[str]] = {}
    for route in routes:
        if route["name"] == skipped_route_name:
            continue
        for exit_record in route.get("exits") or []:
            neighbour_name = exit_record.get("to")
            if not neighbour_name or neighbour_name == skipped_route_name:
                continue
            adjacency.setdefault(route["name"], []).append(neighbour_name)
            adjacency.setdefault(neighbour_name, []).append(route["name"])
    return adjacency


def hop_counts_from_place(routes: Sequence[dict], start_place_name: str, skipped_route_name: str) -> Dict[str, int]:
    adjacency = place_adjacency(routes, skipped_route_name)
    hops_by_place_name = {start_place_name: 0} if start_place_name else {}
    queue = deque(hops_by_place_name)
    while queue:
        place_name = queue.popleft()
        for neighbour_name in adjacency.get(place_name, []):
            if neighbour_name in hops_by_place_name:
                continue
            hops_by_place_name[neighbour_name] = hops_by_place_name[place_name] + 1
            queue.append(neighbour_name)
    return hops_by_place_name


def route_field_move_needed(route: dict) -> str:
    gate = route.get("gate") or {}
    if gate.get("needs") is not None:
        return gate["needs"]
    derived_field_move = SEA_ROUTE_FIELD_MOVE if route.get("mode") == "sea" else ""
    return derived_field_move


def is_route_open_at_story_step(route: dict, field_moves_held: Set[str], story_step: int) -> bool:
    field_move_needed = route_field_move_needed(route)
    opens_after_beat_index = (route.get("gate") or {}).get("after")
    has_field_move = not field_move_needed or field_move_needed in field_moves_held
    story_allows = opens_after_beat_index is None or opens_after_beat_index <= story_step
    route_is_open = has_field_move and story_allows
    return route_is_open


def places_reachable_at_story_step(routes, story_beats, start_place_name, story_step, closed_route_name) -> Set[str]:
    field_moves_held = {beat.get("unlocks") for beat in story_beats[: story_step + 1] if beat.get("unlocks")}
    closed_place_names = {route["name"] for route in routes
                          if route["name"] == closed_route_name or not is_route_open_at_story_step(route, field_moves_held, story_step)}
    if not start_place_name or start_place_name in closed_place_names:
        return set()
    adjacency = place_adjacency(routes)
    reachable_place_names = {start_place_name}
    stack = [start_place_name]
    while stack:
        for neighbour_name in adjacency.get(stack.pop(), []):
            if neighbour_name in reachable_place_names or neighbour_name in closed_place_names:
                continue
            reachable_place_names.add(neighbour_name)
            stack.append(neighbour_name)
    return reachable_place_names
