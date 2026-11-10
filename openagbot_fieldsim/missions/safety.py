"""Veiligheid: waar mag de robot nooit komen, en twee controles daarop.

  1. Vooraf (`check_path`, `precheck_segments`): elke geplande route wordt met de breedte van robot en werktuig
     tegen alle gevaren gelegd. Een stuk dat een sloot, verboden gebied, nest, dijk, gebouw of weg raakt wordt
     afgekeurd (rood in de UI) en de sim weigert zo'n missie te rijden.
  2. Tijdens het rijden (`Geofence`): 20 keer per seconde worden de hoeken van robot en werktuig getest. Komt er
     een in een stop-gebied (ook de slootzone), dan pauzeert de sim, krijgt de
     sim-robot een stop en komt er een melding. Een `mijden`-gebied geeft alleen een waarschuwing.

Gevaren (`hazards`):
  stop    sloot en water met de slootzone eromheen (boerderij-instelling, standaard 2 m), verboden gebied `stoppen`, verboden rijzone, nest in zijn
          periode, dijk (modus ontwijken/doorgang), gebouw, rijbaan van een weg, buiten het wereldgebied
  mijden  verboden gebied `mijden`, hek, boom, marker, niet-doorrijdbaar gat in een veld

Bij een doorgang (dam, duiker, brug, hek, oversteek) is het gevaar dat hij kruist over de breedte van de doorgang
weggehaald: dat is de enige plek waar de robot een sloot, dijk, hek of weg over mag.

Dezelfde gevaren gaan als `geofence` mee in veldmissie.json (`geofence_export`), zodat de echte robot dezelfde
grenzen kan bewaken (zie docs/veldmissie.md in OpenAgbot).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import shapely
from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from openagbot_fieldsim.missions.boundary import DITCH_ZONE_M, KIND_LABEL, PLAN_MARGIN_M, dike_mode, ditch_zone

STOP, AVOID = "stop", "mijden"
# Soorten gevaar waar een doorgang doorheen mag
CROSSABLE = {"water", "dijk", "hek", "weg"}
DEFAULT_SAFETY = {"pause": True, "stop_robot": True, "ditch_zone_m": DITCH_ZONE_M, "plan_margin_m": PLAN_MARGIN_M}


@dataclass
class Hazard:
    kind: str             # water | weg | gebouw | dijk | verboden | nest | hek | boom | marker | gat
    level: str            # stop | mijden
    geom: object          # shapely, lokale meters
    id: str = ""
    name: str = ""

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)


def passage_corridor(p, extra_m: float = 0.0, extend_m: float = 0.0):
    """Vlak van een doorgang: de rijlijn met de breedte van de doorgang (vlakke einden).
    `extend_m` trekt beide einden door (de aanloop door de slootzone naar het veld)."""
    if len(p.xy) < 2:
        return Polygon()
    pts = [(q[0], q[1]) for q in p.xy]
    if extend_m > 0:
        def out(a, b):
            ll = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
            return (b[0] + (b[0] - a[0]) / ll * extend_m, b[1] + (b[1] - a[1]) / ll * extend_m)
        pts = [out(pts[1], pts[0]), *pts, out(pts[-2], pts[-1])]
    return LineString(pts).buffer(max(0.1, p.width_m / 2.0 + extra_m), cap_style="flat", join_style="round")


def safety_settings(world) -> dict:
    return {**DEFAULT_SAFETY, **((getattr(world, "farm_settings", None) or {}).get("safety") or {})}


def hazards(world, date=None, mission_dike_mode: str | None = None) -> list[Hazard]:
    """Alle gevaren van de wereld, met de doorgangen eruit gesneden. Per (datum, dijkmodus) onthouden."""
    key = (str(date), mission_dike_mode)
    cache = world.__dict__.setdefault("_hazard_cache", {})
    if key in cache:
        return cache[key]
    out: list[Hazard] = []

    def poly(xy):
        g = Polygon(xy)
        return g if g.is_valid else g.buffer(0)

    # Sloot en water: het water zelf plus de slootzone eromheen (boerderij-instelling). Wie daarin komt stopt.
    zone = ditch_zone(world)[0]
    for s in world.surfaces:
        if len(s.xy) < 3:
            continue
        if s.kind == "water":
            out.append(Hazard("water", STOP, poly(s.xy).buffer(zone), s.id, s.name or "water"))
        elif s.kind == "road":
            out.append(Hazard("weg", STOP, poly(s.xy), s.id, s.name or "weg"))
    for d in world.ditches:
        if len(d.xy) >= 2:
            out.append(Hazard("water", STOP, LineString(d.xy).buffer(d.width_m / 2.0, cap_style="flat").buffer(zone),
                              d.id, d.name or "sloot"))
    for d in world.dikes:
        if len(d.xy) >= 2 and dike_mode(world, d, mission_dike_mode) != "negeren":
            out.append(Hazard("dijk", STOP, LineString(d.xy).buffer(d.half_base_m, cap_style="flat"), d.id,
                              d.name or "dijk"))
    for g in world.no_go:
        if len(g.xy) >= 3:
            out.append(Hazard("verboden", STOP if g.action == "stoppen" else AVOID, poly(g.xy), g.id,
                              g.name or "verboden gebied"))
    for z in world.zones:
        if z.klass == "verboden" and len(z.xy) >= 3:
            out.append(Hazard("verboden", STOP, poly(z.xy), z.id, z.name or "verboden rijzone"))
    for n in world.nests:
        if n.active(date):
            out.append(Hazard("nest", STOP, Point(n.xy).buffer(max(0.1, n.radius_m)), n.id, n.name or "vogelnest"))
    for b in world.buildings:
        if len(b.xy) >= 3:
            out.append(Hazard("gebouw", STOP, poly(b.xy), b.id, b.name))
    for f in world.fences:
        if len(f.xy) >= 2:
            out.append(Hazard("hek", AVOID, LineString(f.xy).buffer(0.1), f.id, "hek"))
    for t in world.trees:
        out.append(Hazard("boom", AVOID, Point(t.xy).buffer(0.3), t.id, "boom"))
    for m in world.markers:
        if m.radius_m > 0:
            out.append(Hazard("marker", AVOID, Point(m.xy).buffer(m.radius_m), m.id, m.name or m.kind))
    for f in world.fields:
        for i, h in enumerate(f.holes):
            if not h.passable and len(h.xy) >= 3:
                out.append(Hazard("gat", AVOID, poly(h.xy), f"{f.id}_gat{i}", h.name or f"gat in {f.name}"))

    corridors = [passage_corridor(p) for p in world.passages if len(p.xy) >= 2]
    if corridors:
        cut = unary_union(corridors)
        # door de slootzone heen loopt de doorgang verder dan de getekende lijn: de aanloop naar het veld
        cut_water = unary_union([passage_corridor(p, extend_m=zone + 3.0) for p in world.passages if len(p.xy) >= 2])
        kept = []
        for h in out:
            c = cut_water if h.kind == "water" else cut
            if h.kind in CROSSABLE and h.geom.intersects(c):
                g = h.geom.difference(c)
                if g.is_empty:
                    continue
                h = Hazard(h.kind, h.level, g, h.id, h.name)
            kept.append(h)
        out = kept
    out = [h for h in out if not h.geom.is_empty]
    cache[key] = out
    return out


def _tree(world, date, mission_dike_mode):
    key = ("tree", str(date), mission_dike_mode)
    cache = world.__dict__.setdefault("_hazard_cache", {})
    if key not in cache:
        hz = hazards(world, date, mission_dike_mode)
        cache[key] = (hz, STRtree([h.geom for h in hz]) if hz else None)
    return cache[key]


def world_bounds(world):
    """Gebied waarbinnen de robot mag zijn: het wereldgebied plus de marge."""
    return Polygon(world.area_xy).buffer(world.margin_m)


# ---------------------------------------------------------------------------------------------------- vooraf
def check_path(world, pts, half_width_m: float, date=None, mission_dike_mode: str | None = None,
               levels=(STOP,)) -> list[dict]:
    """Route (punten x, y) met de breedte van robot/werktuig tegen de gevaren. Geeft de overtredingen."""
    line_pts = [(p[0], p[1]) for p in pts]
    if len(line_pts) < 2:
        return []
    line = LineString(line_pts)
    if line.length < 1e-6:
        return []
    swept = line.buffer(max(0.05, half_width_m), cap_style="round", join_style="round")
    hz, tree = _tree(world, date, mission_dike_mode)
    out = []
    if tree is not None:
        for j in tree.query(swept, predicate="intersects"):
            h = hz[int(j)]
            if h.level not in levels:
                continue
            hit = swept.intersection(h.geom)
            if hit.is_empty or hit.area < 1e-4:
                continue
            c = hit.representative_point()
            out.append({"kind": h.kind, "label": h.label, "level": h.level, "id": h.id, "name": h.name,
                        "area_m2": round(hit.area, 2), "at": [round(c.x, 2), round(c.y, 2)]})
    bounds = world_bounds(world)
    if not bounds.contains(line):
        c = line.difference(bounds).representative_point()
        out.append({"kind": "buiten", "label": "buiten het wereldgebied", "level": STOP, "id": "", "name": "",
                    "area_m2": 0.0, "at": [round(c.x, 2), round(c.y, 2)]})
    return out


def precheck_segments(world, segments, half_width_m: float, date=None, mission_dike_mode: str | None = None) -> dict:
    """Alle segmenten van een missie controleren; een afgekeurd segment krijgt ok = False (rood in de UI)."""
    violations = []
    for i, sg in enumerate(segments):
        found = check_path(world, sg.pts, half_width_m, date, mission_dike_mode)
        if found:
            sg.ok = False
            sg.meta["reden"] = ", ".join(sorted({f"{v['label']}{' ' + v['name'] if v['name'] else ''}" for v in found}))
            for v in found:
                violations.append({"segment": i, "segment_label": sg.label, **v})
    return {"ok": not violations, "violations": violations, "half_width_m": round(half_width_m, 3)}


def check_waypoints(world, latlon: list, half_width_m: float, date=None) -> dict:
    """Controle van een waypoint-route in lat/lon (bv. een puntenmissie of een bestand voor de robot)."""
    pts = [world.frame.to_xy(float(p[0]), float(p[1])) for p in latlon]
    v = check_path(world, pts, half_width_m, date)
    return {"ok": not v, "violations": v}


# ---------------------------------------------------------------------------------------------------- export
def _ll_ring(world, coords):
    out = []
    for x, y in coords:
        lat, lon = world.frame.to_latlon(x, y)
        out.append([round(lat, 9), round(lon, 9)])
    return out


def geofence_export(world, allowed, segments, half_width_m: float, date=None, mission_dike_mode=None,
                    reach_m: float = 30.0) -> dict:
    """Geofence voor de echte robot: waar hij mag zijn en de stop-gebieden langs de route (lat/lon).

    `toegestaan`: de vlakken waarbinnen robot en werktuig moeten blijven. `verboden`: stop-gebieden binnen
    `reach_m` van de route. `rand_m`: de afstand die de robot zelf moet aanhouden (halve breedte).
    """
    # Zoekgebied: de toegestane vlakken, of anders de (vereenvoudigde) route, met `reach_m` eromheen
    shapes = [a for a in allowed if a is not None and not a.is_empty]
    if not shapes:
        shapes = [LineString([(p[0], p[1]) for p in sg.pts]).simplify(1.0) for sg in segments if len(sg.pts) >= 2]
    near = unary_union([g.buffer(reach_m, quad_segs=2) for g in shapes]) if shapes else None
    forbidden = []
    for h in hazards(world, date, mission_dike_mode):
        if h.level != STOP or (near is not None and not h.geom.intersects(near)):
            continue
        g = h.geom.simplify(0.05)
        for poly in getattr(g, "geoms", [g]):
            if isinstance(poly, Polygon) and not poly.is_empty:
                forbidden.append({"id": h.id, "soort": h.kind, "naam": h.name, "actie": "stoppen",
                                  "polygon": _ll_ring(world, poly.exterior.coords)})
    polys = []
    for a in allowed:
        for poly in getattr(a, "geoms", [a]):
            if isinstance(poly, Polygon) and not poly.is_empty:
                polys.append({"polygon": _ll_ring(world, poly.exterior.coords),
                              "gaten": [_ll_ring(world, r.coords) for r in poly.interiors]})
    return {"toegestaan": polys, "verboden": forbidden, "halve_breedte_m": round(half_width_m, 3),
            "slootzone_m": ditch_zone(world)[0], "datum": str(date) if date else None}


# ---------------------------------------------------------------------------------------------------- tijdens het rijden
class Geofence:
    """Bewaking in de sim. `check(points)` geeft de gebieden waar de robot net in is gekomen."""

    def __init__(self, world, date=None, mission_dike_mode: str | None = None):
        self.world = world
        self.settings = safety_settings(world)
        # sloot en water zitten al met hun slootzone in de gevaren (hazards)
        self.zones: list[Hazard] = list(hazards(world, date, mission_dike_mode))
        self.tree = STRtree([h.geom for h in self.zones]) if self.zones else None
        self.bounds = world_bounds(world)
        shapely.prepare(self.bounds)
        self.inside: set[int] = set()
        self.outside = False
        self.last: dict | None = None
        self.count = 0

    def reset(self):
        """Na het neerzetten van de robot: wat hij nu raakt opnieuw melden."""
        self.inside.clear()
        self.outside = False

    def check(self, points) -> list[dict]:
        """`points`: [(x, y), ...] hoeken van robot en werktuig. Nieuwe overtredingen sinds de vorige keer."""
        new: list[dict] = []
        hit: set[int] = set()
        if self.tree is not None and points:
            geoms = shapely.points(points)
            _, idx = self.tree.query(geoms, predicate="intersects")
            hit = {int(i) for i in idx}
        for i in sorted(hit - self.inside):
            h = self.zones[i]
            new.append({"kind": h.kind, "label": h.label, "level": h.level, "id": h.id, "name": h.name})
        self.inside = hit
        out_now = bool(points) and not all(self.bounds.contains(Point(p)) for p in points)
        if out_now and not self.outside:
            new.append({"kind": "buiten", "label": "buiten het wereldgebied", "level": STOP, "id": "", "name": ""})
        self.outside = out_now
        if new:
            self.count += len(new)
            self.last = new[-1]
        return new

    def state(self) -> dict:
        return {"inside": [{"kind": self.zones[i].kind, "level": self.zones[i].level, "id": self.zones[i].id,
                            "name": self.zones[i].name} for i in sorted(self.inside)],
                "outside": self.outside, "last": self.last, "count": self.count}


def footprint_points(x: float, y: float, heading_deg: float, wheelbase: float, track: float,
                     implement=None) -> list[tuple[float, float]]:
    """Hoeken van de robot (wielen plus 15 cm) en van het werktuig, in wereldcoördinaten.

    Robotframe: oorsprong midden achteras, vooruit = rijrichting, rechts = positief.
    """
    h = math.radians(heading_deg)
    fx, fy = math.sin(h), math.cos(h)
    rx, ry = fy, -fx
    half = track / 2.0 + 0.15
    local = [(-0.25, -half), (-0.25, half), (wheelbase + 0.25, -half), (wheelbase + 0.25, half),
             (wheelbase / 2.0, -half), (wheelbase / 2.0, half)]
    if implement is not None:
        for x0, x1, fwd in getattr(implement, "sections", []):
            local += [(fwd, x0), (fwd, x1), (fwd, (x0 + x1) / 2.0)]
    return [(x + fx * f + rx * r, y + fy * f + ry * r) for f, r in local]
