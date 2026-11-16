"""API van de vloot: echte robots toevoegen, live volgen en commando's sturen.

  GET    /api/fleet                         alle robots met hun stand (zonder spoor; dat gaat over /ws)
  POST   /api/fleet {name, url, kind?, color?}   robot toevoegen
  PUT    /api/fleet/{id} {name?, url?, color?, kind?, enabled?}
  DELETE /api/fleet/{id}
  POST   /api/fleet/probe {url, kind?}      adres testen zonder iets te bewaren
  POST   /api/fleet/stop                    ALLE robots stoppen
  POST   /api/fleet/{id}/stop               deze robot stoppen
  POST   /api/fleet/{id}/trail/clear        gereden spoor wissen
  GET    /api/fleet/{id}/missions           missies die op de robot staan
  POST   /api/fleet/{id}/mission/start {name, resume?, max_kmh?, confirm}   rijdt weg: `confirm` moet true zijn
  POST   /api/fleet/{id}/mission/stop
  POST   /api/fleet/{id}/map                veiligheidskaart van de geladen wereld naar de robot

De live stand (5 Hz per robot, omgerekend naar de geladen wereld) zit in het bericht van /ws onder `fleet`.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from openagbot_fieldsim.app import SimApp
from openagbot_fieldsim.fleet import RobotError, adapters
from openagbot_fieldsim.fleet.model import clean_url


class NewRobot(BaseModel):
    name: str
    url: str
    kind: str = "openagbot"
    color: str | None = None


class EditRobot(BaseModel):
    name: str | None = None
    url: str | None = None
    kind: str | None = None
    color: str | None = None
    enabled: bool | None = None


class Probe(BaseModel):
    url: str
    kind: str = "openagbot"


class MissionStart(BaseModel):
    name: str
    resume: bool = False
    max_kmh: float | None = None
    confirm: bool = False


def register(app: FastAPI, sim_app: SimApp) -> None:
    fleet = sim_app.fleet

    def link(robot_id: str):
        try:
            return fleet.get(robot_id)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))

    def robot_call(fn, *args, **kw):
        """Een commando naar de robot; zijn weigering of stilte wordt een nette fout voor de UI."""
        try:
            return fn(*args, **kw)
        except RobotError as e:
            raise HTTPException(502, str(e))

    def frame_terrain():
        sim = sim_app.sim
        return (None, None) if sim is None else (sim.world.frame, sim.terrain)

    @app.get("/api/fleet")
    def fleet_list():
        d = fleet.snapshot(*frame_terrain())
        for r in d["robots"]:
            r.pop("trail", None)
        return d

    @app.post("/api/fleet")
    def fleet_add(body: NewRobot):
        try:
            l = fleet.add(body.name, body.url, body.kind, body.color)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"status": "ok", "robot": l.config.to_dict()}

    @app.post("/api/fleet/probe")
    def fleet_probe(body: Probe):
        try:
            a = adapters.make(body.kind, clean_url(body.url))
        except ValueError as e:
            raise HTTPException(400, str(e))
        tel = robot_call(a.telemetry)
        try:
            info = a.info()
        except RobotError:
            info = {}
        pose = None
        frame, terrain = frame_terrain()
        if frame is not None and tel.has_position:
            from openagbot_fieldsim.fleet.manager import _in_terrain
            x, y = frame.to_xy(tel.lat, tel.lon)
            pose = {"x": round(x, 2), "y": round(y, 2), "in_world": _in_terrain(terrain, x, y)}
        return {"status": "ok", "info": info, "fix": tel.fix, "fix_label": tel.fix_label,
                "has_position": tel.has_position, "pose": pose}

    @app.post("/api/fleet/stop")
    def fleet_stop_all():
        return {"status": "ok", "robots": fleet.stop_all()}

    @app.put("/api/fleet/{robot_id}")
    def fleet_edit(robot_id: str, body: EditRobot):
        link(robot_id)
        try:
            l = fleet.update(robot_id, **body.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"status": "ok", "robot": l.config.to_dict()}

    @app.delete("/api/fleet/{robot_id}")
    def fleet_remove(robot_id: str):
        link(robot_id)
        fleet.remove(robot_id)
        return {"status": "ok"}

    @app.post("/api/fleet/{robot_id}/stop")
    def fleet_stop(robot_id: str):
        return {"status": "ok", "robot": robot_call(link(robot_id).adapter.stop)}

    @app.post("/api/fleet/{robot_id}/trail/clear")
    def fleet_trail_clear(robot_id: str):
        link(robot_id).clear_trail()
        return {"status": "ok"}

    @app.get("/api/fleet/{robot_id}/missions")
    def fleet_missions(robot_id: str):
        return {"missions": robot_call(link(robot_id).adapter.missions)}

    @app.post("/api/fleet/{robot_id}/mission/start")
    def fleet_mission_start(robot_id: str, body: MissionStart):
        l = link(robot_id)
        if not body.confirm:
            raise HTTPException(400, "een missie starten laat de echte robot wegrijden: stuur confirm = true mee")
        r = robot_call(l.adapter.mission_start, body.name, body.resume, body.max_kmh)
        l.clear_trail()
        return {"status": "started", "robot": r}

    @app.post("/api/fleet/{robot_id}/mission/stop")
    def fleet_mission_stop(robot_id: str):
        return {"status": "ok", "robot": robot_call(link(robot_id).adapter.mission_stop)}

    @app.post("/api/fleet/{robot_id}/map")
    def fleet_map(robot_id: str):
        """Veiligheidskaart (sloten, verboden gebieden) van de geladen wereld naar deze robot. Start niets."""
        if sim_app.sim is None:
            raise HTTPException(503, "geen wereld geladen")
        return {"status": "ok", "sent": robot_call(link(robot_id).adapter.send, sim_app.kaart())}
