"""Wereld naar 'scene' voor de 3D-UI: alles in lokale meters, plus het hoogteraster als binaire floats."""
from __future__ import annotations

import numpy as np

from openagbot_fieldsim.world.geometry import centroid, polygon_area
from openagbot_fieldsim.world.model import BRIDGING


def _ditch_pieces(world, d) -> list:
    """Sloot voor de tekening, onderbroken bij een dam, duiker of brug (daar ligt geen water)."""
    cors = [p for p in world.passages if p.kind in BRIDGING and len(p.xy) >= 2]
    if not cors or len(d.xy) < 2:
        return [d.xy]
    try:
        from shapely.geometry import LineString
        from shapely.ops import unary_union
        cut = unary_union([LineString(p.xy).buffer(p.width_m / 2.0, cap_style="flat") for p in cors])
        rest = LineString(d.xy).difference(cut)
        return [list(g.coords) for g in getattr(rest, "geoms", [rest])
                if g.geom_type == "LineString" and g.length > 0.3]
    except Exception:       # noqa: BLE001 - zonder shapely: de hele sloot
        return [d.xy]


def _r(pts, nd=3):
    return [[round(x, nd), round(y, nd)] for x, y in pts]


def _ditch_zone(world) -> dict:
    """Slootzone voor de tekening: de verboden strook langs sloot en water als ringen (lokale meters)."""
    try:
        from shapely.ops import unary_union
        from openagbot_fieldsim.missions.boundary import ditch_zone
        from openagbot_fieldsim.missions.safety import hazards
        width = ditch_zone(world)[0]
        geoms = [h.geom for h in hazards(world) if h.kind == "water"]
        if not geoms or width <= 0:
            return {"width": width, "rings": []}
        u = unary_union(geoms).simplify(0.15)
        rings = []
        for g in getattr(u, "geoms", [u]):
            for r in (g.exterior, *g.interiors):
                rings.append(_r(list(r.coords)[:-1], 2))
        return {"width": width, "rings": rings}
    except Exception:       # noqa: BLE001 - zonder shapely geen zone in beeld
        return {"width": 0.0, "rings": []}


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
                    "area_ha": round(polygon_area(f.xy) / 1e4, 2), "center": _r([centroid(f.xy)])[0],
                    "holes": [{"poly": _r(h.xy), "passable": h.passable} for h in f.holes],
                    "headland_rounds": f.headland_rounds, "work_width_m": f.work_width_m, "source": f.source}
                   for f in world.fields],
        "surfaces": [{"kind": s.kind, "name": s.name, "poly": _r(s.xy), "level": s.level_m} for s in world.surfaces],
        "ditches": [{"line": _r(piece), "width": d.width_m, "depth": d.depth_m, "water_depth": d.water_depth_m,
                     "level": round(lv, 3)}
                    for d, lv in zip(world.ditches, levels) for piece in _ditch_pieces(world, d)],
        "dikes": [{"id": d.id, "name": d.name, "line": _r(d.xy), "crest": d.crest_width_m, "height": d.height_m,
                   "half_base": round(d.half_base_m, 2), "mode": d.mode} for d in world.dikes],
        "no_go": [{"id": g.id, "name": g.name, "poly": _r(g.xy), "action": g.action} for g in world.no_go],
        "zones": [{"id": z.id, "name": z.name, "poly": _r(z.xy), "klass": z.klass} for z in world.zones],
        "passages": [{"id": p.id, "name": p.name, "line": _r(p.xy), "width": p.width_m, "kind": p.kind,
                      "direction": p.direction, "confirm": p.confirm} for p in world.passages],
        "nests": [{"id": n.id, "name": n.name, "x": round(n.xy[0], 2), "y": round(n.xy[1], 2), "radius": n.radius_m,
                   "start": n.start, "end": n.end, "active": n.active()} for n in world.nests],
        "markers": [{"id": m.id, "name": m.name, "kind": m.kind, "x": round(m.xy[0], 2), "y": round(m.xy[1], 2),
                     "radius": m.radius_m, "note": m.note} for m in world.markers],
        "ditch_zone": _ditch_zone(world),
        "farm_settings": world.farm_settings, "overlay": world.overlay, "overlay_report": world.overlay_report,
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
