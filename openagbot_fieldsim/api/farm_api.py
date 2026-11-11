"""API voor de hele boerderij: routegraaf, boerderijmissie en veiligheid.

  POST /api/farm/plan {fields?, settings, return_home, order}   hele-boerderij-missie plannen
  GET  /api/farm/mission                     laatst geplande boerderijmissie
  GET  /api/farm/veldmissie.json             missiebestand v2 (velden, doorgangen, transport, geofence)
  GET  /api/farm/waypoints.json              zelfde route als waypoints voor de huidige robotsoftware
  POST /api/farm/drive {speed_kmh?, force?, mode}  in de sim rijden met de echte OpenAgbot (missievolger of waypoints)
  POST /api/farm/push {robot_url}            kaart, missie en waypoints naar de ECHTE robot (start niets)
  GET  /api/safety/kaart.json                veiligheidskaart voor de robot (openagbot.kaart, data/kaart.json)
  POST /api/safety/push {robot_url}          alleen de kaart naar de ECHTE robot
  GET  /api/farm/graph                       gebieden en doorgangen (welke bruikbaar, wat verbinden ze)
  POST /api/farm/suggest_passages            voorstellen voor doorgangen (niets wordt bewaard)
  GET  /api/safety                           gevaren (stop / mijden) in lokale meters + toestand van de bewaking
  POST /api/safety/check {points, half_width_m}       route (x, y) vooraf controleren
  POST /api/safety/date {date, dike_mode}    bewaking in de sim voor een missiedatum (nesten) of dijkmodus
"""
from __future__ import annotations

import json
import math
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from openagbot_fieldsim.agbot import robot_link
from openagbot_fieldsim.missions.farm_route import FarmGraph, farm_view, plan_farm, suggest_passages, to_veldmissie_farm
from openagbot_fieldsim.missions.field_plan import PlanSettings, _poly_json, to_waypoints
from openagbot_fieldsim.missions.safety import check_path, hazards


class FarmPlanBody(BaseModel):
    fields: list[str] | None = None
    settings: dict = {}
    return_home: bool = True
    order: str = "auto"


class FarmDriveBody(BaseModel):
    speed_kmh: float | None = None
    lookahead_m: float = 2.0
    place_robot: bool = True
    force: bool = False
    mode: str = "missie"                 # missie = missievolger van de robot | waypoints = punt naar punt


class RobotBody(BaseModel):
    robot_url: str


class CheckBody(BaseModel):
    points: list[list[float]]
    half_width_m: float = 0.6
    date: str | None = None


class DateBody(BaseModel):
    date: str | None = None
    dike_mode: str | None = None


class SuggestBody(BaseModel):
    settings: dict = {}


def refuse_unsafe(plan: dict, force: bool) -> None:
    """Een missie die door een verboden gebied of sloot gaat rijdt de sim niet (tenzij uitdrukkelijk geforceerd)."""
    safety = plan.get("safety") or {}
    if safety.get("ok", True) or force:
        return
    what = sorted({v.get("label", "gevaar") + (f" {v['name']}" if v.get("name") else "")
                   for v in safety.get("violations", [])})
    raise HTTPException(409, "veiligheidscontrole: de route raakt " + ", ".join(what[:5]) +
                        ". Pas het veld, de vrije ruimte of de doorgang aan (rode stukken op de kaart).")


