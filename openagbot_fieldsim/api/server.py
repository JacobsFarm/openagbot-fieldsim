"""HTTP/WebSocket-API van de sim, en de UI als statische bestanden.

  GET  /api/worlds                       werelden in worlds/
  POST /api/world/load {name}            andere wereld laden (herstart sim en OpenAgbot)
  GET  /api/world                        scene in lokale meters
  GET  /api/world/terrain?cell=2         hoogteraster: header X-Terrain (json) + float32 little endian
  WS   /ws                               live toestand, 30 Hz
  POST /api/sim/reset {x?,y?,heading?}   robot neerzetten (standaard op de startplek)
  POST /api/sim/pause {paused}
  POST /api/sim/faults {...}             storingen (zie Simulation.set_faults)
  GET  /api/agbot                        status + console van de OpenAgbot-backend
  POST /api/agbot/restart
  GET  /api/missions/ab/{field}?width=   AB-missie plannen (alleen bekijken)
  POST /api/missions/ab {field, width, start}   missie opslaan in data/ab_line.json van de sim-robot, en starten
  POST /api/agbot/stop                   noodstop via de API van de robot
  GET  /api/agbot/plan                   gepland rijpad van de lopende AB-missie (uit OpenAgbot zelf)
  /api/implements...                     aanbouwdelen: zie api/implements_api.py
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openagbot_fieldsim.api import implements_api, points_api
from openagbot_fieldsim.api.scene import scene, terrain_bytes
from openagbot_fieldsim.app import SimApp
from openagbot_fieldsim.missions.ab import plan_ab, save_ab_line, start_command
from openagbot_fieldsim.replay.ride_log import list_logs, load_log, waypoints_from_log


class LoadWorld(BaseModel):
    name: str


class NewWorld(BaseModel):
    name: str
    label: str = ""
    points: str
    margin_m: float = 50.0


class Reset(BaseModel):
    x: float | None = None
    y: float | None = None
    heading: float | None = None


class Pause(BaseModel):
    paused: bool


class Faults(BaseModel):
    fix: str | None = None
    heading_valid: bool | None = None
    stream_enabled: bool | None = None
    correction_age_s: float | None = None
    noise_scale: float | None = None
    mount_errors: bool | None = None
    steering_responsive: bool | None = None
    steering_stalled: bool | None = None
    vesc_online: bool | None = None
    vesc_fault_code: int | None = None
    clear_stuck: bool | None = None


class Redrive(BaseModel):
    source: str
    file: str
    spacing_m: float = 2.0
    speed_kmh: float = 3.0
    lookahead_m: float = 2.5


class AbMission(BaseModel):
    field: str
    width: float = 3.0
    headland: float = 8.0
    speed_kmh: float = 3.0
    turn_speed_kmh: float = 2.0
    skip: int = 1
    start: bool = False
    place_robot: bool = True


def create_app(sim_app: SimApp) -> FastAPI:
    app = FastAPI(title="OpenAgbot FieldSim", version="0.1.0")

    def need_sim():
        if sim_app.sim is None:
            raise HTTPException(503, "geen wereld geladen")
        return sim_app.sim

    @app.get("/api/worlds")
    def worlds():
        return {"current": sim_app.settings.world, "worlds": sim_app.list_worlds()}

    @app.post("/api/world/load")
    def world_load(body: LoadWorld):
        try:
            sim_app.load(body.name)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        return {"status": "ok", "world": body.name}

    @app.post("/api/world/new")
    def world_new(body: NewWorld):
        import re
        from openagbot_fieldsim.world.model import new_world_from_points, parse_points
        if not re.fullmatch(r"[a-z0-9_]{2,40}", body.name):
            raise HTTPException(400, "naam: kleine letters, cijfers en _ (2-40 tekens)")
        folder = os.path.join(sim_app.settings.worlds_dir, body.name)
        if os.path.exists(os.path.join(folder, "world.json")):
            raise HTTPException(409, f"wereld {body.name} bestaat al")
        try:
            w = new_world_from_points(body.name, body.label, parse_points(body.points), body.margin_m)
        except ValueError as e:
            raise HTTPException(400, str(e))
        w.save(folder)
        return {"status": "ok", "world": body.name, "area": [list(p) for p in w.area]}

    @app.post("/api/world/build")
    def world_build(body: LoadWorld):
        try:
            sim_app.build_world(body.name)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return sim_app.build_status

    @app.get("/api/world/build")
    def world_build_status():
        return sim_app.build_status

    @app.get("/api/world")
    def world_scene():
        sim = need_sim()
        return scene(sim.world, sim.terrain, sim.params)

    @app.get("/api/world/terrain")
    def world_terrain(cell: float = 2.0):
        sim = need_sim()
        header, data = terrain_bytes(sim.terrain, max(0.5, cell))
        return Response(data, media_type="application/octet-stream", headers={"X-Terrain": json.dumps(header)})

    @app.post("/api/sim/reset")
    def sim_reset(body: Reset):
        sim = need_sim()
        try:
            sim_app.agbot_request("/api/stop", {})
        except Exception:
            pass
        sim.reset_pose(body.x, body.y, body.heading)
        sim.vehicle.s.stuck = None
        return {"status": "ok"}

    @app.post("/api/world/start_pose")
    def world_start_pose(body: Reset):
        """Huidige (of opgegeven) robotpositie bewaren als startplek van deze wereld."""
        from openagbot_fieldsim.world.model import Pose
        sim = need_sim()
        s = sim.vehicle.s
        x, y = (body.x, body.y) if body.x is not None else (s.x, s.y)
        heading = body.heading if body.heading is not None else s.heading
        lat, lon = sim.world.frame.to_latlon(x, y)
        sim.world.start_pose = Pose(lat, lon, round(heading, 1))
        sim.world.sources["start_pose"] = "user"
        sim.world.save(sim.world.folder)
        return {"status": "ok", "start_pose": sim.world.start_pose.to_dict()}

    @app.post("/api/sim/pause")
    def sim_pause(body: Pause):
        need_sim().paused = body.paused
        return {"paused": body.paused}

    @app.post("/api/sim/faults")
    def sim_faults(body: Faults):
        need_sim().set_faults(**body.model_dump())
        return {"status": "ok", "gnss": vars(sim_app.sim.gnss.settings)}

    @app.get("/api/agbot")
    def agbot_status():
        return sim_app.agbot.status()

    @app.post("/api/agbot/restart")
    def agbot_restart():
        sim_app.restart_agbot()
        return sim_app.agbot.status()

    @app.post("/api/agbot/stop")
    def agbot_stop():
        try:
            return sim_app.agbot_request("/api/stop", {})
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")

    @app.get("/api/agbot/plan")
    def agbot_plan():
        """Het rijpad zoals OpenAgbot het zelf plant (banen + kopakkerbochten), omgerekend naar lokale meters."""
        try:
            r = sim_app.agbot_request("/api/ab/plan")
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        if r.get("status") != "ok":
            return {"status": r.get("status", "leeg")}
        plan, frame = r["plan"], need_sim().world.frame
        path = []
        bochten = plan.get("bochten", [])
        for i, baan in enumerate(plan.get("banen", [])):
            path += [frame.to_xy(p[0], p[1]) for p in baan["pad"]]
            if i < len(bochten):
                path += [frame.to_xy(p[0], p[1]) for p in bochten[i]["pad"]]
        return {"status": "ok", "path": [[round(x, 3), round(y, 3)] for x, y in path],
                "order": [b["baan"] for b in plan.get("banen", [])], "versie": plan.get("versie"),
                "werkbreedte_m": plan.get("werkbreedte_m")}

    @app.get("/api/missions/ab/{field_id}")
    def mission_preview(field_id: str, width: float = 3.0, headland: float = 8.0):
        sim = need_sim()
        try:
            plan = plan_ab(sim.world, field_id, width, headland)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        sim_app.missions[field_id] = plan
        return plan

    @app.post("/api/missions/ab")
    def mission_save(body: AbMission):
        sim = need_sim()
        try:
            plan = plan_ab(sim.world, body.field, body.width, body.headland, speed_kmh=body.speed_kmh,
                           turn_speed_kmh=body.turn_speed_kmh, banen_overslaan=body.skip)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        path = save_ab_line(sim_app.agbot.data_dir, plan["entry"])
        sim_app.missions[body.field] = plan
        result = {"status": "saved", "path": path, "plan": plan}
        if body.start:
            if body.place_robot:
                # Robot een stukje voor A neerzetten, in de rijrichting: zoals je hem in het veld klaarzet
                import math
                h = math.radians(plan["heading_deg"])
                ax, ay = plan["a"]
                sim.reset_pose(ax - 4.0 * math.sin(h), ay - 4.0 * math.cos(h), plan["heading_deg"])
                sim.vehicle.s.stuck = None
                result["placed"] = True
            try:
                import time
                time.sleep(0.6)       # GNSS en koers even laten bijkomen na het neerzetten
                result["agbot"] = sim_app.agbot_request("/api/nav/start_ab", start_command(plan["entry"]))
                result["status"] = "started"
            except Exception as e:
                raise HTTPException(502, f"opgeslagen in {path}, maar starten mislukte: {e}")
        return result

    # ------------------------------------------------------------------ ritlogs
    def log_sources() -> dict:
        out = {"sim": os.path.join(sim_app.agbot.data_dir, "logs"),
               "robot": os.path.join(sim_app.settings.agbot_dir, "data", "logs")}
        if sim_app.world and sim_app.world.folder:
            out["wereld"] = os.path.join(sim_app.world.folder, "logs")
        return out

    def log_path(source: str, name: str) -> str:
        folder = log_sources().get(source)
        if folder is None or os.path.basename(name) != name or not (name.startswith("rit_") and name.endswith(".csv")):
            raise HTTPException(400, "onbekende ritlog")
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            raise HTTPException(404, name)
        return path

    @app.get("/api/logs")
    def logs():
        return list_logs(log_sources())

    @app.get("/api/logs/{source}/{name}")
    def log_data(source: str, name: str, step: int = 2):
        return load_log(log_path(source, name), need_sim().world.frame, max(1, step))

    @app.post("/api/logs/redrive")
    def log_redrive(body: Redrive):
        """Route uit een rit als waypoints aan de sim-robot geven en met pure pursuit laten narijden."""
        import math
        sim = need_sim()
        d = load_log(log_path(body.source, body.file), sim.world.frame, 1)
        wps = waypoints_from_log(d, sim.world.frame, body.spacing_m)
        if len(wps) < 2:
            raise HTTPException(400, "te weinig punten in deze rit")
        os.makedirs(sim_app.agbot.data_dir, exist_ok=True)
        with open(os.path.join(sim_app.agbot.data_dir, "waypoints.json"), "w", encoding="utf-8") as fh:
            json.dump(wps, fh, indent=4)
        x0, y0 = sim.world.frame.to_xy(wps[0]["lat"], wps[0]["lon"])
        x1, y1 = sim.world.frame.to_xy(wps[1]["lat"], wps[1]["lon"])
        heading = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0
        sim.reset_pose(x0 - 2.0 * math.sin(math.radians(heading)), y0 - 2.0 * math.cos(math.radians(heading)), heading)
        sim.vehicle.s.stuck = None
        import time
        time.sleep(0.6)
        try:
            r = sim_app.agbot_request("/api/nav/start_pure_pursuit", {"target_speed_kmh": body.speed_kmh,
                                                                       "lookahead_distance": body.lookahead_m,
                                                                       "gain": 1.0})
        except Exception as e:
            raise HTTPException(502, f"OpenAgbot antwoordt niet: {e}")
        return {"status": "started", "waypoints": len(wps), "agbot": r}

    points_api.register(app, sim_app)
    implements_api.register(app, sim_app)

    @app.websocket("/ws")
    async def ws_state(ws: WebSocket):
        await ws.accept()
        trail_from = 0
        strip_from, strip_key = 0, None
        try:
            while True:
                sim = sim_app.sim
                if sim is not None:
                    imp = sim.implement
                    key = (id(sim), id(imp), imp.generation if imp else 0)
                    if key != strip_key:
                        strip_from, strip_key = 0, key
                    snap = sim.snapshot(trail_from, strip_from)
                    trail_from = snap["trail"]["total"]
                    if snap["implement"]:
                        strip_from = snap["implement"]["strip"]["total"]
                    snap["agbot"] = {"running": sim_app.agbot.running, "url": sim_app.agbot.url}
                    await ws.send_text(json.dumps(snap, separators=(",", ":")))
                await asyncio.sleep(1 / 30)
        except (WebSocketDisconnect, RuntimeError):
            return

    static = sim_app.settings.static_dir
    if os.path.isdir(static):
        app.mount("/assets", StaticFiles(directory=os.path.join(static, "assets"), check_dir=False), name="assets")

        @app.get("/")
        def index():
            return FileResponse(os.path.join(static, "index.html"))

        @app.get("/{path:path}")
        def static_files(path: str):
            full = os.path.normpath(os.path.join(static, path))
            if full.startswith(os.path.normpath(static)) and os.path.isfile(full):
                return FileResponse(full)
            return FileResponse(os.path.join(static, "index.html"))
    else:
        @app.get("/")
        def no_ui():
            return JSONResponse({"msg": "UI niet gebouwd: cd ui && npm install && npm run build"})

    return app
