"""Wereldbouwer: een echte plek in Nederland als speelwereld (PLAN.md hoofdstuk 4).

Invoer: worlds/<naam>/world.json met alleen het gebied (veelhoek in lat/lon). Uitvoer: dezelfde world.json met
percelen (BRP), vlakken (BGT), gebouwen (BGT-footprint + hoogte uit 3D BAG) en een hoogteraster (AHN DTM) in
terrain.npz. Alles in de FreeCAD-stijl: vlakke kleuren per soort, simpele blokken met plat of zadeldak.

Coördinaten: BGT en BRP komen van PDOK in CRS84 (lon/lat, door PDOK correct uit RD omgerekend). AHN en 3D BAG zijn
in RD. Om RD exact naar de sim te krijgen halen we een paar BGT-objecten in beide stelsels op en passen een affiene
afbeelding RD -> lokaal ENU toe (over een paar km is die op de millimeter; de rest-afwijking wordt gelogd).

  python -m openagbot_fieldsim.world.builder eigen_bedrijf
"""
from __future__ import annotations

import datetime
import math
import os
import sys
from typing import Callable

import numpy as np

from openagbot_fieldsim.world.geometry import ccw, point_in_polygon, polygon_area
from openagbot_fieldsim.world.model import Building, Field, Roof, Surface, Tree, World, load_world
from openagbot_fieldsim.world.sources import ahn
from openagbot_fieldsim.world.sources.http import fetch, fetch_features, fetch_json
from openagbot_fieldsim.world.terrain import Terrain, points_in_polygon

BGT = "https://api.pdok.nl/lv/bgt/ogc/v1/collections/{c}/items?bbox={bbox}&limit=1000&f=json"
BRP = "https://api.pdok.nl/rvo/gewaspercelen/ogc/v1/collections/brpgewas/items?bbox={bbox}&limit=1000&f=json"
BAG3D = "https://api.3dbag.nl/collections/pand/items?bbox={bbox}&limit=100"
RD = "&crs=http://www.opengis.net/def/crs/EPSG/0/28992"

# BGT-klasse -> soort oppervlak in de sim (kleur in ui/src/theme.js, grip in world/model.py)
BGT_KIND = {
    ("wegdeel", "rijbaan lokale weg"): "road", ("wegdeel", "rijbaan regionale weg"): "road",
    ("wegdeel", "rijbaan autoweg"): "road", ("wegdeel", "rijbaan autosnelweg"): "road",
    ("wegdeel", "inrit"): "paved", ("wegdeel", "fietspad"): "paved", ("wegdeel", "voetpad"): "paved",
    ("wegdeel", "parkeervlak"): "paved", ("wegdeel", "erf"): "yard",
    ("onbegroeidterreindeel", "erf"): "yard", ("onbegroeidterreindeel", "onverhard"): "track",
    ("onbegroeidterreindeel", "half verhard"): "track", ("onbegroeidterreindeel", "open verharding"): "paved",
    ("onbegroeidterreindeel", "gesloten verharding"): "paved",
    ("ondersteunendwegdeel", "*"): "verge",
    ("begroeidterreindeel", "rietland"): "verge", ("begroeidterreindeel", "moeras"): "verge",
    ("begroeidterreindeel", "houtwal"): "verge", ("begroeidterreindeel", "groenvoorziening"): "verge",
    ("waterdeel", "*"): "water",
}
SURFACE_ORDER = ["verge", "track", "yard", "paved", "road", "water"]   # later getekend = bovenop
FIELD_CATEGORIES = ("Bouwland", "Grasland")
WATER_DEPTH_M = 0.8          # sloot onder het maaiveld langs de rand
WATER_LEVEL_M = 0.35         # waterpeil onder het maaiveld langs de rand


