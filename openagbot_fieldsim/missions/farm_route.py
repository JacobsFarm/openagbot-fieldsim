"""Van veld naar veld: routegraaf van de boerderij en de hele-boerderij-missie.

De robot mag op drie soorten plekken rijden:

  - in een veld: het rijgebied van de veldmissie (perceel min de vrije ruimte langs sloot, weg, hek, ... en min de
    halve breedte van robot/werktuig);
  - in een rijgebied: erf, verharding en kavelpad uit de kaart, plus de rijzones `werken` en `alleen_rijden` die je
    zelf tekent (ook min obstakels en de halve breedte);
  - door een doorgang (dam, duiker, brug, hek, oversteek, oprit): de rijlijn die je tekent.

  - over een rijpad (doorgang van de soort `rijpad`, zie missions/lanes.py): een vaste lijn met een zone eromheen.
    Zodra er rijpaden getekend zijn rijdt hij buiten de velden ALLEEN nog over rijpaden en doorgangen (`strict`);
    het vrije rijden over erf en kavelpad vervalt dan. Boerderij-instelling `transit_mode`: auto | rijpaden | vrij.

Een veld of rijgebied verlaat hij ALLEEN via een doorgang. Twee velden die tegen elkaar liggen zijn dus niet
vanzelf verbonden: zonder doorgang rijdt de robot er niet tussendoor en dus ook nooit ongemerkt over een sloot.
`suggest_passages` stelt doorgangen voor op plekken waar twee gebieden dicht bij elkaar liggen zonder sloot, hek of
ander gevaar ertussen (bij een dam is de sloot in de kaart onderbroken).

De graaf: knopen = de twee einden van elke doorgang; kanten = de doorgang zelf (met richting) en het pad binnen één
gebied tussen twee knopen (zichtlijnen langs de hoekpunten van het gebied). `plan_farm` kiest steeds het
dichtstbijzijnde veld dat nog moet, plant het (missions/field_plan.py) vanaf de plek waar de robot binnenkomt en
rijdt ertussen als `transport` (werktuig omhoog) en `doorgang` (eigen maximumsnelheid, eventueel eerst stoppen).
"""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import asdict, replace

from shapely import prepare
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from openagbot_fieldsim.missions.boundary import effective_clearance, field_area, world_obstacles
from openagbot_fieldsim.missions.dubins import dubins_all
from openagbot_fieldsim.missions.field_plan import (FORMAT, VERSION, FieldPlanner, PlanSettings, Segment, _compass,
                                                    _poly_json, _r, robot_turn_radius)
from openagbot_fieldsim.missions.lanes import dedupe, fillet, is_lane, lane_corridor, turn_text
from openagbot_fieldsim.missions.safety import check_path, geofence_export, hazards, passage_corridor, precheck_segments

TRANSIT_SURFACES = ("yard", "paved", "track")
PASSAGE_PENALTY_M = 5.0          # een doorgang telt iets zwaarder dan zijn lengte (langzaam, opletten)
SNAP_M = 10.0                    # zo ver mag een doorgang-einde of de startplek van een gebied af liggen
MAX_VERTS = 220


def _len(pts) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _with_theta(pts, last_theta: float | None = None) -> list:
    out = []
    for i, p in enumerate(pts):
        if i + 1 < len(pts):
            q = pts[i + 1]
            th = math.atan2(q[1] - p[1], q[0] - p[0])
        elif last_theta is not None:
            th = last_theta
        elif out:
            th = out[-1][2]
        else:
            th = 0.0
        out.append((p[0], p[1], th))
    return out


def _largest(g):
    if g is None or g.is_empty:
        return None
    parts = [p for p in getattr(g, "geoms", [g]) if isinstance(p, Polygon) and not p.is_empty]
    return max(parts, key=lambda p: p.area) if parts else None


class Region:
    """Een gebied waar het regelpunt van de robot vrij mag rijden (veld of rijgebied)."""

    def __init__(self, rid: str, kind: str, geom: Polygon, name: str, field_id: str | None = None):
        self.id, self.kind, self.geom, self.name, self.field_id = rid, kind, geom, name, field_id
        self.ok = geom.buffer(0.08)
        prepare(self.ok)
        self._verts = None
        self._adj = None
        self._cache: dict = {}

    def contains(self, xy) -> bool:
        return self.ok.covers(Point(xy))

    def snap(self, xy) -> tuple[float, float]:
        """Dichtstbijzijnde punt in het gebied (het punt zelf als het er al in ligt)."""
        p = Point(xy)
        if self.geom.covers(p):
            return (xy[0], xy[1])
        inner = self.geom.buffer(-0.05)
        q = nearest_points(inner if not inner.is_empty else self.geom, p)[0]
        return (q.x, q.y)

    def _visible(self, a, b) -> bool:
        return self.ok.covers(LineString([a, b]))

    def verts(self) -> list:
        if self._verts is None:
            tol = 0.25
            while True:
                nav = self.geom.buffer(-tol).simplify(tol)
                if nav.is_empty:
                    nav = self.geom.simplify(0.05)
                vs = []
                for poly in getattr(nav, "geoms", [nav]):
                    if isinstance(poly, Polygon):
                        for ring in (poly.exterior, *poly.interiors):
                            vs += [(c[0], c[1]) for c in list(ring.coords)[:-1]]
                if len(vs) <= MAX_VERTS or tol > 3.0:
                    break
                tol *= 1.6
            self._verts = vs
        return self._verts

    def adjacency(self) -> list:
        if self._adj is None:
            vs = self.verts()
            adj: list[list] = [[] for _ in vs]
            for i in range(len(vs)):
                for j in range(i + 1, len(vs)):
                    if self._visible(vs[i], vs[j]):
                        d = math.hypot(vs[i][0] - vs[j][0], vs[i][1] - vs[j][1])
                        adj[i].append((j, d))
                        adj[j].append((i, d))
            self._adj = adj
        return self._adj

    def path(self, a, b) -> list | None:
        """Kortste pad binnen het gebied van a naar b (beide in het gebied), of None."""
        a, b = self.snap(a), self.snap(b)
        key = (round(a[0], 2), round(a[1], 2), round(b[0], 2), round(b[1], 2))
        if key in self._cache:
            return self._cache[key]
        res = None
        if math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6 or self._visible(a, b):
            res = [a, b]
        else:
            vs, adj = self.verts(), self.adjacency()
            n = len(vs)
            goal = {i: math.hypot(vs[i][0] - b[0], vs[i][1] - b[1]) for i in range(n) if self._visible(vs[i], b)}
            dist = {i: math.hypot(vs[i][0] - a[0], vs[i][1] - a[1]) for i in range(n) if self._visible(a, vs[i])}
            prev: dict = {}
            pq = [(d, i) for i, d in dist.items()]
            heapq.heapify(pq)
            best, best_i, done = None, None, set()
            while pq:
                d, i = heapq.heappop(pq)
                if i in done:
                    continue
                done.add(i)
                if best is not None and d >= best:
                    break
                if i in goal and (best is None or d + goal[i] < best):
                    best, best_i = d + goal[i], i
                for j, w in adj[i]:
                    if j not in done and d + w < dist.get(j, math.inf):
                        dist[j] = d + w
                        prev[j] = i
                        heapq.heappush(pq, (d + w, j))
            if best_i is not None:
                chain = [best_i]
                while chain[-1] in prev:
                    chain.append(prev[chain[-1]])
                res = [a] + [vs[i] for i in reversed(chain)] + [b]
        self._cache[key] = res
        return res

    def distance(self, a, b) -> float:
        p = self.path(a, b)
        return math.inf if p is None else _len(p)


