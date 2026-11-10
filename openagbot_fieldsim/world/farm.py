"""`farm.json`: de eigen aanpassingen van de boer, los van de brondata in `world.json`.

"Bouw" (PDOK, AHN) schrijft `world.json` opnieuw; alles wat je zelf tekent of wijzigt staat hier en blijft dus
bewaard. Bij het laden wordt dit bestand over de brondata gelegd (`apply_farm`):

    {
      "format": "openagbot_fieldsim.farm", "version": 1,
      "settings": {"dike_mode": null, "clearance": {...}, "safety": {...}},
      "start_pose": {"lat", "lon", "heading_deg"} | null,
      "hidden":  [{"layer": "fields", "id": "perceel_3", "anchor": [lat, lon]}],     # verborgen bronobjecten
      "objects": {"fields": [...], "ditches": [...], "dikes": [...], "no_go": [...], "zones": [...],
                  "passages": [...], "nests": [...], "markers": [...], "trees": [...], "fences": [...]}
    }

Een object in `objects` is een eigen object, of (met `"replaces": {"id", "anchor"}`) een overschreven bronobject.
Het bronobject wordt eerst op id gezocht en gecontroleerd met het anker (een punt in of op het object); klopt het id
na een nieuwe bouw niet meer (BRP-percelen worden doorgenummerd), dan wordt op het anker gezocht. Is het bronobject
echt weg, dan blijft de overschrijving als eigen object bestaan: je werk verdwijnt nooit stil.

Alle coördinaten zijn lat/lon (ETRS89), net als in world.json.
"""
from __future__ import annotations

import copy
import json
import math
import os
import time

from openagbot_fieldsim.world.model import LAYERS, ensure_ids, geometry_of

FORMAT = "openagbot_fieldsim.farm"
VERSION = 1
FILE = "farm.json"
BACKUPS = 30                       # zoveel oude versies blijven in backups/ staan
DEFAULT_SETTINGS = {
    "dike_mode": None,             # None = per dijk; anders voor alle dijken: ontwijken | negeren | doorgang
    "clearance": {},               # vrije ruimte per soort (m), over de standaard van de planner heen
    # ditch_zone_m: verboden strook langs elke sloot en elk water (robot stopt als hij erin komt);
    # plan_margin_m: zoveel blijft de planner daar nog buiten, zodat een kleine stuurfout geen stop geeft
    "safety": {"pause": True, "stop_robot": True, "ditch_zone_m": 2.0, "plan_margin_m": 0.3},
}


def empty_farm() -> dict:
    return {"format": FORMAT, "version": VERSION, "settings": copy.deepcopy(DEFAULT_SETTINGS), "start_pose": None,
            "hidden": [], "objects": {}}


