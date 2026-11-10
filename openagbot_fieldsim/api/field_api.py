"""API voor veldmissies (kopakkerrondjes + banen + bochten als één rijpad), zie missions/field_plan.py.

  POST /api/fieldmission/plan {field, settings}      plannen (en bewaren in worlds/<naam>/missions/)
  GET  /api/fieldmission/{field}                     laatst geplande missie van dat veld
  GET  /api/fieldmission/{field}/veldmissie.json     missiebestand (lat/lon, segmenten met snelheid en werktuig)
  GET  /api/fieldmission/{field}/waypoints.json      zelfde route als waypoints voor de huidige robotsoftware
  POST /api/fieldmission/drive {field, ...}          in de sim rijden met de echte OpenAgbot (waypoints + pure pursuit)
  POST /api/fieldmission/push {field, robot_url}     waypoints naar de ECHTE robot (start niets)
  GET  /api/fieldmission/boundaries/{field}          randen van het perceel: wat ligt ernaast, hoeveel ruimte
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from openagbot_fieldsim.missions.boundary import edge_summary, field_area
from openagbot_fieldsim.missions.field_plan import PlanSettings, plan_field, plan_view, to_veldmissie, to_waypoints


class PlanBody(BaseModel):
    field: str
    settings: dict = {}


class DriveBody(BaseModel):
    field: str
    speed_kmh: float | None = None       # None = werksnelheid uit de missie
    lookahead_m: float = 2.0
    place_robot: bool = True
    force: bool = False                  # ook rijden als de veiligheidscontrole de route afkeurt


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

    @app.post("/api/fieldmission/plan")
    def plan(body: PlanBody):
        w = world()
        try:
            s = PlanSettings.from_dict(body.settings)
            vehicle = sim_app.sim.params if sim_app.sim is not None else None
            p = plan_field(w, body.field, s, vehicle)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        view = plan_view(p)
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
        wps = to_waypoints(c["plan"], world())
        if sim_app.sim is not None:      # bewaking voor de datum van deze missie (nesten in hun periode)
            sim_app.sim.set_geofence(c["plan"]["settings"].get("date"), c["plan"]["settings"].get("dike_mode"))
        data_dir = sim_app.agbot.data_dir
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "waypoints.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(wps, fh, indent=1)
        if body.place_robot and sim_app.sim is not None:
            seg = c["plan"]["segments"][0]
            x0, y0, th = seg.pts[0]
            h = (90.0 - math.degrees(th)) % 360.0
            hr = math.radians(h)
            sim_app.sim.reset_pose(x0 - 2.0 * math.sin(hr), y0 - 2.0 * math.cos(hr), h)
            sim_app.sim.vehicle.s.stuck = None
            time.sleep(0.6)
        speed = body.speed_kmh or c["plan"]["settings"]["speed_kmh"]
        try:
            r = sim_app.agbot_request("/api/nav/start_pure_pursuit", {"target_speed_kmh": speed,
                                                                       "lookahead_distance": body.lookahead_m,
                                                                       "gain": 1.0})
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        return {"status": "started", "waypoints": len(wps), "agbot": r}

    @app.post("/api/fieldmission/push")
    def push(body: PushBody):
        """Naar de ECHTE robot: waypoints vervangen. Start niets; rijden doe je op de robot zelf."""
        from openagbot_fieldsim.api.farm_api import refuse_unsafe
        c = get_plan(body.field)
        refuse_unsafe(c["plan"], False)      # een afgekeurde route gaat niet naar de echte robot
        url = body.robot_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "robot-adres moet met http:// beginnen, bv. http://192.168.4.1:8000")
        wps = to_waypoints(c["plan"], world())

        def call(method, p, data=None):
            req = urllib.request.Request(url + p, method=method, headers={"Content-Type": "application/json"},
                                         data=None if data is None else json.dumps(data).encode("utf-8"))
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        try:
            call("DELETE", "/api/waypoints")
            for wp in wps:
                call("POST", "/api/waypoints", wp)
            got = call("GET", "/api/waypoints")
        except Exception as e:
            raise HTTPException(502, f"robot op {url} niet bereikbaar of weigert: {e}")
        return {"status": "ok", "sent": len(wps), "robot_has": len(got), "at": time.strftime("%H:%M:%S")}
