from __future__ import annotations

import copy
import dataclasses
import functools
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Optional

import numpy as np
from PIL import Image

from .tileset import NUM_PRIMARY_METATILES, NUM_PRIMARY_PALS, NUM_PRIMARY_TILES, NUM_PALS_TOTAL, Tileset, TilesetPair

OVERLAY_FORMAT_VERSION = 1
ARK_TILESET_PAIR_NAME = "ark"
OVERLAY_FILE_NAME = "overlay.json"
PREVIOUS_OVERLAY_FILE_NAME = "overlay.previous.json"
OVERLAY_DIRECTORY_ENVIRONMENT_VARIABLE = "POKEROUTES_TILE_OVERRIDES"
MAXIMUM_SECONDARY_TILE_COUNT = 512
MAXIMUM_SECONDARY_METATILE_COUNT = 512
PIXELS_PER_TILE_SIDE = 8
COLOURS_PER_PALETTE = 16
TILES_PER_SHEET_ROW = 16
ALLOWED_TILESET_PAIR_NAME_CHARACTERS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
OVERLAY_SECTIONS = ("tiles", "palettes", "metatiles", "metatile_attributes", "custom_metatiles", "classes")


class OverlayRevisionConflict(Exception):
    pass


class InvalidOverlay(ValueError):
    pass


def empty_overlay() -> dict:
    fresh_overlay = {"format": OVERLAY_FORMAT_VERSION, "revision": 0}
    fresh_overlay.update({section_name: {} for section_name in OVERLAY_SECTIONS})
    return fresh_overlay


def checkout_root_of_this_package() -> Path:
    package_checkout_root = Path(__file__).resolve().parents[1]
    return package_checkout_root


