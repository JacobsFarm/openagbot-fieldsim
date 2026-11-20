"""Opgeslagen wachtrijen: een rij velden met de instellingen erbij, om over twee weken dezelfde missie te rijden.

Per wereld in worlds/<naam>/missions/wachtrijen.json.

  GET    /api/queues                   alle wachtrijen van deze wereld (nieuwst gebruikt eerst)
  POST   /api/queues {id?, name, ...}  opslaan (zelfde id = overschrijven)
  POST   /api/queues/{id}/used         'laatst gereden' bijwerken
  DELETE /api/queues/{id}              weghalen
"""
from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

FILE = "wachtrijen.json"


class QueueBody(BaseModel):
    id: str | None = None
    name: str
    fields: list[str]
    optimize: bool = False               # True = de planner kiest de volgorde, False = de volgorde van de rij
    settings: dict = {}
    start: str = "startplek"
    return_home: bool = True
    start_point: str | None = None       # alleen bij één veld: startpunt op dat veld
    advanced: bool = False               # opgeslagen in de geavanceerde stand (instellingen dan ook tonen)


def register(app: FastAPI, sim_app) -> None:
    def path() -> str:
        if sim_app.world is None:
            raise HTTPException(503, "geen wereld geladen")
        return os.path.join(sim_app.world.folder, "missions", FILE)

    def load() -> list[dict]:
        try:
            with open(path(), encoding="utf-8") as fh:
                data = json.load(fh)
            return [q for q in data.get("queues", []) if isinstance(q, dict) and q.get("id")]
        except (OSError, ValueError):
            return []

    def save(queues: list[dict]) -> None:
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"version": 1, "queues": queues}, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, p)

    def listing(queues: list[dict]) -> dict:
        known = {f.id: f.name for f in sim_app.world.fields}
        out = []
        for q in sorted(queues, key=lambda q: q.get("last_used") or q.get("saved_at") or "", reverse=True):
            out.append({**q, "missing": [f for f in q.get("fields", []) if f not in known],
                        "field_names": [known.get(f, f) for f in q.get("fields", [])]})
        return {"queues": out}

    @app.get("/api/queues")
    def queues():
        return listing(load())

    @app.post("/api/queues")
    def put(body: QueueBody):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "geef de wachtrij een naam")
        if not body.fields:
            raise HTTPException(400, "de wachtrij is leeg")
        qs = load()
        old = next((q for q in qs if q["id"] == body.id), None) if body.id else None
        if old is None:                  # zelfde naam = dezelfde wachtrij bijwerken
            old = next((q for q in qs if q.get("name", "").lower() == name.lower()), None)
        rec = body.model_dump()
        rec.update(id=old["id"] if old else uuid.uuid4().hex[:10], name=name,
                   saved_at=time.strftime("%Y-%m-%d %H:%M"), last_used=(old or {}).get("last_used"))
        qs = [q for q in qs if q["id"] != rec["id"]] + [rec]
        try:
            save(qs)
        except OSError as e:
            raise HTTPException(500, f"opslaan mislukt: {e}")
        return {**listing(qs), "saved": rec["id"]}

    @app.post("/api/queues/{qid}/used")
    def used(qid: str):
        qs = load()
        q = next((q for q in qs if q["id"] == qid), None)
        if q is None:
            raise HTTPException(404, "wachtrij bestaat niet")
        q["last_used"] = time.strftime("%Y-%m-%d %H:%M")
        save(qs)
        return listing(qs)

    @app.delete("/api/queues/{qid}")
    def remove(qid: str):
        qs = load()
        if not any(q["id"] == qid for q in qs):
            raise HTTPException(404, "wachtrij bestaat niet")
        qs = [q for q in qs if q["id"] != qid]
        save(qs)
        return listing(qs)
