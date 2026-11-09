"""Puntmissies: zelf gezette GPS-punten of onkruidplanten, een rijvolgorde, en export naar de robot.

Formaat = `data/waypoints.json` van OpenAgbot: [{"way_point_name", "lat", "lon"}, ...]; de robot rijdt ze met
pure pursuit (`/api/nav/start_pure_pursuit`). Voor onkruid is de volgorde het werk: kortste route langs alle planten
(nearest neighbour + 2-opt), beginnend bij de robot. Bochten krapper dan de draaicirkel vangt pure pursuit zelf af;
de sim laat zien of dat goed gaat voordat de robot het veld in gaat.

Import: CSV (lat, lon[, naam]) of GeoJSON-punten; ook de detectielogger van de robot (metadata met lat/lon).
"""
from __future__ import annotations

import csv
import io
import json
import math
import random


def parse_points_file(name: str, data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    if name.lower().endswith((".json", ".geojson")):
        d = json.loads(text)
        out = []
        if isinstance(d, dict) and d.get("type") == "FeatureCollection":
            for f in d["features"]:
                g = f.get("geometry") or {}
                if g.get("type") == "Point":
                    lon, lat = g["coordinates"][:2]
                    out.append({"lat": lat, "lon": lon, "name": str((f.get("properties") or {}).get("name", ""))})
        elif isinstance(d, list):      # waypoints.json of detectie-metadata
            for p in d:
                lat = p.get("lat", p.get("latitude"))
                lon = p.get("lon", p.get("longitude"))
                if lat is not None and lon is not None:
                    out.append({"lat": float(lat), "lon": float(lon), "name": p.get("way_point_name") or p.get("name", "")})
        return out
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for r in rows:
        try:
            lat, lon = float(r[0]), float(r[1])
        except (ValueError, IndexError):
            continue                     # kopregel of lege regel
        out.append({"lat": lat, "lon": lon, "name": r[2].strip() if len(r) > 2 else ""})
    return out


def route_order(xy: list[tuple[float, float]], start: tuple[float, float] | None = None, iterations: int = 4) -> list[int]:
    """Volgorde langs alle punten: nearest neighbour vanaf de start, dan 2-opt (open route)."""
    n = len(xy)
    if n < 3:
        return list(range(n))
    left = set(range(n))
    cur = start or xy[0]
    order = []
    while left:
        j = min(left, key=lambda k: (xy[k][0] - cur[0]) ** 2 + (xy[k][1] - cur[1]) ** 2)
        order.append(j)
        left.remove(j)
        cur = xy[j]
    pts = ([start] if start else [xy[order[0]]]) + [xy[i] for i in (order if start else order[1:])]
    idx = ([-1] if start else [order[0]]) + (order if start else order[1:])
    m = len(pts)

    def d(a, b):
        return math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])

    for _ in range(iterations):
        improved = False
        for i in range(1, m - 1):                # punt 0 (start) ligt vast
            for k in range(i + 1, m):
                before = d(i - 1, i) + (d(k, k + 1) if k + 1 < m else 0.0)
                after = d(i - 1, k) + (d(i, k + 1) if k + 1 < m else 0.0)
                if after < before - 1e-9:
                    pts[i:k + 1] = pts[i:k + 1][::-1]
                    idx[i:k + 1] = idx[i:k + 1][::-1]
                    improved = True
        if not improved:
            break
    return [i for i in idx if i >= 0]


def route_length(xy, order, start=None) -> float:
    pts = ([start] if start else []) + [xy[i] for i in order]
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def to_waypoints(points: list[dict], prefix: str = "punt") -> list[dict]:
    return [{"way_point_name": p.get("name") or f"{prefix} {i + 1}", "lat": round(p["lat"], 9),
             "lon": round(p["lon"], 9)} for i, p in enumerate(points)]


def random_weeds(world, field_id: str, count: int = 60, clusters: int = 5, seed: int = 3) -> list[dict]:
    """Ridderzuring-achtige verdeling in een veld (in pollen) om routes mee te testen."""
    from openagbot_fieldsim.world.geometry import bbox, point_in_polygon
    f = world.field_by_id(field_id)
    rng = random.Random(seed)
    x0, y0, x1, y1 = bbox(f.xy)
    centers = []
    while len(centers) < clusters:
        c = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        if point_in_polygon(*c, f.xy):
            centers.append(c)
    out = []
    tries = 0
    while len(out) < count and tries < count * 50:
        tries += 1
        cx, cy = rng.choice(centers)
        x, y = cx + rng.gauss(0, 12), cy + rng.gauss(0, 12)
        if point_in_polygon(x, y, f.xy):
            lat, lon = world.frame.to_latlon(x, y)
            out.append({"lat": lat, "lon": lon, "name": f"zuring {len(out) + 1}"})
    return out