class Builder:
    def __init__(self, folder: str, progress: Callable[[str], None] | None = None):
        self.folder = folder
        self.world = load_world(folder)
        self.cache = os.path.join(folder, "cache")
        self.log = progress or (lambda msg: print(msg, flush=True))
        f = self.world.frame
        x0, y0, x1, y1 = _bbox(self.world.area_xy)
        m = self.world.margin_m
        self.xy_bbox = (x0 - m, y0 - m, x1 + m, y1 + m)
        corners = [f.to_latlon(x, y) for x, y in ((x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m))]
        lats, lons = [c[0] for c in corners], [c[1] for c in corners]
        self.ll_bbox = f"{min(lons):.6f},{min(lats):.6f},{max(lons):.6f},{max(lats):.6f}"
        self.rd_to_xy = None

    # ------------------------------------------------------------------ hulp
    def _xy(self, lonlat_ring):
        f = self.world.frame
        return [f.to_xy(lat, lon) for lon, lat, *_ in lonlat_ring]

    def _ll(self, xy):
        return [self.world.frame.to_latlon(x, y) for x, y in xy]

    def _in_area(self, xy) -> bool:
        x0, y0, x1, y1 = self.xy_bbox
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in xy)

    # ------------------------------------------------------------------ RD -> ENU
    def fit_rd(self):
        """Affiene afbeelding RD -> lokaal xy uit dezelfde BGT-objecten in beide stelsels."""
        ll = fetch_features(BGT.format(c="pand", bbox=self.ll_bbox), self.cache)[:60]
        rd = fetch_features(BGT.format(c="pand", bbox=self.ll_bbox) + RD, self.cache)[:60]
        if len(ll) < 3:
            ll = fetch_features(BGT.format(c="waterdeel", bbox=self.ll_bbox), self.cache)[:60]
            rd = fetch_features(BGT.format(c="waterdeel", bbox=self.ll_bbox) + RD, self.cache)[:60]
        # Sleutel = object + registratietijd: de BGT geeft ook oudere versies van hetzelfde object terug
        def key(g):
            p = g["properties"]
            return p.get("lokaal_id"), p.get("tijdstip_registratie")

        by_id = {key(g): g for g in rd}
        src, dst = [], []
        for g in ll:
            other = by_id.get(key(g))
            if other is None:
                continue
            for a, b in zip(_rings(g["geometry"]), _rings(other["geometry"])):
                for p, q in zip(a, b):
                    src.append(q[:2])
                    dst.append(self.world.frame.to_xy(p[1], p[0]))
        if len(src) < 6:
            raise RuntimeError("te weinig BGT-objecten om RD aan de wereld te koppelen")
        A = np.array([[x, y, 1.0] for x, y in src])
        B = np.array(dst)
        keep = np.ones(len(A), dtype=bool)
        for _ in range(4):               # robuust: punten die niet passen (andere versie, volgorde) eruit
            coef, *_ = np.linalg.lstsq(A[keep], B[keep], rcond=None)
            resid = np.sqrt(((A @ coef - B) ** 2).sum(axis=1))
            keep = resid < max(0.05, 3.0 * float(np.median(resid[keep])))
        resid = resid[keep]
        src = [p for p, k in zip(src, keep) if k]
        self.rd_coef = coef
        self.rd_to_xy = lambda x, y: (coef[0, 0] * x + coef[1, 0] * y + coef[2, 0],
                                      coef[0, 1] * x + coef[1, 1] * y + coef[2, 1])
        inv = np.linalg.inv(np.array([[coef[0, 0], coef[1, 0]], [coef[0, 1], coef[1, 1]]]))
        self.xy_to_rd = lambda x, y: tuple(inv @ np.array([x - coef[2, 0], y - coef[2, 1]]))
        self.log(f"RD gekoppeld met {len(src)} punten, rest-afwijking max {resid.max() * 1000:.1f} mm")
        return float(resid.max())

    # ------------------------------------------------------------------ AHN
    def terrain(self, cell: float = 1.0) -> Terrain:
        x0, y0, x1, y1 = self.xy_bbox
        rd = [self.xy_to_rd(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        rx, ry = [p[0] for p in rd], [p[1] for p in rd]
        bbox = (min(rx) - 2, min(ry) - 2, max(rx) + 2, max(ry) + 2)
        self.log(f"AHN DTM 0,5 m ophalen: {bbox[2] - bbox[0]:.0f} x {bbox[3] - bbox[1]:.0f} m")
        z, xl, yt, c = ahn.fetch_dtm(bbox, fetch, os.path.join(self.cache, "ahn"))
        self.ahn_nodata = float(np.isnan(z).mean())
        z = ahn.fill_nodata(z)
        nx = int(math.ceil((x1 - x0) / cell)) + 1
        ny = int(math.ceil((y1 - y0) / cell)) + 1
        gx, gy = np.meshgrid(x0 + np.arange(nx) * cell, y0 + np.arange(ny) * cell)
        inv = np.linalg.inv(np.array([[self.rd_coef[0, 0], self.rd_coef[1, 0]], [self.rd_coef[0, 1], self.rd_coef[1, 1]]]))
        dx, dy = gx - self.rd_coef[2, 0], gy - self.rd_coef[2, 1]
        rx = inv[0, 0] * dx + inv[0, 1] * dy
        ry = inv[1, 0] * dx + inv[1, 1] * dy
        # bilineair in het RD-raster (rij 0 = noord)
        fc, fr = (rx - xl) / c - 0.5, (yt - ry) / c - 0.5
        c0 = np.clip(np.floor(fc).astype(int), 0, z.shape[1] - 2)
        r0 = np.clip(np.floor(fr).astype(int), 0, z.shape[0] - 2)
        tc, tr = np.clip(fc - c0, 0, 1), np.clip(fr - r0, 0, 1)
        a = z[r0, c0] + (z[r0, c0 + 1] - z[r0, c0]) * tc
        b = z[r0 + 1, c0] + (z[r0 + 1, c0 + 1] - z[r0 + 1, c0]) * tc
        out = a + (b - a) * tr
        self.log(f"reliëf: {nx} x {ny} cellen van {cell} m, NAP {out.min():.2f} tot {out.max():.2f} m, "
                 f"{self.ahn_nodata * 100:.0f} % opgevuld (water, gebouwen)")
        return Terrain(x0, y0, cell, out)

    # ------------------------------------------------------------------ BGT
    def surfaces(self) -> list[Surface]:
        out: list[tuple[int, Surface]] = []
        for coll in ("begroeidterreindeel", "onbegroeidterreindeel", "ondersteunendwegdeel", "wegdeel", "waterdeel"):
            feats = fetch_features(BGT.format(c=coll, bbox=self.ll_bbox), self.cache)
            n = 0
            for g in feats:
                p = g["properties"]
                if p.get("eind_registratie") or p.get("relatieve_hoogteligging", 0) not in (0, None):
                    continue
                key = p.get("functie") if coll in ("wegdeel",) else p.get("fysiek_voorkomen")
                kind = BGT_KIND.get((coll, key)) or BGT_KIND.get((coll, "*"))
                if kind is None:
                    continue
                for ring in _outer_rings(g["geometry"]):
                    xy = self._xy(ring)
                    if len(xy) >= 3 and self._in_area(xy) and abs(polygon_area(xy)) > 2.0:
                        out.append((SURFACE_ORDER.index(kind), Surface(kind, self._ll(xy), key or coll)))
                        n += 1
            self.log(f"BGT {coll}: {n} vlakken")
        out.sort(key=lambda t: t[0])
        return [s for _, s in out]

    # ------------------------------------------------------------------ BRP
    def fields(self) -> list[Field]:
        feats = fetch_features(BRP.format(bbox=self.ll_bbox), self.cache)
        out = []
        for g in feats:
            p = g["properties"]
            if p.get("category") not in FIELD_CATEGORIES:
                continue
            for ring in _outer_rings(g["geometry"]):
                xy = self._xy(ring)
                if len(xy) < 3 or abs(polygon_area(xy)) < 500 or not self._center_in_area(xy):
                    continue
                n = len(out) + 1
                out.append(Field(f"perceel_{n}", f"{p.get('gewas', 'perceel')} {n}", self._ll(xy),
                                 p.get("gewas", "")))
        self.log(f"BRP: {len(out)} percelen (bouwland en grasland)")
        return out

    def _center_in_area(self, xy) -> bool:
        cx = sum(p[0] for p in xy) / len(xy)
        cy = sum(p[1] for p in xy) / len(xy)
        return point_in_polygon(cx, cy, self.world.area_xy)

    # ------------------------------------------------------------------ gebouwen
    def buildings(self) -> list[Building]:
        feats = fetch_features(BGT.format(c="pand", bbox=self.ll_bbox), self.cache)
        heights = self._bag3d_heights()
        out = []
        for g in feats:
            p = g["properties"]
            if p.get("eind_registratie"):
                continue
            rings = _outer_rings(g["geometry"])
            if not rings:
                continue
            xy = max((self._xy(r) for r in rings), key=lambda r: abs(polygon_area(r)))
            if not self._in_area(xy) or abs(polygon_area(xy)) < 4.0:
                continue
            h = heights.get(str(p.get("bag_pnd") or "").zfill(16))
            out.append(self._building(len(out) + 1, xy, h))
        self.log(f"gebouwen: {len(out)} ({len(heights)} hoogtes uit 3D BAG in dit gebied)")
        return out

    def _bag3d_heights(self) -> dict:
        x0, y0, x1, y1 = self.xy_bbox
        rd = [self.xy_to_rd(x, y) for x, y in ((x0, y0), (x1, y1), (x1, y0), (x0, y1))]
        bbox = f"{min(p[0] for p in rd):.0f},{min(p[1] for p in rd):.0f},{max(p[0] for p in rd):.0f},{max(p[1] for p in rd):.0f}"
        out = {}
        url = BAG3D.format(bbox=bbox)
        try:
            for _ in range(40):
                page = fetch_json(url, os.path.join(self.cache, "3dbag"))
                for feat in page.get("features", []):
                    for oid, obj in feat.get("CityObjects", {}).items():
                        a = obj.get("attributes", {})
                        if "b3_h_maaiveld" in a and oid.startswith("NL.IMBAG.Pand."):
                            out[oid.rsplit(".", 1)[-1]] = a
                nxt = [link["href"] for link in page.get("links", []) if link.get("rel") == "next"]
                if not nxt:
                    break
                url = nxt[0]
        except RuntimeError as e:
            self.log(f"3D BAG niet bereikbaar ({e}); gebouwen krijgen een standaardhoogte")
        return out

    def _building(self, n, xy, a) -> Building:
        ground = a.get("b3_h_maaiveld") if a else None
        top_min = (a.get("b3_h_dak_min") or a.get("b3_h_dak_50p")) if a else None
        top_max = (a.get("b3_h_dak_max") or a.get("b3_h_dak_70p") or top_min) if a else None
        if ground is not None and top_min is not None:
            slanted = a.get("b3_dak_type") in ("slanted", "multiple horizontal")
        else:
            # Geen hoogte in 3D BAG (nieuw, of te klein): 4 m plat, zoals een schuur
            ground, top_min, top_max, slanted = 0.0, 4.0, 4.0, False
        eaves = max(2.0, float(top_min) - float(ground))
        ridge = max(eaves, float(top_max) - float(ground))
        rect, fill = _min_area_rect(xy)
        kind = "house" if abs(polygon_area(xy)) < 220 else "barn"
        if slanted and ridge - eaves > 1.0 and fill > 0.88:
            # Zadeldak op de omhullende rechthoek: lekker simpel en herkenbaar
            return Building(f"pand_{n}", f"pand {n}", kind, self._ll(rect), round(eaves, 2),
                            Roof("gable", round(ridge, 2), "long"))
        h = (eaves + ridge) / 2.0 if slanted else ridge
        return Building(f"pand_{n}", f"pand {n}", kind, self._ll(_simplify(xy, 0.3)), round(h, 2), Roof("flat"))

    # ------------------------------------------------------------------ alles
    def build(self, cell: float = 1.0) -> World:
        w = self.world
        self.fit_rd()
        terrain = self.terrain(cell)
        surfaces = self.surfaces()
        fields = self.fields()
        buildings = self.buildings()
        terrain = self._carve_water(terrain, surfaces)
        terrain.save(os.path.join(self.folder, "terrain.npz"))
        today = datetime.date.today().isoformat()
        w.terrain = {"type": "grid", "file": "terrain.npz", "cell_m": cell, "source": "AHN DTM 0,5 m (PDOK)"}
        w.surfaces, w.fields, w.buildings = surfaces, fields, buildings
        w.trees = []
        w.sources.update({k: {"source": v, "downloaded": today} for k, v in (
            ("terrain", "AHN DTM 0,5 m (PDOK WCS)"), ("surfaces", "BGT (PDOK OGC API)"),
            ("fields", "BRP gewaspercelen (PDOK OGC API)"), ("buildings", "BGT pand + 3D BAG (api.3dbag.nl)"))})
        if w.start_pose is None or w.sources.get("start_pose") == "auto":
            pose = self._start_pose(w, terrain)
            if pose is not None:
                w.start_pose = pose
                w.sources["start_pose"] = "auto"
        w.save(self.folder)
        self.log(f"klaar: {len(fields)} percelen, {len(surfaces)} vlakken, {len(buildings)} gebouwen")
        return w

    def _start_pose(self, w: World, terrain: Terrain):
        """Startplek op het grootste erf: het punt dat het verst van de rand ligt, op vlak terrein, niet bij water."""
        from openagbot_fieldsim.world.geometry import distance_to_segment
        from openagbot_fieldsim.world.model import Pose
        w.__post_init__()                     # xy van de nieuwe objecten
        # Het gebied is rond het eigen bedrijf gekozen: begin bij het erf dat het dichtst bij het midden ligt
        ax_, ay_ = sum(p[0] for p in w.area_xy) / len(w.area_xy), sum(p[1] for p in w.area_xy) / len(w.area_xy)

        def dist(sf):
            return math.hypot(sum(p[0] for p in sf.xy) / len(sf.xy) - ax_, sum(p[1] for p in sf.xy) / len(sf.xy) - ay_)

        yards = sorted((sf for sf in w.surfaces if sf.kind == "yard" and abs(polygon_area(sf.xy)) > 300), key=dist)
        for yard in yards[:8]:
            xy = yard.xy
            x0, y0, x1, y1 = _bbox(xy)
            best = None
            for gx in np.arange(x0, x1, 2.0):
                for gy in np.arange(y0, y1, 2.0):
                    if not point_in_polygon(gx, gy, xy) or w.surface_at(gx, gy) != "yard":
                        continue
                    d = min(distance_to_segment(gx, gy, a, b) for a, b in zip(xy, xy[1:] + xy[:1]))
                    slope = abs(terrain.height(gx + 1.5, gy) - terrain.height(gx - 1.5, gy)) +                         abs(terrain.height(gx, gy + 1.5) - terrain.height(gx, gy - 1.5))
                    if d > 2.5 and slope < 0.15 and (best is None or d > best[0]):
                        best = (d, gx, gy)
            if best:
                i, _ = __import__("openagbot_fieldsim.world.geometry", fromlist=["longest_edge"]).longest_edge(xy)
                (ax, ay), (bx, by) = xy[i], xy[(i + 1) % len(xy)]
                lat, lon = w.frame.to_latlon(best[1], best[2])
                return Pose(lat, lon, round(math.degrees(math.atan2(bx - ax, by - ay)) % 360.0, 1))
        return None

    def _carve_water(self, terrain: Terrain, surfaces) -> Terrain:
        """Water ligt in AHN als 'geen data'; graaf het uit tot onder het maaiveld langs de rand."""
        z = terrain.z.astype(np.float64)
        nx, ny, c = terrain.nx, terrain.ny, terrain.cell
        gx, gy = np.meshgrid(terrain.x0 + np.arange(nx) * c, terrain.y0 + np.arange(ny) * c)
        for s in surfaces:
            if s.kind != "water":
                continue
            xy = ccw([self.world.frame.to_xy(*p) for p in s.polygon])
            xs, ys = [p[0] for p in xy], [p[1] for p in xy]
            sel = (gx >= min(xs) - c) & (gx <= max(xs) + c) & (gy >= min(ys) - c) & (gy <= max(ys) + c)
            if not sel.any():
                continue
            inside = points_in_polygon(gx[sel], gy[sel], xy)
            rim = np.array([terrain.height(x, y) for x, y in xy])
            edge = float(np.median(rim)) if len(rim) else 0.0
            zs = z[sel]
            zs[inside] = np.minimum(zs[inside], edge - WATER_DEPTH_M)
            z[sel] = zs
            s.level_m = round(edge - WATER_LEVEL_M, 3)
        return Terrain(terrain.x0, terrain.y0, c, z)


# ---------------------------------------------------------------------- geometrie
def _bbox(xy):
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return min(xs), min(ys), max(xs), max(ys)


def _rings(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return list(c)
    if t == "MultiPolygon":
        return [r for poly in c for r in poly]
    return []


def _outer_rings(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return [c[0]] if c else []
    if t == "MultiPolygon":
        return [poly[0] for poly in c if poly]
    return []


def _simplify(xy, tol):
    """Douglas-Peucker; houdt gebouwen simpel."""
    if len(xy) <= 4:
        return xy
    pts = list(xy)
    if pts[0] == pts[-1]:
        pts = pts[:-1]

    def dp(seq):
        if len(seq) < 3:
            return seq
        (ax, ay), (bx, by) = seq[0], seq[-1]
        dx, dy = bx - ax, by - ay
        ll = math.hypot(dx, dy) or 1e-9
        dists = [abs(dy * (x - ax) - dx * (y - ay)) / ll for x, y in seq[1:-1]]
        i = max(range(len(dists)), key=dists.__getitem__)
        if dists[i] > tol:
            return dp(seq[:i + 2])[:-1] + dp(seq[i + 1:])
        return [seq[0], seq[-1]]

    half = len(pts) // 2
    out = dp(pts[:half + 1])[:-1] + dp(pts[half:] + [pts[0]])[:-1]
    return out if len(out) >= 3 else pts


def _hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _min_area_rect(xy):
    """Kleinste omhullende rechthoek en hoe goed de footprint hem vult (1 = precies een rechthoek)."""
    hull = _hull([(round(x, 3), round(y, 3)) for x, y in xy])
    best = None
    for i in range(len(hull)):
        (ax, ay), (bx, by) = hull[i], hull[(i + 1) % len(hull)]
        a = math.atan2(by - ay, bx - ax)
        c, s = math.cos(a), math.sin(a)
        us = [x * c + y * s for x, y in hull]
        vs = [-x * s + y * c for x, y in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            best = (area, a, min(us), max(us), min(vs), max(vs))
    area, a, u0, u1, v0, v1 = best
    c, s = math.cos(a), math.sin(a)
    rect = [(u * c - v * s, u * s + v * c) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    return rect, abs(polygon_area(xy)) / area if area > 0 else 0.0


def main(argv=None):
    from openagbot_fieldsim.settings import load_settings
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return
    folder = args[0] if os.path.isdir(args[0]) else os.path.join(load_settings().worlds_dir, args[0])
    Builder(folder).build()


if __name__ == "__main__":
    main()