@functools.lru_cache(maxsize=1)
def main_checkout_root_shared_by_every_worktree() -> Path:
    package_checkout_root = checkout_root_of_this_package()
    try:
        git_common_directory_output = subprocess.run(
            ["git", "-C", str(package_checkout_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return package_checkout_root
    if not git_common_directory_output:
        return package_checkout_root
    main_checkout_root = Path(git_common_directory_output).parent
    return main_checkout_root


def overlay_root_directory() -> Path:
    configured_directory = os.environ.get(OVERLAY_DIRECTORY_ENVIRONMENT_VARIABLE)
    if configured_directory:
        return Path(configured_directory)
    shared_overlay_root = main_checkout_root_shared_by_every_worktree() / "tilesets" / "overrides"
    return shared_overlay_root


def require_safe_tileset_pair_name(tileset_pair_name: str) -> str:
    is_safe_name = bool(tileset_pair_name) and set(tileset_pair_name) <= ALLOWED_TILESET_PAIR_NAME_CHARACTERS
    if not is_safe_name:
        raise InvalidOverlay(f"tileset pair name {tileset_pair_name!r} must be lowercase letters, digits and _")
    return tileset_pair_name


def overlay_file_path_for_tileset_pair(tileset_pair_name: str) -> Path:
    overlay_file_path = overlay_root_directory() / require_safe_tileset_pair_name(tileset_pair_name) / OVERLAY_FILE_NAME
    return overlay_file_path


def overlay_file_change_token(tileset_pair_name: str) -> int:
    overlay_file_path = overlay_file_path_for_tileset_pair(tileset_pair_name)
    if not overlay_file_path.is_file():
        return 0
    modification_time_in_nanoseconds = overlay_file_path.stat().st_mtime_ns
    return modification_time_in_nanoseconds


def read_overlay_for_tileset_pair(tileset_pair_name: str) -> dict:
    overlay_file_path = overlay_file_path_for_tileset_pair(tileset_pair_name)
    if not overlay_file_path.is_file():
        return empty_overlay()
    stored_overlay = json.loads(overlay_file_path.read_text())
    normalised_overlay = normalise_overlay(stored_overlay)
    return normalised_overlay


def normalise_overlay(candidate_overlay: dict) -> dict:
    if not isinstance(candidate_overlay, dict):
        raise InvalidOverlay("an overlay must be a JSON object")
    normalised_overlay = empty_overlay()
    normalised_overlay["revision"] = int(candidate_overlay.get("revision", 0))
    for section_name in OVERLAY_SECTIONS:
        section_content = candidate_overlay.get(section_name) or {}
        if not isinstance(section_content, dict):
            raise InvalidOverlay(f"overlay section {section_name} must be an object")
        normalised_overlay[section_name] = copy.deepcopy(section_content)
    return normalised_overlay


def write_overlay_for_tileset_pair(tileset_pair_name: str, new_overlay: dict, expected_revision: Optional[int]) -> dict:
    overlay_file_path = overlay_file_path_for_tileset_pair(tileset_pair_name)
    stored_overlay = read_overlay_for_tileset_pair(tileset_pair_name)
    if expected_revision is not None and int(expected_revision) != stored_overlay["revision"]:
        raise OverlayRevisionConflict(
            f"the {tileset_pair_name} overlay is at revision {stored_overlay['revision']}, not {expected_revision}")
    overlay_to_store = normalise_overlay(new_overlay)
    overlay_to_store["revision"] = stored_overlay["revision"] + 1
    overlay_file_path.parent.mkdir(parents=True, exist_ok=True)
    keep_previous_overlay_file(overlay_file_path)
    write_text_file_atomically(overlay_file_path, json.dumps(overlay_to_store, indent=1, sort_keys=True))
    return overlay_to_store


def keep_previous_overlay_file(overlay_file_path: Path) -> None:
    if not overlay_file_path.is_file():
        return
    previous_overlay_file_path = overlay_file_path.with_name(PREVIOUS_OVERLAY_FILE_NAME)
    write_text_file_atomically(previous_overlay_file_path, overlay_file_path.read_text())


def write_text_file_atomically(destination_path: Path, file_text: str) -> None:
    temporary_path = destination_path.with_name(destination_path.name + f".{os.getpid()}.tmp")
    temporary_path.write_text(file_text)
    os.replace(temporary_path, destination_path)


def tile_pixels_from_hex(tile_pixels_hex: str) -> np.ndarray:
    expected_length = PIXELS_PER_TILE_SIDE * PIXELS_PER_TILE_SIDE
    if not isinstance(tile_pixels_hex, str) or len(tile_pixels_hex) != expected_length:
        raise InvalidOverlay(f"a tile must be {expected_length} hex digits, one per pixel")
    try:
        colour_indices = [int(hex_digit, 16) for hex_digit in tile_pixels_hex]
    except ValueError as bad_digit:
        raise InvalidOverlay(f"tile pixels must be hex digits: {tile_pixels_hex!r}") from bad_digit
    tile_pixels = np.array(colour_indices, dtype=np.uint8).reshape(PIXELS_PER_TILE_SIDE, PIXELS_PER_TILE_SIDE)
    return tile_pixels


def tile_pixels_to_hex(tile_pixels: np.ndarray) -> str:
    tile_pixels_hex = "".join("%x" % int(colour_index) for colour_index in np.asarray(tile_pixels).ravel())
    return tile_pixels_hex


def colour_from_hex(colour_hex: str) -> tuple:
    is_well_formed = isinstance(colour_hex, str) and len(colour_hex) == 7 and colour_hex.startswith("#")
    if not is_well_formed:
        raise InvalidOverlay(f"a colour must look like #rrggbb, not {colour_hex!r}")
    red_green_blue = tuple(int(colour_hex[offset:offset + 2], 16) for offset in (1, 3, 5))
    return red_green_blue


def colour_to_hex(red_green_blue) -> str:
    colour_hex = "#%02x%02x%02x" % tuple(int(channel) for channel in red_green_blue)
    return colour_hex


def apply_overlay_to_tileset_pair(base_pair: TilesetPair, overlay: dict) -> TilesetPair:
    primary_tiles, secondary_tiles = overlaid_tile_sheets(base_pair, overlay["tiles"])
    primary_palettes, secondary_palettes = overlaid_palettes(base_pair, overlay["palettes"])
    primary_metatiles, secondary_metatiles = overlaid_metatile_entries(base_pair, overlay["metatiles"])
    primary_attributes, secondary_attributes = overlaid_metatile_attributes(
        base_pair, overlay["metatile_attributes"], len(secondary_metatiles))
    overridden_tile_indices = {int(tile_index) for tile_index in overlay["tiles"]}
    overlaid_primary = dataclasses.replace(
        base_pair.primary, tiles=primary_tiles, palettes=primary_palettes, metatiles=primary_metatiles,
        attributes=primary_attributes,
        tile_frames=frames_without_overridden_tiles(base_pair.primary.tile_frames, overridden_tile_indices, 0))
    overlaid_secondary = dataclasses.replace(
        base_pair.secondary, tiles=secondary_tiles, palettes=secondary_palettes, metatiles=secondary_metatiles,
        attributes=secondary_attributes,
        tile_frames=frames_without_overridden_tiles(base_pair.secondary.tile_frames, overridden_tile_indices,
                                                    NUM_PRIMARY_TILES))
    overlaid_pair = TilesetPair(overlaid_primary, overlaid_secondary)
    return overlaid_pair


def frames_without_overridden_tiles(tile_frames: dict, overridden_tile_indices: set, sheet_offset: int) -> dict:
    remaining_frames = {local_index: frames for local_index, frames in tile_frames.items()
                        if local_index + sheet_offset not in overridden_tile_indices}
    return remaining_frames


def overlaid_tile_sheets(base_pair: TilesetPair, overlay_tiles: Dict[str, str]):
    primary_tiles = base_pair.primary.tiles.copy()
    secondary_tiles = base_pair.secondary.tiles.copy()
    for tile_index_text, tile_pixels_hex in overlay_tiles.items():
        tile_index = int(tile_index_text)
        tile_pixels = tile_pixels_from_hex(tile_pixels_hex)
        if tile_index < NUM_PRIMARY_TILES:
            primary_tiles = tiles_with_one_tile_set(primary_tiles, tile_index, tile_pixels, NUM_PRIMARY_TILES)
            continue
        secondary_tiles = tiles_with_one_tile_set(secondary_tiles, tile_index - NUM_PRIMARY_TILES, tile_pixels,
                                                  MAXIMUM_SECONDARY_TILE_COUNT)
    return primary_tiles, secondary_tiles


def tiles_with_one_tile_set(tiles: np.ndarray, local_tile_index: int, tile_pixels: np.ndarray, sheet_capacity: int):
    if not 0 <= local_tile_index < sheet_capacity:
        raise InvalidOverlay(f"tile slot {local_tile_index} is outside a sheet of {sheet_capacity} tiles")
    grown_tiles = tiles
    if local_tile_index >= len(tiles):
        missing_tile_count = local_tile_index + 1 - len(tiles)
        blank_tiles = np.zeros((missing_tile_count, PIXELS_PER_TILE_SIDE, PIXELS_PER_TILE_SIDE), dtype=np.uint8)
        grown_tiles = np.concatenate([tiles, blank_tiles])
    grown_tiles[local_tile_index] = tile_pixels
    return grown_tiles


def overlaid_palettes(base_pair: TilesetPair, overlay_palettes: Dict[str, list]):
    primary_palettes = base_pair.primary.palettes.copy()
    secondary_palettes = base_pair.secondary.palettes.copy()
    for palette_slot_text, colour_hexes in overlay_palettes.items():
        palette_slot = int(palette_slot_text)
        if not 0 <= palette_slot < NUM_PALS_TOTAL:
            raise InvalidOverlay(f"palette slot {palette_slot} must be 0-{NUM_PALS_TOTAL - 1}")
        if len(colour_hexes) != COLOURS_PER_PALETTE:
            raise InvalidOverlay(f"palette {palette_slot} needs {COLOURS_PER_PALETTE} colours")
        palette_colours = np.array([colour_from_hex(colour_hex) for colour_hex in colour_hexes], dtype=np.uint8)
        target_palettes = primary_palettes if palette_slot < NUM_PRIMARY_PALS else secondary_palettes
        target_palettes[palette_slot] = palette_colours
    return primary_palettes, secondary_palettes


def overlaid_metatile_entries(base_pair: TilesetPair, overlay_metatiles: Dict[str, list]):
    primary_metatiles = base_pair.primary.metatiles.copy()
    secondary_metatiles = base_pair.secondary.metatiles.copy()
    for metatile_id_text, tile_entries in sorted(overlay_metatiles.items(), key=lambda pair_item: int(pair_item[0])):
        metatile_id = int(metatile_id_text)
        entries_row = validated_metatile_entries(metatile_id, tile_entries)
        if metatile_id < NUM_PRIMARY_METATILES:
            primary_metatiles = rows_with_one_row_set(primary_metatiles, metatile_id, entries_row, len(primary_metatiles))
            continue
        secondary_metatiles = rows_with_one_row_set(secondary_metatiles, metatile_id - NUM_PRIMARY_METATILES,
                                                    entries_row, MAXIMUM_SECONDARY_METATILE_COUNT)
    return primary_metatiles, secondary_metatiles


def validated_metatile_entries(metatile_id: int, tile_entries: list) -> np.ndarray:
    entry_count_per_metatile = 8
    if not isinstance(tile_entries, list) or len(tile_entries) != entry_count_per_metatile:
        raise InvalidOverlay(f"metatile {metatile_id} needs {entry_count_per_metatile} tile entries")
    entries_row = np.array([int(tile_entry) & 0xFFFF for tile_entry in tile_entries], dtype=np.uint16)
    return entries_row


def rows_with_one_row_set(rows: np.ndarray, local_row_index: int, new_row: np.ndarray, row_capacity: int) -> np.ndarray:
    if not 0 <= local_row_index < row_capacity:
        raise InvalidOverlay(f"metatile slot {local_row_index} is outside a tileset of {row_capacity} metatiles")
    if local_row_index > len(rows):
        raise InvalidOverlay(f"new metatile {local_row_index} leaves a gap after the last one ({len(rows) - 1})")
    if local_row_index == len(rows):
        grown_rows = np.concatenate([rows, new_row[np.newaxis, :]])
        return grown_rows
    updated_rows = rows.copy()
    updated_rows[local_row_index] = new_row
    return updated_rows


def overlaid_metatile_attributes(base_pair: TilesetPair, overlay_attributes: Dict[str, int], secondary_count: int):
    primary_attributes = base_pair.primary.attributes.copy()
    secondary_attributes = np.zeros(secondary_count, dtype=np.uint16)
    secondary_attributes[:len(base_pair.secondary.attributes)] = base_pair.secondary.attributes[:secondary_count]
    for metatile_id_text, attribute_value in overlay_attributes.items():
        metatile_id = int(metatile_id_text)
        if metatile_id < NUM_PRIMARY_METATILES and metatile_id < len(primary_attributes):
            primary_attributes[metatile_id] = int(attribute_value) & 0xFFFF
            continue
        local_metatile_id = metatile_id - NUM_PRIMARY_METATILES
        if not 0 <= local_metatile_id < secondary_count:
            raise InvalidOverlay(f"attribute for metatile {metatile_id}, which does not exist")
        secondary_attributes[local_metatile_id] = int(attribute_value) & 0xFFFF
    return primary_attributes, secondary_attributes


def validate_overlay_against_tileset_pair(base_pair: TilesetPair, overlay: dict) -> dict:
    normalised_overlay = normalise_overlay(overlay)
    apply_overlay_to_tileset_pair(base_pair, normalised_overlay)
    for metatile_id_text, custom_properties in normalised_overlay["custom_metatiles"].items():
        require_custom_metatile_properties_are_well_formed(int(metatile_id_text), custom_properties)
    return normalised_overlay


def require_custom_metatile_properties_are_well_formed(metatile_id: int, custom_properties: dict) -> None:
    if not isinstance(custom_properties, dict):
        raise InvalidOverlay(f"custom metatile {metatile_id} must be an object")
    class_letter = custom_properties.get("class", "g")
    if not (isinstance(class_letter, str) and len(class_letter) == 1 and class_letter.isalpha()):
        raise InvalidOverlay(f"custom metatile {metatile_id} needs a one-letter class, not {class_letter!r}")


def apply_overlay_to_ark_class_table(class_table: dict, overlay: dict) -> dict:
    overlaid_class_table = copy.deepcopy(class_table)
    overlaid_class_table.setdefault("classes", {}).update(copy.deepcopy(overlay["classes"]))
    class_of_metatile = overlaid_class_table.setdefault("class_of_metatile", {})
    props = overlaid_class_table.setdefault("props", {})
    for metatile_id_text, custom_properties in overlay["custom_metatiles"].items():
        class_letter = custom_properties.get("class", "g")
        class_of_metatile[str(int(metatile_id_text))] = "o" if custom_properties.get("collision") else class_letter
        if custom_properties.get("in_palette", True):
            props[custom_prop_name(int(metatile_id_text))] = custom_prop_entry(int(metatile_id_text), custom_properties)
    return overlaid_class_table


def custom_prop_name(metatile_id: int) -> str:
    prop_name = "custom_%03x" % metatile_id
    return prop_name


def custom_prop_entry(metatile_id: int, custom_properties: dict) -> dict:
    prop_entry = {"cells": [[metatile_id]], "collision": 1 if custom_properties.get("collision") else 0,
                  "name": custom_properties.get("name") or "tile %03X" % metatile_id}
    return prop_entry


def overlay_has_changes(overlay: dict) -> bool:
    has_changes = any(overlay.get(section_name) for section_name in OVERLAY_SECTIONS)
    return has_changes


def write_overlaid_tileset_pair_files(base_pair: TilesetPair, overlay: dict, tilesets_output_directory: Path) -> list:
    overlaid_pair = apply_overlay_to_tileset_pair(base_pair, overlay)
    written_directories = []
    for kind_folder, base_tileset, overlaid_tileset in (("primary", base_pair.primary, overlaid_pair.primary),
                                                         ("secondary", base_pair.secondary, overlaid_pair.secondary)):
        tileset_output_directory = Path(tilesets_output_directory) / kind_folder / base_tileset.path.name
        write_one_tileset_in_pokeemerald_format(base_tileset, overlaid_tileset, tileset_output_directory)
        written_directories.append(tileset_output_directory)
    return written_directories


def write_one_tileset_in_pokeemerald_format(base_tileset: Tileset, overlaid_tileset: Tileset, output_directory: Path):
    output_directory.mkdir(parents=True, exist_ok=True)
    write_indexed_tile_sheet(base_tileset.path / "tiles.png", overlaid_tileset.tiles, output_directory / "tiles.png")
    write_jasc_palettes(overlaid_tileset.palettes, output_directory / "palettes")
    (output_directory / "metatiles.bin").write_bytes(overlaid_tileset.metatiles.astype("<u2").tobytes())
    (output_directory / "metatile_attributes.bin").write_bytes(overlaid_tileset.attributes.astype("<u2").tobytes())


def write_indexed_tile_sheet(original_sheet_path: Path, tiles: np.ndarray, output_sheet_path: Path) -> None:
    sheet_row_count = (len(tiles) + TILES_PER_SHEET_ROW - 1) // TILES_PER_SHEET_ROW
    padded_tiles = np.zeros((sheet_row_count * TILES_PER_SHEET_ROW, PIXELS_PER_TILE_SIDE, PIXELS_PER_TILE_SIDE),
                            dtype=np.uint8)
    padded_tiles[:len(tiles)] = tiles
    sheet_pixels = (padded_tiles.reshape(sheet_row_count, TILES_PER_SHEET_ROW, PIXELS_PER_TILE_SIDE, PIXELS_PER_TILE_SIDE)
                    .transpose(0, 2, 1, 3)
                    .reshape(sheet_row_count * PIXELS_PER_TILE_SIDE, TILES_PER_SHEET_ROW * PIXELS_PER_TILE_SIDE))
    sheet_image = Image.frombytes("P", (sheet_pixels.shape[1], sheet_pixels.shape[0]), np.ascontiguousarray(sheet_pixels).tobytes())
    with Image.open(original_sheet_path) as original_sheet:
        sheet_image.putpalette(original_sheet.getpalette() or [])
    sheet_image.save(output_sheet_path, bits=4, optimize=True)


def write_jasc_palettes(palettes: np.ndarray, palettes_directory: Path) -> None:
    palettes_directory.mkdir(parents=True, exist_ok=True)
    for palette_slot, palette_colours in enumerate(palettes):
        colour_lines = ["%d %d %d" % tuple(int(channel) for channel in colour) for colour in palette_colours]
        palette_text = "\r\n".join(["JASC-PAL", "0100", str(COLOURS_PER_PALETTE), *colour_lines]) + "\r\n"
        (palettes_directory / f"{palette_slot:02d}.pal").write_bytes(palette_text.encode("ascii"))