class FarmGraph:
    def __init__(self, world, settings: PlanSettings | None = None, half_width_m: float | None = None,
                 turn_radius_m: float | None = None, start_xy: tuple[float, float] | None = None):
        self.world = world
        self.s = settings or PlanSettings()
        self.R = float(turn_radius_m or self.s.turn_radius_m or robot_turn_radius(1.2, 1.0, 30.0) * self.s.turn_margin)
        # Onderweg telt de transportbreedte, niet de werkbreedte (die kan een camerabeeld of neergeklapte boom zijn)
        self.half = half_width_m if half_width_m is not None else max((self.s.transport_width_m or 0.0) / 2.0,
                                                                     self.s.robot_half_width_m)
        self.clr = effective_clearance(world, self.s.clearance)
        self.warnings: list[str] = []
        self.skipped: dict[str, str] = {}             # veld -> waarom er geen rijgebied is
        self.regions: list[Region] = []
        self.nodes: dict = {}                         # (passage-id, kant) -> {"xy", "region", "passage", "side"}
        # Doorgangen (dam, hek, ...) en de STUKKEN van rijpaden (id "<rijpad>#<n>") waar de route over loopt
        self.passages = {p.id: p for p in world.passages if not is_lane(p)}
        self.unusable: dict[str, str] = {}
        # Rijpaden: vaste lijnen met een zone. Zijn ze er, dan rijdt de robot buiten de velden alleen daarover.
        mode = (getattr(world, "farm_settings", None) or {}).get("transit_mode", "auto")
        lane_src = [p for p in world.passages if is_lane(p)]
        self.strict = mode == "rijpaden" or (mode != "vrij" and bool(lane_src))
        self.lanes: dict = {}                         # id -> rijpad (bruikbaar)
        self.piece_lane: dict[str, str] = {}          # stuk-id -> rijpad-id
        self.lane_notes: dict[str, str] = {}          # rijpad dat maar deels bruikbaar is -> waarom
        self.lane_ok = None                           # waar robot en werktuig op een rijpad mogen komen
        self.start = None                             # (gebied, punt) waar de route begint (alleen bij start_xy)
        self.start_error: str | None = None
        self._lines: dict[str, list] = {}
        self._splits: dict[str, list] = {}
        self._jxy: dict = {}
        self._uf: dict = {}
        self._turns: dict = {}
        self._build_regions()
        self._prepare_lanes(lane_src)
        self._build_nodes()
        if start_xy is not None:
            self._locate_start(start_xy)
        self._finish_lanes()

    # ------------------------------------------------------------------ gebieden
    def _build_regions(self):
        w, s = self.world, self.s
        for f in w.fields:
            try:
                area = field_area(w, f.id, s.clearance, s.border_min_m, date=s.date, mission_dike_mode=s.dike_mode)
            except ValueError as e:
                self.skipped[f.id] = str(e)
                continue
            g = _largest((area.full or area.allowed).buffer(-(self.half - 0.05), join_style="mitre"))
            if g is None or g.area < 4.0:
                self.skipped[f.id] = "te smal voor robot en werktuig na de vrije ruimte langs de randen"
                continue
            self.regions.append(Region(f"veld:{f.id}", "veld", g, f.name, f.id))
        polys = []
        for sf in ([] if self.strict else w.surfaces):
            if sf.kind in TRANSIT_SURFACES and len(sf.xy) >= 3:
                g = Polygon(sf.xy)
                polys.append(g if g.is_valid else g.buffer(0))
        for z in ([] if self.strict else w.zones):
            if z.klass in ("werken", "alleen_rijden") and len(z.xy) >= 3:
                g = Polygon(z.xy)
                polys.append(g if g.is_valid else g.buffer(0))
        if polys:
            # binnen het vlak blijven met de halve breedte; daarna de obstakels met hun vrije ruimte eruit
            # (vlakken die net niet aansluiten, bv. door afronding, worden over 5 cm aan elkaar gezet)
            free = unary_union([g.buffer(0.05, join_style="mitre") for g in polys]).buffer(
                -(self.half + 0.15), join_style="mitre")
            keep = []
            for o in world_obstacles(w, date=s.date, mission_dike_mode=s.dike_mode):
                if o.kind == "verhard":
                    continue
                room = o.clearance if o.clearance is not None else self.clr.get(o.kind, 0.0)
                keep.append(o.geom.buffer(room + self.half, join_style="round"))
            if keep:
                free = free.difference(unary_union(keep))
            parts = [p for p in getattr(free, "geoms", [free]) if isinstance(p, Polygon) and p.area >= 2.0]
            for i, p in enumerate(sorted(parts, key=lambda p: -p.area)):
                self.regions.append(Region(f"rijgebied:{i + 1}", "rijgebied", p, f"rijgebied {i + 1}"))

    def field_region(self, field_id: str) -> int | None:
        for i, r in enumerate(self.regions):
            if r.field_id == field_id:
                return i
        return None

    def locate(self, xy, snap_m: float = SNAP_M, prefer_field: str | None = None):
        """(gebied-index, punt in het gebied) voor een plek, of None als er binnen `snap_m` geen gebied is."""
        p = Point(xy)
        best = None
        for i, r in enumerate(self.regions):
            d = 0.0 if r.geom.covers(p) else r.geom.distance(p)
            if d > snap_m:
                continue
            key = (d > 1e-6, 0 if r.field_id == prefer_field and prefer_field else 1, d, r.kind != "rijgebied")
            if best is None or key < best[0]:
                best = (key, i)
        if best is None:
            return None
        return best[1], self.regions[best[1]].snap(xy)

    # ------------------------------------------------------------------ doorgangen
    def _attach(self, end, toward, other_region: int | None = None):
        """Gebied waar een einde van een doorgang op aansluit: eerst in het verlengde van de rijlijn, dan dichtbij."""
        p = Point(end)
        for i, r in enumerate(self.regions):
            if i != other_region and r.geom.covers(p):
                return i, (end[0], end[1])
        dx, dy = end[0] - toward[0], end[1] - toward[1]
        ll = math.hypot(dx, dy) or 1.0
        ray = LineString([end, (end[0] + dx / ll * SNAP_M, end[1] + dy / ll * SNAP_M)])
        best = None
        for i, r in enumerate(self.regions):
            if i == other_region or not r.geom.intersects(ray):
                continue
            hit = r.geom.intersection(ray)
            d = hit.distance(p)
            if best is None or d < best[0]:
                q = nearest_points(hit, p)[0]
                best = (d, i, (q.x + dx / ll * 0.15, q.y + dy / ll * 0.15))
        if best is None:
            for i, r in enumerate(self.regions):
                if i == other_region:
                    continue
                d = r.geom.distance(p)
                if d <= SNAP_M / 2.0 and (best is None or d < best[0]):
                    best = (d, i, None)
        if best is None:
            return None
        return best[1], self.regions[best[1]].snap(best[2] or end)

    def _build_nodes(self):
        for p in self.passages.values():
            name = p.name or p.id
            if len(p.xy) < 2:
                self.unusable[p.id] = "geen rijlijn"
                continue
            if p.width_m < 2.0 * self.half + 0.1:
                self.unusable[p.id] = (f"te smal: {p.width_m:.1f} m voor transportbreedte "
                                       f"{2.0 * self.half:.1f} m")
                self.warnings.append(f"doorgang {name}: {self.unusable[p.id]}")
                continue
            a = self._attach(p.xy[0], p.xy[1]) or self._attach_lane(p.xy[0])
            b = self._attach(p.xy[-1], p.xy[-2], a[0] if a else None) or self._attach_lane(p.xy[-1])
            if a is None or b is None:
                self.unusable[p.id] = ("sluit niet aan op een veld of rijpad (teken de lijn van veld tot veld, of tot "
                                       "op een rijpad)" if self.strict else
                                       "sluit niet aan op een veld of rijgebied (teken de lijn van veld tot veld)")
                self.warnings.append(f"doorgang {name}: {self.unusable[p.id]}")
                continue
            line = [a[1], *[(q[0], q[1]) for q in p.xy], b[1]]
            bad = check_path(self.world, line, self.half, self.s.date, self.s.dike_mode)
            if bad:
                what = ", ".join(sorted({v["label"] for v in bad}))
                self.unusable[p.id] = f"de rijlijn raakt {what}; maak de doorgang breder of verleg hem"
                self.warnings.append(f"doorgang {name}: {self.unusable[p.id]}")
                continue
            self.nodes[(p.id, 0)] = {"xy": a[1], "region": a[0], "passage": p.id, "side": 0}
            self.nodes[(p.id, 1)] = {"xy": b[1], "region": b[0], "passage": p.id, "side": 1}

    # ------------------------------------------------------------------ rijpaden
    def _key(self, xy):
        return (round(xy[0], 2), round(xy[1], 2))

    def _find(self, k):
        while self._uf.get(k, k) != k:
            self._uf[k] = self._uf.get(self._uf[k], self._uf[k])
            k = self._uf[k]
        return k

    def _split(self, lane_id: str, xy) -> tuple:
        """Knoop op een rijpad op het punt van de lijn dat het dichtst bij `xy` ligt. Geeft de sleutel van de knoop."""
        line = LineString(self._lines[lane_id])
        d = line.project(Point(xy))
        k = self._key(xy)
        self._jxy.setdefault(k, (float(xy[0]), float(xy[1])))
        self._uf.setdefault(k, k)
        self._splits[lane_id].append((d, k))
        return k

    def _nearest_lane(self, xy, skip: str | None = None):
        """(afstand, rijpad-id, punt op de lijn) van het dichtstbijzijnde bruikbare rijpad, of None."""
        p = Point(xy)
        best = None
        for lid, pts in self._lines.items():
            if lid == skip:
                continue
            line = LineString(pts)
            d = line.distance(p)
            if best is None or d < best[0]:
                q = line.interpolate(line.project(p))
                best = (d, lid, (q.x, q.y))
        return best

    def _prepare_lanes(self, lane_src) -> None:
        """Rijpaden keuren en hun lijnen aan elkaar knopen: kruisingen, T-splitsingen en waar ze een veld in gaan."""
        for p in lane_src:
            name = p.name or p.id
            pts = dedupe([(q[0], q[1]) for q in p.xy])
            if len(pts) < 2 or _len(pts) < 0.5:
                self.unusable[p.id] = "geen rijlijn"
            elif p.width_m < 2.0 * self.half + 0.1:
                self.unusable[p.id] = (f"zone te smal: {p.width_m:.1f} m voor transportbreedte "
                                       f"{2.0 * self.half:.1f} m")
            else:
                bad = check_path(self.world, pts, self.half, self.s.date, self.s.dike_mode)
                if bad:
                    what = ", ".join(sorted({v["label"] for v in bad}))
                    self.unusable[p.id] = (f"de rijlijn raakt {what}; verleg het rijpad of teken daar een doorgang "
                                           "(dam, duiker, brug, oversteek)")
            if p.id in self.unusable:
                self.warnings.append(f"rijpad {name}: {self.unusable[p.id]}")
                continue
            self.lanes[p.id] = p
            self._lines[p.id] = pts
            self._splits[p.id] = []
        if not self.lanes:
            return
        fields = [r.geom.buffer(self.half + 0.05, join_style="mitre") for r in self.regions if r.kind == "veld"]
        self.lane_ok = unary_union([*(lane_corridor(p) for p in self.lanes.values()), *fields]).buffer(0.05)
        prepare(self.lane_ok)
        # Een einde dat binnen de zone van een ander rijpad ligt sluit daarop aan: de lijn loopt door tot die lijn
        for lid, p in self.lanes.items():
            for end in (0, -1):
                near = self._nearest_lane(self._lines[lid][end], skip=lid)
                if near and 0.05 < near[0] <= self.lanes[near[1]].width_m / 2.0:
                    if end == 0:
                        self._lines[lid].insert(0, near[2])
                    else:
                        self._lines[lid].append(near[2])
        ids = list(self.lanes)
        for lid in ids:
            self._split(lid, self._lines[lid][0])
            self._split(lid, self._lines[lid][-1])
        for i, a in enumerate(ids):                              # kruisingen en T-splitsingen
            la = LineString(self._lines[a])
            for b in ids[i + 1:]:
                lb = LineString(self._lines[b])
                for q in _points(la.intersection(lb)):
                    self._split(a, q)
                    self._split(b, q)
                for x, y in ((a, b), (b, a)):                    # einde van x op de lijn van y
                    for end in (self._lines[x][0], self._lines[x][-1]):
                        if LineString(self._lines[y]).distance(Point(end)) < 0.03:
                            self._split(y, end)
        for lid in ids:                                          # waar het rijpad een veld in of uit gaat
            line = LineString(self._lines[lid])
            for r in self.regions:
                if r.kind == "veld" and line.intersects(r.geom):
                    for q in _points(line.intersection(r.geom.boundary)):
                        self._split(lid, q)

    def _attach_lane(self, end):
        """Einde van een doorgang dat op een rijpad uitkomt: knoop op die lijn. (("K", sleutel), punt) of None."""
        near = self._nearest_lane(end)
        if near is None or near[0] > self.lanes[near[1]].width_m / 2.0 + 0.5:
            return None
        return ("K", self._split(near[1], near[2])), near[2]

    def _locate_start(self, xy) -> None:
        """Waar begint de route? Met rijpaden: in een veld (vrij naar het rijpad), of binnen de zone van een rijpad
        (hij pakt de lijn daar op). Anders weigert de planner: eerst met de hand naar een rijpad rijden."""
        if not self.strict:
            return
        p = Point(xy)
        for i, r in enumerate(self.regions):
            if r.kind == "veld" and r.geom.buffer(self.half + 0.3, join_style="mitre").covers(p):
                self.start = (i, r.snap(xy))
                return
        near = self._nearest_lane(xy)
        if near is not None and near[0] <= self.lanes[near[1]].width_m / 2.0:
            self.start = (("K", self._split(near[1], near[2])), near[2])
            return
        if near is None:
            self.start_error = ("de robot staat niet in een veld en er is geen bruikbaar rijpad: teken een rijpad "
                                "(doorgang van de soort rijpad) van het erf naar het veld")
        else:
            lane = self.lanes[near[1]]
            self.start_error = (f"de robot staat niet in een veld en niet op een rijpad: rijpad "
                                f"'{lane.name or lane.id}' ligt {near[0]:.1f} m verderop en zijn zone is "
                                f"{lane.width_m / 2.0:.1f} m aan elke kant. Rijd hem met de hand tot in de zone; "
                                "daar pakt hij de lijn zelf op")

    def _region_of(self, key):
        """Gebied van een knoop op een rijpad: het veld waar hij in ligt, anders de kruising zelf ("J:x,y")."""
        root = self._find(key)
        xy = self._jxy[root]
        p = Point(xy)
        for i, r in enumerate(self.regions):
            if r.kind == "veld" and r.ok.covers(p):
                return i
        return f"J:{root[0]},{root[1]}"

    def _finish_lanes(self) -> None:
        """Rijpaden opknippen in stukken tussen de knopen; elk stuk is een kant van de graaf, net als een doorgang."""
        for lid, p in self.lanes.items():
            pts = self._lines[lid]
            line = LineString(pts)
            cum = [0.0]
            for a, b in zip(pts, pts[1:]):
                cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
            splits = sorted(self._splits[lid])
            merged = [splits[0]]
            for d, k in splits[1:]:
                if d - merged[-1][0] < 0.3:                      # zelfde plek: één knoop
                    self._uf[self._find(k)] = self._find(merged[-1][1])
                else:
                    merged.append((d, k))
            self._splits[lid] = merged
        for lid, p in self.lanes.items():
            pts = self._lines[lid]
            line = LineString(pts)
            cum = [0.0]
            for a, b in zip(pts, pts[1:]):
                cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
            merged = self._splits[lid]
            for n, ((d0, k0), (d1, k1)) in enumerate(zip(merged, merged[1:])):
                a, b = line.interpolate(d0), line.interpolate(d1)
                piece = dedupe([(a.x, a.y), *[q for q, c in zip(pts, cum) if d0 + 0.05 < c < d1 - 0.05], (b.x, b.y)])
                pid = f"{lid}#{n}"
                smooth, bad = fillet(piece, self.R, self.s.point_step_m)
                reason = None
                if bad:
                    reason = turn_text(bad[0], self.R)
                elif not self.lane_ok.covers(LineString(smooth).buffer(self.half)):
                    reason = (f"een bocht komt met draaicirkel {self.R:.1f} m buiten de zone van "
                              f"{max(p.width_m, p.bend_width_m or 0.0):.1f} m breed; geef het rijpad een "
                              f"grotere 'breedte in de bocht'")
                if reason:
                    self.unusable[pid] = reason
                    if lid not in self.lane_notes:
                        self.lane_notes[lid] = f"{reason}; maak de bocht ruimer of de zone daar breder"
                        self.warnings.append(f"rijpad {p.name or p.id}: {self.lane_notes[lid]}")
                    continue
                self.passages[pid] = replace(p, id=pid, xy=piece)
                self.piece_lane[pid] = lid
                self.nodes[(pid, 0)] = {"xy": (a.x, a.y), "region": self._region_of(k0), "passage": pid, "side": 0}
                self.nodes[(pid, 1)] = {"xy": (b.x, b.y), "region": self._region_of(k1), "passage": pid, "side": 1}
        for n in self.nodes.values():                            # doorgang-einden die op een rijpad uitkwamen
            if isinstance(n["region"], tuple):
                n["region"] = self._region_of(n["region"][1])
        if self.start is not None and isinstance(self.start[0], tuple):
            self.start = (self._region_of(self.start[0][1]), self.start[1])

    def _run(self, pid: str, from_side: int, reach_m: float | None = None) -> list:
        """Punten vanaf knoop (pid, from_side) langs het rijpad, door over de volgende stukken van hetzelfde rijpad,
        tot `reach_m` (standaard ruim genoeg voor een bocht)."""
        reach = reach_m if reach_m is not None else 4.0 * self.R + 2.0
        lid, n = pid.rsplit("#", 1)
        n, step = int(n), (1 if from_side == 0 else -1)
        pts: list = []
        while f"{lid}#{n}" in self.passages and _len(pts) < reach:
            pts += self.passage_points(f"{lid}#{n}", from_side)
            n += step
        return dedupe(pts)

    def _turn_ok(self, ka, kb) -> bool:
        """Past de bocht van stuk `ka` (aangekomen op die knoop) naar stuk `kb` (vertrek) binnen de zones?"""
        key = (ka, kb)
        if key not in self._turns:
            # Niet alleen de twee stukken zelf: een rijpad is ook geknipt waar een hek of ander rijpad erop
            # uitkomt, en de bocht mag de lengte van het hele rijpad voor en na de kruising gebruiken.
            path = dedupe([*self._run(ka[0], ka[1])[::-1], *self._run(kb[0], kb[1])])
            smooth, bad = fillet(path, self.R, self.s.point_step_m)
            self._turns[key] = not bad and self.lane_ok.covers(LineString(smooth).buffer(self.half))
        return self._turns[key]

    def _dist(self, region, a, b) -> float:
        """Afstand binnen een gebied; op een kruising van rijpaden liggen de knopen op dezelfde plek."""
        if isinstance(region, str):
            return math.hypot(a[0] - b[0], a[1] - b[1])
        return self.regions[region].distance(a, b)

    def region_name(self, region) -> str:
        return "kruising van rijpaden" if isinstance(region, str) else self.regions[region].name

    def passage_points(self, pid: str, from_side: int) -> list:
        p = self.passages[pid]
        pts = [self.nodes[(pid, 0)]["xy"], *[(q[0], q[1]) for q in p.xy], self.nodes[(pid, 1)]["xy"]]
        clean = [pts[0]]
        for q in pts[1:]:
            if math.hypot(q[0] - clean[-1][0], q[1] - clean[-1][1]) > 0.05:
                clean.append(q)
        return clean if from_side == 0 else clean[::-1]

    def _may_pass(self, pid: str, from_side: int) -> bool:
        d = self.passages[pid].direction
        return d == "beide" or (d == "heen" and from_side == 0) or (d == "terug" and from_side == 1)

    # ------------------------------------------------------------------ kortste route
    def dijkstra(self, region: int, xy) -> tuple[dict, dict]:
        """Afstand vanaf (gebied, punt) tot elke doorgangsknoop. prev[knoop] = vorige knoop of "S"."""
        dist: dict = {}
        prev: dict = {}
        pq: list = []
        for key, n in self.nodes.items():
            if n["region"] == region:
                d = self._dist(region, xy, n["xy"])
                if d < math.inf:
                    dist[key] = d
                    prev[key] = "S"
                    heapq.heappush(pq, (d, key))
        done = set()
        while pq:
            d, key = heapq.heappop(pq)
            if key in done:
                continue
            done.add(key)
            n = self.nodes[key]
            pid, side = key
            if self._may_pass(pid, side):                        # door de doorgang naar de andere kant
                other = (pid, 1 - side)
                nd = d + _len(self.passage_points(pid, side)) + (0.0 if pid in self.piece_lane else PASSAGE_PENALTY_M)
                if other not in done and nd < dist.get(other, math.inf):
                    dist[other], prev[other] = nd, key
                    heapq.heappush(pq, (nd, other))
            junction = isinstance(n["region"], str)
            came = prev[key] == (pid, 1 - side)                  # over dit stuk hier aangekomen
            if junction and not came:
                continue                                         # op een kruising alleen het eigen stuk op
            for k2, n2 in self.nodes.items():                    # binnen het gebied naar een andere doorgang
                if k2 in done or n2["region"] != n["region"] or k2[0] == pid:
                    continue
                if junction and pid in self.piece_lane and k2[0] in self.piece_lane and not self._turn_ok(key, k2):
                    continue                                     # die bocht past niet binnen de zone
                nd = d + self._dist(n["region"], n["xy"], n2["xy"])
                if nd < dist.get(k2, math.inf):
                    dist[k2], prev[k2] = nd, key
                    heapq.heappush(pq, (nd, k2))
        return dist, prev

    def entry(self, region: int, src_region: int, src_xy, dist: dict):
        """Hoe ver is `region` en waar kom je binnen: (afstand, knoop of None als je er al bent)."""
        if region == src_region:
            return 0.0, None
        best = None
        for key, n in self.nodes.items():
            if n["region"] == region and key in dist and (best is None or dist[key] < best[0]):
                # alleen knopen waar je door de doorgang BINNENKOMT (prev is de andere kant)
                best = (dist[key], key)
        return best if best else (math.inf, None)

    def hops(self, src_region: int, src_xy, dst_region: int, dst_xy, dist: dict, prev: dict):
        """Route als lijst stukken: ("gebied", index, punten) en ("doorgang", id, punten). None = onbereikbaar."""
        def path(region, a, b):
            return None if isinstance(region, str) else self.regions[region].path(a, b)

        best = None
        if src_region == dst_region:
            d = self._dist(dst_region, src_xy, dst_xy)
            if d < math.inf:
                best = (d, None)
        for key, n in self.nodes.items():
            if n["region"] == dst_region and key in dist:
                d = dist[key] + self._dist(dst_region, n["xy"], dst_xy)
                if best is None or d < best[0]:
                    best = (d, key)
        if best is None or best[0] == math.inf:
            return None
        if best[1] is None:
            return [h for h in [("gebied", dst_region, path(dst_region, src_xy, dst_xy))] if h[2] is not None]
        chain = [best[1]]
        while prev[chain[-1]] != "S":
            chain.append(prev[chain[-1]])
        chain.reverse()
        out = []
        first = self.nodes[chain[0]]
        out.append(("gebied", first["region"], path(first["region"], src_xy, first["xy"])))
        for a, b in zip(chain, chain[1:]):
            if a[0] == b[0]:
                out.append(("doorgang", a[0], self.passage_points(a[0], a[1])))
            else:
                r = self.nodes[a]["region"]
                out.append(("gebied", r, path(r, self.nodes[a]["xy"], self.nodes[b]["xy"])))
        out.append(("gebied", dst_region, path(dst_region, self.nodes[chain[-1]]["xy"], dst_xy)))
        return [h for h in out if h[2] is not None and (h[0] == "doorgang" or _len(h[2]) > 0.05)]

    def segments(self, hops, label: str, speed_kmh: float, meta: dict | None = None) -> list[Segment]:
        out = []
        i = 0
        while i < len(hops):
            kind, ref, pts = hops[i]
            i += 1
            if kind == "doorgang" and ref in self.piece_lane:
                # Opeenvolgende stukken rijpad worden één segment: de lijn, met de bochten afgerond op de draaicirkel.
                # De robot bewaakt de breedte van het smalste rijpad in het segment.
                line, ids = list(pts), [self.piece_lane[ref]]
                while i < len(hops) and hops[i][0] == "doorgang" and hops[i][1] in self.piece_lane:
                    line += hops[i][2]
                    if self.piece_lane[hops[i][1]] not in ids:
                        ids.append(self.piece_lane[hops[i][1]])
                    i += 1
                lanes = [self.lanes[k] for k in ids]
                smooth, bad = fillet(line, self.R, self.s.point_step_m)
                ok = not bad and self.lane_ok.covers(LineString(smooth).buffer(self.half))
                narrow = min(lanes, key=lambda q: q.width_m)
                sg = Segment("doorgang", "rijpad " + " > ".join(q.name or q.id for q in lanes), "omhoog",
                             min(speed_kmh, *(q.max_speed_kmh for q in lanes)), _with_theta(smooth), ok,
                             {**(meta or {}), "doorgang": narrow.id, "soort": "rijpad", "rijpaden": ids,
                              "bevestigen": True if any(q.confirm for q in lanes) else None})
                if not ok:
                    sg.meta["reden"] = "bocht past niet binnen de zone van het rijpad"
                out.append(sg)
            elif kind == "doorgang":
                p = self.passages[ref]
                out.append(Segment("doorgang", f"doorgang {p.name or p.id}", "omhoog",
                                   min(speed_kmh, p.max_speed_kmh), _with_theta(pts), True,
                                   {**(meta or {}), "doorgang": p.id, "soort": p.kind,
                                    "bevestigen": True if p.confirm else None}))
            else:
                out.append(Segment("transport", label, "omhoog", speed_kmh, _with_theta(pts), True,
                                   {**(meta or {}), "gebied": self.regions[ref].name}))
        return out

    def _connects(self, p) -> list[str]:
        """Namen van de gebieden waar een doorgang of rijpad op aansluit."""
        if not is_lane(p):
            return [self.region_name(self.nodes[(p.id, k)]["region"]) for k in (0, 1)] if (p.id, 0) in self.nodes else []
        names = []
        for (pid, _), n in self.nodes.items():
            if self.piece_lane.get(pid) == p.id and isinstance(n["region"], int):
                if self.regions[n["region"]].name not in names:
                    names.append(self.regions[n["region"]].name)
        return names

    def view(self) -> dict:
        return {
            "regions": [{"id": r.id, "kind": r.kind, "name": r.name, "field": r.field_id,
                         "poly": _poly_json(r.geom.simplify(0.1))} for r in self.regions],
            "passages": [{"id": p.id, "name": p.name, "kind": p.kind, "usable": p.id not in self.unusable,
                          "reason": self.unusable.get(p.id) or self.lane_notes.get(p.id),
                          "connects": self._connects(p)} for p in self.world.passages],
            "strict": self.strict, "skipped_fields": self.skipped, "warnings": self.warnings,
        }


