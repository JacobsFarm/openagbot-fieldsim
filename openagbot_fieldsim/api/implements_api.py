"""API voor de aanbouwdelen.

  GET  /api/implements                       catalogus (met offset en secties in het robotframe van de sim)
  GET  /api/implements/{id}/{pose}.glb       model uit FreeCAD (tools/export_implements.py)
  POST /api/implement {id|null}              werktuig aankoppelen of afkoppelen
  POST /api/implement/set {mode?, rate_l_ha?}   mode: auto | down | up
  POST /api/implement/reset                  bewerkte strook, tellers en planten wissen
  POST /api/implement/targets                boor: de punten (tab Punten) als planten nemen
  GET  /api/implement/targets                planten met status (open/done/missed_speed/out_of_reach) en dx
"""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from openagbot_fieldsim.implements.catalog import ASSETS, CATALOG, get


class Choose(BaseModel):
    id: str | None = None


class Setting(BaseModel):
    mode: str | None = None
    rate_l_ha: float | None = None


def register(app: FastAPI, sim_app) -> None:
    def sim():
        if sim_app.sim is None:
            raise HTTPException(503, "geen simulatie")
        return sim_app.sim

    def state():
        imp = sim().implement
        if imp is None:
            raise HTTPException(400, "geen werktuig aangekoppeld")
        return imp

    @app.get("/api/implements")
    def implements_list():
        wb = sim_app.sim.params.wheelbase_m if sim_app.sim else 1.2
        return {"implements": [s.to_dict(wb) for s in CATALOG], "current": sim_app.implement_id}

    @app.get("/api/implements/{implement_id}/{pose}.glb")
    def implement_glb(implement_id: str, pose: str):
        try:
            get(implement_id)
        except KeyError:
            raise HTTPException(404, "onbekend werktuig")
        path = os.path.join(ASSETS, implement_id, os.path.basename(pose) + ".glb")
        if not os.path.isfile(path):
            raise HTTPException(404, "model niet geëxporteerd (tools/export_implements.py)")
        return FileResponse(path, media_type="model/gltf-binary")

    @app.post("/api/implement")
    def implement_choose(body: Choose):
        if body.id:
            try:
                get(body.id)
            except KeyError:
                raise HTTPException(404, f"onbekend werktuig {body.id}")
        sim_app.implement_id = body.id or None
        imp = sim().set_implement(sim_app.implement_id)
        if imp is not None and imp.spec.kind == "spot":
            _targets_from_points(imp)
        return {"current": sim_app.implement_id}

    @app.post("/api/implement/set")
    def implement_set(body: Setting):
        imp = state()
        if body.mode is not None:
            if body.mode not in ("auto", "down", "up"):
                raise HTTPException(400, "mode: auto, down of up")
            imp.mode = body.mode
        if body.rate_l_ha is not None:
            imp.rate_l_ha = max(0.0, float(body.rate_l_ha))
        return {"mode": imp.mode, "rate_l_ha": imp.rate_l_ha}

    @app.post("/api/implement/reset")
    def implement_reset():
        state().reset()
        return {"status": "ok"}

    def _targets_from_points(imp):
        w = sim_app.world
        path = os.path.join(w.folder, "missions", "punten.json")
        pts = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                pts = json.load(fh)
        imp.set_targets([w.frame.to_xy(p["lat"], p["lon"]) for p in pts], [p.get("name", "") for p in pts])
        return len(pts)

    @app.post("/api/implement/targets")
    def implement_targets():
        imp = state()
        if imp.spec.kind != "spot":
            raise HTTPException(400, "alleen voor een puntwerktuig (boor)")
        return {"targets": _targets_from_points(imp)}

    @app.get("/api/implement/targets")
    def implement_targets_get():
        return {"targets": state().target_list()}