def farm_settings(raw: dict | None) -> dict:
    """Instellingen met de standaard eronder (ook voor oudere bestanden zonder een sleutel)."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for k, v in (raw or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load_farm(folder: str) -> dict | None:
    path = os.path.join(folder, FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        farm = json.load(fh)
    if farm.get("format") != FORMAT:
        raise ValueError(f"{path}: geen farm-bestand (format={farm.get('format')!r})")
    if int(farm.get("version", 1)) > VERSION:
        raise ValueError(f"{path}: versie {farm['version']} is nieuwer dan deze sim ({VERSION})")
    farm.setdefault("hidden", [])
    farm.setdefault("objects", {})
    farm["settings"] = farm_settings(farm.get("settings"))
    return farm


def save_farm(folder: str, farm: dict, backup: bool = True) -> str:
    """Bewaren met automatische back-up van de vorige versie; eerst naar een tijdelijk bestand, dan omwisselen."""
    problems = validate_farm(farm)
    if problems:
        raise ValueError("; ".join(problems[:6]))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, FILE)
    if backup and os.path.exists(path):
        bdir = os.path.join(folder, "backups")
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = os.path.join(bdir, f"farm_{stamp}.json")
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(bdir, f"farm_{stamp}_{n}.json")
        with open(path, "rb") as src, open(target, "wb") as dst:
            dst.write(src.read())
        old = sorted(f for f in os.listdir(bdir) if f.startswith("farm_") and f.endswith(".json"))
        for name in old[:-BACKUPS]:
            os.remove(os.path.join(bdir, name))
    data = {"format": FORMAT, "version": VERSION, "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "settings": farm_settings(farm.get("settings")), "start_pose": farm.get("start_pose"),
            "hidden": farm.get("hidden", []),
            "objects": {k: v for k, v in farm.get("objects", {}).items() if v}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def list_backups(folder: str) -> list[str]:
    bdir = os.path.join(folder, "backups")
    if not os.path.isdir(bdir):
        return []
    return sorted((f for f in os.listdir(bdir) if f.startswith("farm_") and f.endswith(".json")), reverse=True)


# ---------------------------------------------------------------------------------------------------- controle
def _ok_ll(p) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) >= 2 and all(isinstance(v, (int, float)) for v in p[:2])
            and all(math.isfinite(v) for v in p[:2]) and -90 <= p[0] <= 90 and -180 <= p[1] <= 180)


def validate_farm(farm: dict) -> list[str]:
    """Problemen die het bestand onbruikbaar maken (lege lijst = goed)."""
    out = []
    for layer, objs in farm.get("objects", {}).items():
        if layer not in LAYERS:
            out.append(f"onbekende laag {layer!r}")
            continue
        key = LAYERS[layer][0]
        need = {"polygon": 3, "footprint": 3, "line": 2, "point": 1}[key]
        for i, obj in enumerate(objs):
            name = obj.get("name") or obj.get("id") or f"{layer}[{i}]"
            try:
                pts = geometry_of(layer, obj)
            except (KeyError, TypeError):
                out.append(f"{name}: geen geometrie")
                continue
            if len(pts) < need:
                out.append(f"{name}: minstens {need} punten nodig")
            elif not all(_ok_ll(p) for p in pts):
                out.append(f"{name}: ongeldige coördinaat")
            for h in obj.get("holes", []) if layer == "fields" else []:
                if len(h.get("polygon", [])) < 3 or not all(_ok_ll(p) for p in h["polygon"]):
                    out.append(f"{name}: gat met ongeldige punten")
    for h in farm.get("hidden", []):
        if h.get("layer") not in LAYERS or not h.get("id"):
            out.append(f"verborgen object zonder laag of id: {h}")
    return out


# ---------------------------------------------------------------------------------------------------- ankers
def _meters(a, b) -> float:
    k = math.cos(math.radians(a[0]))
    return math.hypot((a[0] - b[0]) * 111320.0, (a[1] - b[1]) * 111320.0 * k)


def _in_ring(p, ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[i - 1]
        if (a[0] > p[0]) != (b[0] > p[0]) and p[1] < (b[1] - a[1]) * (p[0] - a[0]) / (b[0] - a[0]) + a[1]:
            inside = not inside
    return inside


def anchor_of(layer: str, obj: dict) -> list[float]:
    """Een punt dat bij het object hoort: in het vlak, op de lijn, of het punt zelf."""
    pts = geometry_of(layer, obj)
    key = LAYERS[layer][0]
    if key in ("polygon", "footprint") and len(pts) >= 3:
        try:
            from shapely.geometry import Polygon
            g = Polygon([(p[1], p[0]) for p in pts])
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                rp = g.representative_point()
                return [round(rp.y, 9), round(rp.x, 9)]
        except Exception:      # noqa: BLE001 - zonder shapely of bij een kapotte ring: zwaartepunt van de punten
            pass
        return [round(sum(p[0] for p in pts) / len(pts), 9), round(sum(p[1] for p in pts) / len(pts), 9)]
    p = pts[len(pts) // 2]
    return [round(p[0], 9), round(p[1], 9)]


def _matches(layer: str, obj: dict, anchor, tol_m: float = 3.0) -> bool:
    if not anchor:
        return True
    pts = geometry_of(layer, obj)
    if LAYERS[layer][0] in ("polygon", "footprint"):
        return _in_ring(anchor, pts)
    if len(pts) == 1:
        return _meters(anchor, pts[0]) <= tol_m
    return min(_seg_dist(anchor, a, b) for a, b in zip(pts, pts[1:])) <= tol_m


def _seg_dist(p, a, b) -> float:
    k = math.cos(math.radians(p[0])) * 111320.0
    px, py = (p[1] - a[1]) * k, (p[0] - a[0]) * 111320.0
    bx, by = (b[1] - a[1]) * k, (b[0] - a[0]) * 111320.0
    ll = bx * bx + by * by
    t = 0.0 if ll == 0 else max(0.0, min(1.0, (px * bx + py * by) / ll))
    return math.hypot(px - t * bx, py - t * by)


def find_source(layer: str, objs: list[dict], oid: str, anchor) -> int | None:
    """Index van het bronobject: op id (als het anker daar ook bij past), anders op het anker."""
    for i, o in enumerate(objs):
        if o.get("id") == oid and o.get("_source", "bron") == "bron" and _matches(layer, o, anchor):
            return i
    if anchor:
        for i, o in enumerate(objs):
            if o.get("_source", "bron") == "bron" and _matches(layer, o, anchor, 2.0):
                return i
    return None


# ---------------------------------------------------------------------------------------------------- toepassen
def apply_farm(base: dict, farm: dict) -> dict:
    """Brondata + eigen aanpassingen -> één wereld (dict in het formaat van world.json, met `_source` per object)."""
    out = copy.deepcopy(base)
    ensure_ids(out)
    report: list[str] = []
    for h in farm.get("hidden", []):
        layer = h.get("layer")
        objs = out.get(layer, [])
        i = find_source(layer, objs, h.get("id"), h.get("anchor"))
        if i is not None:
            del objs[i]
    for layer, objs in farm.get("objects", {}).items():
        if layer not in LAYERS:
            report.append(f"laag {layer!r} in farm.json is onbekend en overgeslagen")
            continue
        target = out.setdefault(layer, [])
        for obj in objs:
            obj = copy.deepcopy(obj)
            rep = obj.pop("replaces", None)
            if rep:
                i = find_source(layer, target, rep.get("id"), rep.get("anchor"))
                if i is not None:
                    obj["id"] = target[i]["id"]
                    obj["_source"] = "overschreven"
                    obj["_replaces"] = rep
                    target[i] = obj
                    continue
                report.append(f"{obj.get('name') or obj.get('id')}: het bronobject {rep.get('id')} bestaat niet meer; "
                              "je versie is als eigen object bewaard")
            obj["_source"] = "eigen"
            used = {o.get("id") for o in target}
            if not obj.get("id") or obj["id"] in used:
                stem = obj.get("id") or f"{LAYERS[layer][1]}_eigen"
                n = 1
                while f"{stem}_{n}" in used:
                    n += 1
                obj["id"] = f"{stem}_{n}"
            target.append(obj)
    if farm.get("start_pose"):
        out["start_pose"] = farm["start_pose"]
    out["farm_settings"] = farm_settings(farm.get("settings"))
    out["_overlay"] = True
    out["_overlay_report"] = report
    return out


def set_start_pose(folder: str, lat: float, lon: float, heading_deg: float) -> dict:
    """Startplek in farm.json zetten (overleeft een nieuwe bouw)."""
    farm = load_farm(folder) or empty_farm()
    farm["start_pose"] = {"lat": round(lat, 9), "lon": round(lon, 9), "heading_deg": round(heading_deg, 1)}
    save_farm(folder, farm)
    return farm["start_pose"]
