"""Play-test window data (webui/static/playtest.js).

    GET /api/playtest/route?id=<generated route id>
        {"collision": ["0110…", …], "texts": [{"x", "y", "kind", "text"}, …]}
        collision rows from the tiled layout, sign and person text as the Emerald export writes it
    GET /api/playtest/sheet.png?pose=walking|running|surfing|surf_blob|tall_grass|jump_shadow[&tiler=ark|learned]
        the whole sprite sheet for that pose, read from a ROM tree on this machine (404 when none has it)
"""
import io
from pathlib import Path

from PIL import Image

import route_items

PLAYER_SHEET_FILES_BY_POSE = {
    "walking": ["people/brendan/walking.png"],
    "running": ["people/brendan/running.png"],
    "surfing": ["people/brendan/surfing.png"],
}
FIELD_EFFECT_FILES_BY_POSE = {
    "surf_blob": ["surf_blob.png"],
    "tall_grass": ["tall_grass.png"],
    "jump_shadow": ["shadow_medium.png", "shadow_small.png"],
}
OBJECT_PICTURES_DIRECTORY = Path("graphics/object_events/pics")
FIELD_EFFECT_PICTURES_DIRECTORY = Path("graphics/field_effects/pics")
TEXT_EVENT_KINDS = ("sign", "npc", "trainer", "story_blocker")

cached_sheet_png_by_pose_and_tiler: dict = {}


def candidate_sheet_paths_for_pose(pose: str) -> list:
    object_sheet_paths = [OBJECT_PICTURES_DIRECTORY / name for name in PLAYER_SHEET_FILES_BY_POSE.get(pose, [])]
    field_effect_sheet_paths = [FIELD_EFFECT_PICTURES_DIRECTORY / name for name in FIELD_EFFECT_FILES_BY_POSE.get(pose, [])]
    candidate_paths = object_sheet_paths + field_effect_sheet_paths
    return candidate_paths


def find_sheet_file_for_pose(pose: str, tiler: str):
    for rom_root in route_items.graphics_roots(tiler):
        for relative_path in candidate_sheet_paths_for_pose(pose):
            if (rom_root / relative_path).is_file():
                return rom_root / relative_path
    return None


def sheet_png_with_transparent_background(sheet_path: Path) -> bytes:
    sheet_image = Image.open(sheet_path)
    if sheet_image.mode == "P":
        sheet_image.info["transparency"] = 0
    png_buffer = io.BytesIO()
    sheet_image.convert("RGBA").save(png_buffer, "PNG")
    sheet_png_bytes = png_buffer.getvalue()
    return sheet_png_bytes


def sheet_png_for_pose(pose: str, tiler: str):
    cache_key = (pose, tiler)
    if cache_key in cached_sheet_png_by_pose_and_tiler:
        return cached_sheet_png_by_pose_and_tiler[cache_key]
    sheet_path = find_sheet_file_for_pose(pose, tiler)
    if sheet_path is None:
        return None
    cached_sheet_png_by_pose_and_tiler[cache_key] = sheet_png_with_transparent_background(sheet_path)
    return cached_sheet_png_by_pose_and_tiler[cache_key]


def collision_rows_of_layout(layout) -> list:
    collision_rows = ["".join("1" if blocked else "0" for blocked in collision_row) for collision_row in layout.collision]
    return collision_rows


def text_of_route_event(route, export_module, route_event, sign_number: int) -> str:
    if route_event.kind == "sign":
        return export_module._sign_text(route, route_event, sign_number)
    if route_event.kind == "story_blocker":
        return export_module.STORY_GATE_BLOCKER_TEXT
    return export_module._npc_text(route, route_event)


def event_texts_of_route(route, export_module) -> list:
    event_texts = []
    sign_number = 0
    for route_event in route.events:
        if route_event.kind not in TEXT_EVENT_KINDS:
            continue
        event_text = text_of_route_event(route, export_module, route_event, sign_number)
        sign_number += route_event.kind == "sign"
        event_texts.append({"x": int(route_event.x), "y": int(route_event.y), "kind": route_event.kind, "text": event_text})
    return event_texts


def playtest_route_data(server_module, generated_route_id: str):
    if generated_route_id not in server_module._results:
        return None
    route, layout = server_module._results[generated_route_id]
    playtest_data = {
        "collision": collision_rows_of_layout(layout),
        "texts": event_texts_of_route(route, server_module.G().export),
    }
    return playtest_data


def handle(request_handler, method: str, parsed_url, query: dict, server_module) -> bool:
    if method != "GET" or not parsed_url.path.startswith("/api/playtest/"):
        return False
    if parsed_url.path == "/api/playtest/route":
        playtest_data = playtest_route_data(server_module, query.get("id", [""])[0])
        if playtest_data is None:
            return request_handler._json({"error": "unknown or expired route id; generate again"}, 404) or True
        return request_handler._json(playtest_data) or True
    if parsed_url.path == "/api/playtest/sheet.png":
        sheet_png = sheet_png_for_pose(query.get("pose", [""])[0], route_items.query_tiler(query))
        if sheet_png is None:
            return request_handler._json({"error": "no sheet for that pose in any ROM tree on this machine"}, 404) or True
        return request_handler._send(200, sheet_png, "image/png") or True
    return request_handler._json({"error": f"unknown play-test endpoint {parsed_url.path}"}, 404) or True
