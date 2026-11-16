"""Rijpaden: vaste lijnen waarover de robot tussen velden en over het erf rijdt, met een zone eromheen.

Een rijpad is een doorgang van de soort `rijpad` (zelfde tekengereedschap: lijn, breedte, richting, snelheid):

  - de LIJN volgt de robot strikt, zoals een AB-lijn; alleen bochten worden afgerond op zijn draaicirkel;
  - de ZONE (lijn + halve breedte aan elke kant) is het enige gebied buiten de velden waar hij tijdens de missie
    mag zijn. Rijdt hij eruit, dan stopt hij (de robot bewaakt de breedte zelf, net als bij een doorgang);
  - rijpaden die elkaar kruisen of op elkaar eindigen vormen samen een netwerk; waar een rijpad een veld in
    loopt kan de robot erop en eraf.

Anders dan een dam of hek haalt een rijpad GEEN gevaar weg: over een sloot, dijk of weg heen blijft een doorgang
(dam, duiker, brug, oversteek) nodig, en het rijpad loopt daar dan overheen.

Hier staat de meetkunde (zone, bochten afronden); het netwerk zelf bouwt `FarmGraph` in farm_route.py.
"""
from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon
from shapely.ops import substring, unary_union

LANE_KIND = "rijpad"
MIN_TURN_DEG = 2.0            # kleinere knik = recht door
MAX_TURN_DEG = 170.0          # scherper dan dit is keren op de plaats: kan de robot niet
BEND_REACH_M = 4.0            # zo ver voor en na een knik geldt de bochtbreedte minstens


def is_lane(passage) -> bool:
    return passage.kind == LANE_KIND


def lane_corridor(p, extra_m: float = 0.0):
    """Zone van een rijpad: de lijn met de halve breedte aan elke kant (ronde einden)."""
    if len(p.xy) < 2:
        return Polygon()
    line = LineString([(q[0], q[1]) for q in p.xy])
    zone = line.buffer(max(0.1, p.width_m / 2.0 + extra_m), cap_style="round", join_style="round")
    wide = [piece.buffer(p.bend_width_m / 2.0 + extra_m, cap_style="round", join_style="round")
            for piece in bend_pieces(p)]
    return unary_union([zone, *wide]) if wide else zone


def bend_reach(p) -> float:
    """Zo ver voor en na een knik (langs de lijn) is de zone op bochtbreedte."""
    return max(BEND_REACH_M, float(p.bend_width_m or 0.0))


def bend_pieces(p) -> list:
    """Stukken van de rijlijn waar de bochtbreedte geldt: rond elke knik en aan beide einden (daar sluit hij op een
    ander rijpad aan en draait de robot ook). Leeg als het rijpad geen bredere bochten heeft."""
    bw = getattr(p, "bend_width_m", None)
    if not bw or bw <= p.width_m or len(p.xy) < 2:
        return []
    pts = dedupe(p.xy)
    if len(pts) < 2:
        return []
    line = LineString(pts)
    reach, at, s = bend_reach(p), [0.0], 0.0
    for i in range(1, len(pts) - 1):
        s += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        if abs(_turn(pts[i - 1], pts[i], pts[i + 1])) >= math.radians(MIN_TURN_DEG):
            at.append(s)
    at.append(line.length)
    spans: list[list[float]] = []
    for s in at:
        a, b = max(0.0, s - reach), min(line.length, s + reach)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [substring(line, a, b) for a, b in spans if b - a > 1e-6]


def dedupe(pts, tol: float = 0.02) -> list:
    out: list = []
    for p in pts:
        q = (float(p[0]), float(p[1]))
        if not out or math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > tol:
            out.append(q)
    return out


def _straighten(pts, tol: float = 0.01) -> list:
    """Punten weglaten die op de rechte lijn tussen hun buren liggen (een rijpad is geknipt op elke kruising; zo'n
    knippunt mag een bocht verderop niet zijn aanloop afnemen)."""
    out = list(pts[:1])
    for i in range(1, len(pts) - 1):
        a, p, b = out[-1], pts[i], pts[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = math.hypot(dx, dy)
        between = ll > 1e-9 and 0.0 < ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (ll * ll) < 1.0
        if not between or abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / ll > tol:
            out.append(p)
    return out + list(pts[-1:]) if len(pts) > 1 else out


def _turn(a, p, b) -> float:
    ux, uy, vx, vy = p[0] - a[0], p[1] - a[1], b[0] - p[0], b[1] - p[1]
    return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)


