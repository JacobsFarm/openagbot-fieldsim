"""Velden in en uit: AgOpenGPS-veldmap, KML (Google Earth) en GeoJSON.

Alles komt binnen als objecten in het formaat van farm.json (lat/lon), zodat de editor ze kan tonen voor je bewaart:

    {"objects": {"fields": [...], "markers": [...], ...}, "report": ["wat er gebeurde", ...]}

AgOpenGPS (formaat uit de broncode, AgOpenGPS.Core/Streamers/Field en Models/Base/LocalPlane.cs):

  Field.txt     regel na `StartFix`: lat,lon van de oorsprong. Alle punten zijn lokale meters rond dat punt.
  Boundary.txt  `$Boundary`, dan per grens: [True/False = doorrijdbaar], aantal, en `oost,noord,koers` per punt
                (koers in radialen). De eerste grens is de buitengrens, elke volgende een gat.
  Flags.txt     `$Flags`, aantal, dan `lat,lon,oost,noord,koers,kleur,nummer,notitie` (kleur 0 rood, 1 groen, 2 geel).

  lokaal -> lat/lon (precies zoals AgOpenGPS zelf):
      lat = lat0 + noord / mPerGraadLat(lat0)
      lon = lon0 + oost / mPerGraadLon(lat)             (bij de breedte van het PUNT, niet van de oorsprong)
      mPerGraadLat(b) = 111132.92 - 559.82 cos 2b + 1.175 cos 4b - 0.0023 cos 6b
      mPerGraadLon(b) = 111412.84 cos b - 93.5 cos 3b + 0.118 cos 5b

  Nieuwere versies schrijven getallen met "N3": vanaf 1000 m komt er een duizendtal-komma in (`1,234.500`) die
  botst met de komma tussen de velden. `parse_numbers` kan daar tegen.

GeoJSON is het volledige formaat (alle lagen met alle eigenschappen): daarmee kopieer je een boerderij van de ene
wereld naar de andere. KML en AgOpenGPS kennen alleen grenzen, gaten en punten.
"""
from __future__ import annotations

import json
import math
import re
import time
import xml.etree.ElementTree as ET

from openagbot_fieldsim.world.model import LAYERS

DEG = math.pi / 180.0


# ---------------------------------------------------------------------------------------------------- AgOpenGPS: vlak
def aog_m_per_deg_lat(lat0: float) -> float:
    b = lat0 * DEG
    return 111132.92 - 559.82 * math.cos(2.0 * b) + 1.175 * math.cos(4.0 * b) - 0.0023 * math.cos(6.0 * b)


def aog_m_per_deg_lon(lat: float) -> float:
    b = lat * DEG
    return 111412.84 * math.cos(b) - 93.5 * math.cos(3.0 * b) + 0.118 * math.cos(5.0 * b)


def aog_to_latlon(easting: float, northing: float, lat0: float, lon0: float) -> tuple[float, float]:
    lat = lat0 + northing / aog_m_per_deg_lat(lat0)
    return lat, lon0 + easting / aog_m_per_deg_lon(lat)


