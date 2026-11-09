"""Ritlogs van OpenAgbot (`data/logs/rit_*.csv`) inlezen voor replay en vergelijking.

De CSV is dezelfde op de robot en in de sim (de logger van OpenAgbot draait in beide). Per regel een regellus-tik
(25 Hz) met positie (regelpunt), koers, stuurhoek, snelheden, afwijking van de lijn, GNSS-kwaliteit en VESC-data.
Hier gaan alleen de kolommen mee die de replay toont; de rest blijft in het bestand.
"""
from __future__ import annotations

import csv
import math
import os

COLUMNS = {   # CSV-kolom -> sleutel in de replay
    "Heading_Echt": "heading", "Stuurhoek": "steer", "Doel_kmh": "target_kmh", "Echt_kmh": "speed_kmh",
    "Afwijking_m": "xte", "Pitch_deg": "pitch", "WP_Doel": "swath", "Accu_V": "battery_v",
}


def _t(stamp: str) -> float | None:
    try:
        h, m, s = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def list_logs(sources: dict[str, str]) -> list[dict]:
    out = []
    for source, folder in sources.items():
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder), reverse=True):
            if name.startswith("rit_") and name.endswith(".csv"):
                path = os.path.join(folder, name)
                out.append({"source": source, "file": name, "size_kb": round(os.path.getsize(path) / 1024)})
    return out


def load_log(path: str, frame, step: int = 1) -> dict:
    """Lees een ritlog en zet de posities om naar lokale meters van de wereld (`frame`)."""
    rows = []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    t0 = None
    out = {k: [] for k in ("t", "x", "y", "mode", *COLUMNS.values())}
    day = 0.0
    prev = None
    for i, r in enumerate(rows):
        if i % step:
            continue
        lat, lon, t = _f(r.get("Lat")), _f(r.get("Lon")), _t(r.get("Tijdstip", ""))
        if lat is None or lon is None or t is None or (lat == 0.0 and lon == 0.0):
            continue
        if prev is not None and t + day < prev - 3600:     # over middernacht
            day += 86400.0
        t += day
        prev = t
        if t0 is None:
            t0 = t
        x, y = frame.to_xy(lat, lon)
        out["t"].append(round(t - t0, 3))
        out["x"].append(round(x, 3))
        out["y"].append(round(y, 3))
        out["mode"].append(r.get("Modus", ""))
        for col, key in COLUMNS.items():
            out[key].append(_f(r.get(col)))
    return {**out, "stats": stats(out), "file": os.path.basename(path)}


def stats(d: dict) -> dict:
    """Kerngetallen: duur, afstand, afwijking op de banen (alleen TRACKING, zoals extract_ride_report.py)."""
    n = len(d["t"])
    if n < 2:
        return {"points": n}
    dist = sum(math.hypot(d["x"][i] - d["x"][i - 1], d["y"][i] - d["y"][i - 1]) for i in range(1, n))
    xte = [abs(e) for e, m in zip(d["xte"], d["mode"]) if e is not None and m == "TRACKING"]
    xte.sort()
    pick = lambda q: xte[min(len(xte) - 1, int(q * len(xte)))] if xte else None  # noqa: E731
    xs, ys = d["x"], d["y"]
    return {
        "points": n,
        "duration_s": d["t"][-1],
        "distance_m": round(dist, 1),
        "xte_median_m": pick(0.5), "xte_p95_m": pick(0.95), "xte_max_m": xte[-1] if xte else None,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "center_distance_m": round(math.hypot((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2), 1),
    }


def waypoints_from_log(d: dict, frame, spacing_m: float = 2.0, modes=("TRACKING", "TURNING")) -> list[dict]:
    """Route uit een rit als waypoints (elke `spacing_m`), om hem in de sim na te laten rijden."""
    pts = []
    last = None
    for x, y, m in zip(d["x"], d["y"], d["mode"]):
        if m not in modes:
            continue
        if last is None or math.hypot(x - last[0], y - last[1]) >= spacing_m:
            lat, lon = frame.to_latlon(x, y)
            pts.append({"lat": lat, "lon": lon, "way_point_name": f"rit {len(pts) + 1}"})
            last = (x, y)
    return pts