def _points(geom) -> list:
    """Losse punten uit een doorsnede (punt, punten, of de einden van een stuk dat samenvalt)."""
    out = []
    for g in getattr(geom, "geoms", [geom]):
        if g.is_empty:
            continue
        if g.geom_type == "Point":
            out.append((g.x, g.y))
        elif g.geom_type == "LineString":
            cs = list(g.coords)
            out += [(cs[0][0], cs[0][1]), (cs[-1][0], cs[-1][1])]
        else:
            out += _points(g)
    return out


def _approach(region: Region, pts: list, goal_pose, radius: float, step: float) -> list:
    """Laatste stuk naar de eerste stand van de veldmissie als Dubins-bocht, als die binnen het gebied past."""
    total = _len(pts)
    if total < 0.5:
        return _with_theta(pts, goal_pose[2])
    line = LineString(pts)
    for back in (3.0 * radius, 5.0 * radius, 8.0 * radius, total):
        d = min(back, total)
        p = line.interpolate(total - d)
        q = line.interpolate(min(total, total - d + 0.3))
        if p.distance(q) < 1e-6:
            continue
        th = math.atan2(q.y - p.y, q.x - p.x)
        for path in dubins_all((p.x, p.y, th), goal_pose, radius):
            sample = path.sample(step)
            if region.ok.covers(LineString([(s[0], s[1]) for s in sample])):
                head = []
                acc = 0.0
                for a, b in zip(pts, pts[1:]):
                    seg = math.hypot(b[0] - a[0], b[1] - a[1])
                    if acc + seg >= total - d - 1e-6:
                        head.append(a)
                        break
                    head.append(a)
                    acc += seg
                return _with_theta(head + [(p.x, p.y)])[:-1] + sample if head else sample
        if d >= total:
            break
    return _with_theta(pts, goal_pose[2])


