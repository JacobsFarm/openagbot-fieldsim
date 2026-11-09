"""Vlakke meetkunde in lokale meters (x = oost, y = noord). Klein en zonder afhankelijkheden."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]


def polygon_area(poly: Sequence[Point]) -> float:
    """Oppervlakte met teken: positief = tegen de klok in."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def ccw(poly: Sequence[Point]) -> list[Point]:
    """Zelfde veelhoek, tegen de klok in (wat three.js en de triangulatie verwachten)."""
    pts = list(poly)
    return pts if polygon_area(pts) >= 0 else pts[::-1]


def point_in_polygon(x: float, y: float, poly: Sequence[Point]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def bbox(points: Iterable[Point]) -> tuple[float, float, float, float]:
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def centroid(poly: Sequence[Point]) -> Point:
    a = polygon_area(poly)
    if abs(a) < 1e-9:
        xs, ys = zip(*poly)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        k = x1 * y2 - x2 * y1
        cx += (x1 + x2) * k
        cy += (y1 + y2) * k
    return cx / (6.0 * a), cy / (6.0 * a)


def distance_to_segment(px: float, py: float, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def rectangle(cx: float, cy: float, w: float, h: float, angle_deg: float = 0.0) -> list[Point]:
    """Rechthoek w (langs x) bij h rond (cx, cy), gedraaid tegen de klok in."""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + dx * c - dy * s, cy + dx * s + dy * c))
    return out


def circle(cx: float, cy: float, r: float, n: int = 24) -> list[Point]:
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def longest_edge(poly: Sequence[Point]) -> tuple[int, float]:
    """Index i van de langste rand (poly[i] -> poly[i+1]) en de lengte."""
    best, best_len = 0, -1.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        d = math.hypot(x2 - x1, y2 - y1)
        if d > best_len:
            best, best_len = i, d
    return best, best_len
