"""Veldmissie: het hele veld bewerken zoals een boer het doet, als één rijpad dat de robot echt kan rijden.

    1. Toegestaan gebied: perceel (BRP) min de vrije ruimte langs sloot, weg, hek, gebouw (missions/boundary.py).
    2. Kopakker: `rondjes` rondjes langs de rand, met de werktuigrand op de grens. Bolle hoeken worden afgerond met
       de draaicirkel van de robot (anders kan hij de hoek niet nemen); het stukje in de hoek blijft over en staat
       in het rapport. 0 rondjes = kopakker overslaan: dan blijft er langs de kopse kanten een strook onbewerkt
       waar de robot draait.
    3. Hart: rechte banen in de richting met de minste tijd (alle randrichtingen en elke 5 graden geprobeerd, zoals
       Fields2Cover), elk een halve werkbreedte de bewerkte kopakker in zodat er bij een schuine kopakker geen
       driehoekjes blijven staan. Bij een hoekig veld wordt het hart in vakken verdeeld (boustrophedon).
    4. Volgorde per vak: gewoon heen-en-weer of met banen overslaan (zelfde patroon als `maak_baan_volgorde` van de
       robot), de goedkoopste wint. De bochten zijn Dubins-paden met de draaicirkel van de robot: een U-bocht als
       de zijsprong groter is dan 2R, anders een peer (omega), zoals AgOpenGPS. Elke bocht moet met robot en
       werktuig binnen het toegestane gebied blijven; past hij niet, dan wordt de baan aan dat eind ingekort.
       Bij kopakker eerst draait de robot op de al bewerkte kopakker, bij kopakker laatst op de nog onbewerkte.
    5. Uitvoer: een lijst segmenten (rondje / overgang / baan / bocht) met snelheid en werktuig omhoog/omlaag
       (`to_veldmissie`), en een waypoint-route die de huidige robotsoftware al kan rijden (`to_waypoints`).

Alles in lokale meters (x = oost, y = noord); koersen in kompasgraden (0 = noord, met de klok mee).
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field

from shapely import prepare
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from openagbot_fieldsim.missions.boundary import FieldArea, edge_summary, effective_clearance, field_area
from openagbot_fieldsim.missions.dubins import dubins_all

FORMAT = "openagbot.veldmissie"
VERSION = 2            # 2: geofence, veiligheidscontrole, en (boerderijmissie) velden, doorgangen en transport


# ---------------------------------------------------------------------------------------------------- instellingen
@dataclass
class PlanSettings:
    work_width_m: float = 3.0
    overlap_m: float = 0.15                 # overlap tussen naast elkaar liggende werkgangen
    headland_rounds: int = 3                # 0 = kopakker overslaan
    headland_order: str = "eerst"           # "eerst": rondjes, dan het hart; "laatst": hart, dan de rondjes
    headland_straight: bool = True          # rondje 2 en verder recht trekken (alleen rondje 1 volgt de slingerende rand)
    headland_straighten_m: float = 1.0      # zoveel mag een bochtje in de rand uit de rechte lijn liggen
    round_direction: str = "rechts"         # "rechts" = met de klok mee (veld rechts van de robot), "links" = tegen
    swath_heading_deg: float | None = None  # kompaskoers van de banen; None = de beste uitrekenen
    order: str = "auto"                     # "auto", "heen_en_weer", "overslaan_1", "overslaan_2", ...
    turn_radius_m: float | None = None      # None = uit de robot (wielbasis, stuurhoek) x turn_margin
    turn_margin: float = 1.15
    robot_half_width_m: float = 0.6         # buitenkant wielen vanaf het midden
    clearance: dict = field(default_factory=dict)   # vrije ruimte per soort; leeg = boerderij-instelling/standaard
    date: str | None = None                 # datum van de missie (jjjj-mm-dd) voor nesten in hun periode; None = vandaag
    dike_mode: str | None = None            # dijken: None = per dijk, of ontwijken | negeren | doorgang
    transport_speed_kmh: float = 3.0        # boerderijmissie: rijden tussen de velden
    border_min_m: float = 0.2               # altijd minstens zo ver binnen de BRP-lijn
    speed_kmh: float = 3.0                  # werksnelheid op de banen
    headland_speed_kmh: float | None = None  # rondjes; None = speed_kmh
    turn_speed_kmh: float = 2.0
    swath_extend_m: float | None = None     # banen zoveel de bewerkte kopakker in; None = halve werkbreedte
    point_step_m: float = 0.5               # afstand tussen de punten in bochten en rondjes
    min_swath_m: float = 2.0                # kortere stukjes baan vallen weg
    # De laatste baan past meestal niet precies en schuift dan over zijn buurbaan. Overlapt hij meer dan dit (% van de
    # werkbreedte), dan vervalt hij en blijft de smalle strook aan de slootkant liggen. 100 = altijd die baan rijden.
    max_swath_overlap_pct: float = 100.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "PlanSettings":
        d = dict(d or {})
        known = {k for k in cls.__dataclass_fields__}
        s = cls(**{k: v for k, v in d.items() if k in known and k != "clearance"})
        if d.get("clearance"):
            s.clearance = {k: float(v) for k, v in d["clearance"].items() if v is not None}
        return s


def robot_turn_radius(wheelbase_m: float, track_width_m: float, max_wheel_angle_deg: float) -> float:
    """Kleinste draaicirkel van het midden van de achteras (regelpunt van de robot) bij Ackermann.

    De robot begrenst het BINNENSTE voorwiel op max_angle_degrees (vehicle_controller._max_center_angle), dus het
    midden van de achteras draait om een cirkel van L / tan(max) + spoor / 2.
    """
    return wheelbase_m / math.tan(math.radians(max_wheel_angle_deg)) + track_width_m / 2.0


# ---------------------------------------------------------------------------------------------------- hulp
def _compass(theta: float) -> float:
    return (90.0 - math.degrees(theta)) % 360.0


def _theta(compass_deg: float) -> float:
    return math.radians(90.0 - compass_deg)


def _parts(g, min_area: float = 1.0) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    polys = list(g.geoms) if hasattr(g, "geoms") else [g]
    return sorted([p for p in polys if isinstance(p, Polygon) and p.area >= min_area], key=lambda p: -p.area)


def _ring_coords(poly: Polygon, clockwise: bool) -> list[tuple[float, float]]:
    pts = list(poly.exterior.coords)[:-1]
    s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    ccw = s > 0
    if ccw == clockwise:
        pts = pts[::-1]
    return pts


def _resample_closed(pts, start_s: float, step: float) -> list[tuple[float, float, float]]:
    """Gesloten ring vanaf booglengte start_s één keer rond, punten om `step` m, met theta."""
    ring = LineString(pts + [pts[0]])
    L = ring.length
    n = max(8, int(math.ceil(L / step)))
    out = []
    for i in range(n + 1):
        s = (start_s + L * i / n) % L
        p = ring.interpolate(s)
        q = ring.interpolate((s + 0.3) % L)
        out.append((p.x, p.y, math.atan2(q.y - p.y, q.x - p.x)))
    return out


def _pose_on_ring(pts, s: float):
    ring = LineString(pts + [pts[0]])
    L = ring.length
    p = ring.interpolate(s % L)
    q = ring.interpolate((s + 0.3) % L)
    return (p.x, p.y, math.atan2(q.y - p.y, q.x - p.x))


def _ring_arc(pts, s0: float, s1: float, step: float):
    """Stuk ring van s0 naar s1 in rijrichting (s1 > s0 na modulo), met theta."""
    ring = LineString(pts + [pts[0]])
    L = ring.length
    span = (s1 - s0) % L
    n = max(2, int(math.ceil(span / step)))
    out = []
    for i in range(n + 1):
        s = (s0 + span * i / n) % L
        p = ring.interpolate(s)
        q = ring.interpolate((s + 0.3) % L)
        out.append((p.x, p.y, math.atan2(q.y - p.y, q.x - p.x)))
    return out


def maak_baan_volgorde(aantal_banen: int, banen_overslaan: int = 1) -> list[int]:
    """Zelfde patroon als app/services/ab_navigator.maak_baan_volgorde van de robot."""
    stap = max(1, int(banen_overslaan) + 1)
    volgorde = []
    for start in range(stap):
        laag = list(range(start, aantal_banen, stap))
        if start % 2 == 1:
            laag.reverse()
        volgorde.extend(laag)
    return volgorde


# ---------------------------------------------------------------------------------------------------- planner
@dataclass
class Segment:
    type: str                   # rondje | overgang | baan | bocht | aanrijden
    label: str
    implement: str              # omlaag | omhoog
    speed_kmh: float
    pts: list                   # [(x, y, theta), ...]
    ok: bool = True             # binnen het toegestane gebied en door de veiligheidscontrole?
    meta: dict = field(default_factory=dict)    # veld, doorgang (boerderijmissie), reden als ok False is

    @property
    def length(self) -> float:
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(self.pts, self.pts[1:]))


class FieldPlanner:
    def __init__(self, world, field_id: str, settings: PlanSettings, turn_radius_m: float,
                 start_xy: tuple[float, float] | None = None):
        self.world = world
        self.field_id = field_id
        self.s = settings
        self.R = turn_radius_m
        self.start_xy = start_xy
        self.warnings: list[str] = []
        self.w = settings.work_width_m
        self.w_eff = max(0.1, settings.work_width_m - settings.overlap_m)
        self.half = max(settings.work_width_m / 2.0, settings.robot_half_width_m)   # buitenkant robot/werktuig
        settings.clearance = effective_clearance(world, settings.clearance)    # volledig, zo komt het in de uitvoer
        self.area: FieldArea = field_area(world, field_id, settings.clearance, settings.border_min_m,
                                          date=settings.date, mission_dike_mode=settings.dike_mode)
        self.warnings += self.area.warnings
        self.allowed = self.area.allowed                  # werkgebied: rondjes, banen, cijfers
        self.full = self.area.full if self.area.full is not None else self.allowed    # + niet-bewerken-zones
        self._straight = None
        # Waar het regelpunt mag komen: robot en werktuig binnen het rijgebied (dus ook keren in een niet-bewerken-zone)
        self.drive = self.full.buffer(-(self.half - 0.05), join_style="mitre")
        if self.full is not self.allowed:
            # Een uitgesneden zone geeft nieuwe scherpe hoeken: de mitre-krimp van het grotere rijgebied kan daar net
            # krapper uitvallen dan die van het werkgebied, waar rondje 1 op ligt. Wat in het werkgebied past, past.
            self.drive = unary_union([self.drive, self.allowed.buffer(-(self.half - 0.05), join_style="mitre")])
        self.crop = None                                  # nog niet bewerkt gewas (voor overgangen)
        self._fit_cache: dict = {}
        self.drive_ok = self.drive.buffer(0.1)            # 10 cm speling: rondje 1 ligt precies op de rand
        prepare(self.drive_ok)
        self.allowed_ok = self.allowed.buffer(0.05)
        prepare(self.allowed_ok)

    # ------------------------------------------------------------------ kopakker
    def headland_rings(self) -> list[list[list[tuple[float, float]]]]:
        """Per rondje k de ringen (meer dan een als het veld op een smalle hals in delen uiteenvalt)."""
        rings = []
        cw = self.s.round_direction != "links"
        min_area = 4.0 * self.R * self.R
        for k in range(max(0, int(self.s.headland_rounds))):
            off = self.half + k * self.w_eff
            # Rondje 1 volgt de echte rand; vanaf rondje 2 de rechtgetrokken rand, zodat een bochtje langs de sloot
            # maar door één werkgang loopt en niet door alle rondjes
            base = self.allowed if k == 0 else self.straight_edge()
            eroded = base.buffer(-(off + self.R), join_style="round")
            g = None
            if not eroded.is_empty:
                g = eroded.buffer(self.R, join_style="round").intersection(base.buffer(-off + 0.01, join_style="mitre"))
                if base is not self.allowed:
                    g = g.intersection(self.allowed.buffer(-(self.half - 0.02), join_style="mitre"))
            parts = _parts(g, min_area)
            if not parts:
                self.warnings.append(f"maar {k} van de {self.s.headland_rounds} rondjes passen in dit veld")
                break
            rings.append([_ring_coords(g.simplify(0.05), clockwise=cw) for g in parts])
        return rings

    def straight_edge(self):
        """De rand van het werkgebied met de kleine bochten eruit (rechte stukken tussen de echte hoeken).

        Ligt nergens binnen de echte rand en hooguit `headland_straighten_m` erbuiten: een recht rondje overlapt het
        slingerende rondje ernaast dus hooguit zoveel, en laat nooit een strook liggen."""
        if self._straight is None:
            t = min(float(self.s.headland_straighten_m), 0.35 * self.w_eff) if self.s.headland_straight else 0.0
            g = self.allowed
            if t > 0.05:
                g = self.allowed.buffer(t / 2.0, join_style="mitre", mitre_limit=2.0).simplify(t / 2.0)
                if not g.is_valid:
                    g = g.buffer(0)
                g = _parts(g)[0] if _parts(g) else self.allowed
            self._straight = g
        return self._straight

    def travel_rings(self):
        """Zonder kopakkerrondjes: een rijring op een halve breedte van de rand, alleen om tussen vakken te rijden."""
        cw = self.s.round_direction != "links"
        eroded = self.allowed.buffer(-(self.half + self.R), join_style="round")
        parts = _parts(eroded.buffer(self.R, join_style="round").intersection(
            self.allowed.buffer(-self.half + 0.01, join_style="mitre")) if not eroded.is_empty else None,
            4.0 * self.R * self.R)
        return [_ring_coords(g.simplify(0.05), clockwise=cw) for g in parts] or None

    # ------------------------------------------------------------------ hart: banen
    def core_region(self, n_rounds: int):
        """Het hart (kan uit meer delen bestaan): Polygon, MultiPolygon of None."""
        if n_rounds > 0:
            worked = self.half + (n_rounds - 1) * self.w_eff + self.w / 2.0     # binnenrand bewerkte kopakker
            base = self.allowed if n_rounds == 1 else self.straight_edge()      # zelfde rand als het binnenste rondje
            g = base.buffer(-(worked - self.s.overlap_m), join_style="mitre").intersection(
                self.allowed.buffer(-self.half, join_style="mitre"))
        else:
            g = self.allowed.buffer(-(self.half - self.w / 2.0), join_style="mitre")
        parts = _parts(g, self.w * self.s.min_swath_m * 2.0)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else MultiPolygon(parts)

    def _footprint_ok(self, a, b) -> bool:
        return LineString([a, b]).buffer(self.w / 2.0, cap_style="flat").within(self.allowed_ok)

    def _trim_footprint(self, a, b, step: float = 0.25):
        """Baaneinden inkorten tot het hele werktuig (ook de hoeken bij een schuine rand) binnen de grens valt."""
        if self._footprint_ok(a, b):
            return a, b
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        for end in (0, 1):
            cut = 0.0
            while cut < 3.0 * self.w and L - cut > self.s.min_swath_m:
                tip = a if end == 0 else b
                other = b if end == 0 else a
                piece = LineString([tip, _towards(tip, other, min(L - 0.1, self.w))])
                if piece.buffer(self.w / 2.0, cap_style="flat").within(self.allowed_ok):
                    break
                tip = _towards(tip, other, step)
                if end == 0:
                    a = tip
                else:
                    b = tip
                cut += step
                L = math.hypot(b[0] - a[0], b[1] - a[1])
        return a, b

    def _lines(self, region: Polygon, clip: Polygon, heading_deg: float, trim: bool = False):
        """Banen in kompasrichting heading_deg: [(v_index, [(x0,y0), (x1,y1)]), ...] (A->B in de rijrichting)."""
        th = _theta(heading_deg)
        ux, uy = math.cos(th), math.sin(th)
        vx, vy = -uy, ux                       # links van de rijrichting
        cs = [c for part in _parts(region) for c in part.exterior.coords]
        vs = [x * vx + y * vy for x, y in cs]
        us = [x * ux + y * uy for x, y in cs]
        v0, v1 = min(vs), max(vs)
        u0, u1 = min(us) - 5.0, max(us) + 5.0
        span = v1 - v0
        if span < self.w * 0.5:
            return []
        n = max(1, int(math.ceil((span - self.w) / self.w_eff - 1e-6)) + 1)
        from_high = False                       # banen vanaf de v1-kant leggen (de rest-strook komt dan bij v0)
        if n > 2 and self.s.max_swath_overlap_pct < 100.0:
            step_last = (span - self.w) - (n - 2) * self.w_eff          # afstand van de laatste baan tot zijn buur
            overlap_pct = 100.0 * (self.w - step_last) / self.w
            limit = max(float(self.s.max_swath_overlap_pct), 100.0 * self.s.overlap_m / self.w)   # gewone overlap mag
            if overlap_pct > limit + 1e-6:
                n -= 1
                wv = self._water_v(vx, vy)
                from_high = wv is not None and wv - v0 < v1 - wv       # sloot aan de v0-kant: daar de strook
        out = []
        for i in range(n):
            if n == 1:
                v = (v0 + v1) / 2.0
            elif from_high:
                v = v1 - self.w / 2.0 - (n - 1 - i) * self.w_eff
            else:
                v = min(v0 + self.w / 2.0 + i * self.w_eff, v1 - self.w / 2.0)
            line = LineString([(u0 * ux + v * vx, u0 * uy + v * vy), (u1 * ux + v * vx, u1 * uy + v * vy)])
            hit = line.intersection(clip)
            parts = [hit] if isinstance(hit, LineString) else list(getattr(hit, "geoms", []))
            for p in parts:
                if isinstance(p, LineString) and p.length >= self.s.min_swath_m:
                    a, b = p.coords[0], p.coords[-1]
                    if (b[0] - a[0]) * ux + (b[1] - a[1]) * uy < 0:
                        a, b = b, a
                    if trim:
                        a, b = self._trim_footprint(a, b)
                        if math.hypot(b[0] - a[0], b[1] - a[1]) < self.s.min_swath_m:
                            continue
                    out.append((i, [a, b]))
        return out

    def _water_v(self, vx: float, vy: float):
        """Gemiddelde dwarspositie (v) van de slootranden van het veld, gewogen naar lengte; None = geen sloot."""
        tot = s = 0.0
        for p in self.area.pieces:
            if p.kind != "water" or p.length_m <= 0:
                continue
            for a, b in zip(p.pts, p.pts[1:]):
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                s += L * ((a[0] + b[0]) / 2.0 * vx + (a[1] + b[1]) / 2.0 * vy)
                tot += L
        return s / tot if tot > 0 else None

    def _clip_region(self, core: Polygon, n_rounds: int) -> Polygon:
        ext = self.w / 2.0 if self.s.swath_extend_m is None else self.s.swath_extend_m
        if n_rounds == 0:
            return core.intersection(self.drive)          # middenlijn op een halve breedte van de rand
        return core.buffer(ext, join_style="mitre").intersection(self.drive_ok)

    def best_heading(self, core: Polygon, n_rounds: int) -> float:
        if self.s.swath_heading_deg is not None:
            return float(self.s.swath_heading_deg) % 180.0
        clip = self._clip_region(core, n_rounds)
        cands = set(float(a) for a in range(0, 180, 5))
        for part in _parts(core.simplify(1.0)):
            cs = list(part.exterior.coords)
            for a, b in zip(cs, cs[1:]):
                if math.hypot(b[0] - a[0], b[1] - a[1]) > 10.0:
                    cands.add(round(_compass(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0, 2))
        v_work = self.s.speed_kmh / 3.6
        v_turn = self.s.turn_speed_kmh / 3.6
        t_turn = (math.pi * self.R + 2.0 * self.w) / v_turn
        best = None
        for h in sorted(cands):
            lines = self._lines(core, clip, h)
            if not lines:
                continue
            L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for _, (a, b) in lines)
            cost = L / v_work + len(lines) * t_turn
            if best is None or cost < best[0] - 1e-6:
                best = (cost, h)
        if best is None:
            raise ValueError("geen banen te leggen in het hart van het veld")
        return best[1]

    @staticmethod
    def cells(lines) -> list[list[list]]:
        """Banen in vakken: opeenvolgende banen waarvan de stukken één-op-één overlappen (boustrophedon)."""
        by_i: dict[int, list] = {}
        for i, seg in lines:
            by_i.setdefault(i, []).append(seg)
        cells: list[list[list]] = []
        open_cells: list[tuple[int, list]] = []       # (cel-index, laatste segment)

        def rng(seg, u):
            a, b = seg
            return a[0] * u[0] + a[1] * u[1], b[0] * u[0] + b[1] * u[1]

        if not by_i:
            return []
        a, b = lines[0][1]
        L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        for i in sorted(by_i):
            segs = sorted(by_i[i], key=lambda s: rng(s, u)[0])
            new_open = []
            used = set()
            for seg in segs:
                s0, s1 = rng(seg, u)
                match = [(ci, last) for ci, last in open_cells if min(s1, rng(last, u)[1]) - max(s0, rng(last, u)[0]) > 0]
                if len(match) == 1 and match[0][0] not in used:
                    ci, last = match[0]
                    l0, l1 = rng(last, u)
                    back = [sg for sg in segs if min(rng(sg, u)[1], l1) - max(rng(sg, u)[0], l0) > 0]
                    if len(back) == 1:
                        cells[ci].append(seg)
                        new_open.append((ci, seg))
                        used.add(ci)
                        continue
                cells.append([seg])
                new_open.append((len(cells) - 1, seg))
            open_cells = new_open
        return cells

    # ------------------------------------------------------------------ verbindingen
    def _fits(self, pts) -> bool:
        return self.drive_ok.contains(LineString([(p[0], p[1]) for p in pts]))

    def connect(self, a, b, prefer_inside: bool = True):
        """Dubins van stand a naar stand b; (punten, past) - de kortste die binnen het rijgebied blijft."""
        paths = dubins_all(a, b, self.R)
        if not paths:
            return [a, b], False
        for p in paths:
            pts = p.sample(self.s.point_step_m)
            if not prefer_inside or self._fits(pts):
                return pts, True
        return paths[0].sample(self.s.point_step_m), False

    def turn(self, end_pose, start_pose):
        pts, ok = self.connect(end_pose, start_pose)
        return pts, ok

    def _crosses_crop(self, pts) -> float:
        if self.crop is None or self.crop.is_empty:
            return 0.0
        return LineString([(p[0], p[1]) for p in pts]).intersection(self.crop).length

    def travel(self, a, b, rings) -> tuple[list, bool]:
        """Van a naar b: direct (Dubins), of via een kopakkerring als dat buiten het rijgebied komt of door het nog
        niet bewerkte gewas zou gaan."""
        pts, ok = self.connect(a, b)
        if not rings or (ok and self._crosses_crop(pts) < 1.0):
            return pts, ok
        # Ringen die bij beide punten in de buurt komen eerst; de eerste die past wint
        cands = sorted(rings, key=lambda r: max(LineString(r + [r[0]]).distance(Point(a[0], a[1])),
                                                LineString(r + [r[0]]).distance(Point(b[0], b[1]))))
        direct = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:])) + \
            20.0 * self._crosses_crop(pts)
        best = None
        for ring in cands[:4]:
            route, okk, length = self._travel_ring(a, b, ring)
            if okk and (best is None or length < best[2]):
                best = (route, okk, length)
        if best is not None and (not ok or best[2] < direct):
            return best[0], True
        return pts, ok

    def _travel_ring(self, a, b, ring):
        lead = 2.5 * self.R
        best = None
        for direction_ring in (ring, ring[::-1]):
            rr = LineString(direction_ring + [direction_ring[0]])
            s_in = (rr.project(Point(a[0], a[1])) + lead) % rr.length
            s_out = (rr.project(Point(b[0], b[1])) - lead) % rr.length
            p_in = _pose_on_ring(direction_ring, s_in)
            p_out = _pose_on_ring(direction_ring, s_out)
            p1, ok1 = self.connect(a, p_in)
            arc = _ring_arc(direction_ring, s_in, s_out, self.s.point_step_m)
            p2, ok2 = self.connect(p_out, b)
            route = p1 + arc[1:] + p2[1:]
            length = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(route, route[1:]))
            okk = ok1 and ok2
            length += 20.0 * self._crosses_crop(route)
            if best is None or (okk and not best[1]) or (okk == best[1] and length < best[2]):
                best = (route, okk, length)
        return best

    # ------------------------------------------------------------------ volgorde in een vak
    def _pattern_orders(self, m: int) -> list[tuple[str, list[int]]]:
        if self.s.order != "auto":
            if self.s.order == "heen_en_weer":
                return [("heen_en_weer", list(range(m)))]
            k = int(self.s.order.rsplit("_", 1)[-1])
            return [(self.s.order, maak_baan_volgorde(m, k))]
        out = [("heen_en_weer", list(range(m)))]
        for k in (1, 2, 3):
            if m > k + 1:
                out.append((f"overslaan_{k}", maak_baan_volgorde(m, k)))
        return out

    def plan_cell(self, cell: list[list], entry_pose):
        """Beste volgorde voor één vak, gegeven waar de robot binnenkomt. Geeft (banen, bochten, naam, kosten)."""
        m = len(cell)
        best = None
        for name, order in self._pattern_orders(m):
            for mirror in (False, True):
                seq = [m - 1 - i for i in order] if mirror else order
                for flip in (False, True):
                    swaths = []
                    for j, idx in enumerate(seq):
                        a, b = cell[idx]
                        fwd = (j % 2 == 0) != flip
                        swaths.append([a, b] if fwd else [b, a])
                    cost, turns = self._cost_swaths(swaths, entry_pose)
                    if best is None or cost < best[0]:
                        best = (cost, swaths, turns, name)
        return best

    def _pose(self, a, b, at_end: bool):
        th = math.atan2(b[1] - a[1], b[0] - a[0])
        p = b if at_end else a
        return (p[0], p[1], th)

    def _cost_swaths(self, swaths, entry_pose):
        cost = 0.0
        if entry_pose is not None:
            p0 = self._pose(*swaths[0], at_end=False)
            cost += math.hypot(p0[0] - entry_pose[0], p0[1] - entry_pose[1]) * 1.2
        turns = []
        for s0, s1 in zip(swaths, swaths[1:]):
            e, b = self._pose(*s0, at_end=True), self._pose(*s1, at_end=False)
            length = self._fit_length(e, b)
            if length is None:
                trim = self.R + self.half
                e2 = (*_towards(e[:2], s0[0], trim), e[2])
                b2 = (*_towards(b[:2], s1[1], trim), b[2])
                length = self._fit_length(e2, b2)
                length = 500.0 + (length or 1000.0) if length is None else length + 4.0 * trim
            cost += length
            turns.append(length)
        return cost, turns

    def _fit_length(self, a, b):
        """Lengte van de kortste Dubins die binnen het rijgebied past, of None."""
        key = (round(a[0], 2), round(a[1], 2), round(a[2], 3), round(b[0], 2), round(b[1], 2), round(b[2], 3))
        if key in self._fit_cache:
            return self._fit_cache[key]
        res = None
        for p in dubins_all(a, b, self.R):
            if self._fits(p.sample(max(0.5, self.s.point_step_m))):
                res = p.length
                break
        self._fit_cache[key] = res
        return res

    # ------------------------------------------------------------------ inkorten tot de bocht past
    def fit_turns(self, swaths, label_prefix: str):
        """Bochten tussen opeenvolgende banen; past een bocht niet, kort dan de twee baaneinden in."""
        turns = []
        step = 0.5
        max_trim = 4.0 * self.R + self.w
        for k in range(len(swaths) - 1):
            trimmed = 0.0
            while True:
                a0, b0 = swaths[k]
                a1, b1 = swaths[k + 1]
                pts, ok = self.turn(self._pose(a0, b0, True), self._pose(a1, b1, False))
                if ok or trimmed >= max_trim:
                    break
                if math.hypot(b0[0] - a0[0], b0[1] - a0[1]) < 2 * step + 1 or \
                        math.hypot(b1[0] - a1[0], b1[1] - a1[1]) < 2 * step + 1:
                    break
                swaths[k] = [a0, _towards(b0, a0, step)]
                swaths[k + 1] = [_towards(a1, b1, step), b1]
                trimmed += step
            if not ok:
                self.warnings.append(f"{label_prefix} bocht {k + 1}: past niet binnen het toegestane gebied")
            turns.append((pts, ok, trimmed))
        return turns

    # ------------------------------------------------------------------ alles
    def plan(self) -> dict:
        t0 = time.time()
        s = self.s
        rings = self.headland_rings()
        n_rounds = len(rings)
        core = self.core_region(n_rounds)
        segments: list[Segment] = []
        v_hl = s.headland_speed_kmh or s.speed_kmh

        # Startpunt: waar de robot het veld op komt (startplek van de wereld), anders de eerste hoek
        start_xy = self.start_xy
        if start_xy is None:
            c = self.allowed.exterior.coords[0]
            start_xy = (c[0], c[1])

        def add(seg: Segment):
            if len(seg.pts) >= 2:
                segments.append(seg)

        def last_pose():
            return segments[-1].pts[-1] if segments else None

        step = 0.5
        max_trim_steps = int((4.0 * self.R + self.w) / step)

        def trim_last_exit() -> bool:
            """Laatste baan aan het eind inkorten (geen ruimte om van de baan af te komen)."""
            if not segments or segments[-1].type != "baan":
                return False
            sg = segments[-1]
            a, b = sg.pts[0], sg.pts[-1]
            if math.hypot(b[0] - a[0], b[1] - a[1]) < s.min_swath_m + step:
                return False
            nb = _towards(b[:2], a[:2], step)
            sg.pts = [a, (nb[0], nb[1], b[2])]
            return True

        def enter_ring(ring, rl, lane_change):
            """Waar de robot een ring op kan rijden: eerst vlak voor hem, dan verder weg, ook achter hem."""
            lp = last_pose()
            base = rl.project(Point(lp[0], lp[1]))
            best = None
            leads = [2.5, 4.0, 6.0, 9.0, 13.0, 18.0, -2.0, -4.0, -7.0, -11.0]
            leads += [24.0 + 3.0 * i for i in range(int(rl.length / (3.0 * self.R)))]
            for lead in leads:
                s0_try = (base + lead * self.R) % rl.length
                p_try, ok_try = self.connect(lp, _pose_on_ring(ring, s0_try))
                if ok_try and (lane_change or self._crosses_crop(p_try) < 1.0):
                    L_try = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(p_try, p_try[1:]))
                    if best is None or L_try < best[0]:
                        best = (L_try, p_try, s0_try)
                    if 0 < lead <= 4.0:
                        break
            if best is not None:
                return best[1], True, best[2]
            s0 = (base + 2.5 * self.R) % rl.length
            pts, ok = self.connect(lp, _pose_on_ring(ring, s0))
            return pts, ok, s0

        def do_rounds(order_idx):
            for k in order_idx:
                todo = list(range(len(rings[k])))
                while todo:
                    ref = last_pose() or (start_xy[0], start_xy[1], 0.0)
                    pi = min(todo, key=lambda i: LineString(rings[k][i] + [rings[k][i][0]]).distance(
                        Point(ref[0], ref[1])))
                    todo.remove(pi)
                    ring = rings[k][pi]
                    name = f"rondje {k + 1}" + ("abcdefghij"[pi % 10] if len(rings[k]) > 1 else "")
                    rl = LineString(ring + [ring[0]])
                    if not segments:
                        s0 = rl.project(Point(*start_xy))
                    else:
                        lane_change = prev_k[0] is not None and abs(k - prev_k[0]) == 1 and \
                            rl.distance(Point(*last_pose()[:2])) < 1.5 * self.w
                        pts, ok, s0 = enter_ring(ring, rl, lane_change)
                        trims = 0
                        while not ok and trims < max_trim_steps and trim_last_exit():
                            pts, ok, s0 = enter_ring(ring, rl, lane_change)
                            trims += 1
                        if not ok and not lane_change:
                            pts, ok = self.travel(last_pose(), _pose_on_ring(ring, s0), rings[0])
                        add(Segment("overgang", f"naar {name}", "omlaag" if lane_change else "omhoog",
                                    v_hl if lane_change else s.turn_speed_kmh, pts, ok))
                    pts = _resample_closed(ring, s0, s.point_step_m)
                    add(Segment("rondje", name, "omlaag", v_hl, pts, self._fits(pts)))
                    prev_k[0] = k

        prev_k = [None]
        heading = None
        cells_out = []

        def do_core():
            nonlocal heading
            if core is None:
                self.warnings.append("geen hart over na de kopakker: alleen rondjes")
                return
            heading = self.best_heading(core, n_rounds)
            lines = self._lines(core, self._clip_region(core, n_rounds), heading, trim=True)
            cells = self.cells(lines)
            travel_ring = [r for k in range(n_rounds) for r in rings[k]] if rings else self.travel_rings()
            remaining = list(range(len(cells)))
            while remaining:
                entry = last_pose() or (start_xy[0], start_xy[1], 0.0)
                options = [(ci, self.plan_cell(cells[ci], entry)) for ci in remaining]
                ci, (cost, swaths, _, name) = min(options, key=lambda t: t[1][0])
                remaining.remove(ci)
                # Nog niet bewerkt gewas: de vakken die nog moeten (niet het vak waar hij nu heen gaat)
                todo_geom = [LineString(sw).buffer(self.w / 2.0, cap_style="flat") for cj in remaining
                             for sw in cells[cj]]
                self.crop = unary_union(todo_geom).buffer(-0.5) if todo_geom else None
                turns = self.fit_turns(swaths, f"vak {len(cells_out) + 1}")
                if segments:
                    pts, ok = self.travel(last_pose(), self._pose(*swaths[0], at_end=False), travel_ring)
                    trims = 0
                    while not ok and trims < max_trim_steps:
                        cut_exit = trim_last_exit()
                        a0, b0 = swaths[0]
                        cut_entry = math.hypot(b0[0] - a0[0], b0[1] - a0[1]) > s.min_swath_m + step
                        if cut_entry:
                            swaths[0] = [_towards(a0, b0, step), b0]
                        if not (cut_exit or cut_entry):
                            break
                        pts, ok = self.travel(last_pose(), self._pose(*swaths[0], at_end=False), travel_ring)
                        trims += 1
                    add(Segment("overgang", f"naar vak {len(cells_out) + 1}", "omhoog", s.turn_speed_kmh, pts, ok))
                for k, sw in enumerate(swaths):
                    a, b = sw
                    th = math.atan2(b[1] - a[1], b[0] - a[0])
                    add(Segment("baan", f"vak {len(cells_out) + 1} baan {k + 1}", "omlaag", s.speed_kmh,
                                [(a[0], a[1], th), (b[0], b[1], th)], self._fits([a, b])))
                    if k < len(turns):
                        pts, ok, _ = turns[k]
                        add(Segment("bocht", f"vak {len(cells_out) + 1} bocht {k + 1}", "omhoog",
                                    s.turn_speed_kmh, pts, ok))
                cells_out.append({"swaths": len(swaths), "pattern": name})

        if s.headland_order == "laatst":
            do_core()
            do_rounds(list(range(n_rounds - 1, -1, -1)))
        else:
            do_rounds(list(range(n_rounds)))
            do_core()

        if not segments:
            raise ValueError("niets te plannen in dit veld")
        if self.area.no_work is not None:
            segments = self._lift_no_work(segments)
        from openagbot_fieldsim.missions.safety import precheck_segments
        safety = precheck_segments(self.world, segments, self.half, date=s.date, mission_dike_mode=s.dike_mode)
        if not safety["ok"]:
            self.warnings.append(f"veiligheidscontrole: {len(safety['violations'])} stukken raken een verboden "
                                 f"gebied of obstakel ({', '.join(sorted({v['label'] for v in safety['violations']}))})")
        stats = self.stats(segments, rings, core)
        bad = [sg.label for sg in segments if not sg.ok]
        if bad:
            self.warnings.append(f"{len(bad)} stukken komen buiten het toegestane gebied: {', '.join(bad[:5])}")
        return {
            "field_id": self.field_id,
            "settings": asdict(s),
            "turn_radius_m": round(self.R, 3),
            "swath_heading_deg": None if heading is None else round(heading, 2),
            "cells": cells_out,
            "rounds": n_rounds,
            "segments": segments,
            "allowed": self.allowed,
            "full": self.full,
            "no_work": self.area.no_work,
            "safety": safety,
            "half_width_m": self.half,
            "core": core,
            "edges": self.area.pieces,
            "edge_summary": edge_summary(self.area),
            "stats": stats,
            "warnings": self.warnings,
            "plan_time_s": round(time.time() - t0, 2),
        }

    def _lift_no_work(self, segments: list) -> list:
        """Waar een werkgang door een niet-werken-gebied gaat (doorrijdbaar gat, rijzone): werktuig omhoog."""
        nw = self.area.no_work
        out = []
        for sg in segments:
            line = LineString([(p[0], p[1]) for p in sg.pts])
            if sg.implement != "omlaag" or not line.intersects(nw):
                out.append(sg)
                continue
            pieces = []
            for geom, down in ((line.difference(nw), True), (line.intersection(nw), False)):
                for g in getattr(geom, "geoms", [geom]):
                    if isinstance(g, LineString) and g.length > 0.05:
                        mid = g.interpolate(0.5, normalized=True)
                        pieces.append((line.project(mid), g, down))
            if not any(not down for _, _, down in pieces):
                out.append(sg)
                continue
            for _, g, down in sorted(pieces, key=lambda t: t[0]):
                cs = list(g.coords)
                th = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in zip(cs, cs[1:])]
                pts = [(c[0], c[1], th[min(i, len(th) - 1)]) for i, c in enumerate(cs)]
                out.append(Segment(sg.type if down else "overgang", sg.label if down else f"{sg.label} (niet werken)",
                                   "omlaag" if down else "omhoog", sg.speed_kmh, pts, sg.ok, dict(sg.meta)))
        return out

    # ------------------------------------------------------------------ cijfers
    def stats(self, segments, rings, core) -> dict:
        worked = []
        for sg in segments:
            if sg.implement == "omlaag":
                line = LineString([(p[0], p[1]) for p in sg.pts])
                if line.length > 0.01:
                    worked.append(line.buffer(self.w / 2.0, cap_style="flat", join_style="round"))
        cover = unary_union(worked) if worked else Polygon()
        fld = self.area.field_poly
        allowed = self.allowed if self.area.no_work is None else self.allowed.difference(self.area.no_work)
        sum_area = sum(g.area for g in worked)
        by_type: dict[str, dict] = {}
        total_t = 0.0
        for sg in segments:
            d = by_type.setdefault(sg.type, {"n": 0, "length_m": 0.0, "time_s": 0.0})
            L = sg.length
            t = L / max(0.1, sg.speed_kmh / 3.6)
            d["n"] += 1
            d["length_m"] += L
            d["time_s"] += t
            total_t += t
        for d in by_type.values():
            d["length_m"] = round(d["length_m"], 1)
            d["time_s"] = round(d["time_s"], 0)
        work_len = sum(d["length_m"] for k, d in by_type.items() if k in ("rondje", "baan"))
        total_len = sum(d["length_m"] for d in by_type.values())
        missed = allowed.difference(cover)
        return {
            "field_m2": round(fld.area, 1),
            "allowed_m2": round(allowed.area, 1),
            "outside_allowed_m2": round(fld.area - allowed.area, 1),
            "worked_m2": round(cover.intersection(allowed).area, 1),
            "coverage_allowed_pct": round(100.0 * cover.intersection(allowed).area / max(1.0, allowed.area), 2),
            "coverage_field_pct": round(100.0 * cover.intersection(fld).area / max(1.0, fld.area), 2),
            "missed_m2": round(missed.area, 1),
            "overlap_pct": round(100.0 * (sum_area - cover.area) / max(1.0, cover.area), 1),
            "worked_outside_allowed_m2": round(cover.difference(allowed.buffer(0.05)).area, 2),
            "length_m": round(total_len, 1),
            "work_length_m": round(work_len, 1),
            "efficiency_pct": round(100.0 * work_len / max(1.0, total_len), 1),
            "time_min": round(total_t / 60.0, 1),
            "ha_per_h": round(cover.intersection(allowed).area / 1e4 / max(1e-6, total_t / 3600.0), 3),
            "by_type": by_type,
            "missed_geom": missed,
        }


def _towards(p, q, d):
    L = math.hypot(q[0] - p[0], q[1] - p[1]) or 1.0
    return (p[0] + (q[0] - p[0]) * d / L, p[1] + (q[1] - p[1]) * d / L)


# ---------------------------------------------------------------------------------------------------- api
def plan_field(world, field_id: str, settings: PlanSettings | dict | None = None, vehicle=None,
               start_xy: tuple[float, float] | None = None) -> dict:
    """Plan een veldmissie. `vehicle` = VehicleParams (draaicirkel); anders de OpenAgbot-standaard."""
    s = settings if isinstance(settings, PlanSettings) else PlanSettings.from_dict(settings)
    if s.turn_radius_m:
        R = float(s.turn_radius_m)
    else:
        wb, tw, ang = (1.2, 1.0, 30.0) if vehicle is None else (vehicle.wheelbase_m, vehicle.track_width_m,
                                                               vehicle.max_wheel_angle_deg)
        R = robot_turn_radius(wb, tw, ang) * s.turn_margin
    if start_xy is None and world.start_pose is not None:
        sx, sy, _ = world.start_xy_heading()
        start_xy = (sx, sy)
    return FieldPlanner(world, field_id, s, R, start_xy).plan()


def _r(v, n=3):
    return round(float(v), n)


def _poly_json(g):
    if g is None or g.is_empty:
        return []
    polys = list(g.geoms) if hasattr(g, "geoms") else [g]
    out = []
    for p in polys:
        if isinstance(p, Polygon):
            out.append([[[_r(x), _r(y)] for x, y in p.exterior.coords]] +
                       [[[_r(x), _r(y)] for x, y in r.coords] for r in p.interiors])
    return out


def plan_view(plan: dict) -> dict:
    """JSON voor de UI (lokale meters)."""
    st = {k: v for k, v in plan["stats"].items() if k != "missed_geom"}
    missed = plan["stats"]["missed_geom"]
    missed = missed.simplify(0.1) if not missed.is_empty else missed
    return {
        "kind": "veldmissie",
        "field_id": plan["field_id"],
        "settings": plan["settings"],
        "turn_radius_m": plan["turn_radius_m"],
        "swath_heading_deg": plan["swath_heading_deg"],
        "rounds": plan["rounds"],
        "cells": plan["cells"],
        "allowed": _poly_json(plan["allowed"]),
        "skipped": _poly_json(plan["full"].difference(plan["allowed"]).buffer(-0.01)) if plan.get("full") is not None
        else [],
        "no_work": _poly_json(plan.get("no_work")),
        "safety": plan.get("safety", {"ok": True, "violations": []}),
        "core": _poly_json(plan["core"]),
        "missed": _poly_json(missed),
        "edges": [e.to_dict() for e in plan["edges"]],
        "edge_summary": plan["edge_summary"],
        "segments": [{"type": sg.type, "label": sg.label, "implement": sg.implement, "speed_kmh": sg.speed_kmh,
                      "ok": sg.ok, "meta": sg.meta, "pts": [[_r(p[0], 2), _r(p[1], 2)] for p in sg.pts]}
                     for sg in plan["segments"]],
        "stats": st,
        "warnings": plan["warnings"],
        "plan_time_s": plan["plan_time_s"],
    }


def to_veldmissie(plan: dict, world, field_name: str = "") -> dict:
    """Missiebestand voor de robot (formaat openagbot.veldmissie v1, zie docs/veldmissie.md in OpenAgbot)."""
    f = world.frame

    def ll(p):
        lat, lon = f.to_latlon(p[0], p[1])
        return [round(lat, 9), round(lon, 9)]

    segs = []
    for i, sg in enumerate(plan["segments"]):
        pts = sg.pts if sg.type not in ("baan",) else [sg.pts[0], sg.pts[-1]]
        seg = {
            "nr": i, "type": sg.type, "label": sg.label, "werktuig": sg.implement, "snelheid_kmh": sg.speed_kmh,
            "punten": [ll(p) for p in pts],
            "koers_deg": [round(_compass(p[2]), 2) for p in pts],
        }
        if not sg.ok:
            seg["veilig"] = False
        for k in ("veld", "doorgang", "bevestigen"):
            if sg.meta.get(k) is not None:
                seg[k] = sg.meta[k]
        segs.append(seg)
    st = {k: v for k, v in plan["stats"].items() if k not in ("missed_geom",)}
    s = plan["settings"]
    from openagbot_fieldsim.missions.safety import geofence_export
    half = plan.get("half_width_m", max(s["work_width_m"] / 2.0, s["robot_half_width_m"]))
    return {
        "format": FORMAT, "versie": VERSION,
        "gemaakt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "veld": {"id": plan["field_id"], "naam": field_name,
                 "grens": [ll(p) for p in plan["allowed"].exterior.coords]},
        "robot": {"draaicirkel_m": plan["turn_radius_m"], "regelpunt": "midden achteras",
                  "halve_breedte_m": max(s["work_width_m"] / 2.0, s["robot_half_width_m"])},
        "werkbreedte_m": s["work_width_m"], "overlap_m": s["overlap_m"],
        "kopakker": {"rondjes": plan["rounds"], "volgorde": s["headland_order"], "richting": s["round_direction"]},
        "banen": {"koers_deg": plan["swath_heading_deg"], "vakken": plan["cells"]},
        "vrije_ruimte_m": s["clearance"],
        "geofence": geofence_export(world, [plan.get("full") or plan["allowed"]], plan["segments"], half,
                                    date=s.get("date"),
                                    mission_dike_mode=s.get("dike_mode")),
        "veiligheid": {"ok": plan.get("safety", {}).get("ok", True),
                       "overtredingen": plan.get("safety", {}).get("violations", [])},
        "segmenten": segs,
        "cijfers": st,
        "waarschuwingen": plan["warnings"],
    }


def to_waypoints(plan: dict, world, straight_step_m: float = 25.0, curve_step_m: float = 1.0) -> list[dict]:
    """Route voor de HUIDIGE robotsoftware (data/waypoints.json, POST /api/nav/start_pure_pursuit).

    Rechte banen: begin en eind (plus een punt om de `straight_step_m` m), bochten en rondjes om de
    `curve_step_m` m. De Navigator volgt het lijnstuk tussen twee punten en gaat door zodra hij de loodlijn door het
    punt passeert. Wat hij (nog) niet kent: snelheid per stuk en werktuig omhoog/omlaag - dat staat in de
    veldmissie (to_veldmissie) en vraagt een aanpassing in OpenAgbot.
    """
    f = world.frame
    out = []

    def push(name, x, y):
        if out:
            px, py = out[-1]["_xy"]
            if math.hypot(x - px, y - py) < 0.3:
                return
        lat, lon = f.to_latlon(x, y)
        out.append({"way_point_name": name, "lat": round(lat, 9), "lon": round(lon, 9), "_xy": (x, y)})

    for i, sg in enumerate(plan["segments"]):
        tag = sg.label.replace(" ", "_")
        if sg.type == "baan":
            (ax, ay, _), (bx, by, _) = sg.pts[0], sg.pts[-1]
            L = math.hypot(bx - ax, by - ay)
            n = max(1, int(math.ceil(L / straight_step_m)))
            for k in range(n + 1):
                push(f"{tag}_{k}", ax + (bx - ax) * k / n, ay + (by - ay) * k / n)
        else:
            acc = curve_step_m
            prev = None
            for j, p in enumerate(sg.pts):
                if prev is not None:
                    acc += math.hypot(p[0] - prev[0], p[1] - prev[1])
                prev = p
                if acc >= curve_step_m or j == len(sg.pts) - 1:
                    push(f"{tag}_{j}", p[0], p[1])
                    acc = 0.0
    for wp in out:
        wp.pop("_xy", None)
    return out
