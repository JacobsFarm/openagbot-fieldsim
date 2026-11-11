"""Kaart, missie en waypoints naar een OpenAgbot sturen (de sim-robot of de echte), over zijn HTTP-API.

  PUT /api/kaart               veiligheidskaart (openagbot.kaart): geldt op de robot altijd, ook handmatig
  PUT /api/missies/<naam>      missiebestand (openagbot.veldmissie) voor het tabblad Veldmissie
  PUT /api/waypoints           dezelfde route als waypoints (voor robots zonder missievolger: per punt een POST)

Niets hiervan start een rit: starten doe je op de robot zelf (of, in de sim, met `start_mission`).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request


def mission_name(text: str) -> str:
    """Naam waaronder een missie op de robot staat: alleen letters, cijfers, - en _."""
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", text).strip("_")[:60]
    return name or "missie"


def call(url: str, method: str, path: str, data=None, timeout: float = 10.0):
    req = urllib.request.Request(url.rstrip("/") + path, method=method,
                                 headers={"Content-Type": "application/json"},
                                 data=None if data is None else json.dumps(data).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail")
        except Exception:      # noqa: BLE001
            detail = None
        raise RuntimeError(f"{method} {path}: {e.code} {detail or e.reason}") from e


def send(url: str, kaart: dict | None = None, name: str | None = None, mission: dict | None = None,
         waypoints: list | None = None) -> dict:
    """Stuurt wat er is. Geeft per onderdeel wat de robot antwoordde; een robot met oude software (zonder
    /api/kaart en /api/missies) krijgt alleen de waypoints en dat staat dan in `old_software`."""
    out: dict = {"old_software": False}
    if kaart is not None:
        try:
            out["kaart"] = call(url, "PUT", "/api/kaart", kaart).get("kaart")
        except RuntimeError as e:
            if " 404 " not in str(e) and " 405 " not in str(e):
                raise
            out["old_software"] = True
    if mission is not None and not out["old_software"]:
        try:
            out["mission"] = call(url, "PUT", f"/api/missies/{mission_name(name or 'missie')}", mission).get("missie")
        except RuntimeError as e:
            if " 404 " not in str(e) and " 405 " not in str(e):
                raise
            out["old_software"] = True
    if waypoints is not None:
        try:
            out["waypoints"] = call(url, "PUT", "/api/waypoints", waypoints).get("total")
        except RuntimeError as e:
            if " 405 " not in str(e) and " 404 " not in str(e):
                raise
            call(url, "DELETE", "/api/waypoints")
            for wp in waypoints:
                call(url, "POST", "/api/waypoints", wp)
            out["waypoints"] = len(call(url, "GET", "/api/waypoints"))
    return out


def start_mission(url: str, name: str, resume: bool = False, max_kmh: float | None = None) -> dict:
    r = call(url, "POST", "/api/missie/start", {"naam": mission_name(name), "hervat": resume, "max_kmh": max_kmh})
    if r.get("status") != "started":
        raise RuntimeError(r.get("msg") or str(r))
    return r
