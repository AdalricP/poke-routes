#!/usr/bin/env python3
"""Route tools for the headless agent runs (Agent tab / Improve box): a tiny stdio MCP
server, stdlib only, so the agent can tool-call the generator instead of scripting it.

    python3 webui/route_tools.py       (improve.py passes it to `claude` via --mcp-config)

Everything runs in-process on the code of the checkout this file sits in (the job's
worktree), and pokeroutes is re-imported on every generate, so generator edits show up
without restarting anything. Output goes under out/agent/ (gitignored). No network.

Tools: list_specs, spec_fields, generate_route, route_stats, crop_route_image, route_grid,
paint_route, save_spec, show_in_editor, generate_world, generate_region, tilers,
check_examples, check_web_ui, screenshot_web_ui. Generated routes are kept in memory by id.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import io
import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "agent"
EXAMPLES = ("bernsteinweg", "erlenbruch", "kiefernheide")
_routes: dict = {}   # id -> (spec, route, tiler)
_wire = sys.stdout   # MCP messages only; anything the generator prints goes to stderr
sys.stdout = sys.stderr
sys.path.insert(0, str(ROOT))


def _fresh():
    """Re-import pokeroutes so edits made during the job are used."""
    for k in [k for k in sys.modules if k == "pokeroutes" or k.startswith("pokeroutes.")]:
        del sys.modules[k]
    from pokeroutes import cli
    return cli


def _inside(p: str, base: Path = ROOT) -> Path:
    q = (ROOT / p).resolve()
    if base.resolve() not in (q, *q.parents):
        raise ValueError(f"{p} is outside {base}")
    return q


def _write(route_id: str, spec, route, tiler: str) -> dict:
    cli = _fresh()
    out = OUT / route_id
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        cli._generate_and_write(spec, argparse.Namespace(out=str(out), stages=True, tileset=tiler), route=route)
    rep = json.loads((out / "report.json").read_text())
    return {"id": route_id, "summary": log.getvalue().strip().splitlines()[0] if log.getvalue().strip() else "",
            "size": [route.w, route.h], "report": rep,
            "images": {n: str(out / f) for n, f in (("tiled", "emerald.png"), ("plan", "plan.png"), ("stages", "stages.png"))
                       if (out / f).exists()},
            "emerald_dir": str(out / "emerald")}


# ------------------------------------------------------------------ tools

def list_specs(a):
    specs = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "examples").glob("*.yaml"))
    specs += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "region_out").glob("*/routes/*.yaml"))
    return {"specs": specs, "generated": sorted(_routes)}


def spec_fields(a):
    _fresh()
    from pokeroutes import places, spec
    from pokeroutes.classify import CLASS_NAMES

    def dflt(f):
        if f.default is not dataclasses.MISSING:
            return f.default
        if f.default_factory is not dataclasses.MISSING:
            return f.default_factory()
        return None
    return {"fields": {f.name: dflt(f) for f in dataclasses.fields(spec.RouteSpec)},
            "exit_fields": {f.name: dflt(f) for f in dataclasses.fields(spec.Exit)},
            "biomes": list(spec.BIOMES), "archetypes": sorted(getattr(places, "ARCHETYPES", {})),
            "classes": CLASS_NAMES}


def build_route_spec_from_object(spec_object: dict):
    from pokeroutes.spec import Exit, RouteSpec

    if not isinstance(spec_object, dict):
        raise ValueError("spec_object must be a JSON object")
    unwrapped_spec_object = unwrap_saved_editor_spec(spec_object)
    route_spec_field_names = {route_spec_field.name for route_spec_field in dataclasses.fields(RouteSpec)}
    exit_field_names = {exit_field.name for exit_field in dataclasses.fields(Exit)}
    route_spec_arguments = {field_name: field_value for field_name, field_value in unwrapped_spec_object.items()
                            if field_name in route_spec_field_names and field_name not in ("exits", "size", "id")}
    route_spec_arguments["name"] = str(unwrapped_spec_object.get("name") or "Agent Route")
    route_spec_arguments["size"] = size_of_spec_object(unwrapped_spec_object)
    route_spec_arguments["exits"] = [Exit(**{exit_key: exit_value for exit_key, exit_value in exit_object.items()
                                             if exit_key in exit_field_names and exit_value not in (None, "")})
                                     for exit_object in unwrapped_spec_object.get("exits") or []]
    route_spec = RouteSpec(**route_spec_arguments)
    route_spec.id = "".join(character for character in route_spec.name.title() if character.isalnum()) or "Route"
    return route_spec


def unwrap_saved_editor_spec(spec_object: dict) -> dict:
    if spec_object.get("format") != "poke-routes-spec":
        return spec_object
    unwrapped_spec_object = dict(spec_object.get("params") or {})
    if spec_object.get("region"):
        unwrapped_spec_object["region"] = spec_object["region"]
    if spec_object.get("endings"):
        unwrapped_spec_object["endings"] = spec_object["endings"]
    return unwrapped_spec_object


def size_of_spec_object(spec_object: dict) -> list:
    if isinstance(spec_object.get("size"), list) and len(spec_object["size"]) == 2:
        size_in_metatiles = [int(spec_object["size"][0]), int(spec_object["size"][1])]
        return size_in_metatiles
    size_in_metatiles = [int(spec_object.get("width") or 48), int(spec_object.get("height") or 80)]
    return size_in_metatiles


def load_route_spec_for_tool_call(tool_arguments: dict):
    from pokeroutes.spec import RouteSpec

    if tool_arguments.get("spec_object"):
        route_spec = build_route_spec_from_object(tool_arguments["spec_object"])
        return route_spec
    route_spec = RouteSpec.load(_inside(tool_arguments.get("spec") or "examples/bernsteinweg.yaml"))
    return route_spec


def apply_overrides_to_route_spec(route_spec, overrides: dict):
    from pokeroutes.spec import Exit, RouteSpec

    known_field_names = {route_spec_field.name for route_spec_field in dataclasses.fields(RouteSpec)}
    for field_name, field_value in (overrides or {}).items():
        if field_name not in known_field_names:
            raise ValueError(f"unknown spec field {field_name!r} (see spec_fields)")
        setattr(route_spec, field_name, [Exit(**exit_object) for exit_object in field_value] if field_name == "exits" else field_value)
    return route_spec


def generate_one_route_from_spec(route_spec, tiler_name: str, overrides: dict) -> dict:
    from pokeroutes.export import seal_edges
    from pokeroutes.generate import generate

    route = generate(route_spec)
    seal_edges(route)
    overrides_hash = hashlib.sha1(json.dumps(overrides or {}, sort_keys=True, default=str).encode()).hexdigest()[:8]
    route_id = f"{route_spec.id}-{route_spec.seed}-{overrides_hash}" + ("-ark" if tiler_name == "ark" else "")
    _routes[route_id] = (route_spec, route, tiler_name)
    generated_route_summary = _write(route_id, route_spec, route, tiler_name)
    return generated_route_summary


def generate_route(a):
    _fresh()
    tiler_name = "ark" if a.get("tiler") == "ark" else "learned"
    requested_seeds = [int(seed) for seed in (a.get("seeds") or [])][:6]
    if not requested_seeds:
        base_spec = load_route_spec_for_tool_call(a)
        requested_seeds = [int(a["seed"]) if a.get("seed") is not None else base_spec.seed]

    generated_routes = []
    for requested_seed in requested_seeds:
        route_spec = apply_overrides_to_route_spec(load_route_spec_for_tool_call(a), a.get("overrides"))
        route_spec.seed = requested_seed
        generated_routes.append(generate_one_route_from_spec(route_spec, tiler_name, a.get("overrides")))
    if len(generated_routes) == 1:
        return generated_routes[0]
    return {"routes": generated_routes}


def _get(a):
    rid = str(a.get("id", ""))
    if rid not in _routes:
        raise ValueError(f"no generated route {rid!r}; known: {sorted(_routes)}")
    return rid, _routes[rid]


def route_grid(a):
    rid, (_spec, route, _t) = _get(a)
    x0, y0 = max(0, int(a.get("x0", 0))), max(0, int(a.get("y0", 0)))
    x1, y1 = min(route.w, int(a.get("x1", route.w))), min(route.h, int(a.get("y1", route.h)))
    rows = ["".join(r[x0:x1]) for r in route.cls[y0:y1]]
    return {"id": rid, "origin": [x0, y0], "rows": rows,
            "zones": [{"kind": z.kind, **{k: str(v) for k, v in getattr(z, "meta", {}).items() if k != "area"}}
                      for z in route.zones],
            "events": [{"kind": e.kind, "x": int(e.x), "y": int(e.y)} for e in route.events]}


def paint_route(a):
    from pokeroutes.classify import LEDGE_CLASSES, defuse_ledge_corners

    rid, (spec, route, tiler) = _get(a)
    h, w = route.cls.shape
    n = 0
    painted_ledges = False
    for c in a.get("cells") or []:
        x, y, k = int(c[0]), int(c[1]), str(c[2])
        if 0 <= x < w and 0 <= y < h and len(k) == 1:
            route.cls[y, x] = k
            painted_ledges = painted_ledges or k in LEDGE_CLASSES
            n += 1
    for r in a.get("rects") or []:
        x0, y0, x1, y1, k = int(r[0]), int(r[1]), int(r[2]), int(r[3]), str(r[4])
        if len(k) == 1:
            route.cls[max(0, y0):min(h, y1 + 1), max(0, x0):min(w, x1 + 1)] = k
            painted_ledges = painted_ledges or k in LEDGE_CLASSES
            n += max(0, min(w, x1 + 1) - max(0, x0)) * max(0, min(h, y1 + 1) - max(0, y0))
    if painted_ledges:
        route.cls = defuse_ledge_corners(route.cls)
    res = _write(rid, spec, route, tiler)
    res["painted"] = n
    return res


def generate_region(a):
    cli = _fresh()
    specs = [str(_inside(s)) for s in a.get("specs") or []]
    if not specs:
        raise ValueError("give specs: a list of spec paths")
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        cli.cmd_region(argparse.Namespace(specs=specs, stages=False, viewer=False,
                                          tileset="ark" if a.get("tiler") == "ark" else "learned",
                                          special_budget=a.get("special_budget")))
    return {"log": log.getvalue().strip().splitlines()}


def check_examples(a):
    res = {}
    for name in EXAMPLES:
        p = subprocess.run([sys.executable, "-m", "pokeroutes", "generate", f"examples/{name}.yaml"],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        res[name] = {"ok": p.returncode == 0,
                     "output": (p.stdout + p.stderr).strip().splitlines()[-4:]}
    return res


def route_stats(a):
    from pokeroutes.classify import CLASS_NAMES, WALKABLE_CLASSES

    route_id, (route_spec, route, _tiler_name) = _get(a)
    class_codes, class_cell_counts = numpy_unique_counts(route.cls)
    total_cell_count = int(route.cls.size)
    class_shares = {CLASS_NAMES.get(class_code, class_code): round(class_cell_count / total_cell_count, 3)
                    for class_code, class_cell_count in sorted(zip(class_codes, class_cell_counts), key=lambda pair: -pair[1])}
    walkable_cell_count = sum(class_cell_count for class_code, class_cell_count in zip(class_codes, class_cell_counts)
                              if class_code in WALKABLE_CLASSES)
    route_statistics = {"id": route_id, "size": [route.w, route.h], "biome": route_spec.biome,
                        "class_shares": class_shares,
                        "walkable_share": round(walkable_cell_count / total_cell_count, 3),
                        "max_walkable_in_spec": route_spec.max_walkable,
                        "zones_by_kind": count_by_kind(route.zones), "events_by_kind": count_by_kind(route.events)}
    return route_statistics


def numpy_unique_counts(class_grid):
    import numpy

    class_codes, class_cell_counts = numpy.unique(class_grid, return_counts=True)
    class_codes_and_counts = ([str(class_code) for class_code in class_codes], [int(class_cell_count) for class_cell_count in class_cell_counts])
    return class_codes_and_counts


def count_by_kind(zones_or_events) -> dict:
    counts_by_kind: dict = {}
    for zone_or_event in zones_or_events:
        counts_by_kind[zone_or_event.kind] = counts_by_kind.get(zone_or_event.kind, 0) + 1
    return counts_by_kind


pixels_per_metatile = 16


def crop_route_image(a):
    from PIL import Image

    route_id, (_route_spec, route, _tiler_name) = _get(a)
    tiled_image_path = OUT / route_id / "emerald.png"
    if not tiled_image_path.exists():
        raise ValueError(f"no tiled image for {route_id}; generate it again")
    left_metatile, top_metatile = max(0, int(a.get("x0", 0))), max(0, int(a.get("y0", 0)))
    right_metatile, bottom_metatile = min(route.w, int(a.get("x1", route.w))), min(route.h, int(a.get("y1", route.h)))
    if right_metatile <= left_metatile or bottom_metatile <= top_metatile:
        raise ValueError("empty crop box; x1 > x0 and y1 > y0 are needed")
    scale_factor = max(1, min(4, int(a.get("scale", 2))))

    tiled_image = Image.open(tiled_image_path)
    cropped_image = tiled_image.crop((left_metatile * pixels_per_metatile, top_metatile * pixels_per_metatile,
                                      right_metatile * pixels_per_metatile, bottom_metatile * pixels_per_metatile))
    scaled_crop = cropped_image.resize((cropped_image.width * scale_factor, cropped_image.height * scale_factor), Image.NEAREST)
    crop_path = OUT / route_id / f"crop-{left_metatile}-{top_metatile}-{right_metatile}-{bottom_metatile}-x{scale_factor}.png"
    scaled_crop.save(crop_path)
    crop_result = {"id": route_id, "image": str(crop_path), "box_in_metatiles": [left_metatile, top_metatile, right_metatile, bottom_metatile]}
    return crop_result


saved_spec_directory_name = "specs"
spec_fields_never_saved = ("id", "tileset", "species_file", "special_budget", "exclude_areas", "extra")


def save_spec(a):
    _fresh()
    from pokeroutes.spec import RouteSpec

    if a.get("from_id"):
        _route_id, (route_spec, _route, _tiler_name) = _get({"id": a["from_id"]})
    else:
        route_spec = build_route_spec_from_object(a.get("spec_object") or {})
    spec_path = path_for_saved_spec(str(a.get("path") or f"{route_spec.id or 'route'}.yaml"))

    spec_yaml_text = render_route_spec_as_yaml(route_spec)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec_yaml_text)
    RouteSpec.load(spec_path)
    saved_spec = {"path": str(spec_path.relative_to(ROOT)), "yaml": spec_yaml_text}
    return saved_spec


def path_for_saved_spec(requested_path: str) -> Path:
    bare_requested_path = requested_path[len(saved_spec_directory_name) + 1:] if requested_path.startswith(saved_spec_directory_name + "/") else requested_path
    if not bare_requested_path.endswith((".yaml", ".yml")):
        bare_requested_path += ".yaml"
    spec_path = _inside(f"{saved_spec_directory_name}/{bare_requested_path}", ROOT / saved_spec_directory_name)
    return spec_path


def render_route_spec_as_yaml(route_spec) -> str:
    import yaml
    from pokeroutes.spec import RouteSpec

    default_spec = RouteSpec(name=route_spec.name, biome=route_spec.biome)
    spec_as_dictionary = dataclasses.asdict(route_spec)
    default_as_dictionary = dataclasses.asdict(default_spec)
    always_saved_fields = ("name", "size", "seed", "biome", "exits")
    saved_fields = {field_name: field_value for field_name, field_value in spec_as_dictionary.items()
                    if field_name not in spec_fields_never_saved
                    and (field_name in always_saved_fields or field_value != default_as_dictionary.get(field_name))}
    spec_yaml_text = yaml.safe_dump(saved_fields, sort_keys=False, allow_unicode=True, width=120)
    return spec_yaml_text


def show_in_editor(a):
    import os
    from pokeroutes.spec import RouteSpec

    _fresh()
    editor_specs_file = os.environ.get("POKEROUTES_EDITOR_SPECS_FILE")
    spec_path = _inside(str(a.get("path") or ""))
    if not spec_path.is_file() or spec_path.suffix not in (".yaml", ".yml", ".json"):
        raise ValueError(f"{a.get('path')!r} is not a spec file in the repo (save one with save_spec first)")
    RouteSpec.load(spec_path) if spec_path.suffix != ".json" else json.loads(spec_path.read_text())
    if not editor_specs_file:
        return {"shown": False, "why": "not running inside an Agent job, so there is no Agent tab to show it in"}

    offered_spec = {"path": str(spec_path.relative_to(ROOT)), "note": str(a.get("note") or "")[:200], "text": spec_path.read_text()}
    offered_specs = read_offered_specs(Path(editor_specs_file))
    offered_specs = [already_offered for already_offered in offered_specs if already_offered.get("path") != offered_spec["path"]] + [offered_spec]
    Path(editor_specs_file).parent.mkdir(parents=True, exist_ok=True)
    Path(editor_specs_file).write_text(json.dumps(offered_specs, indent=1))
    shown_result = {"shown": True, "path": offered_spec["path"], "offered_so_far": [already_offered["path"] for already_offered in offered_specs]}
    return shown_result


def read_offered_specs(editor_specs_file: Path) -> list:
    if not editor_specs_file.is_file():
        return []
    try:
        offered_specs = json.loads(editor_specs_file.read_text())
    except ValueError:
        return []
    return offered_specs if isinstance(offered_specs, list) else []


def generate_world(a):
    _fresh()
    from pokeroutes.region.cli import generate

    world_seed = int(a.get("seed", 1))
    world_output_directory = OUT / "world" / str(world_seed)
    log_capture = io.StringIO()
    with contextlib.redirect_stdout(log_capture):
        world, _written_directory, _written_paths = generate(world_seed, out=world_output_directory, strip=True, verbose=True)
    route_spec_paths = sorted(str(spec_path.relative_to(ROOT)) for spec_path in world_output_directory.glob("routes/*.yaml"))
    world_result = {"seed": world_seed, "map_image": str(world_output_directory / "region.png"),
                    "stages_image": str(world_output_directory / "stages.png"),
                    "towns": [getattr(city, "name", str(city)) for city in world.cities],
                    "route_specs": route_spec_paths, "log": log_capture.getvalue().strip().splitlines()[-12:]}
    return world_result


def tilers(a):
    _fresh()
    from pokeroutes import spec
    from pokeroutes.ruletile import find_rom

    models_index = json.loads((ROOT / "pokeroutes" / "models" / "index.json").read_text())
    try:
        ark_tileset_root = str(find_rom())
        ark_problem = ""
    except FileNotFoundError as missing_ark_tileset:
        ark_tileset_root, ark_problem = None, str(missing_ark_tileset)
    tiler_overview = {"learned": {"available": (ROOT / "vendor" / "pokeemerald").is_dir(),
                                  "needs": "vendor/pokeemerald (a pret/pokeemerald checkout, see README Quickstart)",
                                  "tileset_per_biome": dict(spec.BIOME_TILESET),
                                  "models": {name: {"model": entry.get("model"), "note": entry.get("note", "")}
                                             for name, entry in models_index.items()}},
                      "ark": {"available": bool(ark_tileset_root), "root": ark_tileset_root, "problem": ark_problem}}
    return tiler_overview


def job_server_port() -> int:
    import os

    port_number = int(os.environ.get("POKEROUTES_JOB_SERVER_PORT") or 8800)
    return port_number


def start_job_server_copy(port_number: int):
    import os
    import tempfile
    import time
    import urllib.request

    server_environment = dict(os.environ, IMPROVE_WORKERS="0", PORT=str(port_number))
    server_log_file = tempfile.TemporaryFile(mode="w+")
    server_process = subprocess.Popen([sys.executable, str(ROOT / "webui" / "server.py")], cwd=ROOT, env=server_environment,
                                      stdout=server_log_file, stderr=subprocess.STDOUT, start_new_session=True)
    server_process.server_log_file = server_log_file
    give_up_at = time.time() + 30
    while time.time() < give_up_at:
        if server_process.poll() is not None:
            raise RuntimeError(f"the server copy on port {port_number} exited at start: {stop_job_server_copy(server_process)[-1500:]}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port_number}/api/tilers", timeout=2).read()
            return server_process
        except OSError:
            time.sleep(0.4)
    server_output = stop_job_server_copy(server_process)
    raise RuntimeError(f"the server copy on port {port_number} did not answer within 30 s: {server_output[-1500:]}")


def stop_job_server_copy(server_process) -> str:
    import os
    import signal

    for stop_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(server_process.pid, stop_signal)
        except ProcessLookupError:
            break
        try:
            server_process.wait(timeout=8)
            break
        except subprocess.TimeoutExpired:
            continue
    server_process.server_log_file.seek(0)
    server_output = server_process.server_log_file.read()
    server_process.server_log_file.close()
    return server_output


def fetch_from_job_server(port_number: int, path: str, json_body=None) -> dict:
    import urllib.error
    import urllib.request

    request_body = None if json_body is None else json.dumps(json_body).encode()
    http_request = urllib.request.Request(f"http://127.0.0.1:{port_number}{path}", data=request_body,
                                          headers={"Content-Type": "application/json"} if request_body else {})
    try:
        with urllib.request.urlopen(http_request, timeout=120) as http_response:
            response_body = http_response.read()
            response_status = http_response.status
    except urllib.error.HTTPError as http_error:
        response_body, response_status = http_error.read(), http_error.code
    except OSError as connection_error:
        return {"path": path, "ok": False, "error": str(connection_error)}
    response_text = response_body.decode(errors="replace")
    has_error_field = response_text.lstrip().startswith("{") and '"error"' in response_text[:400]
    fetched = {"path": path, "status": response_status, "ok": response_status < 400 and not has_error_field,
               "bytes": len(response_body), "start": response_text[:300] if (response_status >= 400 or has_error_field) else ""}
    return fetched


def check_web_ui(a):
    port_number = job_server_port()
    page_paths = [str(page_path) for page_path in (a.get("paths") or ["/"])][:12]

    server_process = start_job_server_copy(port_number)
    try:
        checks = [fetch_from_job_server(port_number, "/api/spec-schema"),
                  fetch_from_job_server(port_number, "/api/generate", {"seed": 7}),
                  *[fetch_from_job_server(port_number, page_path) for page_path in page_paths]]
    finally:
        server_output = stop_job_server_copy(server_process)
    server_errors = [output_line for output_line in server_output.splitlines() if "Traceback" in output_line or "Error" in output_line][-8:]
    web_ui_check = {"port": port_number, "all_ok": all(check["ok"] for check in checks) and not server_errors,
                    "checks": checks, "server_errors": server_errors}
    return web_ui_check


browser_candidate_paths = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
browser_candidate_commands = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")


def find_headless_browser():
    import os
    import shutil

    configured_browser = os.environ.get("POKEROUTES_BROWSER")
    if configured_browser:
        return configured_browser
    installed_browser_path = next((candidate_path for candidate_path in browser_candidate_paths if Path(candidate_path).exists()), None)
    if installed_browser_path:
        return installed_browser_path
    browser_on_path = next((shutil.which(candidate_command) for candidate_command in browser_candidate_commands if shutil.which(candidate_command)), None)
    return browser_on_path


def screenshot_web_ui(a):
    import tempfile
    import time

    browser_path = find_headless_browser()
    if not browser_path:
        raise ValueError("no Chrome, Chromium, Brave or Edge found; set POKEROUTES_BROWSER to one, or use check_web_ui")
    page_path = str(a.get("path") or "/")
    width_in_pixels = max(320, min(2560, int(a.get("width", 1400))))
    height_in_pixels = max(240, min(1600, int(a.get("height", 900))))
    wait_in_milliseconds = max(1000, min(30000, int(a.get("wait_ms", 8000))))
    port_number = job_server_port()
    screenshot_path = OUT / "screens" / f"ui-{int(time.time())}-{hashlib.sha1(page_path.encode()).hexdigest()[:6]}.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)

    server_process = start_job_server_copy(port_number)
    try:
        with tempfile.TemporaryDirectory(prefix="poke-routes-browser-") as browser_profile_directory:
            browser_run = subprocess.run([browser_path, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                          "--no-first-run", "--no-default-browser-check", f"--user-data-dir={browser_profile_directory}",
                                          f"--window-size={width_in_pixels},{height_in_pixels}",
                                          f"--virtual-time-budget={wait_in_milliseconds}", f"--screenshot={screenshot_path}",
                                          f"http://127.0.0.1:{port_number}{page_path}"],
                                         capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as browser_timeout:
        raise RuntimeError(f"{Path(browser_path).name} did not finish a headless screenshot in 45 s (some sandboxes block "
                           "headless browsers); fall back to check_web_ui and generate_route images") from browser_timeout
    finally:
        stop_job_server_copy(server_process)
    if not screenshot_path.exists():
        raise RuntimeError(f"the browser made no screenshot: {(browser_run.stderr or browser_run.stdout)[-600:]}")
    screenshot_result = {"image": str(screenshot_path), "url_path": page_path, "size": [width_in_pixels, height_in_pixels]}
    return screenshot_result


S, I, N, O = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "object"}
TOOLS = {
    "list_specs": (list_specs, "List route spec files (examples/, region_out/) and routes generated in this session.", {}),
    "spec_fields": (spec_fields, "Every RouteSpec field with its default, exit fields, biomes, special-area "
                                 "archetypes and the class codes used in the route grid.", {}),
    "generate_route": (generate_route, "Generate a route, like `python3 -m pokeroutes generate`. Give a spec file (spec) or "
                       "a spec object (spec_object: the editor's params from the editor context, a saved editor spec, or "
                       "RouteSpec fields), optional overrides, and a seed or up to 6 seeds to compare. Writes the tiled render "
                       "(tiled), the class plan and a stage sheet as PNGs plus the Emerald files under out/agent/<id>/ and returns "
                       "their paths, the route report and an id per route for route_stats / crop_route_image / route_grid / "
                       "paint_route / save_spec. Read the PNGs to look at the result.",
                       {"spec": {**S, "description": "spec path relative to the repo, default examples/bernsteinweg.yaml"},
                        "spec_object": {**O, "description": "a spec as JSON instead of a file"},
                        "seed": I, "seeds": {"type": "array", "items": I, "description": "several seeds, one route each"},
                        "overrides": {**O, "description": "spec fields to change, e.g. {\"meander\": 0.8, \"lakes\": 0}"},
                        "tiler": {**S, "enum": ["learned", "ark"]}}),
    "route_stats": (route_stats, "Numbers for a generated route: share of each terrain class, walkable share against the "
                    "spec's max_walkable, zones and events by kind. Use it to compare seeds or check a tweak did something.",
                    {"id": S}),
    "crop_route_image": (crop_route_image, "Zoom into part of a generated route's tiled render: a box in metatiles "
                         "(x1, y1 exclusive), scaled 1-4x, saved as a PNG to Read. For checking tile seams and details.",
                         {"id": S, "x0": I, "y0": I, "x1": I, "y1": I, "scale": I}),
    "route_grid": (route_grid, "The class grid (one char per metatile, see spec_fields.classes) of a generated route, "
                   "optionally just the box x0..x1, y0..y1 (exclusive), with its zones and events.",
                   {"id": S, "x0": I, "y0": I, "x1": I, "y1": I}),
    "paint_route": (paint_route, "Hand-fix a generated route: set classes on cells ([x, y, cls]) and/or filled "
                    "rectangles ([x0, y0, x1, y1, cls], inclusive), then re-tile and re-export it (same outputs as "
                    "generate_route). Lasts for this session only; to keep a fix, change the spec or the generator.",
                    {"id": S, "cells": {"type": "array", "items": {"type": "array"}},
                     "rects": {"type": "array", "items": {"type": "array"}}}),
    "generate_region": (generate_region, "Generate several specs as one region, like `python3 -m pokeroutes region` "
                        "(no repeated special areas, exits lined up). Output goes to out/<route id>/.",
                        {"specs": {"type": "array", "items": S}, "tiler": {**S, "enum": ["learned", "ark"]},
                         "special_budget": I}),
    "save_spec": (save_spec, "Save a route spec as YAML under specs/ (validated by loading it back). Give from_id (a "
                  "route you generated, with its seed and overrides) or spec_object. Only non-default fields are written.",
                  {"path": {**S, "description": "file name under specs/, e.g. misty-lake.yaml"}, "from_id": S, "spec_object": O}),
    "show_in_editor": (show_in_editor, "Offer a spec file to the user: their Agent tab gets an \"Open in editor\" button that "
                       "loads it into the editor and generates it. Call once per spec they should look at.",
                       {"path": {**S, "description": "spec path in the repo, e.g. specs/misty-lake.yaml"},
                        "note": {**S, "description": "one line shown next to the button"}}),
    "generate_world": (generate_world, "Generate a whole region from a seed, like `python3 -m pokeroutes.region generate`: "
                       "terrain, climate, legendaries, towns and the route network. Returns the region map PNG, the stage strip "
                       "and one spec file per route, which generate_route can take.", {"seed": I}),
    "tilers": (tilers, "Which tilers work in this checkout (learned needs vendor/pokeemerald; ark needs ARK_ROM), the "
               "tileset each biome uses and the learned models available.", {}),
    "check_web_ui": (check_web_ui, "Start this job's own copy of the web UI server, check /api/spec-schema, a generate "
                     "and the given pages (default /), report failures and server tracebacks, and stop it again.",
                     {"paths": {"type": "array", "items": S, "description": "page or API paths, e.g. [\"/\", \"/editor.html\"]"}}),
    "screenshot_web_ui": (screenshot_web_ui, "Screenshot a page of this job's own server copy with a headless browser "
                          "(Chrome, Chromium, Brave or Edge) and return the PNG path to Read. The editor generates its "
                          "default route on load, so wait_ms 8000 shows a finished scene.",
                          {"path": {**S, "description": "page path, default /"}, "width": I, "height": I, "wait_ms": I}),
    "check_examples": (check_examples, "Run the three example specs through the CLI (the standard check before "
                       "committing) and report which pass.", {}),
}


# ------------------------------------------------------------------ MCP over stdio

def _send(msg):
    _wire.write(json.dumps(msg, default=str) + "\n")
    _wire.flush()


def handle(req):
    m, rid = req.get("method"), req.get("id")
    if rid is None:
        return None   # notifications (initialized, cancelled) need no answer
    if m == "initialize":
        return {"protocolVersion": (req.get("params") or {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "poke-routes", "version": "0.1"}}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d,
                           "inputSchema": {"type": "object", "properties": p}} for n, (_f, d, p) in TOOLS.items()]}
    if m == "tools/call":
        p = req.get("params") or {}
        if p.get("name") not in TOOLS:
            return {"content": [{"type": "text", "text": f"unknown tool {p.get('name')}"}], "isError": True}
        try:
            out = TOOLS[p["name"]][0](p.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}]}
        except Exception as e:
            traceback.print_exc()
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if m == "ping":
        return {}
    raise KeyError(m)


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        try:
            res = handle(req)
        except KeyError as e:
            _send({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": f"no method {e}"}})
            continue
        if res is not None:
            _send({"jsonrpc": "2.0", "id": req["id"], "result": res})


if __name__ == "__main__":
    main()