def latlon_to_aog(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """(oost, noord) in het lokale vlak van AgOpenGPS rond (lat0, lon0)."""
    return (lon - lon0) * aog_m_per_deg_lon(lat), (lat - lat0) * aog_m_per_deg_lat(lat0)


_PLAIN = re.compile(r"^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*$")
_THOUSANDS = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_numbers(line: str, count: int) -> list[float]:
    """`count` getallen uit een regel met komma's ertussen, ook als er duizendtal-komma's in staan."""
    parts = line.strip().split(",")
    if len(parts) == count and all(_PLAIN.match(p) for p in parts):
        return [float(p) for p in parts]
    found = _THOUSANDS.findall(line)
    if len(found) == count:
        return [float(t.replace(",", "")) for t in found]
    if len(parts) > count:                 # duizendtallen zonder decimalen: stukken van 3 cijfers horen bij de vorige
        merged: list[str] = []
        for p in parts:
            p = p.strip()
            if merged and re.fullmatch(r"\d{3}(?:\.\d+)?", p) and re.fullmatch(r"[-+]?\d{1,3}(?:\d{3})*", merged[-1]) \
                    and len(parts) - len(merged) > count - len(merged):
                merged[-1] += p
            else:
                merged.append(p)
        if len(merged) == count and all(_PLAIN.match(p) for p in merged):
            return [float(p) for p in merged]
    raise ValueError(f"verwacht {count} getallen, regel is {line.strip()!r}")


def _file(files: dict[str, str], name: str) -> str | None:
    for k, v in files.items():
        if k.replace("\\", "/").split("/")[-1].lower() == name.lower():
            return v.lstrip("﻿")
    return None


def read_aog(files: dict[str, str]) -> dict:
    """AgOpenGPS-veldmap (bestandsnaam -> tekst) lezen. Geeft start_fix, boundaries en flags in AOG-eenheden."""
    field_txt = _file(files, "Field.txt")
    if field_txt is None:
        raise ValueError("geen Field.txt: dit is geen AgOpenGPS-veldmap")
    lines = [ln.strip() for ln in field_txt.splitlines()]
    start = None
    for i, ln in enumerate(lines):
        if ln.lower() == "startfix" and i + 1 < len(lines):
            start = parse_numbers(lines[i + 1], 2)
    if start is None or not (-90 <= start[0] <= 90 and -180 <= start[1] <= 180):
        raise ValueError("Field.txt heeft geen geldige StartFix (lat,lon)")
    report: list[str] = []
    boundaries = []
    btxt = _file(files, "Boundary.txt")
    if btxt:
        rows = [ln.strip() for ln in btxt.splitlines() if ln.strip()]
        i = 1 if rows and rows[0].startswith("$") else 0
        while i < len(rows):
            drive_thru = False
            first = True
            while i < len(rows) and rows[i].lower() in ("true", "false"):      # oude versies: twee regels
                if first:
                    drive_thru = rows[i].lower() == "true"
                    first = False
                i += 1
            if i >= len(rows):
                break
            try:
                n = int(rows[i])
            except ValueError:
                report.append(f"Boundary.txt regel {i + 1}: aantal verwacht, gevonden {rows[i]!r}; rest overgeslagen")
                break
            i += 1
            pts = []
            for _ in range(n):
                if i >= len(rows):
                    report.append("Boundary.txt houdt midden in een grens op")
                    break
                try:
                    e, nn, h = parse_numbers(rows[i], 3)
                except ValueError:
                    try:
                        e, nn = parse_numbers(rows[i], 2)
                        h = 0.0
                    except ValueError as err:
                        report.append(f"Boundary.txt regel {i + 1}: {err}")
                        i += 1
                        continue
                pts.append((e, nn, h))
                i += 1
            if len(pts) >= 3:
                boundaries.append({"drive_thru": drive_thru, "pts": pts})
            elif n:
                report.append(f"grens met {len(pts)} punten overgeslagen")
    flags = []
    ftxt = _file(files, "Flags.txt")
    if ftxt:
        rows = [ln.strip() for ln in ftxt.splitlines() if ln.strip()]
        for ln in rows:
            if ln.startswith("$") or re.fullmatch(r"\d+", ln):
                continue
            w = ln.split(",")
            try:
                if len(w) >= 8:
                    flags.append({"lat": float(w[0]), "lon": float(w[1]), "easting": float(w[2]),
                                  "northing": float(w[3]), "heading": float(w[4]), "color": int(float(w[5])),
                                  "id": int(float(w[6])), "note": ",".join(w[7:]).strip()})
                elif len(w) == 7:
                    flags.append({"lat": float(w[0]), "lon": float(w[1]), "easting": float(w[2]),
                                  "northing": float(w[3]), "heading": 0.0, "color": int(float(w[4])),
                                  "id": int(float(w[5])), "note": w[6].strip()})
                else:
                    report.append(f"Flags.txt: regel niet te lezen: {ln!r}")
            except ValueError:
                report.append(f"Flags.txt: regel niet te lezen: {ln!r}")
    return {"start_fix": (start[0], start[1]), "boundaries": boundaries, "flags": flags, "report": report}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "veld"


def _simplify_ll(ring: list, tol_m: float) -> list:
    """Punten op een rechte lijn weghalen (Douglas-Peucker in meters); AgOpenGPS zet om de 2 m een punt."""
    if tol_m <= 0 or len(ring) <= 4:
        return ring
    lat0, lon0 = ring[0]
    k = math.cos(lat0 * DEG) * 111320.0
    xy = [((p[1] - lon0) * k, (p[0] - lat0) * 111320.0) for p in ring]
    # gesloten ring: begin bij het punt dat het verst van punt 0 ligt (zeker een hoek) en loop één keer rond
    far0 = max(range(len(xy)), key=lambda i: (xy[i][0] - xy[0][0]) ** 2 + (xy[i][1] - xy[0][1]) ** 2)
    ring = ring[far0:] + ring[:far0]
    xy = xy[far0:] + xy[:far0] + [xy[far0]]
    keep = [False] * len(xy)
    far = max(range(len(xy) - 1), key=lambda i: (xy[i][0] - xy[0][0]) ** 2 + (xy[i][1] - xy[0][1]) ** 2)
    keep[0] = keep[far] = True
    stack = [(0, far), (far, len(xy) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = xy[a]
        bx, by = xy[b]
        ll = math.hypot(bx - ax, by - ay) or 1e-9
        best, bi = 0.0, None
        for i in range(a + 1, b):
            d = abs((bx - ax) * (ay - xy[i][1]) - (ax - xy[i][0]) * (by - ay)) / ll
            if d > best:
                best, bi = d, i
        if bi is not None and best > tol_m:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    out = [p for p, k_ in zip(ring, keep) if k_]
    return out if len(out) >= 3 else ring


FLAG_COLOR = {0: "rood", 1: "groen", 2: "geel"}


def marker_from_note(lat: float, lon: float, note: str, name: str = "", color: int | None = None):
    """Vlag/punt -> (laag, object). 'nest' wordt een vogelnest, 'boom' een boom, de rest een marker."""
    low = (note or name or "").lower()
    lat, lon = round(lat, 9), round(lon, 9)
    label = (note or name or "vlag").strip()
    if "nest" in low:
        return "nests", {"name": label, "lat": lat, "lon": lon, "radius_m": 5.0, "start": "03-15", "end": "08-15"}
    if re.search(r"\bboom\b|\bbomen\b|\btree\b", low):
        return "trees", {"lat": lat, "lon": lon, "height_m": 10.0, "crown": "ball"}
    kind = next((k for k in ("poort", "hek", "dam", "paal", "put", "steen", "kabel", "riool", "water")
                 if k in low), "marker")
    kind = {"hek": "poort", "dam": "poort", "riool": "put", "water": "put"}.get(kind, kind)
    obj = {"name": label, "kind": kind, "lat": lat, "lon": lon, "radius_m": 0.0 if kind in ("poort", "kabel") else 0.5,
           "note": (f"{note} " if note and note != label else "") + (f"({FLAG_COLOR.get(color, '')} vlag)"
                                                                     if color is not None else "")}
    obj["note"] = obj["note"].strip()
    return "markers", obj


def aog_to_objects(files: dict[str, str], name: str, simplify_m: float = 0.0) -> dict:
    """AgOpenGPS-veldmap -> objecten voor farm.json. Buitengrens = veld, binnengrenzen = gaten, vlaggen = markers."""
    aog = read_aog(files)
    lat0, lon0 = aog["start_fix"]
    report = list(aog["report"])
    objects: dict[str, list] = {}

    def ring(pts):
        out = [[round(v, 9) for v in aog_to_latlon(e, n, lat0, lon0)] for e, n, _ in pts]
        if len(out) > 3 and out[0] == out[-1]:
            out = out[:-1]
        return _simplify_ll(out, simplify_m)

    if aog["boundaries"]:
        outer, *inner = aog["boundaries"]
        fld = {"id": _slug(name), "name": name, "crop": "", "polygon": ring(outer["pts"])}
        holes = [{"polygon": ring(b["pts"]), "passable": bool(b["drive_thru"]), "name": f"gat {i + 1}"}
                 for i, b in enumerate(inner)]
        if holes:
            fld["holes"] = holes
        objects["fields"] = [fld]
        report.append(f"{name}: grens met {len(fld['polygon'])} punten"
                      + (f" (was {len(outer['pts'])})" if len(fld["polygon"]) != len(outer["pts"]) else "")
                      + (f", {len(holes)} gaten" if holes else ""))
    else:
        report.append(f"{name}: geen grens in Boundary.txt (alleen vlaggen)")
    for fl in aog["flags"]:
        # De lat/lon van de vlag is wat de GPS mat; oost/noord is hetzelfde punt in het lokale vlak
        layer, obj = marker_from_note(fl["lat"], fl["lon"], fl["note"], color=fl["color"])
        objects.setdefault(layer, []).append(obj)
    if aog["flags"]:
        report.append(f"{name}: {len(aog['flags'])} vlaggen")
    return {"objects": objects, "report": report, "start_fix": [lat0, lon0]}


def _fmt(v: float, nd: int) -> str:
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _aog_ring(ring_ll: list, lat0: float, lon0: float, spacing_m: float) -> list[str]:
    pts = [latlon_to_aog(p[0], p[1], lat0, lon0) for p in ring_ll]
    if spacing_m > 0:                      # AgOpenGPS verwacht punten dicht op elkaar (zelf zet hij ze om de 2 m)
        dense = []
        for a, b in zip(pts, pts[1:] + pts[:1]):
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(math.ceil(d / spacing_m - 1e-9)))
            dense += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
        pts = dense
    out = []
    n = len(pts)
    for i, (e, nn) in enumerate(pts):
        pe, pn = pts[i - 1]
        ne, n2 = pts[(i + 1) % n]
        h = math.atan2(ne - pe, n2 - pn) % (2.0 * math.pi)
        out.append(f"{_fmt(e, 3)},{_fmt(nn, 3)},{_fmt(h, 5)}")
    return out


def export_aog(field: dict, flags: list[dict] | None = None, extra_holes: list[list] | None = None,
               spacing_m: float = 2.5, start_fix: tuple[float, float] | None = None) -> dict[str, str]:
    """Veld (dict uit farm/world, lat/lon) -> bestanden van een AgOpenGPS-veldmap.

    `extra_holes`: ringen (lat/lon) die als niet-doorrijdbaar gat mee gaan (sloot, dijk, verboden gebied, nest):
    AgOpenGPS kent die soorten niet. `flags`: [{"lat", "lon", "note", "color"}].
    De StartFix is het midden van het veld, zodat de lokale getallen klein blijven (onder de 1000 m, zie boven).
    """
    poly = field["polygon"]
    if start_fix is None:
        start_fix = (round(sum(p[0] for p in poly) / len(poly), 7), round(sum(p[1] for p in poly) / len(poly), 7))
    lat0, lon0 = start_fix
    stamp = time.strftime("%Y-%B-%d %I:%M:%S %p")
    out = {"Field.txt": "\n".join([stamp, "$FieldDir", "FieldNew", "$Offsets", "0,0", "Convergence", "0", "StartFix",
                                   f"{_fmt(lat0, 12)},{_fmt(lon0, 12)}"]) + "\n"}     # volle precisie: 7 decimalen is 5 mm
    rows = ["$Boundary"]
    rings = [(False, poly)] + [(bool(h.get("passable")), h["polygon"]) for h in field.get("holes", [])] \
        + [(False, r) for r in (extra_holes or [])]
    for drive_thru, r in rings:
        pts = _aog_ring(r, lat0, lon0, spacing_m)
        rows += ["True" if drive_thru else "False", str(len(pts)), *pts]
    out["Boundary.txt"] = "\n".join(rows) + "\n"
    rows = ["$Flags", str(len(flags or []))]
    for i, fl in enumerate(flags or []):
        e, n = latlon_to_aog(fl["lat"], fl["lon"], lat0, lon0)
        note = str(fl.get("note", "")).replace(",", ";").replace("\n", " ")
        rows.append(f"{fl['lat']:.7f},{fl['lon']:.7f},{_fmt(e, 3)},{_fmt(n, 3)},0,{int(fl.get('color', 0))},"
                    f"{i + 1},{note}")
    out["Flags.txt"] = "\n".join(rows) + "\n"
    out["Field.kml"] = to_kml({"fields": [field], "markers": [{"name": f.get("note", ""), "lat": f["lat"],
                                                               "lon": f["lon"]} for f in (flags or [])]},
                              field.get("name", "veld"))
    return out


# ---------------------------------------------------------------------------------------------------- KML
KML_NS = "{http://www.opengis.net/kml/2.2}"
_SKIP_FOLDER = re.compile(r"section|worked|ab[_ ]?line|curve|contour|track|recorded|tram", re.I)
_LINE_LAYER = (("ditches", ("sloot", "ditch", "watergang", "greppel")), ("dikes", ("dijk", "dike", "kade")),
               ("fences", ("hek", "fence", "afrastering", "draad")),
               ("passages", ("doorgang", "dam", "duiker", "brug", "poort", "oversteek", "passage")))
_POLY_LAYER = (("no_go", ("verboden", "no-go", "nogo", "no go")), ("zones", ("rijzone", "zone", "transport")))


def _coords(text: str) -> list:
    out = []
    for tok in (text or "").split():
        p = tok.split(",")
        if len(p) >= 2:
            out.append([round(float(p[1]), 9), round(float(p[0]), 9)])
    return out


def _open_ring(r: list) -> list:
    return r[:-1] if len(r) > 3 and r[0] == r[-1] else r


def kml_to_objects(text: str, simplify_m: float = 0.0) -> dict:
    """KML: vlakken worden velden (met gaten), lijnen en punten krijgen hun soort uit de naam of de map."""
    root = ET.fromstring(text.lstrip("﻿").encode("utf-8"))
    ns = KML_NS if root.tag.startswith(KML_NS) else ""
    objects: dict[str, list] = {}
    report: list[str] = []
    used: set[str] = set()

    def text_of(el, tag):
        c = el.find(f"{ns}{tag}")
        return (c.text or "").strip() if c is not None and c.text else ""

    def walk(el, folder):
        for ch in el:
            tag = ch.tag.replace(ns, "")
            if tag in ("Folder", "Document"):
                walk(ch, text_of(ch, "name") if tag == "Folder" else folder)
            elif tag == "Placemark":
                placemark(ch, folder)

    def props(pm):
        d = text_of(pm, "description")
        if d.startswith("{"):
            try:
                return json.loads(d)
            except ValueError:
                pass
        return {}

    def placemark(pm, folder):
        name = text_of(pm, "name")
        pr = props(pm)
        low = f"{folder} {name}".lower()
        if folder and _SKIP_FOLDER.search(folder) and "layer" not in pr:
            return
        for poly in pm.iter(f"{ns}Polygon"):
            outer = poly.find(f"{ns}outerBoundaryIs/{ns}LinearRing/{ns}coordinates")
            ring = _simplify_ll(_open_ring(_coords(outer.text if outer is not None else "")), simplify_m)
            if len(ring) < 3:
                continue
            holes = []
            for inner in poly.findall(f"{ns}innerBoundaryIs/{ns}LinearRing/{ns}coordinates"):
                h = _open_ring(_coords(inner.text))
                if len(h) >= 3:
                    holes.append({"polygon": h, "passable": False})
            layer = pr.get("layer") or next((l for l, words in _POLY_LAYER if any(w in low for w in words)),
                                            "fields")
            obj = {k: v for k, v in pr.items() if k not in ("layer", "polygon", "holes")}
            obj.setdefault("name", name or "veld")
            obj["polygon"] = ring
            if layer == "fields":
                sid = _slug(obj["name"])
                n = 1
                while (sid if n == 1 else f"{sid}_{n}") in used:
                    n += 1
                obj["id"] = sid if n == 1 else f"{sid}_{n}"
                used.add(obj["id"])
                obj.setdefault("crop", "")
                if holes:
                    obj["holes"] = holes
            objects.setdefault(layer, []).append(obj)
        for ls in pm.iter(f"{ns}LineString"):
            c = ls.find(f"{ns}coordinates")
            line = _coords(c.text if c is not None else "")
            if len(line) < 2:
                continue
            layer = pr.get("layer") or next((l for l, words in _LINE_LAYER if any(w in low for w in words)), None)
            if layer is None:
                report.append(f"lijn {name!r} overgeslagen (geen sloot, dijk, hek of doorgang in de naam)")
                continue
            obj = {k: v for k, v in pr.items() if k not in ("layer", "line")}
            if layer != "fences":
                obj.setdefault("name", name)
            obj["line"] = line
            objects.setdefault(layer, []).append(obj)
        for pt in pm.iter(f"{ns}Point"):
            c = pt.find(f"{ns}coordinates")
            p = _coords(c.text if c is not None else "")
            if not p:
                continue
            if pr.get("layer") in ("nests", "markers", "trees"):
                obj = {k: v for k, v in pr.items() if k != "layer"}
                obj.update(lat=p[0][0], lon=p[0][1])
                objects.setdefault(pr["layer"], []).append(obj)
            else:
                layer, obj = marker_from_note(p[0][0], p[0][1], name)
                objects.setdefault(layer, []).append(obj)

    walk(root, "")
    for layer, objs in objects.items():
        report.append(f"{len(objs)} x {layer}")
    if not objects:
        report.append("geen vlakken, lijnen of punten gevonden")
    return {"objects": objects, "report": report}


def _kml_ring(ring: list) -> str:
    pts = list(ring) + [ring[0]]
    return " ".join(f"{p[1]:.7f},{p[0]:.7f},0" for p in pts)


def _x(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_kml(objects: dict[str, list], title: str = "boerderij") -> str:
    """Objecten (lat/lon) -> KML. Velden in de map Boundaries (zoals AgOpenGPS), de rest per laag, met de
    eigenschappen als JSON in de beschrijving (zodat inlezen niets verliest)."""
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">', " <Document>",
           f"  <name>{_x(title)}</name>"]
    folder_name = {"fields": "Boundaries"}
    for layer, objs in objects.items():
        if not objs or layer not in LAYERS:
            continue
        key = LAYERS[layer][0]
        out += ["  <Folder>", f"   <name>{folder_name.get(layer, layer)}</name>"]
        for o in objs:
            pr = {k: v for k, v in o.items() if k not in (key, "holes", "lat", "lon") and not k.startswith("_")}
            pr["layer"] = layer
            out += ["   <Placemark>", f"    <name>{_x(o.get('name') or o.get('id') or layer)}</name>",
                    f"    <description>{_x(json.dumps(pr, ensure_ascii=False))}</description>"]
            if key in ("polygon", "footprint"):
                out += ["    <Polygon><tessellate>1</tessellate>",
                        f"     <outerBoundaryIs><LinearRing><coordinates>{_kml_ring(o[key])}</coordinates>"
                        "</LinearRing></outerBoundaryIs>"]
                for h in o.get("holes", []):
                    out.append(f"     <innerBoundaryIs><LinearRing><coordinates>{_kml_ring(h['polygon'])}"
                               "</coordinates></LinearRing></innerBoundaryIs>")
                out.append("    </Polygon>")
            elif key == "line":
                out.append("    <LineString><tessellate>1</tessellate><coordinates>"
                           + " ".join(f"{p[1]:.7f},{p[0]:.7f},0" for p in o["line"]) + "</coordinates></LineString>")
            else:
                out.append(f"    <Point><coordinates>{o['lon']:.7f},{o['lat']:.7f},0</coordinates></Point>")
            out.append("   </Placemark>")
        out.append("  </Folder>")
    out += [" </Document>", "</kml>", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------- GeoJSON
def to_geojson(objects: dict[str, list], name: str = "boerderij") -> dict:
    """Alle lagen als GeoJSON FeatureCollection (coördinaten lon, lat), met `layer` en alle eigenschappen."""
    feats = []
    for layer, objs in objects.items():
        if layer not in LAYERS:
            continue
        key = LAYERS[layer][0]
        for o in objs:
            pr = {k: v for k, v in o.items() if k not in (key, "lat", "lon") and not k.startswith("_")}
            pr["layer"] = layer
            if key in ("polygon", "footprint"):
                close = lambda r: [[p[1], p[0]] for p in list(r) + [r[0]]]      # noqa: E731
                rings = [close(o[key])] + [close(h["polygon"]) for h in o.get("holes", [])]
                geom = {"type": "Polygon", "coordinates": rings}
            elif key == "line":
                geom = {"type": "LineString", "coordinates": [[p[1], p[0]] for p in o["line"]]}
            else:
                geom = {"type": "Point", "coordinates": [o["lon"], o["lat"]]}
            feats.append({"type": "Feature", "properties": pr, "geometry": geom})
    return {"type": "FeatureCollection", "name": name, "features": feats}


def geojson_to_objects(data: dict | str, simplify_m: float = 0.0) -> dict:
    if isinstance(data, str):
        data = json.loads(data.lstrip("﻿"))
    feats = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    objects: dict[str, list] = {}
    report: list[str] = []
    used: set[str] = set()

    def ll(c):
        return [round(float(c[1]), 9), round(float(c[0]), 9)]

    def add_polygon(pr, rings):
        ring = _simplify_ll(_open_ring([ll(c) for c in rings[0]]), simplify_m)
        if len(ring) < 3:
            return
        layer = pr.get("layer") if pr.get("layer") in LAYERS and LAYERS[pr["layer"]][0] in ("polygon", "footprint") \
            else "fields"
        key = LAYERS[layer][0]
        obj = {k: v for k, v in pr.items() if k not in ("layer", "holes")}
        obj[key] = ring
        if layer == "fields":
            holes_in = {i: h for i, h in enumerate(pr.get("holes") or [])}
            holes = []
            for i, r in enumerate(rings[1:]):
                h = _open_ring([ll(c) for c in r])
                if len(h) >= 3:
                    holes.append({"polygon": h, "passable": bool(holes_in.get(i, {}).get("passable", False)),
                                  "name": holes_in.get(i, {}).get("name", "")})
            if holes:
                obj["holes"] = holes
            obj.setdefault("name", pr.get("naam") or "veld")
            obj.setdefault("crop", pr.get("gewas", ""))
            sid = str(obj.get("id") or _slug(obj["name"]))
            n = 1
            while (sid if n == 1 else f"{sid}_{n}") in used:
                n += 1
            obj["id"] = sid if n == 1 else f"{sid}_{n}"
            used.add(obj["id"])
        objects.setdefault(layer, []).append(obj)

    for ft in feats or []:
        geom = ft.get("geometry") or {}
        pr = dict(ft.get("properties") or {})
        t = geom.get("type")
        c = geom.get("coordinates")
        try:
            if t == "Polygon":
                add_polygon(pr, c)
            elif t == "MultiPolygon":
                for part in c:
                    add_polygon(dict(pr), part)
            elif t in ("LineString", "MultiLineString"):
                layer = pr.get("layer")
                if layer not in LAYERS or LAYERS[layer][0] != "line":
                    report.append(f"lijn overgeslagen (geen 'layer': ditches, dikes, fences of passages): "
                                  f"{pr.get('name', '')}")
                    continue
                for line in ([c] if t == "LineString" else c):
                    obj = {k: v for k, v in pr.items() if k != "layer"}
                    obj["line"] = [ll(p) for p in line]
                    if len(obj["line"]) >= 2:
                        objects.setdefault(layer, []).append(obj)
            elif t == "Point":
                lat, lon = ll(c)
                if pr.get("layer") in ("nests", "markers", "trees"):
                    obj = {k: v for k, v in pr.items() if k != "layer"}
                    obj.update(lat=lat, lon=lon)
                    objects.setdefault(pr["layer"], []).append(obj)
                else:
                    layer, obj = marker_from_note(lat, lon, str(pr.get("note") or pr.get("name") or ""))
                    objects.setdefault(layer, []).append(obj)
        except (TypeError, ValueError, IndexError, KeyError) as e:
            report.append(f"object overgeslagen: {e}")
    for layer, objs in objects.items():
        report.append(f"{len(objs)} x {layer}")
    if not objects:
        report.append("geen objecten gevonden")
    return {"objects": objects, "report": report}


# ---------------------------------------------------------------------------------------------------- hulp
def all_points(objects: dict[str, list]) -> list:
    pts = []
    for layer, objs in objects.items():
        if layer not in LAYERS:
            continue
        key = LAYERS[layer][0]
        for o in objs:
            pts += [[o["lat"], o["lon"]]] if key == "point" else list(o[key])
    return pts


def merge_objects(target: dict[str, list], new: dict[str, list]) -> dict[str, list]:
    """Objecten samenvoegen; velden met hetzelfde id krijgen een volgnummer."""
    used = {o.get("id") for o in target.get("fields", [])}
    for layer, objs in new.items():
        for o in objs:
            if layer == "fields":
                base, n = o.get("id") or _slug(o.get("name", "veld")), 1
                while (base if n == 1 else f"{base}_{n}") in used:
                    n += 1
                o["id"] = base if n == 1 else f"{base}_{n}"
                used.add(o["id"])
            target.setdefault(layer, []).append(o)
    return target


def world_from_objects(name: str, label: str, objects: dict[str, list], margin_m: float = 40.0):
    """Nieuwe (lege) wereld rond de objecten, plus de farm-dict met die objecten. Bouwen (PDOK) kan daarna."""
    from openagbot_fieldsim.world.farm import empty_farm
    from openagbot_fieldsim.world.model import World
    pts = all_points(objects)
    if len(pts) < 1:
        raise ValueError("geen objecten om een wereld van te maken")
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    lat0, lon0 = (min(lats) + max(lats)) / 2.0, (min(lons) + max(lons)) / 2.0
    dlat = margin_m / 111320.0
    dlon = margin_m / (111320.0 * math.cos(lat0 * DEG))
    a, b, c, d = min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon
    world = World(name=name, label=label or name, origin=(round(lat0, 9), round(lon0, 9), 0.0),
                  area=[(a, c), (a, d), (b, d), (b, c)], margin_m=50.0, terrain={"type": "flat"},
                  sources={"created": "openagbot_fieldsim import"})
    farm = empty_farm()
    farm["objects"] = objects
    return world, farm
