"""Fetch Gen-3-style battle sprites for the regional dex from pokeemerald-expansion."""
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEX = Path(sys.argv[1])
OUT = Path(sys.argv[2])
BASE = "https://raw.githubusercontent.com/rh-hideout/pokeemerald-expansion/master/graphics/pokemon/"
SPECIAL = {"Mr. Mime": "mr_mime", "Farfetch'd": "farfetchd", "Nidoran♀": "nidoran_f", "Nidoran♂": "nidoran_m",
           "Mime Jr.": "mime_jr", "Ho-Oh": "ho_oh", "Porygon-Z": "porygon_z", "Mimikyu": "mimikyu", "Lycanroc": "lycanroc",
           "Type: Null": "type_null", "Jangmo-o": "jangmo_o", "Hakamo-o": "hakamo_o", "Kommo-o": "kommo_o"}


def slug(name):
    return SPECIAL.get(name) or re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read()
    except Exception:
        return None


def fetch(sp):
    s = slug(sp["name"])
    d = OUT / s
    d.mkdir(parents=True, exist_ok=True)
    got = []
    for f in ("anim_front.png", "front.png", "back.png", "normal.pal", "shiny.pal"):
        if (d / f).exists():
            got.append(f)
            continue
        data = get(BASE + s + "/" + f)
        if data:
            (d / f).write_bytes(data)
            got.append(f)
    return sp["name"], s, got


dex = json.loads(DEX.read_text())
with ThreadPoolExecutor(12) as ex:
    res = list(ex.map(fetch, dex))
missing = [(n, s) for n, s, g in res if not ({"anim_front.png", "front.png"} & set(g)) or "back.png" not in g]
print(f"{len(res) - len(missing)}/{len(res)} complete")
for n, s in missing:
    print("missing", n, s)
