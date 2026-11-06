"""Desert mesas in bands: a rock mass in the desert steps up in tiers, each
tier's rim a cliff face, so a big mesa reads as banded layers of rock (Route 111's
mountains, the owner's desert references) instead of one flat rock top."""
from __future__ import annotations

import numpy as np

from .generate import Route

DEPTH_OF_THE_FIRST_INNER_BAND_IN_METATILES = 4
BAND_SPACING_IN_METATILES = 4
BAND_FACE_DEPTH_IN_METATILES = 2


def band_the_mesas(route: Route, depth_into_rock: np.ndarray, cliff: str) -> int:
    depth_past_the_first_band = depth_into_rock - DEPTH_OF_THE_FIRST_INNER_BAND_IN_METATILES
    is_band_face = (depth_past_the_first_band >= 0) & (depth_past_the_first_band % BAND_SPACING_IN_METATILES < BAND_FACE_DEPTH_IN_METATILES)
    band_face = is_band_face & (route.cls == "r")
    route.cls[band_face] = cliff
    band_cell_count = int(band_face.sum())
    route.log.append(f"desert: {band_cell_count} cells of banded cliff inside the mesas")
    return band_cell_count