# ---------------------------------------------------------------------------------------------------- missie
def plan_farm(world, field_ids: list[str] | None = None, settings: PlanSettings | dict | None = None, vehicle=None,
              start_xy: tuple[float, float] | None = None, return_home: bool = True, order: str = "auto") -> dict:
    """Hele-boerderij-missie: volgorde van de velden, elk veld gepland, ertussen alleen via doorgangen.

    `order`: "auto" = steeds het dichtstbijzijnde veld, "gegeven" = de volgorde van `field_ids`.
    """
    t0 = time.time()
    s = settings if isinstance(settings, PlanSettings) else PlanSettings.from_dict(settings)
    if s.turn_radius_m:
        R = float(s.turn_radius_m)
    else:
        wb, tw, ang = (1.2, 1.0, 30.0) if vehicle is None else (vehicle.wheelbase_m, vehicle.track_width_m,
                                                               vehicle.max_wheel_angle_deg)
        R = robot_turn_radius(wb, tw, ang) * s.turn_margin
    if start_xy is None:
        sx, sy, _ = world.start_xy_heading()
        start_xy = (sx, sy)
    graph = FarmGraph(world, s, turn_radius_m=R, start_xy=start_xy)
    half = graph.half
    warnings = list(graph.warnings)
    names = {f.id: f.name for f in world.fields}
    wanted = list(field_ids) if field_ids else [f.id for f in world.fields]
    unreachable = []
    todo = []
    for fid in wanted:
        if fid not in names:
            unreachable.append({"field": fid, "name": fid, "reason": "veld bestaat niet"})
        elif graph.field_region(fid) is None:
            unreachable.append({"field": fid, "name": names[fid], "reason": graph.skipped.get(fid, "geen rijgebied")})
        else:
            todo.append(fid)

    here = graph.start if graph.strict else graph.locate(start_xy)
    if here is None and graph.strict:
        raise ValueError(graph.start_error or "de startplek ligt niet in een veld of op een rijpad")
    if here is None:
        raise ValueError("de startplek ligt niet in of bij een veld of rijgebied (erf, pad, rijzone): zet de "
                         "startplek op het erf of teken daar een rijzone")
    home = here
    if isinstance(here[0], str):
        off = math.hypot(here[1][0] - start_xy[0], here[1][1] - start_xy[1])
        warnings.append("de robot staat op een rijpad en pakt de lijn daar op" if off < 0.3 else
                        f"de robot staat in de zone van een rijpad en pakt de lijn op, {off:.1f} m naast hem")
    elif math.hypot(here[1][0] - start_xy[0], here[1][1] - start_xy[1]) > 0.5:
        warnings.append(f"startplek ligt {math.hypot(here[1][0] - start_xy[0], here[1][1] - start_xy[1]):.1f} m "
                        f"buiten {graph.regions[here[0]].name}; de route begint op de rand daarvan")
    segments: list[Segment] = []
    fields_out = []
    allowed = []
    used_passages: list[str] = []

    def add_transit(hops, label, goal_pose=None, field_meta=None):
        segs = graph.segments(hops, label, s.transport_speed_kmh, field_meta)
        if goal_pose is not None and segs and segs[-1].type == "transport":
            kind, ref, pts = hops[-1]
            segs[-1].pts = _approach(graph.regions[ref], pts, goal_pose, R, s.point_step_m)
        for sg in segs:
            for pid in (sg.meta.get("rijpaden") or [sg.meta["doorgang"]]) if sg.type == "doorgang" else []:
                if pid not in used_passages:
                    used_passages.append(pid)
        segments.extend(sg for sg in segs if len(sg.pts) >= 2)

    while todo:
        dist, prev = graph.dijkstra(*here)
        cands = []
        for fid in (todo if order != "gegeven" else todo[:1]):
            d, key = graph.entry(graph.field_region(fid), here[0], here[1], dist)
            if d < math.inf:
                cands.append((d, fid, key))
        if not cands:
            if order == "gegeven":
                fid = todo.pop(0)
                unreachable.append({"field": fid, "name": names[fid],
                                    "reason": "geen route via rijpaden en doorgangen vanaf de vorige plek"})
                continue
            break
        d, fid, key = min(cands, key=lambda t: t[0])
        todo.remove(fid)
        ri = graph.field_region(fid)
        entry_xy = here[1] if key is None else graph.nodes[key]["xy"]
        fld = world.field_by_id(fid)
        fs = replace(s, clearance=dict(s.clearance))
        if fld.headland_rounds is not None:
            fs.headland_rounds = int(fld.headland_rounds)
        try:
            plan = FieldPlanner(world, fid, fs, R, entry_xy).plan()
        except ValueError as e:
            unreachable.append({"field": fid, "name": names[fid], "reason": f"niet te plannen: {e}"})
            continue
        first = plan["segments"][0].pts[0]
        hops = graph.hops(here[0], here[1], ri, (first[0], first[1]), dist, prev)
        if hops is None:
            unreachable.append({"field": fid, "name": names[fid], "reason": "begin van de veldmissie is vanaf de "
                                "doorgang niet te bereiken"})
            continue
        add_transit(hops, f"naar {fld.name}", first, {"veld": fid})
        for sg in plan["segments"]:
            sg.label = f"{fld.name}: {sg.label}"
            sg.meta["veld"] = fid
            segments.append(sg)
        allowed.append(plan.get("full") or plan["allowed"])
        st = plan["stats"]
        fields_out.append({"id": fid, "name": fld.name, "rounds": plan["rounds"],
                           "swath_heading_deg": plan["swath_heading_deg"], "worked_m2": st["worked_m2"],
                           "allowed_m2": st["allowed_m2"], "coverage_allowed_pct": st["coverage_allowed_pct"],
                           "time_min": st["time_min"], "warnings": plan["warnings"]})
        warnings += [f"{fld.name}: {w}" for w in plan["warnings"]]
        last = plan["segments"][-1].pts[-1]
        here = (ri, graph.regions[ri].snap((last[0], last[1])))
    for fid in todo:
        unreachable.append({"field": fid, "name": names[fid],
                            "reason": ("geen rijpad of doorgang: teken een rijpad tot in dit veld" if graph.strict
                                       else "geen doorgang: teken een doorgang (dam, hek) naar dit veld")})
    if return_home and fields_out:
        dist, prev = graph.dijkstra(*here)
        hops = graph.hops(here[0], here[1], home[0], home[1], dist, prev)
        if hops is None:
            warnings.append("geen route terug naar de startplek (doorgang met één richting?)")
        else:
            add_transit(hops, "terug naar de startplek")
    if not segments:
        reasons = "; ".join(f"{u['name']}: {u['reason']}" for u in unreachable[:4])
        raise ValueError(f"geen enkel veld is te bereiken ({reasons})" if unreachable else "geen velden om te plannen")

    transit = [sg for sg in segments if sg.type in ("transport", "doorgang")]
    pre = precheck_segments(world, transit, half, date=s.date, mission_dike_mode=s.dike_mode)
    # Alleen wat een gevaar raakt telt als overtreding; een baan die een paar cm over de rand van het rijgebied
    # komt (sg.ok False zonder reden) staat bij de waarschuwingen van dat veld.
    violations = [{"segment": i, "segment_label": sg.label, "label": sg.meta["reden"]}
                  for i, sg in enumerate(segments) if not sg.ok and sg.meta.get("reden")]
    if pre["violations"]:
        warnings.append(f"veiligheidscontrole: {len(pre['violations'])} transportstukken raken een gevaar")
    if unreachable:
        warnings.append(f"{len(unreachable)} velden niet in de missie: " +
                        ", ".join(u["name"] for u in unreachable[:6]))

    by_type: dict[str, dict] = {}
    total_t = 0.0
    for sg in segments:
        d = by_type.setdefault(sg.type, {"n": 0, "length_m": 0.0, "time_s": 0.0})
        length = sg.length
        t = length / max(0.1, sg.speed_kmh / 3.6)
        d["n"] += 1
        d["length_m"] += length
        d["time_s"] += t
        total_t += t
    for d in by_type.values():
        d["length_m"] = round(d["length_m"], 1)
        d["time_s"] = round(d["time_s"], 0)
    total_len = sum(d["length_m"] for d in by_type.values())
    work_len = sum(d["length_m"] for k, d in by_type.items() if k in ("rondje", "baan"))
    transit_len = sum(d["length_m"] for k, d in by_type.items() if k in ("transport", "doorgang"))
    worked = sum(f["worked_m2"] for f in fields_out)
    stats = {"fields": len(fields_out), "worked_m2": round(worked, 1),
             "allowed_m2": round(sum(f["allowed_m2"] for f in fields_out), 1),
             "length_m": round(total_len, 1), "work_length_m": round(work_len, 1),
             "transport_m": round(transit_len, 1), "passages": len(used_passages),
             "efficiency_pct": round(100.0 * work_len / max(1.0, total_len), 1),
             "time_min": round(total_t / 60.0, 1),
             "ha_per_h": round(worked / 1e4 / max(1e-6, total_t / 3600.0), 3), "by_type": by_type}
    return {
        "kind": "boerderij", "field_id": None, "settings": asdict(s), "turn_radius_m": round(R, 3),
        "half_width_m": half, "order": [f["id"] for f in fields_out], "fields": fields_out, "segments": segments,
        "allowed": allowed, "graph": graph, "passages_used": used_passages, "unreachable": unreachable,
        "safety": {"ok": not violations, "violations": violations, "half_width_m": round(half, 3)},
        "stats": stats, "warnings": warnings, "plan_time_s": round(time.time() - t0, 2),
    }


