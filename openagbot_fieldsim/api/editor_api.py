"""API van de wereld-editor ("Wereld bewerken" in het hoofdmenu).

De editor werkt in lokale meters (x = oost, y = noord), net als de 3D-wereld. De server rekent om naar lat/lon en
bewaart alleen de EIGEN aanpassingen in worlds/<naam>/farm.json (world/farm.py); world.json blijft de brondata.

  GET  /api/editor/state                 alle lagen (bron + eigen), verborgen bronobjecten, instellingen, schema
  PUT  /api/editor/state                 bewaren (met back-up) en de wereld opnieuw laden
  POST /api/editor/op                    geometrie: split, merge, offset, simplify (verandert niets op schijf)
  POST /api/editor/import                AgOpenGPS-map(pen), KML of GeoJSON -> objecten voor de editor (nog niet bewaard)
  POST /api/editor/import_world          hetzelfde, maar als NIEUWE wereld (tweede boerderij)
  GET  /api/editor/export?format=...     geojson (alles), kml, agopengps (zip met een map per veld)
  POST /api/editor/copy_from {world}     eigen objecten van een andere wereld overnemen
  GET  /api/editor/aerial?x0&y0&x1&y1    luchtfoto (PDOK) als achtergrond; header X-Bounds = hoeken in meters
  GET  /api/editor/backups, POST /api/editor/restore {name}
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from openagbot_fieldsim.geo import LocalFrame
from openagbot_fieldsim.world import interchange as ix
from openagbot_fieldsim.world.farm import (anchor_of, apply_farm, empty_farm, farm_settings, list_backups, load_farm,
                                           save_farm)
from openagbot_fieldsim.world.model import DIKE_MODES, LAYERS, PASSAGE_KINDS, ZONE_CLASSES, ensure_ids

AERIAL = ("https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
          "&LAYERS=Actueel_orthoHR&STYLES=&CRS=EPSG:4326&FORMAT=image/jpeg&BBOX={bbox}&WIDTH={w}&HEIGHT={h}")

# laag -> naam, soort geometrie, bewerkbaar, eigenschappen [sleutel, label, type, standaard, keuzes]
SCHEMA = {
    "fields": {"label": "Veld", "plural": "Velden", "geom": "polygon", "edit": True, "props": [
        ["name", "naam", "text", "", None], ["crop", "gewas", "text", "", None],
        ["headland_rounds", "rondjes kopakker (standaard)", "int", None, None],
        ["work_width_m", "werkbreedte (standaard, m)", "number", None, None]]},
    "no_go": {"label": "Verboden gebied", "plural": "Verboden gebieden", "geom": "polygon", "edit": True, "props": [
        ["name", "naam", "text", "", None], ["action", "actie", "select", "stoppen", ["stoppen", "mijden"]]]},
    "zones": {"label": "Rijzone", "plural": "Rijzones", "geom": "polygon", "edit": True, "props": [
        ["name", "naam", "text", "", None],
        ["klass", "klasse", "select", "alleen_rijden", list(ZONE_CLASSES)]]},
    "passages": {"label": "Doorgang", "plural": "Doorgangen", "geom": "line", "edit": True, "props": [
        ["name", "naam", "text", "", None], ["kind", "soort", "select", "dam", list(PASSAGE_KINDS)],
        ["width_m", "breedte (m)", "number", 4.0, None],
        ["direction", "richting", "select", "beide", ["beide", "heen", "terug"]],
        ["max_speed_kmh", "max. snelheid (km/h)", "number", 2.0, None],
        ["confirm", "eerst stoppen en wachten op vrijgave", "bool", False, None]]},
    "ditches": {"label": "Sloot", "plural": "Sloten", "geom": "line", "edit": True, "props": [
        ["name", "naam", "text", "", None], ["width_m", "breedte (m)", "number", 2.0, None],
        ["depth_m", "diepte (m)", "number", 0.8, None], ["water_depth_m", "waterdiepte (m)", "number", 0.3, None]]},
    "dikes": {"label": "Dijk", "plural": "Dijken", "geom": "line", "edit": True, "props": [
        ["name", "naam", "text", "", None], ["crest_width_m", "kruinbreedte (m)", "number", 2.0, None],
        ["height_m", "hoogte (m)", "number", 1.5, None], ["slope", "talud 1 : n", "number", 2.5, None],
        ["mode", "modus", "select", "ontwijken", list(DIKE_MODES)]]},
    "fences": {"label": "Hek", "plural": "Hekken", "geom": "line", "edit": True, "props": [
        ["height_m", "hoogte (m)", "number", 1.2, None], ["post_spacing_m", "paalafstand (m)", "number", 4.0, None]]},
    "nests": {"label": "Vogelnest", "plural": "Vogelnesten", "geom": "point", "edit": True, "props": [
        ["name", "naam", "text", "", None], ["radius_m", "straal (m)", "number", 5.0, None],
        ["start", "van (mm-dd)", "text", "03-15", None], ["end", "tot (mm-dd)", "text", "08-15", None]]},
    "markers": {"label": "Marker", "plural": "Markers", "geom": "point", "edit": True, "props": [
        ["name", "naam", "text", "", None],
        ["kind", "soort", "select", "marker", ["marker", "steen", "paal", "put", "kabel", "poort"]],
        ["radius_m", "straal om omheen te rijden (m)", "number", 0.5, None], ["note", "notitie", "text", "", None]]},
    "trees": {"label": "Boom", "plural": "Bomen", "geom": "point", "edit": True, "props": [
        ["height_m", "hoogte (m)", "number", 10.0, None], ["crown", "kroon", "select", "ball", ["ball", "cone"]],
        ["clearance_m", "eigen vrije ruimte (m)", "number", None, None]]},
    "surfaces": {"label": "Vlak (kaart)", "plural": "Vlakken (kaart)", "geom": "polygon", "edit": False, "props": [
        ["kind", "soort", "text", "", None], ["name", "naam", "text", "", None]]},
    "buildings": {"label": "Gebouw", "plural": "Gebouwen", "geom": "polygon", "edit": False, "props": [
        ["name", "naam", "text", "", None], ["kind", "soort", "text", "", None]]},
}


class SaveBody(BaseModel):
    objects: dict[str, list[dict]] = {}
    hidden: list[dict] = []
    settings: dict = {}


class OpBody(BaseModel):
    op: str
    polys: list[dict] = []
    line: list[list[float]] = []
    distance: float = 0.0
    tolerance: float = 0.1
    gap_m: float = 0.5


class ImportBody(BaseModel):
    format: str = "auto"
    name: str = ""
    files: dict[str, str]
    simplify_m: float = 0.03


class ImportWorldBody(ImportBody):
    world: str
    label: str = ""


class CopyBody(BaseModel):
    world: str
    layers: list[str] | None = None


class RestoreBody(BaseModel):
    name: str


def _geom_key(layer: str) -> str:
    return LAYERS[layer][0]


def import_files(files: dict[str, str], fmt: str = "auto", name: str = "", simplify_m: float = 0.03) -> dict:
    """Bestanden (naam -> tekst) inlezen. AgOpenGPS: een map per veld; meer mappen tegelijk mag."""
    objects: dict[str, list] = {}
    report: list[str] = []
    names = {k.replace("\\", "/"): v for k, v in files.items()}
    has_aog = any(k.split("/")[-1].lower() == "field.txt" for k in names)
    if fmt == "agopengps" or (fmt == "auto" and has_aog):
        groups: dict[str, dict] = {}
        for k, v in names.items():
            folder = k.rsplit("/", 1)[0] if "/" in k else ""
            groups.setdefault(folder, {})[k.split("/")[-1]] = v
        for folder, fs in sorted(groups.items()):
            if not any(n.lower() == "field.txt" for n in fs):
                continue
            fname = folder.split("/")[-1] or name or "veld"
            try:
                r = ix.aog_to_objects(fs, fname, simplify_m)
            except ValueError as e:
                report.append(f"{fname}: {e}")
                continue
            ix.merge_objects(objects, r["objects"])
            report += r["report"]
        if not groups:
            raise ValueError("geen Field.txt gevonden")
        return {"objects": objects, "report": report}
    for k, v in sorted(names.items()):
        low = k.lower()
        try:
            if low.endswith(".kml") or (fmt == "kml"):
                r = ix.kml_to_objects(v, simplify_m)
            elif low.endswith((".geojson", ".json")) or fmt == "geojson":
                r = ix.geojson_to_objects(v, simplify_m)
            else:
                report.append(f"{k}: onbekend bestand (verwacht een AgOpenGPS-map, .kml of .geojson)")
                continue
        except (ValueError, KeyError) as e:
            report.append(f"{k}: niet te lezen: {e}")
            continue
        ix.merge_objects(objects, r["objects"])
        report += [f"{k.split('/')[-1]}: {line}" for line in r["report"]]
    return {"objects": objects, "report": report}


def register(app: FastAPI, sim_app) -> None:
    def world():
        if sim_app.world is None:
            raise HTTPException(503, "geen wereld geladen")
        return sim_app.world

    def base_dict(folder: str) -> dict:
        with open(os.path.join(folder, "world.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        ensure_ids(d)
        return d

    # ------------------------------------------------------------------ omrekenen
    def to_editor(frame: LocalFrame, layer: str, obj: dict, source: str | None = None) -> dict:
        key = _geom_key(layer)
        geom = SCHEMA[layer]["geom"]
        if key == "point":
            pts = [list(frame.to_xy(obj["lat"], obj["lon"]))]
        else:
            pts = [list(frame.to_xy(p[0], p[1])) for p in obj[key]]
        out = {"id": obj.get("id", ""), "layer": layer, "geom": geom,
               "source": source or obj.get("_source", "bron"),
               "pts": [[round(x, 3), round(y, 3)] for x, y in pts],
               "props": {k: v for k, v in obj.items()
                         if k not in (key, "lat", "lon", "holes", "id", "roof") and not k.startswith("_")}}
        if obj.get("_replaces"):
            out["replaces"] = obj["_replaces"]
        if layer == "fields":
            out["holes"] = [{"pts": [[round(v, 3) for v in frame.to_xy(p[0], p[1])] for p in h["polygon"]],
                             "passable": bool(h.get("passable")), "name": h.get("name", "")}
                            for h in obj.get("holes", [])]
        return out

    def from_editor(frame: LocalFrame, layer: str, obj: dict) -> dict:
        key = _geom_key(layer)
        allowed = {p[0] for p in SCHEMA[layer]["props"]}
        d = {k: v for k, v in (obj.get("props") or {}).items() if k in allowed and v is not None and v != ""}
        for k, _, typ, *_ in SCHEMA[layer]["props"]:
            if k in d and typ in ("number", "int"):
                try:
                    d[k] = int(d[k]) if typ == "int" else float(d[k])
                except (TypeError, ValueError):
                    raise ValueError(f"{obj.get('props', {}).get('name') or layer}: {k} moet een getal zijn")
        if obj.get("id"):
            d["id"] = obj["id"]
        ll = [[round(v, 9) for v in frame.to_latlon(float(p[0]), float(p[1]))] for p in obj.get("pts", [])]
        if key == "point":
            if not ll:
                raise ValueError(f"{layer}: punt zonder plek")
            d["lat"], d["lon"] = ll[0]
        else:
            d[key] = ll
        if layer == "fields":
            holes = []
            for h in obj.get("holes") or []:
                ring = [[round(v, 9) for v in frame.to_latlon(float(p[0]), float(p[1]))] for p in h.get("pts", [])]
                if len(ring) >= 3:
                    holes.append({"polygon": ring, "passable": bool(h.get("passable")), "name": h.get("name", "")})
            if holes:
                d["holes"] = holes
            d.setdefault("name", d.get("id", "veld"))
            d.setdefault("crop", "")
        return d

    def objects_to_editor(frame, objects: dict[str, list]) -> list[dict]:
        out = []
        for layer, objs in objects.items():
            if layer in SCHEMA:
                out += [to_editor(frame, layer, o, "eigen") for o in objs]
        return out

    def state(report: list[str] | None = None) -> dict:
        w = world()
        base = base_dict(w.folder)
        farm = load_farm(w.folder) or empty_farm()
        merged = apply_farm(base, farm)
        frame = w.frame
        layers = {layer: [to_editor(frame, layer, o) for o in merged.get(layer, [])] for layer in SCHEMA}
        hidden = []
        for h in farm.get("hidden", []):
            src = next((o for o in base.get(h.get("layer"), []) if o.get("id") == h.get("id")), None)
            if src is not None and h.get("layer") in SCHEMA:
                e = to_editor(frame, h["layer"], src, "bron")
                e["anchor"] = h.get("anchor")
                hidden.append(e)
        return {
            "world": w.name, "label": w.label, "origin": list(w.origin), "area": [list(p) for p in w.area_xy],
            "margin_m": w.margin_m, "layers": layers, "hidden": hidden,
            "settings": farm_settings(farm.get("settings")), "schema": SCHEMA,
            "start": dict(zip(("x", "y", "heading"), w.start_xy_heading())),
            "report": (report or []) + list(merged.get("_overlay_report", [])),
            "backups": list_backups(w.folder)[:10], "has_farm": os.path.exists(os.path.join(w.folder, "farm.json")),
        }

    @app.get("/api/editor/state")
    def get_state():
        return state()

    # ------------------------------------------------------------------ bewaren
    def fix_polygon(name: str, ring: list, report: list[str]) -> list:
        """Zichzelf kruisende grens herstellen als dat eenduidig kan; anders weigeren."""
        from shapely.geometry import Polygon
        if len(ring) < 3:
            raise ValueError(f"{name}: minstens drie punten nodig")
        g = Polygon(ring)
        if g.is_valid and g.area > 0:
            return ring
        from shapely import make_valid
        fixed = make_valid(g)
        parts = [p for p in getattr(fixed, "geoms", [fixed]) if p.geom_type == "Polygon" and p.area > 0.5]
        if len(parts) != 1:
            raise ValueError(f"{name}: de grens kruist zichzelf; versleep de punten zodat de lijnen elkaar niet snijden")
        report.append(f"{name}: kruisende grens hersteld")
        return [[c[0], c[1]] for c in list(parts[0].exterior.coords)[:-1]]

    @app.put("/api/editor/state")
    def put_state(body: SaveBody):
        w = world()
        frame = w.frame
        base = base_dict(w.folder)
        old = load_farm(w.folder) or empty_farm()
        report: list[str] = []
        farm = empty_farm()
        farm["start_pose"] = old.get("start_pose")
        farm["settings"] = farm_settings({**old.get("settings", {}), **(body.settings or {})})
        try:
            for layer, objs in body.objects.items():
                if layer not in SCHEMA or not SCHEMA[layer]["edit"]:
                    continue
                out = []
                for obj in objs:
                    name = (obj.get("props") or {}).get("name") or obj.get("id") or SCHEMA[layer]["label"]
                    if SCHEMA[layer]["geom"] == "polygon":
                        obj = {**obj, "pts": fix_polygon(name, obj.get("pts", []), report)}
                    elif SCHEMA[layer]["geom"] == "line" and len(obj.get("pts", [])) < 2:
                        raise ValueError(f"{name}: een lijn heeft minstens twee punten")
                    d = from_editor(frame, layer, obj)
                    if obj.get("source") in ("bron", "overschreven"):
                        rep = obj.get("replaces")
                        if not rep:
                            src = next((o for o in base.get(layer, []) if o.get("id") == obj.get("id")), None)
                            if src is not None:
                                rep = {"id": src["id"], "anchor": anchor_of(layer, src)}
                        if rep:
                            d["replaces"] = rep
                    out.append(d)
                if out:
                    farm["objects"][layer] = out
            for h in body.hidden:
                layer, oid = h.get("layer"), h.get("id")
                if layer not in LAYERS or not oid:
                    continue
                anchor = h.get("anchor")
                if not anchor:
                    src = next((o for o in base.get(layer, []) if o.get("id") == oid), None)
                    if src is None:
                        continue
                    anchor = anchor_of(layer, src)
                farm["hidden"].append({"layer": layer, "id": oid, "anchor": anchor})
            save_farm(w.folder, farm)
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            sim_app.load(w.name)                     # wereld, terrein en bewaking opnieuw opbouwen
        except Exception as e:                       # noqa: BLE001 - de UI moet zien wat er mis is
            raise HTTPException(500, f"bewaard, maar de wereld laadt niet: {e}")
        return state(report + ["bewaard in farm.json (vorige versie staat in backups/)"])

    @app.get("/api/editor/backups")
    def backups():
        return {"backups": list_backups(world().folder)}

    @app.post("/api/editor/restore")
    def restore(body: RestoreBody):
        w = world()
        if body.name not in list_backups(w.folder):
            raise HTTPException(404, "onbekende back-up")
        with open(os.path.join(w.folder, "backups", body.name), encoding="utf-8") as fh:
            farm = json.load(fh)
        try:
            save_farm(w.folder, farm)
        except ValueError as e:
            raise HTTPException(400, str(e))
        sim_app.load(w.name)
        return state([f"back-up {body.name} teruggezet"])

    # ------------------------------------------------------------------ geometrie
    @app.post("/api/editor/op")
    def op(body: OpBody):
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import split, unary_union

        def poly(p):
            g = Polygon(p["pts"], [h["pts"] for h in p.get("holes", []) if len(h.get("pts", [])) >= 3])
            return g if g.is_valid else g.buffer(0)

        def out(g, holes_meta=None):
            res = []
            for part in getattr(g, "geoms", [g]):
                if part.geom_type != "Polygon" or part.area < 0.5:
                    continue
                res.append({"pts": [[round(c[0], 3), round(c[1], 3)] for c in list(part.exterior.coords)[:-1]],
                            "holes": [{"pts": [[round(c[0], 3), round(c[1], 3)] for c in list(r.coords)[:-1]],
                                       "passable": False, "name": ""} for r in part.interiors],
                            "area_m2": round(part.area, 1)})
            return res

        try:
            if body.op == "split":
                if not body.polys or len(body.line) < 2:
                    raise ValueError("kies een veld en teken een lijn eroverheen")
                g = poly(body.polys[0])
                a, b = body.line[0], body.line[1]
                c, d = body.line[-2], body.line[-1]
                diag = math.hypot(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) + 1.0

                def beyond(p, q):          # einde doortrekken tot net over de rand (als het binnen het veld ophoudt)
                    if not g.contains(Point(q)):
                        return q
                    ll = math.hypot(q[0] - p[0], q[1] - p[1]) or 1.0
                    ux, uy = (q[0] - p[0]) / ll, (q[1] - p[1]) / ll
                    hit = LineString([q, (q[0] + ux * diag, q[1] + uy * diag)]).intersection(g.exterior)
                    d = min((Point(q).distance(h) for h in getattr(hit, "geoms", [hit]) if not h.is_empty), default=diag)
                    return [q[0] + ux * (d + 0.5), q[1] + uy * (d + 0.5)]

                line = LineString([beyond(b, a), *body.line, beyond(c, d)])
                parts = out(split(g, line))
                if len(parts) < 2:
                    raise ValueError("de lijn verdeelt het veld niet (hij moet van rand tot rand lopen)")
                return {"polys": parts}
            if body.op == "merge":
                if len(body.polys) < 2:
                    raise ValueError("kies twee of meer velden (shift-klik)")
                gap = max(0.0, body.gap_m)
                g = unary_union([poly(p).buffer(gap, join_style="mitre") for p in body.polys]).buffer(
                    -gap, join_style="mitre")
                parts = out(g)
                if len(parts) != 1:
                    raise ValueError(f"de velden liggen meer dan {2 * gap:.1f} m uit elkaar: niet samen te voegen")
                return {"polys": parts}
            if body.op == "offset":
                parts = out(poly(body.polys[0]).buffer(body.distance, join_style="mitre", mitre_limit=2.0))
                if not parts:
                    raise ValueError("na het verschuiven blijft er niets over")
                return {"polys": sorted(parts, key=lambda p: -p["area_m2"])[:1]}
            if body.op == "simplify":
                parts = out(poly(body.polys[0]).simplify(max(0.01, body.tolerance)))
                return {"polys": parts[:1]}
        except ValueError as e:
            raise HTTPException(400, str(e))
        raise HTTPException(400, f"onbekende bewerking {body.op!r}")

    # ------------------------------------------------------------------ import / export
    @app.post("/api/editor/import")
    def do_import(body: ImportBody):
        w = world()
        try:
            r = import_files(body.files, body.format, body.name, body.simplify_m)
        except ValueError as e:
            raise HTTPException(400, str(e))
        objs = objects_to_editor(w.frame, r["objects"])
        report = list(r["report"])
        if objs:
            from shapely.geometry import Point, Polygon
            zone = Polygon(w.area_xy).buffer(w.margin_m)
            far = [o for o in objs if not zone.contains(Point(o["pts"][0]))]
            if far:
                d = max(math.hypot(*o["pts"][0]) for o in far)
                report.append(f"LET OP: {len(far)} objecten liggen buiten deze wereld (tot {d:.0f} m van het midden). "
                              "Maak er een nieuwe wereld van, of vergroot het gebied van deze wereld.")
        return {"objects": objs, "report": report}

    @app.post("/api/editor/import_world")
    def import_world(body: ImportWorldBody):
        if not re.fullmatch(r"[a-z0-9_]{2,40}", body.world):
            raise HTTPException(400, "naam: kleine letters, cijfers en _ (2-40 tekens)")
        folder = os.path.join(sim_app.settings.worlds_dir, body.world)
        if os.path.exists(os.path.join(folder, "world.json")):
            raise HTTPException(409, f"wereld {body.world} bestaat al")
        try:
            r = import_files(body.files, body.format, body.name, body.simplify_m)
            new_world, farm = ix.world_from_objects(body.world, body.label, r["objects"])
            new_world.save(folder)
            save_farm(folder, farm, backup=False)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"status": "ok", "world": body.world, "report": r["report"],
                "fields": len(r["objects"].get("fields", []))}

    def merged_objects(folder: str) -> dict[str, list]:
        merged = apply_farm(base_dict(folder), load_farm(folder) or empty_farm())
        return {layer: merged.get(layer, []) for layer in LAYERS if merged.get(layer)}

    @app.get("/api/editor/export")
    def export(format: str = "geojson", field: str | None = None, obstacles: bool = True, own: bool = False):
        w = world()
        objs = merged_objects(w.folder)
        if own:                                   # alleen wat je zelf tekende of wijzigde (kopie naar andere wereld)
            objs = {layer: [o for o in lst if o.get("_source") in ("eigen", "overschreven")]
                    for layer, lst in objs.items()}
            objs = {k: v for k, v in objs.items() if v}
        if format == "geojson":
            data = json.dumps(ix.to_geojson(objs, w.name), ensure_ascii=False, indent=1).encode("utf-8")
            return Response(data, media_type="application/geo+json",
                            headers={"Content-Disposition": f'attachment; filename="{w.name}.geojson"'})
        if format == "kml":
            keep = {k: v for k, v in objs.items() if k not in ("surfaces", "buildings", "trees")}
            return Response(ix.to_kml(keep, w.label).encode("utf-8"), media_type="application/vnd.google-earth.kml+xml",
                            headers={"Content-Disposition": f'attachment; filename="{w.name}.kml"'})
        if format == "agopengps":
            fields = [f for f in objs.get("fields", []) if field in (None, "", f["id"])]
            if not fields:
                raise HTTPException(404, "geen veld om te exporteren")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in fields:
                    holes, flags = aog_extras(w, f) if obstacles else ([], [])
                    folder = re.sub(r'[<>:"/\\|?*]', "_", f.get("name") or f["id"]).strip() or f["id"]
                    for fn, text in ix.export_aog(f, flags, holes).items():
                        z.writestr(f"{folder}/{fn}", text.replace("\n", "\r\n"))
            name = fields[0]["id"] if len(fields) == 1 else w.name
            return Response(buf.getvalue(), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="agopengps_{name}.zip"'})
        raise HTTPException(400, "format: geojson, kml of agopengps")

    def aog_extras(w, field: dict):
        """Wat AgOpenGPS niet kent als gat meegeven: sloot, dijk, verboden gebied, nest (in het veld); en de vlaggen."""
        from shapely.geometry import Point, Polygon
        from openagbot_fieldsim.missions.safety import hazards
        poly = Polygon([w.frame.to_xy(*p) for p in field["polygon"]])
        if not poly.is_valid:
            poly = poly.buffer(0)
        holes = []
        for h in hazards(w):
            if h.kind not in ("water", "dijk", "verboden", "nest", "marker") or not h.geom.intersects(poly):
                continue
            g = h.geom.intersection(poly.buffer(-0.05)).simplify(0.05)
            for part in getattr(g, "geoms", [g]):
                if part.geom_type == "Polygon" and part.area > 0.5:
                    holes.append([[round(v, 9) for v in w.frame.to_latlon(c[0], c[1])]
                                  for c in list(part.exterior.coords)[:-1]])
        near = poly.buffer(30.0)
        flags = [{"lat": m.lat, "lon": m.lon, "note": m.name or m.kind, "color": 2}
                 for m in w.markers if near.contains(Point(m.xy))]
        flags += [{"lat": n.lat, "lon": n.lon, "note": f"nest {n.name} {n.start} tot {n.end}".strip(), "color": 0}
                  for n in w.nests if near.contains(Point(n.xy))]
        return holes, flags

    @app.post("/api/editor/copy_from")
    def copy_from(body: CopyBody):
        w = world()
        folder = os.path.join(sim_app.settings.worlds_dir, os.path.basename(body.world))
        if not os.path.exists(os.path.join(folder, "world.json")):
            raise HTTPException(404, f"wereld {body.world} bestaat niet")
        src = merged_objects(folder)
        objs = {layer: [{k: v for k, v in o.items() if not k.startswith("_")} for o in lst
                        if o.get("_source") in ("eigen", "overschreven")]
                for layer, lst in src.items() if (body.layers is None or layer in body.layers)}
        objs = {k: v for k, v in objs.items() if v and SCHEMA.get(k, {}).get("edit")}
        return {"objects": objects_to_editor(w.frame, objs),
                "report": [f"{sum(len(v) for v in objs.values())} eigen objecten uit {body.world}"]}

    # ------------------------------------------------------------------ luchtfoto
    @app.get("/api/editor/aerial")
    def aerial(x0: float, y0: float, x1: float, y1: float, px: int = 1600):
        """Luchtfoto van PDOK voor dit stuk (alleen het kader gaat naar PDOK); gecachet in worlds/<naam>/cache."""
        from openagbot_fieldsim.world.sources.http import fetch
        w = world()
        f = w.frame
        if x1 - x0 < 5 or y1 - y0 < 5 or x1 - x0 > 6000 or y1 - y0 > 6000:
            raise HTTPException(400, "kader moet tussen 5 m en 6 km zijn")
        corners = [f.to_latlon(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        la0, la1 = min(c[0] for c in corners), max(c[0] for c in corners)
        lo0, lo1 = min(c[1] for c in corners), max(c[1] for c in corners)
        px = max(256, min(2048, int(px)))          # PDOK weigert grotere beelden; de UI vraagt tegels
        wpx = px if x1 - x0 >= y1 - y0 else max(64, int(px * (x1 - x0) / (y1 - y0)))
        hpx = px if y1 - y0 >= x1 - x0 else max(64, int(px * (y1 - y0) / (x1 - x0)))
        url = AERIAL.format(bbox=f"{la0:.7f},{lo0:.7f},{la1:.7f},{lo1:.7f}", w=wpx, h=hpx)
        import hashlib
        cdir = os.path.join(w.folder, "cache", "luchtfoto")
        cpath = os.path.join(cdir, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".jpg")
        data = b""
        if os.path.exists(cpath):
            with open(cpath, "rb") as fh:
                data = fh.read()
        if data[:3] != b"\xff\xd8\xff":
            try:
                data = fetch(url, None, timeout=40.0, retries=2)
            except RuntimeError as e:
                raise HTTPException(502, f"luchtfoto ophalen mislukt: {e}")
            if data[:3] != b"\xff\xd8\xff":            # een foutmelding van PDOK (xml) nooit bewaren
                why = "buiten Nederland?" if b"too" not in data[:400] else "beeld te groot"
                raise HTTPException(502, f"PDOK gaf geen foto terug ({why})")
            os.makedirs(cdir, exist_ok=True)
            with open(cpath, "wb") as fh:
                fh.write(data)
        sw, ne = f.to_xy(la0, lo0), f.to_xy(la1, lo1)
        return Response(data, media_type="image/jpeg", headers={
            "X-Bounds": json.dumps({"x0": sw[0], "y0": sw[1], "x1": ne[0], "y1": ne[1]}),
            "Cache-Control": "max-age=86400"})
