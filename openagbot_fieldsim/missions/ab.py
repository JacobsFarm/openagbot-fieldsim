"""AB-missies voor een veld uit de wereld, in het formaat van OpenAgbot (`data/ab_line.json`).

Conventie van OpenAgbot (README, ab_navigator.py): baan 0 ligt OP de lijn A->B, baan k ligt k x werkbreedte
opzij aan de `kant` van de lijn, en de baanlengte telt vanaf A. De volgorde (skip pass) rekent de robot zelf uit
`aantal_banen` en `banen_overslaan`, dus die zetten we er niet in: één bron voor de volgorde.

Hier komt alleen bij: A en B zo kiezen dat alle banen binnen het veld vallen, met een kopakker aan beide uiteinden
(ruimte voor de doorloop en de bocht) en een rand langs de zijkant.
"""
from __future__ import annotations

import json
import math
import os

from openagbot_fieldsim.world.geometry import longest_edge


def _line_polygon_span(px, py, dx, dy, poly):
    """Waar de lijn p + s*d de veelhoek snijdt: (s_min, s_max), of None."""
    hits = []
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        # p + s d = a + t e
        s = ((ax - px) * ey - (ay - py) * ex) / den
        t = ((ax - px) * dy - (ay - py) * dx) / den
        if -1e-9 <= t <= 1 + 1e-9:
            hits.append(s)
    if len(hits) < 2:
        return None
    return min(hits), max(hits)


def plan_ab(world, field_id: str, work_width_m: float = 3.0, headland_m: float = 8.0, side_margin_m: float = 0.5,
            speed_kmh: float = 3.0, turn_speed_kmh: float = 2.0, kopakker_extra_m: float = 2.0,
            banen_overslaan: int = 1, edge_index: int | None = None) -> dict:
    """Plan een AB-missie langs de langste rand van het veld (of rand `edge_index`)."""
    fld = world.field_by_id(field_id)
    poly = fld.xy                                # tegen de klok in: binnenkant ligt LINKS van elke rand
    i, _ = longest_edge(poly) if edge_index is None else (edge_index, 0.0)
    # Rand omgekeerd doorlopen, dan ligt het veld RECHTS van A->B (zoals in de README: A linksonder, B linksboven)
    (bx, by), (ax, ay) = poly[i], poly[(i + 1) % len(poly)]
    length = math.hypot(bx - ax, by - ay)
    dx, dy = (bx - ax) / length, (by - ay) / length
    rx, ry = dy, -dx                             # rechts van de rijrichting

    # Hoeveel banen passen er dwars over het veld?
    cross = [(px - ax) * rx + (py - ay) * ry for px, py in poly]
    width_across = max(cross)
    first = side_margin_m + work_width_m / 2.0
    n = max(1, int(math.floor((width_across - side_margin_m - work_width_m / 2.0 - first) / work_width_m)) + 1)

    # Gemeenschappelijk stuk langs de rijrichting waar ELKE baan binnen het veld ligt, min de kopakkers.
    # Alle banen zijn even lang (zo werkt de robot); bij een scheef veld kan het lonen een paar banen te laten
    # vallen voor langere banen, dus kies het aantal met de grootste bewerkte oppervlakte.
    spans = []
    for k in range(n):
        off = first + k * work_width_m
        span = _line_polygon_span(ax + rx * off, ay + ry * off, dx, dy, poly)
        if span is None:
            break
        spans.append(span)
    best = (0.0, 0, 0.0, 0.0)
    lo, hi = -math.inf, math.inf
    for k, (a0, a1) in enumerate(spans, start=1):
        lo, hi = max(lo, a0), min(hi, a1)
        usable = hi - lo - 2.0 * headland_m
        if usable > 0 and k * usable > best[0]:
            best = (k * usable, k, lo + headland_m, hi - headland_m)
    _, n, s_lo, s_hi = best
    if n < 1 or s_hi - s_lo < 5.0:
        raise ValueError(f"veld {fld.name}: geen ruimte voor banen van {work_width_m} m met {headland_m} m kopakker")

    a_xy = (ax + rx * first + dx * s_lo, ay + ry * first + dy * s_lo)
    b_xy = (a_xy[0] + dx * (s_hi - s_lo), a_xy[1] + dy * (s_hi - s_lo))
    lat_a, lon_a = world.frame.to_latlon(*a_xy)
    lat_b, lon_b = world.frame.to_latlon(*b_xy)
    entry = {
        "veldnaam": fld.name,
        "lat_a": round(lat_a, 9), "lon_a": round(lon_a, 9),
        "lat_b": round(lat_b, 9), "lon_b": round(lon_b, 9),
        "werkbreedte_m": work_width_m,
        "aantal_banen": n,
        "banen_overslaan": banen_overslaan,
        "baanlengte_m": round(s_hi - s_lo, 3),
        "kopakker_extra_m": kopakker_extra_m,
        "kant": "rechts",
        "werksnelheid_kmh": speed_kmh,
        "bochtsnelheid_kmh": turn_speed_kmh,
    }
    swaths = []
    for k in range(n):
        o = k * work_width_m
        swaths.append([[a_xy[0] + rx * o, a_xy[1] + ry * o], [b_xy[0] + rx * o, b_xy[1] + ry * o]])
    return {"entry": entry, "field_id": field_id, "a": list(a_xy), "b": list(b_xy), "swaths": swaths,
            "heading_deg": math.degrees(math.atan2(dx, dy)) % 360.0}


def start_command(entry: dict) -> dict:
    """Body voor POST /api/nav/start_ab van OpenAgbot (zelfde velden als de frontend stuurt)."""
    return {
        "work_width_m": entry["werkbreedte_m"], "field_length_m": entry["baanlengte_m"],
        "speed_kmh": entry.get("werksnelheid_kmh", 3.0),
        "lat_a": entry["lat_a"], "lon_a": entry["lon_a"], "lat_b": entry["lat_b"], "lon_b": entry["lon_b"],
        "turn_speed_kmh": entry.get("bochtsnelheid_kmh"), "swath_order": entry.get("baan_volgorde"),
        "aantal_banen": entry.get("aantal_banen"), "banen_overslaan": entry.get("banen_overslaan", 1),
        "kopakker_extra_m": entry.get("kopakker_extra_m"), "kant": entry.get("kant", "rechts"),
    }


def save_ab_line(data_dir: str, entry: dict) -> str:
    """Zet de missie in data/ab_line.json (vervangt een missie met dezelfde veldnaam)."""
    path = os.path.join(data_dir, "ab_line.json")
    items = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                items = json.load(fh)
        except (OSError, ValueError):
            items = []
    items = [e for e in items if e.get("veldnaam") != entry["veldnaam"]] + [entry]
    os.makedirs(data_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(items, fh, indent=4, ensure_ascii=False)
    return path