def farm_view(plan: dict) -> dict:
    """JSON voor de UI (lokale meters); zelfde vorm als een veldmissie, zodat 3D en minikaart hem kunnen tekenen."""
    polys = []
    for a in plan["allowed"]:
        polys += _poly_json(a)
    g: FarmGraph = plan["graph"]
    return {
        "kind": "veldmissie", "farm": True, "field_id": None, "settings": plan["settings"],
        "turn_radius_m": plan["turn_radius_m"], "order": plan["order"], "fields": plan["fields"],
        "allowed": polys, "no_work": [], "core": [], "missed": [], "edges": [], "edge_summary": [],
        "rounds": None, "cells": [], "swath_heading_deg": None,
        "segments": [{"type": sg.type, "label": sg.label, "implement": sg.implement, "speed_kmh": sg.speed_kmh,
                      "ok": sg.ok, "meta": sg.meta, "pts": [[_r(p[0], 2), _r(p[1], 2)] for p in sg.pts]}
                     for sg in plan["segments"]],
        "graph": g.view(), "passages_used": plan["passages_used"], "unreachable": plan["unreachable"],
        "safety": plan["safety"], "stats": plan["stats"], "warnings": plan["warnings"],
        "plan_time_s": plan["plan_time_s"],
    }


def to_veldmissie_farm(plan: dict, world) -> dict:
    """Missiebestand voor de robot: openagbot.veldmissie v2 met velden, doorgangen, transport en geofence."""
    f = world.frame
    g: FarmGraph = plan["graph"]

    def ll(p):
        lat, lon = f.to_latlon(p[0], p[1])
        return [round(lat, 9), round(lon, 9)]

    segs = []
    for i, sg in enumerate(plan["segments"]):
        pts = sg.pts if sg.type != "baan" else [sg.pts[0], sg.pts[-1]]
        seg = {"nr": i, "type": sg.type, "label": sg.label, "werktuig": sg.implement, "snelheid_kmh": sg.speed_kmh,
               "punten": [ll(p) for p in pts], "koers_deg": [round(_compass(p[2]), 2) for p in pts]}
        if not sg.ok:
            seg["veilig"] = False
        for k in ("veld", "doorgang", "bevestigen"):
            if sg.meta.get(k) is not None:
                seg[k] = sg.meta[k]
        segs.append(seg)
    s = plan["settings"]
    used = [g.lanes.get(pid) or g.passages[pid] for pid in plan["passages_used"]]
    corridors = [lane_corridor(p) if is_lane(p) else passage_corridor(p) for p in used]
    regions = [r.geom.buffer(plan["half_width_m"], join_style="mitre") for r in g.regions
               if r.kind == "rijgebied" and any(sg.meta.get("gebied") == r.name for sg in plan["segments"])]
    allowed = [a for a in (*plan["allowed"], *regions, *corridors) if a is not None and not a.is_empty]
    return {
        "format": FORMAT, "versie": VERSION, "soort": "boerderij",
        "gemaakt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "velden": [{"id": fl["id"], "naam": fl["name"], "rondjes": fl["rounds"], "koers_deg": fl["swath_heading_deg"],
                    "bewerkt_m2": fl["worked_m2"]} for fl in plan["fields"]],
        "volgorde": plan["order"],
        "doorgangen": [{"id": p.id, "naam": p.name, "soort": p.kind, "breedte_m": p.width_m,
                        **({"bochtbreedte_m": p.bend_width_m} if p.bend_width_m else {}),
                        "richting": p.direction, "max_snelheid_kmh": p.max_speed_kmh, "bevestigen": p.confirm,
                        "lijn": [ll(q) for q in p.xy]}
                       for p in used],
        "robot": {"draaicirkel_m": plan["turn_radius_m"], "regelpunt": "midden achteras",
                  "halve_breedte_m": plan["half_width_m"]},
        "werkbreedte_m": s["work_width_m"], "overlap_m": s["overlap_m"],
        "vrije_ruimte_m": effective_clearance(world, s["clearance"]),
        "geofence": geofence_export(world, allowed, plan["segments"], plan["half_width_m"], date=s.get("date"),
                                    mission_dike_mode=s.get("dike_mode")),
        "veiligheid": {"ok": plan["safety"]["ok"], "overtredingen": plan["safety"]["violations"]},
        "niet_bereikbaar": plan["unreachable"],
        "segmenten": segs, "cijfers": plan["stats"], "waarschuwingen": plan["warnings"],
    }


