"""Perceelgrenzen controleren: BRP (RVO) tegen het Kadaster en tegen het water (BGT).

  .venv\\Scripts\\python tools\\check_boundaries.py eigen_bedrijf

Per veld: hoeveel kadastrale percelen eronder liggen, welk deel van de BRP-grens binnen het Kadaster valt, hoe ver
de BRP-rand van het water (insteek) ligt waar er water naast ligt, en hoeveel m2 BRP in het water getekend is.
Een BRP-rand op 0,0-0,2 m van het water betekent: de boer heeft tot de insteek getekend, dus daar bepaalt de
vrije ruimte langs de sloot (missieplanner) hoe ver de robot binnen blijft. De wereld moet de kadasterlaag hebben
(`python -m openagbot_fieldsim.world.builder <wereld> --boundaries`).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import LineString, Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from openagbot_fieldsim.world.model import load_world  # noqa: E402


def check(world) -> list[dict]:
    f = world.frame
    kad = [Polygon([f.to_xy(*p) for p in k["polygon"]]).buffer(0) for k in world.extra.get("kadaster", [])]
    water = unary_union([Polygon(s.xy).buffer(0) for s in world.surfaces if s.kind == "water"] +
                        [LineString(d.xy).buffer(d.width_m / 2) for d in world.ditches])
    out = []
    for fl in world.fields:
        b = Polygon(fl.xy).buffer(0)
        ks = [k for k in kad if k.intersection(b).area > 0.5 * min(k.area, b.area) or k.intersection(b).area > 0.3 * b.area]
        ku = unary_union(ks) if ks else None
        ring = b.exterior
        n = max(1, int(ring.length))
        d = [water.distance(ring.interpolate(i)) for i in range(n)] if not water.is_empty else []
        near = [x for x in d if x < 5.0]
        out.append({
            "veld": fl.id, "naam": fl.name, "brp_m2": round(b.area),
            "kadaster_percelen": len(ks),
            "brp_binnen_kadaster_pct": round(100.0 * ku.intersection(b).area / b.area, 1) if ku else None,
            "rand_langs_water_pct": round(100.0 * len(near) / n),
            "afstand_rand_water_m": round(sum(near) / len(near), 2) if near else None,
            "brp_in_water_m2": round(b.intersection(water).area, 1) if not water.is_empty else 0.0,
        })
    return out


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    name = a[0] if a else "eigen_bedrijf"
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    w = load_world(name if os.path.isdir(name) else os.path.join(here, "worlds", name))
    if not w.extra.get("kadaster"):
        print("let op: geen kadasterlaag in deze wereld; eerst de bouwer met --boundaries draaien")
    rows = check(w)
    cols = list(rows[0]) if rows else []
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join("" if r[c] is None else str(r[c]) for c in cols))


if __name__ == "__main__":
    main()
