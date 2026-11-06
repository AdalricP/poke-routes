"""Generic geography (varies by seed, no towns or landmarks), the Arklarna preset,
and towns placed by hand through the edit API.

    python3 -m pytest -q webui/tests/test_region_geography.py
"""
from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.region import edit
from pokeroutes.region.pipeline import build


def land_cell_far_from(doc_world, taken, distance=16):
    rows, columns = np.nonzero(doc_world.land & ~doc_world.lake)
    for row, column in zip(rows.tolist(), columns.tolist()):
        if 8 <= column < doc_world.W - 8 and 8 <= row < doc_world.H - 8 and \
                all(abs(column - x) + abs(row - y) >= distance for x, y in taken):
            return column, row
    raise AssertionError("no free land")


def test_generic_build_has_no_towns_landmarks_or_routes():
    w = build(3, routes=True, strip=False)
    assert w.preset == "generic"
    assert w.cities == [] and w.landmarks == [] and w.routes == []
    assert w.strait is None
    assert w.landmass.max() >= 1


def test_generic_geography_varies_by_seed():
    worlds = [build(seed, routes=False, strip=False) for seed in range(20)]
    assert len({w.geography for w in worlds}) >= 4
    first = worlds[0].land
    different = [w for w in worlds[1:] if (w.land != first).mean() > 0.2]
    assert len(different) >= 15


def test_geography_can_be_forced():
    assert build(5, routes=False, strip=False, geography_kind="archipelago").geography == "archipelago"
    with pytest.raises(ValueError):
        build(5, routes=False, strip=False, geography_kind="moon")


def test_the_arklarna_preset_is_gone():
    with pytest.raises(ValueError):
        build(1, routes=True, strip=False, preset="arklarna")


def test_add_and_delete_towns_through_the_edit_api(tmp_path):
    doc = edit.generate_doc(11, geography_kind="continent")
    w = edit.doc_to_world(doc)
    first = land_cell_far_from(w, [])
    second = land_cell_far_from(w, [first], distance=40)
    doc = edit.add_town(doc, *first)
    doc = edit.add_town(doc, *second, name="Harbour")
    assert [c["name"] for c in doc["cities"]] == ["Town 1", "Harbour"]
    assert "routes" in doc["stale"]
    doc = edit.recompute(doc, ["routes"])
    assert len(doc["routes"]) >= 1
    with pytest.raises(ValueError):
        edit.add_town(doc, *first)
    sea = np.argwhere(~edit.doc_to_world(doc).land)
    if len(sea):
        with pytest.raises(ValueError):
            edit.add_town(doc, int(sea[0][1]), int(sea[0][0]))

    edit.save(doc, tmp_path / "hand")
    loaded = edit.load(tmp_path / "hand" / "region.json")
    assert loaded["preset"] == "generic"
    assert [c["name"] for c in loaded["cities"]] == ["Town 1", "Harbour"]

    town_id = loaded["cities"][0]["id"]
    after = edit.delete_town(loaded, town_id)
    assert [c["name"] for c in after["cities"]] == ["Harbour"]
    after = edit.recompute(after, ["routes"])
    assert after["routes"] == []


def test_documents_saved_before_presets_load_as_generic():
    doc = edit.generate_doc(1)
    doc["grids"]["half"] = doc["grids"].pop("landmass")
    doc.pop("preset")
    w = edit.doc_to_world(doc)
    assert w.preset == "generic"
    assert w.landmass.max() >= 1


def test_legendary_biomes_in_old_documents_read_as_plain_biomes():
    doc = edit.generate_doc(1)
    w = edit.doc_to_world(doc)
    land_cell = tuple(np.argwhere(w.land & ~w.lake)[0])
    w.biome[land_cell] = "l"
    w.overrides["_edit"]["paint_biome"][land_cell] = ord("g")
    reloaded = edit.doc_to_world(edit.world_to_doc(w))
    assert reloaded.biome[land_cell] == "v"
    assert reloaded.overrides["_edit"]["paint_biome"][land_cell] == ord("f")


def river_mouth_of(world, flat_index):
    receivers = np.asarray(world.recv)
    for _ in range(world.W * world.H):
        receiver = int(receivers[flat_index])
        if receiver < 0:
            return flat_index
        flat_index = receiver
    raise AssertionError("river runs in a circle")


def is_on_the_map_edge(world, flat_index):
    row, column = divmod(flat_index, world.W)
    return row in (0, world.H - 1) or column in (0, world.W - 1)


def test_rivers_rise_in_the_high_ground_and_end_in_a_lake_or_the_sea():
    for seed in (3, 6):
        world = build(seed, routes=False, strip=False)
        land_heights = world.height[world.land]
        assert world.height[world.river].max() >= np.quantile(land_heights, 0.85)
        for flat_index in np.flatnonzero(world.river.ravel())[::7].tolist():
            mouth = river_mouth_of(world, flat_index)
            row, column = divmod(mouth, world.W)
            assert not world.land[row, column] or world.lake[row, column] or is_on_the_map_edge(world, mouth)