# ---------------------------------------------------------------------------------------------------- voorstellen
def suggest_passages(world, settings: PlanSettings | dict | None = None, max_gap_m: float = 14.0,
                     min_width_m: float = 3.0) -> list[dict]:
    """Voorstellen voor doorgangen: plekken waar twee gebieden dicht bij elkaar liggen zonder gevaar ertussen.

    Geeft per voorstel de rijlijn in lokale meters, de breedte en welke gebieden hij verbindt. Niets wordt bewaard:
    de editor laat ze zien en jij kiest.
    """
    s = settings if isinstance(settings, PlanSettings) else PlanSettings.from_dict(settings)
    s = replace(s, clearance=dict(s.clearance), work_width_m=min(s.work_width_m, 2.0 * s.robot_half_width_m))
    graph = FarmGraph(world, s)
    half = graph.half
    block = [h.geom for h in hazards(world, s.date, s.dike_mode) if h.level == "stop" or h.kind in ("hek", "gat")]
    blocked = unary_union(block) if block else None
    if blocked is not None:
        prepare(blocked)
    linked = set()
    for p in world.passages:
        if (p.id, 0) in graph.nodes:
            linked.add(frozenset((graph.nodes[(p.id, 0)]["region"], graph.nodes[(p.id, 1)]["region"])))
    out = []
    regs = graph.regions
    for i in range(len(regs)):
        for j in range(i + 1, len(regs)):
            a, b = regs[i], regs[j]
            if (a.kind == "rijgebied" and b.kind == "rijgebied") or frozenset((i, j)) in linked:
                continue
            if a.geom.distance(b.geom) > max_gap_m:
                continue
            ring = a.geom.exterior
            n = max(4, int(ring.length))
            runs, run = [], []
            for k in range(n):
                p = ring.interpolate(ring.length * k / n)
                q = nearest_points(b.geom, p)[0]
                d = p.distance(q)
                ok = 0.05 < d <= max_gap_m
                if ok and blocked is not None:
                    ok = not blocked.intersects(LineString([p, q]).buffer(half + 0.2))
                if ok:
                    run.append((p, q, d))
                elif run:
                    runs.append(run)
                    run = []
            if run:
                runs.append(run)
            runs = [r for r in runs if len(r) * ring.length / n >= min_width_m]
            for r in sorted(runs, key=lambda r: (min(t[2] for t in r), -len(r)))[:1]:
                p, q, d = min(r[len(r) // 4: max(len(r) // 4 + 1, 3 * len(r) // 4)], key=lambda t: t[2])
                ux, uy = (q.x - p.x) / d, (q.y - p.y) / d
                width = round(max(min_width_m, min(6.0, len(r) * ring.length / n)), 1)
                out.append({"line": [[round(p.x - ux * 1.0, 2), round(p.y - uy * 1.0, 2)],
                            [round(q.x + ux * 1.0, 2), round(q.y + uy * 1.0, 2)]],
                            "width_m": width, "kind": "hek" if d < 6.0 else "dam", "gap_m": round(d, 1),
                            "name": f"{a.name} - {b.name}", "connects": [a.name, b.name]})
    return out
