"""API voor puntmissies (zelf gezette GPS-punten, onkruidplanten): zetten, importeren, volgorde, rijden, exporteren.

De punten horen bij de wereld en worden bewaard in worlds/<naam>/missions/punten.json.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from openagbot_fieldsim.missions.points import parse_points_file, random_weeds, route_length, route_order, to_waypoints


class XY(BaseModel):
    x: float
    y: float
    name: str = ""


class ImportFile(BaseModel):
    name: str
    content: str
    replace: bool = True


class Weeds(BaseModel):
    field: str
    count: int = 60


class Drive(BaseModel):
    speed_kmh: float = 2.5
    lookahead_m: float = 1.5
    place_robot: bool = True      # robot eerst 3 m voor punt 1 zetten (anders rijdt hij vanaf waar hij staat)


class Push(BaseModel):
    robot_url: str


def register(app: FastAPI, sim_app) -> None:
    def world():
        if sim_app.world is None:
            raise HTTPException(503, "geen wereld geladen")
        return sim_app.world

    def path():
        w = world()
        return os.path.join(w.folder, "missions", "punten.json")

    def load() -> list[dict]:
        p = path()
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        return []

    def save(points):
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(points, fh, indent=1, ensure_ascii=False)

    def view(points):
        f = world().frame
        xy = [f.to_xy(p["lat"], p["lon"]) for p in points]
        return {"points": [{**p, "x": round(x, 3), "y": round(y, 3)} for p, (x, y) in zip(points, xy)],
                "length_m": round(route_length(xy, list(range(len(xy)))), 1) if len(xy) > 1 else 0.0}

    @app.get("/api/points")
    def points_get():
        return view(load())

    @app.post("/api/points/add")
    def points_add(body: XY):
        pts = load()
        lat, lon = world().frame.to_latlon(body.x, body.y)
        pts.append({"lat": lat, "lon": lon, "name": body.name or f"punt {len(pts) + 1}"})
        save(pts)
        return view(pts)

    @app.post("/api/points/undo")
    def points_undo():
        pts = load()[:-1]
        save(pts)
        return view(pts)

    @app.post("/api/points/clear")
    def points_clear():
        save([])
        return view([])

    @app.post("/api/points/import")
    def points_import(body: ImportFile):
        try:
            new = parse_points_file(body.name, body.content.encode("utf-8"))
        except (ValueError, KeyError) as e:
            raise HTTPException(400, f"niet te lezen: {e}")
        if not new:
            raise HTTPException(400, "geen punten gevonden (CSV: lat, lon[, naam] of GeoJSON-punten)")
        pts = new if body.replace else load() + new
        save(pts)
        return view(pts)

    @app.post("/api/points/weeds")
    def points_weeds(body: Weeds):
        try:
            pts = random_weeds(world(), body.field, max(1, min(500, body.count)))
        except KeyError:
            raise HTTPException(404, f"veld {body.field} bestaat niet")
        save(pts)
        return view(pts)

    @app.post("/api/points/optimize")
    def points_optimize():
        pts = load()
        f = world().frame
        xy = [f.to_xy(p["lat"], p["lon"]) for p in pts]
        s = sim_app.sim.vehicle.s if sim_app.sim else None
        order = route_order(xy, (s.x, s.y) if s else None)
        pts = [pts[i] for i in order]
        save(pts)
        return view(pts)

    @app.post("/api/points/drive")
    def points_drive(body: Drive):
        """In de sim: punten als waypoints.json aan de sim-robot en pure pursuit starten (vanaf waar hij staat)."""
        pts = load()
        if len(pts) < 1:
            raise HTTPException(400, "geen punten")
        data_dir = sim_app.agbot.data_dir
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "waypoints.json"), "w", encoding="utf-8") as fh:
            json.dump(to_waypoints(pts), fh, indent=4)
        if body.place_robot and sim_app.sim is not None:
            import math
            f = world().frame
            x0, y0 = f.to_xy(pts[0]["lat"], pts[0]["lon"])
            x1, y1 = f.to_xy(pts[1]["lat"], pts[1]["lon"]) if len(pts) > 1 else (x0, y0 + 1.0)
            h = math.atan2(x1 - x0, y1 - y0)
            sim_app.sim.reset_pose(x0 - 3.0 * math.sin(h), y0 - 3.0 * math.cos(h), math.degrees(h) % 360.0)
            time.sleep(0.6)
        try:
            r = sim_app.agbot_request("/api/nav/start_pure_pursuit", {"target_speed_kmh": body.speed_kmh,
                                                                       "lookahead_distance": body.lookahead_m,
                                                                       "gain": 1.0})
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        return {"status": "started", "waypoints": len(pts), "agbot": r}

    @app.get("/api/points/waypoints.json")
    def points_download():
        data = json.dumps(to_waypoints(load()), indent=4).encode("utf-8")
        return Response(data, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="waypoints_{world().name}.json"'})

    @app.post("/api/points/push")
    def points_push(body: Push):
        """Naar de ECHTE robot (zijn eigen API): waypoints vervangen. Start niets; rijden doe je op de robot zelf."""
        url = body.robot_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "robot-adres moet met http:// beginnen, bv. http://192.168.4.1:8000")
        pts = to_waypoints(load())
        if not pts:
            raise HTTPException(400, "geen punten")

        def call(method, p, body=None):
            req = urllib.request.Request(url + p, method=method, headers={"Content-Type": "application/json"},
                                         data=None if body is None else json.dumps(body).encode("utf-8"))
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        try:
            call("DELETE", "/api/waypoints")
            for wp in pts:
                call("POST", "/api/waypoints", wp)
            got = call("GET", "/api/waypoints")
        except Exception as e:
            raise HTTPException(502, f"robot op {url} niet bereikbaar of weigert: {e}")
        return {"status": "ok", "sent": len(pts), "robot_has": len(got), "at": time.strftime("%H:%M:%S")}