def _merge_tight(pts, radius: float) -> list:
    """Een met de hand getekende bocht is vaak een rijtje knikjes vlak achter elkaar, krapper dan de draaicirkel.

    Twee knikken dezelfde kant op die samen meer aanloop nodig hebben dan het been ertussen lang is, worden één
    hoek: het snijpunt van de benen ervoor en erna. Zo wordt het rijtje één bocht die de robot wel kan rijden. Het
    pad wijkt dan iets af van de getekende lijn; of dat binnen de zone blijft keurt de aanroeper.
    """
    pts = list(pts)
    lo = math.radians(MIN_TURN_DEG)
    for _ in range(4 * len(pts)):
        best = None
        for i in range(1, len(pts) - 2):
            t1, t2 = _turn(pts[i - 1], pts[i], pts[i + 1]), _turn(pts[i], pts[i + 1], pts[i + 2])
            if abs(t1) < lo or abs(t2) < lo or t1 * t2 < 0 or abs(t1 + t2) > math.radians(150.0):
                continue
            leg = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            if radius * (math.tan(abs(t1) / 2.0) + math.tan(abs(t2) / 2.0)) <= leg:
                continue
            if best is None or leg < best[0]:
                best = (leg, i)
        if best is None:
            break
        i = best[1]
        a, p, q, b = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        ux, uy, vx, vy = p[0] - a[0], p[1] - a[1], b[0] - q[0], b[1] - q[1]
        den = ux * vy - uy * vx
        if abs(den) < 1e-9:
            break
        k = ((q[0] - p[0]) * vy - (q[1] - p[1]) * vx) / den
        pts[i:i + 2] = [(p[0] + ux * k, p[1] + uy * k)]
    return pts


def fillet(pts, radius: float, step: float = 0.3) -> tuple[list, list[dict]]:
    """Elke knik in de lijn afronden met een boog van `radius` die aan beide benen raakt.

    Geeft (nieuwe punten, knikken die niet passen). Een knik past niet als de boog meer lengte nodig heeft dan
    de benen hebben (te scherp of te kort op elkaar) of als hij scherper is dan MAX_TURN_DEG; die knik blijft
    dan staan zoals getekend, zodat de aanroeper hem kan afkeuren. Twee knikken delen het been ertussen naar
    verhouding van wat ze nodig hebben.
    """
    pts = _straighten(dedupe(pts))
    if len(pts) < 3 or radius <= 0:
        return pts, []
    pts = _merge_tight(pts, radius)
    n = len(pts)
    legs = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(n - 1)]
    theta = [0.0] * n
    want = [0.0] * n
    for i in range(1, n - 1):
        ux, uy = (pts[i][0] - pts[i - 1][0]) / legs[i - 1], (pts[i][1] - pts[i - 1][1]) / legs[i - 1]
        vx, vy = (pts[i + 1][0] - pts[i][0]) / legs[i], (pts[i + 1][1] - pts[i][1]) / legs[i]
        th = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)          # + = linksom
        if abs(th) < math.radians(MIN_TURN_DEG):
            continue
        theta[i] = th
        want[i] = math.inf if abs(th) > math.radians(MAX_TURN_DEG) else radius * math.tan(abs(th) / 2.0)

    def share(i: int, j: int, leg: float) -> float:
        """Deel van het been tussen knik i en buurknik j dat knik i mag gebruiken."""
        if want[j] == 0.0 or math.isinf(want[j]):
            return leg
        return leg * want[i] / (want[i] + want[j])

    out = [pts[0]]
    bad: list[dict] = []
    for i in range(1, n - 1):
        t = want[i]
        if t == 0.0:
            out.append(pts[i])
            continue
        room = math.inf if math.isinf(t) else min(share(i, i - 1, legs[i - 1]), share(i, i + 1, legs[i]))
        if math.isinf(t) or t > room + 1e-6:
            bad.append({"at": [round(pts[i][0], 2), round(pts[i][1], 2)],
                        "angle_deg": round(abs(math.degrees(theta[i])), 1),
                        "need_m": None if math.isinf(t) else round(t, 2),
                        "room_m": None if math.isinf(room) else round(room, 2)})
            out.append(pts[i])
            continue
        th = theta[i]
        s = 1.0 if th > 0 else -1.0
        ux, uy = (pts[i][0] - pts[i - 1][0]) / legs[i - 1], (pts[i][1] - pts[i - 1][1]) / legs[i - 1]
        ax, ay = pts[i][0] - ux * t, pts[i][1] - uy * t                # raakpunt op het been erheen
        cx, cy = ax - uy * s * radius, ay + ux * s * radius            # middelpunt, aan de binnenkant van de bocht
        a0 = math.atan2(ay - cy, ax - cx)
        k = max(2, math.ceil(radius * abs(th) / max(0.05, step)))
        for j in range(k + 1):
            a = a0 + th * j / k
            out.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    out.append(pts[-1])
    return dedupe(out, 0.005), bad


def turn_text(bad: dict, radius: float) -> str:
    """Leesbare reden waarom een bocht niet past."""
    if bad.get("need_m") is None:
        return f"bocht van {bad['angle_deg']:.0f}° is keren op de plaats; dat kan de robot niet"
    return (f"bocht van {bad['angle_deg']:.0f}° heeft {bad['need_m']:.1f} m aanloop nodig aan elke kant "
            f"(draaicirkel {radius:.1f} m), er is {bad['room_m']:.1f} m")
