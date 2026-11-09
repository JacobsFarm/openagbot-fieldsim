"""Wereld naar 'scene' voor de 3D-UI: alles in lokale meters, plus het hoogteraster als binaire floats."""
from __future__ import annotations

import numpy as np

from openagbot_fieldsim.world.geometry import centroid, polygon_area


def _r(pts, nd=3):
    return [[round(x, nd), round(y, nd)] for x, y in pts]


def scene(world, terrain, params) -> dict:
    levels = terrain.ditch_levels or [0.0] * len(world.ditches)
    sx, sy, sh = world.start_xy_heading()
    return {
        "name": world.name,
        "label": world.label,
        "origin": {"lat": world.origin[0], "lon": world.origin[1], "h": world.origin[2]},
        "area": _r(world.area_xy),
        "start": {"x": sx, "y": sy, "heading": sh},
        "fields": [{"id": f.id, "name": f.name, "crop": f.crop, "poly": _r(f.xy),
                    "area_ha": round(polygon_area(f.xy) / 1e4, 2), "center": _r([centroid(f.xy)])[0]}
                   for f in world.fields],
        "surfaces": [{"kind": s.kind, "name": s.name, "poly": _r(s.xy), "level": s.level_m} for s in world.surfaces],
        "ditches": [{"line": _r(d.xy), "width": d.width_m, "depth": d.depth_m, "level": round(lv, 3)}
                    for d, lv in zip(world.ditches, levels)],
        "buildings": [{"id": b.id, "name": b.name, "kind": b.kind, "poly": _r(b.xy), "height": b.height_m,
                       "base": round(float(min(terrain.height(x, y) for x, y in b.xy)), 3),
                       "roof": {"type": b.roof.type, "ridge": b.roof.ridge_height_m, "axis": b.roof.ridge_axis}}
                      for b in world.buildings],
        "trees": [[round(t.xy[0], 2), round(t.xy[1], 2), round(terrain.height(*t.xy), 2), t.height_m,
                   1 if t.crown == "cone" else 0] for t in world.trees],
        "fences": [{"line": _r(f.xy), "height": f.height_m, "spacing": f.post_spacing_m} for f in world.fences],
        "terrain": {"x0": terrain.x0, "y0": terrain.y0, "cell": terrain.cell, "nx": terrain.nx, "ny": terrain.ny},
        "robot": {"wheelbase": params.wheelbase_m, "track": params.track_width_m,
                  "wheel_diameter": round(params.wheel_circumference_m / np.pi, 3),
                  "antenna1": list(params.antenna1_from_axle), "antenna_height": params.antenna_height_m,
                  "baseline": params.extras.baseline_m},
    }


def terrain_bytes(terrain, cell: float = 2.0) -> tuple[dict, bytes]:
    t = terrain.resampled(cell)
    return ({"x0": t.x0, "y0": t.y0, "cell": t.cell, "nx": t.nx, "ny": t.ny},
            np.ascontiguousarray(t.z, dtype="<f4").tobytes())