def register(app: FastAPI, sim_app) -> None:
    cache: dict = {}

    def world():
        if sim_app.world is None:
            raise HTTPException(503, "geen wereld geladen")
        return sim_app.world

    def last():
        if cache.get("world") is not sim_app.world or "plan" not in cache:
            raise HTTPException(404, "eerst plannen")
        return cache

    @app.post("/api/farm/plan")
    def plan(body: FarmPlanBody):
        w = world()
        try:
            s = PlanSettings.from_dict(body.settings)
            vehicle = sim_app.sim.params if sim_app.sim is not None else None
            p = plan_farm(w, body.fields, s, vehicle, return_home=body.return_home, order=body.order)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        view = farm_view(p)
        cache.update(plan=p, view=view, world=w)
        try:
            folder = os.path.join(w.folder, "missions")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "veldmissie_boerderij.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump(to_veldmissie_farm(p, w), fh, indent=1, ensure_ascii=False)
        except OSError:
            pass
        return view

    @app.get("/api/farm/mission")
    def mission():
        return last()["view"]

    @app.get("/api/farm/veldmissie.json")
    def download_mission():
        data = json.dumps(to_veldmissie_farm(last()["plan"], world()), indent=1, ensure_ascii=False).encode("utf-8")
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="veldmissie_boerderij.json"'})

    @app.get("/api/farm/waypoints.json")
    def download_waypoints():
        data = json.dumps(to_waypoints(last()["plan"], world()), indent=1).encode("utf-8")
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="waypoints_boerderij.json"'})

    @app.post("/api/farm/drive")
    def drive(body: FarmDriveBody):
        c = last()
        refuse_unsafe(c["plan"], body.force)
        wps = to_waypoints(c["plan"], world())
        name = "boerderij"
        try:
            sent = sim_app.send_to_agbot(name, None if body.force else to_veldmissie_farm(c["plan"], world()), wps)
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        s = c["plan"]["settings"]
        if sim_app.sim is not None:
            sim_app.sim.set_geofence(s.get("date"), s.get("dike_mode"))
            if body.place_robot:
                x0, y0, th = c["plan"]["segments"][0].pts[0]
                sim_app.sim.reset_pose(x0, y0, (90.0 - math.degrees(th)) % 360.0)
                sim_app.sim.vehicle.s.stuck = None
                time.sleep(0.6)
        mode = "waypoints" if (body.mode != "missie" or body.force or sent.get("old_software")) else "missie"
        try:
            if mode == "missie":
                r = robot_link.start_mission(sim_app.agbot.url, name, max_kmh=body.speed_kmh)
            else:
                r = sim_app.agbot_request("/api/nav/start_pure_pursuit", {
                    "target_speed_kmh": body.speed_kmh or s["speed_kmh"], "lookahead_distance": body.lookahead_m,
                    "gain": 1.0})
        except RuntimeError as e:
            raise HTTPException(409, f"de robot start de missie niet: {e}")
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        return {"status": "started", "mode": mode, "mission": name, "waypoints": len(wps), "agbot": r}

    def robot_url(body: RobotBody) -> str:
        url = body.robot_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "robot-adres moet met http:// beginnen, bv. http://192.168.4.1:8000")
        return url

    @app.post("/api/farm/push")
    def push(body: RobotBody):
        """Naar de ECHTE robot: kaart, boerderijmissie en waypoints. Start niets."""
        c = last()
        refuse_unsafe(c["plan"], False)
        url = robot_url(body)
        wps = to_waypoints(c["plan"], world())
        try:
            sent = robot_link.send(url, sim_app.kaart(), "boerderij", to_veldmissie_farm(c["plan"], world()), wps)
        except Exception as e:
            raise HTTPException(502, f"robot op {url} niet bereikbaar of weigert: {e}")
        return {"status": "ok", "sent": len(wps), "robot_has": sent.get("waypoints"), "mission": "boerderij",
                "kaart": sent.get("kaart"), "old_software": sent["old_software"], "at": time.strftime("%H:%M:%S")}

    @app.get("/api/safety/kaart.json")
    def download_kaart():
        world()
        data = json.dumps(sim_app.kaart(), indent=1, ensure_ascii=False).encode("utf-8")
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="kaart.json"'})

    @app.post("/api/safety/push")
    def push_kaart(body: RobotBody):
        """Alleen de veiligheidskaart naar de ECHTE robot: zijn geofence geldt dan altijd, ook handmatig."""
        world()
        url = robot_url(body)
        try:
            sent = robot_link.send(url, sim_app.kaart())
        except Exception as e:
            raise HTTPException(502, f"robot op {url} niet bereikbaar of weigert: {e}")
        if sent["old_software"]:
            raise HTTPException(409, "de software op de robot kent nog geen kaart (PUT /api/kaart ontbreekt)")
        return {"status": "ok", "kaart": sent.get("kaart"), "at": time.strftime("%H:%M:%S")}

    @app.get("/api/farm/graph")
    def graph(width: float = 3.0):
        g = FarmGraph(world(), PlanSettings(work_width_m=width))
        return g.view()

    @app.post("/api/farm/suggest_passages")
    def suggest(body: SuggestBody):
        try:
            return {"passages": suggest_passages(world(), body.settings)}
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))

    @app.get("/api/safety")
    def safety(date: str | None = None):
        w = world()
        out = []
        for h in hazards(w, date):
            if h.kind in ("boom", "gebouw"):          # die tekent de wereld zelf al
                continue
            out.append({"kind": h.kind, "label": h.label, "level": h.level, "id": h.id, "name": h.name,
                        "poly": _poly_json(h.geom.simplify(0.1))})
        gf = sim_app.sim.geofence if sim_app.sim is not None else None
        return {"hazards": out, "state": gf.state() if gf else None,
                "settings": gf.settings if gf else None,
                "nests": [{"id": n.id, "name": n.name, "active": n.active(date), "start": n.start, "end": n.end}
                          for n in w.nests]}

    @app.post("/api/safety/check")
    def safety_check(body: CheckBody):
        v = check_path(world(), body.points, body.half_width_m, body.date)
        return {"ok": not v, "violations": v}

    @app.post("/api/safety/date")
    def safety_date(body: DateBody):
        if sim_app.sim is None:
            raise HTTPException(503, "geen wereld geladen")
        try:
            sim_app.sim.set_geofence(body.date, body.dike_mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"status": "ok", "zones": len(sim_app.sim.geofence.zones)}
