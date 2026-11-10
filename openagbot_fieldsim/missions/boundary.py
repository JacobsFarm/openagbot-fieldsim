"""Perceelrand bekijken: wat ligt er naast het veld (sloot, weg, hek, gebouw, buurperceel) en hoe ver moet de robot
daar uit blijven.

De BRP-grens (RVO, door de boer zelf ingetekend op de luchtfoto) ligt bij een sloot op de insteek en langs een weg
op de rand van de berm. Het gebied waar de robot met werktuig mag komen is:

    toegestaan = perceel (min `border_min_m`) - elk obstakel met zijn eigen vrije ruimte eromheen

Dus niet 'zoveel meter uit de lijn' maar 'zoveel meter van het water / de weg / het hek af': ligt de sloot 3 m
buiten de lijn, dan kost dat geen bewerkte grond; is de BRP-lijn over de slootkant getekend, dan schuift de grens
vanzelf naar binnen. Afstanden gelden voor de buitenkant van robot of werktuig (wat breder is).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

# Vrije ruimte tot het obstakel (m), standaard. Aan te passen per missie.
DEFAULT_CLEARANCE = {
    "water": 1.0,        # vanaf de insteek (BGT waterdeel / sloot): wielen niet op het talud
    "weg": 1.0,          # rijbaan
    "verhard": 0.3,      # erf, pad, inrit: berijdbaar, maar daar werken we niet
    "hek": 0.6,          # afrastering, muur, damwand
    "gebouw": 1.5,
    "boom": 1.5,         # vanaf de stam (een boom kan een eigen waarde hebben)
    "dijk": 0.5,         # vanaf de teen van de dijk
    "verboden": 0.5,     # verboden gebied en verboden rijzone
    "nest": 0.0,         # de straal van het nest is de vrije ruimte
    "gat": 0.3,          # gat in het veld (niet doorrijdbaar)
    "marker": 0.5,       # steen, paal, put: vanaf de rand van de marker
}
# BGT-soort in de wereld -> klasse hierboven
SURFACE_CLASS = {"water": "water", "road": "weg", "paved": "verhard", "yard": "verhard", "track": "verhard"}
SCHEIDING_CLASS = {"hek": "hek", "muur": "hek", "geluidsscherm": "hek", "damwand": "water",
                   "walbescherming": "water", "kademuur": "water"}
KIND_LABEL = {"water": "sloot / water", "weg": "weg", "verhard": "erf / pad", "hek": "hek / afrastering",
              "gebouw": "gebouw", "boom": "boom", "perceel": "buurperceel", "open": "open (berm, grasland)",
              "dijk": "dijk", "verboden": "verboden gebied", "nest": "vogelnest", "gat": "gat in het veld",
              "marker": "steen / paal / put"}


@dataclass
class Obstacle:
    kind: str
    geom: object          # shapely-geometrie in lokale meters
    name: str = ""
    id: str = ""
    clearance: float | None = None      # eigen vrije ruimte; None = de waarde van de soort


DITCH_ZONE_M = 2.0        # standaard slootzone: verboden strook langs sloot en water
PLAN_MARGIN_M = 0.3       # de planner blijft daar nog zoveel buiten


def ditch_zone(world) -> tuple[float, float]:
    """(breedte van de slootzone, marge van de planner) uit de boerderij-instellingen."""
    s = (getattr(world, "farm_settings", None) or {}).get("safety") or {}
    zone = s.get("ditch_zone_m")
    margin = s.get("plan_margin_m")
    return (DITCH_ZONE_M if zone is None else max(0.0, float(zone)),
            PLAN_MARGIN_M if margin is None else max(0.0, float(margin)))


def effective_clearance(world, overrides: dict | None = None) -> dict:
    """Standaard < instelling van de boerderij (farm.json) < wat de missie opgeeft.

    Voor water geldt een ondergrens: de slootzone plus de planmarge. De robot stopt zodra hij in de slootzone komt,
    dus een missie mag er nooit doorheen gepland worden."""
    clr = dict(DEFAULT_CLEARANCE)
    for src in ((getattr(world, "farm_settings", None) or {}).get("clearance"), overrides):
        for k, v in (src or {}).items():
            if v is not None:
                clr[k] = float(v)
    zone, margin = ditch_zone(world)
    clr["water"] = max(clr["water"], zone + margin)
    return clr


def dike_mode(world, dike, mission_mode: str | None = None) -> str:
    """Modus van een dijk: de missie gaat voor de boerderij-instelling, die gaat voor de dijk zelf."""
    return mission_mode or (getattr(world, "farm_settings", None) or {}).get("dike_mode") or dike.mode


def _poly(xy):
    g = Polygon(xy)
    return g if g.is_valid else g.buffer(0)


@dataclass
class EdgePiece:
    kind: str             # klasse uit DEFAULT_CLEARANCE, of 'perceel' / 'open'
    pts: list             # [(x, y), ...] stuk van de perceelrand
    length_m: float
    gap_m: float          # gemiddelde afstand van de lijn tot het obstakel (negatief = obstakel ligt erin)
    setback_m: float      # gemiddelde afstand van de lijn tot de grens van het toegestane gebied

    def to_dict(self):
        return {"kind": self.kind, "label": KIND_LABEL.get(self.kind, self.kind),
                "pts": [[round(x, 3), round(y, 3)] for x, y in self.pts], "length_m": round(self.length_m, 1),
                "gap_m": None if self.gap_m is None else round(self.gap_m, 2), "setback_m": round(self.setback_m, 2)}


@dataclass
class FieldArea:
    field_id: str
    field_poly: Polygon
    allowed: Polygon                     # grootste deel; daar wordt gepland
    no_work: object = None               # binnen `allowed` wel rijden maar niet werken (doorrijdbaar gat, rijzone)
    full: object = None                  # waar de robot mag RIJDEN: `allowed` plus de niet-bewerken-zones
    pieces: list[EdgePiece] = field(default_factory=list)
    dropped_m2: float = 0.0              # stukjes toegestaan gebied die los liggen (te smal om te bereiken)
    warnings: list[str] = field(default_factory=list)


def world_obstacles(world, near: Polygon | None = None, reach_m: float = 30.0, date=None,
                    mission_dike_mode: str | None = None) -> list[Obstacle]:
    """Alle obstakels uit de wereld (in lokale meters), alleen die binnen `reach_m` van `near` liggen.

    `date`: nesten tellen alleen in hun periode (None = vandaag). Dijken tellen volgens hun modus: `negeren` is
    geen obstakel, `ontwijken` en `doorgang` wel (bij `doorgang` mag de route er bij een doorgang overheen).
    """
    zone = near.buffer(reach_m) if near is not None else None
    out: list[Obstacle] = []

    def add(kind, geom, name="", oid="", clearance=None):
        if geom.is_empty:
            return
        if zone is None or geom.intersects(zone):
            out.append(Obstacle(kind, geom, name, oid, clearance))

    for s in world.surfaces:
        k = SURFACE_CLASS.get(s.kind)
        if k and len(s.xy) >= 3:
            add(k, _poly(s.xy), s.name, s.id)
    for d in world.ditches:
        if len(d.xy) >= 2:
            add("water", LineString(d.xy).buffer(d.width_m / 2.0, cap_style="flat"), d.name or "sloot", d.id)
    for f in world.fences:
        if len(f.xy) >= 2:
            add("hek", LineString(f.xy), "hek", f.id)
    for d in world.dikes:
        if len(d.xy) >= 2 and dike_mode(world, d, mission_dike_mode) != "negeren":
            add("dijk", LineString(d.xy).buffer(d.half_base_m, cap_style="flat"), d.name or "dijk", d.id)
    for g in world.no_go:
        if len(g.xy) >= 3:
            add("verboden", _poly(g.xy), g.name or "verboden gebied", g.id)
    for z in world.zones:
        if z.klass == "verboden" and len(z.xy) >= 3:
            add("verboden", _poly(z.xy), z.name or "verboden rijzone", z.id)
    for n in world.nests:
        if n.active(date):
            add("nest", Point(n.xy).buffer(max(0.1, n.radius_m)), n.name or "vogelnest", n.id)
    for m in world.markers:
        if m.radius_m > 0:
            add("marker", Point(m.xy).buffer(m.radius_m), m.name or m.kind, m.id)
    for sch in world.extra.get("scheidingen", []):
        k = SCHEIDING_CLASS.get(sch.get("type"))
        if k and k != "hek":       # hekken staan al in world.fences
            xy = [world.frame.to_xy(*p) for p in sch["line"]]
            if len(xy) >= 2:
                add(k, LineString(xy), sch.get("type", ""))
    for b in world.buildings:
        if len(b.xy) >= 3:
            add("gebouw", _poly(b.xy), b.name, b.id)
    for t in world.trees:
        add("boom", Point(t.xy).buffer(0.3), "boom", t.id, t.clearance_m)
    return out


def field_area(world, field_id: str, clearance: dict | None = None, border_min_m: float = 0.2,
               probe_m: float = 5.0, piece_m: float = 1.0, date=None, mission_dike_mode: str | None = None) -> FieldArea:
    """Toegestaan werkgebied van een veld en de randen met wat er naast ligt."""
    clr = effective_clearance(world, clearance)
    fld = world.field_by_id(field_id)
    poly = Polygon(fld.xy)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if isinstance(poly, MultiPolygon):          # vlinderdas in de grens: het grootste stuk is het veld
        poly = max(poly.geoms, key=lambda g: g.area)
    obstacles = world_obstacles(world, poly, date=date, mission_dike_mode=mission_dike_mode)
    no_work = []
    for h in fld.holes:                          # gaten in het veld (AgOpenGPS: binnengrenzen)
        if len(h.xy) < 3:
            continue
        if h.passable:
            no_work.append(_poly(h.xy))
        else:
            obstacles.append(Obstacle("gat", _poly(h.xy), h.name or "gat"))
    drive_only = [_poly(z.xy) for z in world.zones       # alleen rijden: wel doorheen, niet werken
                  if z.klass == "alleen_rijden" and len(z.xy) >= 3 and _poly(z.xy).intersects(poly)]

    def room(o):
        return o.clearance if o.clearance is not None else clr.get(o.kind, 0.0)

    keep_out = [o.geom.buffer(room(o), join_style="round") for o in obstacles if room(o) > 0 or o.geom.area > 0]
    allowed = poly.buffer(-border_min_m, join_style="mitre")
    if keep_out:
        allowed = allowed.difference(unary_union(keep_out))
    warnings = []
    dropped = 0.0
    if isinstance(allowed, MultiPolygon):
        parts = sorted(allowed.geoms, key=lambda g: g.area, reverse=True)
        allowed = parts[0]
        dropped = sum(g.area for g in parts[1:])
        if dropped > 50:
            warnings.append(f"{dropped:.0f} m2 toegestaan gebied ligt los van het grootste deel en wordt niet gepland")
    if allowed.is_empty:
        raise ValueError(f"veld {fld.name}: na de vrije ruimte langs de randen blijft niets over")
    # orient: buitenrand tegen de klok in
    allowed = Polygon(_ccw(list(allowed.exterior.coords)[:-1]), [list(r.coords) for r in allowed.interiors])

    # Andere velden als buren
    others = [Polygon(f.xy).buffer(0) for f in world.fields if f.id != field_id and len(f.xy) >= 3]
    other_tree = STRtree(others) if others else None
    obs_tree = STRtree([o.geom for o in obstacles]) if obstacles else None

    ring = LineString(list(poly.exterior.coords))
    n = max(4, int(math.ceil(ring.length / piece_m)))
    samples = []
    for i in range(n):
        s0, s1 = ring.length * i / n, ring.length * (i + 1) / n
        a, b = ring.interpolate(s0), ring.interpolate(s1)
        m = ring.interpolate((s0 + s1) / 2)
        # normaal naar buiten: veld ligt links van de rand (ccw), buiten = rechts
        dx, dy = b.x - a.x, b.y - a.y
        ll = math.hypot(dx, dy) or 1.0
        out_pt = Point(m.x + dy / ll * 0.3, m.y - dx / ll * 0.3)
        if poly.contains(out_pt):           # rand loopt kennelijk andersom
            out_pt = Point(m.x - dy / ll * 0.3, m.y + dx / ll * 0.3)
        kind, gap = "open", None
        best = None
        if obs_tree is not None:
            for j in obs_tree.query(m.buffer(probe_m)):
                o = obstacles[int(j)]
                d = o.geom.distance(m)
                if o.geom.contains(m) or o.geom.intersects(m.buffer(0.05)):
                    d = -o.geom.exterior.distance(m) if hasattr(o.geom, "exterior") else 0.0
                score = d - room(o)                     # het obstakel dat de grens het meest naar binnen duwt
                if d <= probe_m and (best is None or score < best[0]):
                    best = (score, o.kind, d)
        if best is not None:
            kind, gap = best[1], best[2]
        elif other_tree is not None and any(others[int(j)].distance(out_pt) < 1.5
                                            for j in other_tree.query(out_pt.buffer(1.5))):
            kind = "perceel"
        setback = allowed.exterior.distance(m) if not allowed.contains(m) else 0.0
        samples.append((kind, (a.x, a.y), (b.x, b.y), gap, setback))

    pieces: list[EdgePiece] = []
    for kind, a, b, gap, sb in samples:
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if pieces and pieces[-1].kind == kind:
            p = pieces[-1]
            w0 = p.length_m
            p.pts.append(b)
            p.length_m += seg
            if gap is not None:
                p.gap_m = gap if p.gap_m is None else (p.gap_m * w0 + gap * seg) / p.length_m
            p.setback_m = (p.setback_m * w0 + sb * seg) / p.length_m
        else:
            pieces.append(EdgePiece(kind, [a, b], seg, gap, sb))
    if len(pieces) > 1 and pieces[0].kind == pieces[-1].kind:     # rond: eerste en laatste stuk samen
        last = pieces.pop()
        first = pieces[0]
        tot = first.length_m + last.length_m
        first.setback_m = (first.setback_m * first.length_m + last.setback_m * last.length_m) / tot
        if last.gap_m is not None or first.gap_m is not None:
            g0 = first.gap_m if first.gap_m is not None else last.gap_m
            g1 = last.gap_m if last.gap_m is not None else first.gap_m
            first.gap_m = (g0 * first.length_m + g1 * last.length_m) / tot
        first.pts = last.pts[:-1] + first.pts
        first.length_m = tot
    # Niet-bewerken-zones: de missie slaat ze over (rondjes en banen volgen de rest van het veld), maar de robot mag
    # er rijden en keren. `allowed` wordt het werkgebied, `full` blijft het rijgebied.
    full = allowed
    skip = [_poly(z.xy) for z in world.zones if z.klass == "niet_bewerken" and len(z.xy) >= 3
            and _poly(z.xy).intersects(allowed)]
    # Een rijzone (alleen rijden) in een hoek of langs de rand haalt de planner ook uit het werkgebied: de rondjes
    # volgen dan de rand van de zone (een afgezette hoek wordt een ruime bocht) in plaats van er met het werktuig
    # omhoog doorheen te slingeren. Een rijzone die het veld in stukken zou knippen (pad dwars door het veld) blijft
    # een stuk waar de banen doorheen lopen met het werktuig omhoog.
    for zp in drive_only:
        if not zp.intersects(allowed):
            continue
        rest = allowed.difference(zp)
        pieces_ = sorted((g.area for g in getattr(rest, "geoms", [rest]) if isinstance(g, Polygon)), reverse=True)
        if len(pieces_) > 1 and sum(pieces_[1:]) > 50:
            no_work.append(zp)
        else:
            skip.append(zp)
    if skip:
        work = allowed.difference(unary_union(skip))
        parts = sorted((g for g in getattr(work, "geoms", [work]) if isinstance(g, Polygon) and not g.is_empty),
                       key=lambda g: g.area, reverse=True)
        if not parts:
            raise ValueError(f"veld {fld.name}: het hele veld ligt in een niet-bewerken-zone")
        if len(parts) > 1 and sum(g.area for g in parts[1:]) > 50:
            warnings.append(f"{sum(g.area for g in parts[1:]):.0f} m2 ligt achter een niet-bewerken-zone los van het "
                            "grootste deel en wordt niet gepland")
        allowed = Polygon(_ccw(list(parts[0].exterior.coords)[:-1]), [list(r.coords) for r in parts[0].interiors])
    nw = unary_union(no_work).intersection(allowed) if no_work else None
    return FieldArea(field_id=field_id, field_poly=poly, allowed=allowed,
                     no_work=nw if nw is not None and not nw.is_empty else None, full=full, pieces=pieces,
                     dropped_m2=dropped, warnings=warnings)


def _ccw(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return pts if s >= 0 else pts[::-1]


def edge_summary(area: FieldArea) -> list[dict]:
    """Per klasse: lengte en gemiddelde afstand uit de lijn (voor het rapport)."""
    tot: dict[str, list[float]] = {}
    for p in area.pieces:
        t = tot.setdefault(p.kind, [0.0, 0.0])
        t[0] += p.length_m
        t[1] += p.setback_m * p.length_m
    return [{"kind": k, "label": KIND_LABEL.get(k, k), "length_m": round(v[0], 1),
             "setback_m": round(v[1] / v[0], 2) if v[0] else 0.0} for k, v in sorted(tot.items(), key=lambda t: -t[1][0])]
