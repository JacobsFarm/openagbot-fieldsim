"""API voor veldmissies (kopakkerrondjes + banen + bochten als één rijpad), zie missions/field_plan.py.

  POST /api/fieldmission/plan {field, settings}      plannen (en bewaren in worlds/<naam>/missions/)
  GET  /api/fieldmission/{field}                     laatst geplande missie van dat veld
  GET  /api/fieldmission/{field}/veldmissie.json     missiebestand (lat/lon, segmenten met snelheid en werktuig)
  GET  /api/fieldmission/{field}/waypoints.json      zelfde route als waypoints voor de huidige robotsoftware
  POST /api/fieldmission/drive {field, mode, ...}    in de sim rijden met de echte OpenAgbot: mode 'missie' (de
                                                     missievolger van de robot, met zijn eigen geofence) of
                                                     'waypoints' (pure pursuit van punt naar punt)
  POST /api/fieldmission/push {field, robot_url}     kaart, missie en waypoints naar de ECHTE robot (start niets)
  GET  /api/fieldmission/boundaries/{field}          randen van het perceel: wat ligt ernaast, hoeveel ruimte

Startpunten per veld (waar de robot de missie van dat veld mag beginnen; bewaard in farm.json):

  GET  /api/startpoints                              alle startpunten (lokale meters)
  POST /api/startpoints/add {field, x?, y?, name?}   punt zetten (zonder x, y: waar de robot nu staat)
  POST /api/startpoints/remove {id}                  punt weghalen
  POST /api/fieldmission/plan {..., start_point}     de route begint bij dat punt (zonder: dichtst bij de robot)
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from openagbot_fieldsim.agbot import robot_link
from openagbot_fieldsim.api.scene import start_points as scene_start_points
from openagbot_fieldsim.world.model import START_SNAP_M, StartPoint
from openagbot_fieldsim.missions.boundary import edge_summary, field_area
from openagbot_fieldsim.missions.field_plan import PlanSettings, plan_field, plan_view, to_veldmissie, to_waypoints


class PlanBody(BaseModel):
    field: str
    settings: dict = {}
    start_point: str | None = None       # id van een startpunt van dit veld; None = het dichtstbijzijnde bij de robot


class StartPointBody(BaseModel):
    field: str
    x: float | None = None               # lokale meters; zonder x, y: waar de robot nu staat
    y: float | None = None
    name: str = ""


class StartPointRemove(BaseModel):
    id: str


class DriveBody(BaseModel):
    field: str
    speed_kmh: float | None = None       # None = werksnelheid uit de missie
    lookahead_m: float = 2.0
    place_robot: bool = True
    force: bool = False                  # ook rijden als de veiligheidscontrole de route afkeurt
    mode: str = "missie"                 # missie = missievolger van de robot | waypoints = punt naar punt


class PushBody(BaseModel):
    field: str
    robot_url: str


def register(app: FastAPI, sim_app) -> None:
    cache: dict[str, dict] = {}           # field -> {"plan", "view", "world"}

    def world():
        if sim_app.world is None:
            raise HTTPException(503, "geen wereld geladen")
        return sim_app.world

    def get_plan(field: str) -> dict:
        c = cache.get(field)
        if c is None or c["world"] is not sim_app.world:
            raise HTTPException(404, "eerst plannen")
        return c

    def mission_dir() -> str:
        return os.path.join(world().folder, "missions")

    # ------------------------------------------------------------------ startpunten per veld
    def save_start_points(w) -> dict:
        from openagbot_fieldsim.world.farm import set_start_points
        w.link_start_points()
        try:
            set_start_points(w.folder, [sp.to_dict() for sp in w.start_points])
        except (OSError, ValueError, TypeError) as e:
            raise HTTPException(500, f"startpunten bewaren mislukt: {e}")
        return {"start_points": scene_start_points(w)}

    @app.get("/api/startpoints")
    def start_points_list():
        return {"start_points": scene_start_points(world())}

    @app.post("/api/startpoints/add")
    def start_points_add(body: StartPointBody):
        """Startpunt bij een veld zetten: op (x, y), of waar de robot nu staat. Komt in farm.json."""
        w = world()
        try:
            w.field_by_id(body.field)
        except KeyError:
            raise HTTPException(404, f"onbekend veld {body.field}")
        if body.x is None or body.y is None:
            if sim_app.sim is None:
                raise HTTPException(400, "geef x en y")
            x, y = sim_app.sim.vehicle.s.x, sim_app.sim.vehicle.s.y
        else:
            x, y = body.x, body.y
        if w.field_distance(w.field_by_id(body.field), x, y) > START_SNAP_M:
            raise HTTPException(400, f"dat punt ligt meer dan {START_SNAP_M:.0f} m buiten het veld")
        lat, lon = w.frame.to_latlon(x, y)
        w.start_points.append(StartPoint(body.field, lat, lon, body.name.strip(), f"start_{uuid.uuid4().hex[:8]}"))
        return save_start_points(w)

    @app.post("/api/startpoints/remove")
    def start_points_remove(body: StartPointRemove):
        w = world()
        keep = [sp for sp in w.start_points if sp.id != body.id]
        if len(keep) == len(w.start_points):
            raise HTTPException(404, "onbekend startpunt")
        w.start_points[:] = keep
        return save_start_points(w)

    def pick_start(w, field: str, start_id: str | None):
        """Het startpunt voor deze missie: het gekozen punt, anders het punt van dit veld dat het dichtst bij de
        robot ligt. None = het veld heeft geen startpunten (dan begint de route bij de startplek van de wereld)."""
        pts = w.start_points_of(field)
        if start_id:
            sp = next((p for p in pts if p.id == start_id), None)
            if sp is None:
                raise ValueError("dat startpunt hoort niet (meer) bij dit veld")
            return sp
        if not pts:
            return None
        if sim_app.sim is not None:
            rx, ry = sim_app.sim.vehicle.s.x, sim_app.sim.vehicle.s.y
        else:
            rx, ry, _ = w.start_xy_heading()
        return min(pts, key=lambda p: math.hypot(p.xy[0] - rx, p.xy[1] - ry))

    @app.post("/api/fieldmission/plan")
    def plan(body: PlanBody):
        w = world()
        try:
            s = PlanSettings.from_dict(body.settings)
            vehicle = sim_app.sim.params if sim_app.sim is not None else None
            sp = pick_start(w, body.field, body.start_point)
            p = plan_field(w, body.field, s, vehicle, None if sp is None else (sp.xy[0], sp.xy[1]))
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        view = plan_view(p)
        if sp is not None and p["segments"]:
            x0, y0 = p["segments"][0].pts[0][:2]
            info = next(d for d in scene_start_points(w) if d["id"] == sp.id)
            view["start_point"] = {**info, "auto": not body.start_point,
                                   "route_m": round(math.hypot(x0 - sp.xy[0], y0 - sp.xy[1]), 1)}
        cache[body.field] = {"plan": p, "view": view, "world": w}
        fld = w.field_by_id(body.field)
        try:
            os.makedirs(mission_dir(), exist_ok=True)
            with open(os.path.join(mission_dir(), f"veldmissie_{body.field}.json"), "w", encoding="utf-8",
                      newline="\n") as fh:
                json.dump(to_veldmissie(p, w, fld.name), fh, indent=1, ensure_ascii=False)
        except OSError:
            pass
        return view

    @app.get("/api/fieldmission/boundaries/{field}")
    def boundaries(field: str, water: float | None = None, road: float | None = None, fence: float | None = None):
        clr = {k: v for k, v in (("water", water), ("weg", road), ("hek", fence)) if v is not None}
        try:
            a = field_area(world(), field, clr)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"edges": [e.to_dict() for e in a.pieces], "summary": edge_summary(a),
                "field_m2": round(a.field_poly.area, 1), "allowed_m2": round(a.allowed.area, 1),
                "warnings": a.warnings}

    @app.get("/api/fieldmission/{field}")
    def last(field: str):
        return get_plan(field)["view"]

    @app.get("/api/fieldmission/{field}/veldmissie.json")
    def download_mission(field: str):
        c = get_plan(field)
        w = world()
        data = json.dumps(to_veldmissie(c["plan"], w, w.field_by_id(field).name), indent=1,
                          ensure_ascii=False).encode("utf-8")
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="veldmissie_{field}.json"'})

    @app.get("/api/fieldmission/{field}/waypoints.json")
    def download_waypoints(field: str):
        c = get_plan(field)
        data = json.dumps(to_waypoints(c["plan"], world()), indent=1).encode("utf-8")
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="waypoints_{field}.json"'})

    @app.post("/api/fieldmission/drive")
    def drive(body: DriveBody):
        """In de sim: de veldmissie als waypoints.json aan de sim-robot geven en pure pursuit starten."""
        from openagbot_fieldsim.api.farm_api import refuse_unsafe
        c = get_plan(body.field)
        refuse_unsafe(c["plan"], body.force)
        w = world()
        wps = to_waypoints(c["plan"], w)
        name = robot_link.mission_name(f"veld_{body.field}")
        mission = to_veldmissie(c["plan"], w, w.field_by_id(body.field).name)
        if sim_app.sim is not None:      # bewaking voor de datum van deze missie (nesten in hun periode)
            sim_app.sim.set_geofence(c["plan"]["settings"].get("date"), c["plan"]["settings"].get("dike_mode"))
        try:
            sent = sim_app.send_to_agbot(name, None if body.force else mission, wps)
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        if body.place_robot and sim_app.sim is not None:
            seg = c["plan"]["segments"][0]
            x0, y0, th = seg.pts[0]
            h = (90.0 - math.degrees(th)) % 360.0
            hr = math.radians(h)
            sim_app.sim.reset_pose(x0 - 2.0 * math.sin(hr), y0 - 2.0 * math.cos(hr), h)
            sim_app.sim.vehicle.s.stuck = None
            time.sleep(0.6)
        speed = body.speed_kmh or c["plan"]["settings"]["speed_kmh"]
        # Een afgekeurde route (force) rijdt als waypoints: de missievolger van de robot weigert hem terecht.
        mode = "waypoints" if (body.mode != "missie" or body.force or sent.get("old_software")) else "missie"
        try:
            if mode == "missie":
                r = robot_link.start_mission(sim_app.agbot.url, name, max_kmh=body.speed_kmh)
            else:
                r = sim_app.agbot_request("/api/nav/start_pure_pursuit", {"target_speed_kmh": speed,
                                                                           "lookahead_distance": body.lookahead_m,
                                                                           "gain": 1.0})
        except RuntimeError as e:
            raise HTTPException(409, f"de robot start de missie niet: {e}")
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        return {"status": "started", "mode": mode, "mission": name, "waypoints": len(wps), "agbot": r}

    @app.post("/api/fieldmission/push")
    def push(body: PushBody):
        """Naar de ECHTE robot: de kaart (geofence, geldt daar altijd), de missie en dezelfde route als
        waypoints. Start niets; rijden doe je op de robot zelf (tabblad Veldmissie)."""
        from openagbot_fieldsim.api.farm_api import refuse_unsafe
        c = get_plan(body.field)
        refuse_unsafe(c["plan"], False)      # een afgekeurde route gaat niet naar de echte robot
        url = body.robot_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "robot-adres moet met http:// beginnen, bv. http://192.168.4.1:8000")
        w = world()
        wps = to_waypoints(c["plan"], w)
        name = robot_link.mission_name(f"veld_{body.field}")
        try:
            sent = robot_link.send(url, sim_app.kaart(), name,
                                   to_veldmissie(c["plan"], w, w.field_by_id(body.field).name), wps)
        except Exception as e:
            raise HTTPException(502, f"robot op {url} niet bereikbaar of weigert: {e}")
        return {"status": "ok", "sent": len(wps), "robot_has": sent.get("waypoints"), "mission": name,
                "kaart": sent.get("kaart"), "old_software": sent["old_software"], "at": time.strftime("%H:%M:%S")}
