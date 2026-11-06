from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIRECTORY.parent))

import route_tools  # noqa: E402
import server  # noqa: E402
from pokeroutes.breeze import SWAY_OFFSET_IN_PIXELS_FOR_EACH_ANIMATION_TICK, breeze_plan_for_learned_model  # noqa: E402
from pokeroutes.route_effects import effect_summary_for_route, emerald_weather_for_route  # noqa: E402
from pokeroutes.spec import RouteSpec  # noqa: E402

LEARNED_TILESET_WITH_TREES = "general_fortree"


def write_spec_file(spec_directory: Path, spec_text: str) -> Path:
    spec_path = spec_directory / "route.yaml"
    spec_path.write_text(spec_text)
    return spec_path


def test_effect_defaults_to_none():
    route_spec = RouteSpec(name="Testweg")
    assert route_spec.effect == "none"


def test_effect_survives_a_load_and_save_round_trip(tmp_path):
    loaded_spec = RouteSpec.load(write_spec_file(tmp_path, "name: Schneeweg\nbiome: forest\neffect: snow\n"))
    saved_spec_text = route_tools.render_route_spec_as_yaml(loaded_spec)
    reloaded_spec = RouteSpec.load(write_spec_file(tmp_path, saved_spec_text))
    assert loaded_spec.effect == "snow"
    assert "effect: snow" in saved_spec_text
    assert reloaded_spec.effect == "snow"


def test_unknown_effect_is_refused(tmp_path):
    with pytest.raises(ValueError, match="effect"):
        RouteSpec.load(write_spec_file(tmp_path, "name: Testweg\neffect: fireworks\n"))


def test_web_ui_schema_offers_the_effect_presets():
    effect_field = next(schema_field for schema_field in server.spec_schema()["fields"] if schema_field["name"] == "effect")
    assert effect_field["choices"] == ["none", "leaves", "snow", "rain", "ash"]
    assert server.spec_from({"effect": "ash"}).effect == "ash"
    assert server.spec_from({"effect": "fireworks"}).effect == "none"


@pytest.mark.parametrize("effect, emerald_weather", [
    ("snow", "WEATHER_SNOW"), ("rain", "WEATHER_RAIN"), ("ash", "WEATHER_VOLCANIC_ASH"),
    ("leaves", "WEATHER_SUNNY"), ("none", "WEATHER_SUNNY"),
])
def test_effect_maps_to_emerald_weather(effect, emerald_weather):
    assert emerald_weather_for_route(RouteSpec(name="Testweg", effect=effect)) == emerald_weather


def test_hand_set_weather_wins_over_the_effect():
    route_spec = RouteSpec(name="Testweg", effect="snow", weather="WEATHER_SHADE")
    assert emerald_weather_for_route(route_spec) == "WEATHER_SHADE"
    assert "WEATHER_SHADE" in effect_summary_for_route(route_spec)["effect_note"]


def test_falling_leaves_explain_why_the_weather_stays():
    assert "no leaf weather" in effect_summary_for_route(RouteSpec(name="Testweg", effect="leaves"))["effect_note"]


def learned_atlas_or_skip():
    if not (server.EMERALD / "data/tilesets").is_dir():
        pytest.skip("vendor/pokeemerald is not checked out")
    learned_atlas = server.build_atlas(server.pair(LEARNED_TILESET_WITH_TREES), "a", "b", "x.png",
                                       server.breeze_plan_for_learned_tileset(LEARNED_TILESET_WITH_TREES))
    return learned_atlas


def test_learned_atlas_has_breeze_frames_for_trees_and_tall_grass():
    _atlas_png, atlas_meta = learned_atlas_or_skip()
    learned_model = json.loads((server.MODELS / f"{LEARNED_TILESET_WITH_TREES}.json").read_text())
    kind_by_metatile = breeze_plan_for_learned_model(learned_model).kind_by_metatile
    swaying_kinds = {kind_by_metatile[metatile_id] for metatile_id in atlas_meta["breeze"]}
    assert swaying_kinds == {"crown", "grass_tips"}
    for metatile_id in atlas_meta["breeze"]:
        assert len(atlas_meta["anim"][str(metatile_id)]) == len(SWAY_OFFSET_IN_PIXELS_FOR_EACH_ANIMATION_TICK) - 1


def test_breeze_frames_keep_the_metatile_edge_columns():
    atlas_png, atlas_meta = learned_atlas_or_skip()
    from io import BytesIO

    import numpy as np
    from PIL import Image

    atlas_pixels = np.array(Image.open(BytesIO(atlas_png)).convert("RGB"))
    cell_size, columns = atlas_meta["cell"], atlas_meta["columns"]

    def cell_pixels(atlas_cell):
        cell_top, cell_left = (atlas_cell // columns) * cell_size, (atlas_cell % columns) * cell_size
        return atlas_pixels[cell_top:cell_top + cell_size, cell_left:cell_left + cell_size]

    for metatile_id in atlas_meta["breeze"]:
        resting_pixels = cell_pixels(atlas_meta["index"][str(metatile_id)])
        for swaying_cell in atlas_meta["anim"][str(metatile_id)]:
            swaying_pixels = cell_pixels(swaying_cell)
            assert np.array_equal(swaying_pixels[:, [0, -1]], resting_pixels[:, [0, -1]])
