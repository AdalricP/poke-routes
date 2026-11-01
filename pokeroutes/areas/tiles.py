"""Named metatiles the area stamps can pin in place.

The autotiler only knows coarse classes (grass, tree, rock...). A special area
needs a few concrete things it cannot invent: a cave mouth, a fence pen, a
flower bed, a boulder, a sign. Everything here was read off real Emerald maps
(the map and cell are noted), so the pieces join the way Game Freak drew them.

Each entry is a grid of metatile ids (``None`` = leave to the autotiler) plus
the class each cell should report to its neighbours. Free-standing pieces
(boulders, mounds, fences, signs) report ``o`` ("other"): the autotiler never
learned ``o``, so the grass around them stays plain grass instead of growing
rock edges, and the generator treats ``o`` as blocked.

``PIECES[name]`` -> {"needs": tileset names the ids come from (``general`` is the
primary every outdoor pair shares), "mt": rows of ids, "cls": rows of classes}.
"""
from __future__ import annotations

from typing import Dict, List, Optional

_ = None

PIECES: Dict[str, dict] = {
    # --- primary (General): works with any outdoor secondary ---------------------
    "boulder": {"needs": ["general"], "mt": [[0x0E0]], "cls": ["o"]},            # Route123 (1,79)
    "boulder_grey": {"needs": ["general"], "mt": [[0x194]], "cls": ["o"]},       # Route108
    "flowers": {"needs": ["general"], "mt": [[0x004]], "cls": ["g"]},            # red/yellow flowers, walkable
    "sign": {"needs": ["general"], "mt": [[0x01B]], "cls": ["o"]},               # Route110 (25,15) wooden sign
    "mailbox": {"needs": ["general"], "mt": [[0x003]], "cls": ["o"]},
    "gravel": {"needs": ["general"], "mt": [[0x024]], "cls": ["v"]},             # Jagged Pass grit
    # dark broad-crowned tree, 2x2 (Route121 (0,6)); reads as an old oak/elm among the pines
    "old_tree": {"needs": ["general"], "mt": [[0x1D4, 0x1D5], [0x1DC, 0x1DD]], "cls": ["ff", "ff"]},
    # pale trodden grass 9-slice (SafariZone_South (4,5)); walkable
    "pale_tl": {"needs": ["general"], "mt": [[0x1D0]], "cls": ["g"]},
    "pale_t": {"needs": ["general"], "mt": [[0x1D1]], "cls": ["g"]},
    "pale_tr": {"needs": ["general"], "mt": [[0x1D2]], "cls": ["g"]},
    "pale_l": {"needs": ["general"], "mt": [[0x1D8]], "cls": ["g"]},
    "pale_c": {"needs": ["general"], "mt": [[0x1D9]], "cls": ["g"]},
    "pale_r": {"needs": ["general"], "mt": [[0x1DA]], "cls": ["g"]},
    "pale_bl": {"needs": ["general"], "mt": [[0x1E0]], "cls": ["g"]},
    "pale_b": {"needs": ["general"], "mt": [[0x1E1]], "cls": ["g"]},
    "pale_br": {"needs": ["general"], "mt": [[0x1E2]], "cls": ["g"]},
    # wooden fence (Route117 (3,7)): corners, runs, posts
    "fence_tl": {"needs": ["general"], "mt": [[0x138]], "cls": ["o"]},
    "fence_tr": {"needs": ["general"], "mt": [[0x13A]], "cls": ["o"]},
    "fence_bl": {"needs": ["general"], "mt": [[0x148]], "cls": ["o"]},
    "fence_br": {"needs": ["general"], "mt": [[0x14A]], "cls": ["o"]},
    "fence_h": {"needs": ["general"], "mt": [[0x149]], "cls": ["o"]},
    "fence_l": {"needs": ["general"], "mt": [[0x140]], "cls": ["o"]},
    "fence_r": {"needs": ["general"], "mt": [[0x142]], "cls": ["o"]},
    # cave mouth cut into a rock face (Route116 (7,47) -> Rusturf Tunnel). Bottom-centre is the door.
    "cave_mouth": {"needs": ["general"],
                   "mt": [[_, 0x09F, _], [0x091, 0x0A7, 0x091]],
                   "cls": ["rkr", "knk"]},
    # --- Fortree secondary -----------------------------------------------------
    # round rock mound, 2x2 (Route120 (52,6)); boulders on a rise
    "rock_mound": {"needs": ["fortree"], "mt": [[0x230, 0x232], [0x240, 0x242]], "cls": ["oo", "oo"]},
    # big rock with a dark hollow at its foot (Route120 (52,5), the Scorched Slab mouth). Door = 0x064.
    "rock_hollow": {"needs": ["fortree"],
                    "mt": [[_, 0x230, 0x231, 0x232, _], [0x230, 0x073, 0x071, 0x075, 0x232],
                           [0x238, 0x07B, 0x064, 0x07D, 0x23A]],
                    "cls": ["gooog", "ooooo", "oonoo"]},
    # tree-house trunk with a door (FortreeCity (1,8)). Door = 0x235.
    "tree_door": {"needs": ["fortree"],
                  "mt": [[_, 0x224, 0x225, 0x226, _], [0x22B, 0x22C, 0x22D, 0x22E, 0x22F],
                         [0x233, 0x234, 0x235, 0x236, 0x237]],
                  "cls": ["fffff", "fffff", "ffnff"]},
}

# door cell (row, col) inside pieces that lead somewhere
DOORS = {"cave_mouth": (1, 1), "rock_hollow": (2, 2), "tree_door": (2, 2)}

# what to paint instead when the target tileset pair lacks a piece
SUBSTITUTE = {"rock_mound": "boulder", "rock_hollow": "cave_mouth", "tree_door": "cave_mouth"}


def available(name: str, primary: str, secondary: str) -> bool:
    p = PIECES.get(name)
    if not p:
        return False
    return all(t in (primary, secondary) for t in p["needs"])


def resolve(name: str, primary: str = "general", secondary: str = "fortree") -> Optional[str]:
    """The piece to use for ``name`` on this tileset pair (following SUBSTITUTE), or None."""
    seen = set()
    while name and name not in seen:
        if available(name, primary, secondary):
            return name
        seen.add(name)
        name = SUBSTITUTE.get(name)
    return None


def cells(name: str) -> List[tuple]:
    """[(dy, dx, metatile or None, class)] for a piece, top-left anchored."""
    p = PIECES[name]
    out = []
    for dy, (row, crow) in enumerate(zip(p["mt"], p["cls"])):
        for dx, (mid, c) in enumerate(zip(row, crow)):
            out.append((dy, dx, mid, c))
    return out
