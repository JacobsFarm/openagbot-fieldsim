"""Adapters: per soort robot de vertaling van zijn eigen API naar `Telemetry` en naar een paar vaste commando's.

De vloot kent alleen `RobotAdapter`. Een andere robot (ander merk, andere softwareversie) krijgt een eigen
subklasse en één regel `register("soort", Klasse)`; de manager, de API en de UI veranderen daar niet door.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from openagbot_fieldsim.agbot import robot_link
from openagbot_fieldsim.fleet.model import Telemetry


class RobotError(RuntimeError):
    """De robot is niet bereikbaar of weigert het commando."""


class RobotAdapter:
    """Basis voor een soort robot. `telemetry` wordt een paar keer per seconde geroepen, de rest op verzoek."""

    kind = "?"
    label = "?"
    capabilities: tuple[str, ...] = ()       # wat de UI mag aanbieden: stop, missions, map, ui

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def info(self) -> dict:
        """Wie ben je: {name, api, simulated, robot: {wheelbase, track, ...}}. Eén keer na het verbinden."""
        raise NotImplementedError

    def telemetry(self) -> Telemetry:
        raise NotImplementedError

    def stop(self) -> dict:
        raise NotImplementedError

    def missions(self) -> list[dict]:
        raise RobotError(f"{self.label} kent geen missies")

    def mission_start(self, name: str, resume: bool = False, max_kmh: float | None = None) -> dict:
        raise RobotError(f"{self.label} kent geen missies")

    def mission_stop(self) -> dict:
        raise RobotError(f"{self.label} kent geen missies")

    def send(self, kaart: dict | None = None, name: str | None = None, mission: dict | None = None,
             waypoints: list | None = None) -> dict:
        raise RobotError(f"{self.label} neemt geen kaart of missie aan")


class OpenAgbotAdapter(RobotAdapter):
    """Bruut OpenAgbot over zijn HTTP-API (dezelfde die zijn eigen webinterface gebruikt)."""

    kind = "openagbot"
    label = "Bruut OpenAgbot"
    capabilities = ("stop", "missions", "map", "ui")

    # /api/status komt elke keer; de rest is trager en verandert minder snel
    SLOW_EVERY = 5

    def __init__(self, url: str, timeout: float = 1.5):
        super().__init__(url)
        self.timeout = timeout
        self._tick = 0
        self._steering: dict | None = None
        self._battery: dict = {}
        self._mission: dict | None = None

    def _call(self, method: str, path: str, data=None, timeout: float | None = None):
        req = urllib.request.Request(self.url + path, method=method, headers={"Content-Type": "application/json"},
                                     data=None if data is None else json.dumps(data).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail")
            except Exception:      # noqa: BLE001
                detail = None
            raise RobotError(f"{method} {path}: {e.code} {detail or e.reason}") from e
        except (OSError, ValueError) as e:       # geen verbinding, time-out of geen JSON
            raise RobotError(f"geen antwoord van {self.url}: {getattr(e, 'reason', e)}") from e

    # ------------------------------------------------------------------ lezen
    def info(self) -> dict:
        d = self._call("GET", "/api/info", timeout=3.0)
        cfg = d.get("config", {})
        veh = cfg.get("vehicle", {})
        return {"name": d.get("naam") or self.label, "api": d.get("api"), "simulated": bool(d.get("simulatie")),
                "robot": {k: veh[k] for k in ("wheelbase_m", "track_width_m") if k in veh}}

    def telemetry(self) -> Telemetry:
        s = self._call("GET", "/api/status")
        slow = self._tick % self.SLOW_EVERY == 0
        self._tick += 1
        if slow:
            self._slow(s)
        steer = (self._steering or {}).get("position") or {}
        if s.get("missie_actief"):
            mode, state, msg = "mission", s.get("missie_state"), s.get("missie_message")
        elif s.get("ab_active"):
            mode, state, msg = "ab", s.get("ab_state"), s.get("ab_message")
        elif s.get("navigator_active"):
            mode, state, msg = "waypoints", s.get("nav_state"), s.get("nav_message")
        elif abs(s.get("speed_kmh") or 0.0) > 0.2:
            mode, state, msg = "manual", "", ""
        else:
            mode, state, msg = "idle", "", ""
        gf = s.get("geofence") or {}
        blocked = gf and not (gf.get("vooruit", True) and gf.get("achteruit", True))
        return Telemetry(
            lat=s.get("lat"), lon=s.get("lon"), heading=s.get("heading"),
            heading_valid=bool(s.get("heading_geldig", True)), speed_kmh=float(s.get("speed_kmh") or 0.0),
            fix=int(s.get("fix") or 0), fix_label=str(s.get("fix_soort") or ""), sats=s.get("sats"),
            correction_age_s=s.get("correctie_leeftijd_s"),
            wheel_left_deg=steer.get("left"), wheel_right_deg=steer.get("right"),
            battery_v=self._battery.get("voltage_v", s.get("accu_v")),
            battery_soc=None if self._battery.get("soc_pct") is None else self._battery["soc_pct"] / 100.0,
            mode=mode, state=str(state or ""), message=str(msg or ""),
            mission=self._mission if mode == "mission" else None,
            fault=s.get("drive_fault") or None, geofence=(gf.get("reden") or "geofence") if blocked else None)

    def _slow(self, status: dict) -> None:
        """Stuurhoeken, accu en missievoortgang. Mislukt er één, dan blijft de vorige waarde staan: de positie
        (uit /api/status) is belangrijker dan deze extra's."""
        try:
            self._steering = self._call("GET", "/api/steering/status").get("steering")
        except RobotError:
            pass
        try:
            self._battery = self._call("GET", "/api/motors").get("battery") or {}
        except RobotError:
            pass
        if not status.get("missie_actief"):
            self._mission = None
            return
        try:
            m = self._call("GET", "/api/missie/live?spoor_vanaf=2000000000").get("missie") or {}
            self._mission = {"name": m.get("naam"), "percent": m.get("procent"),
                             "segment_label": m.get("segment_label"), "remaining_min": m.get("resterend_min"),
                             "xte_m": m.get("afwijking_m"), "implement_down": m.get("werktuig_omlaag")}
        except RobotError:
            pass

    # ------------------------------------------------------------------ commando's
    def stop(self) -> dict:
        return self._call("POST", "/api/stop", {}, timeout=3.0)

    def missions(self) -> list[dict]:
        d = self._call("GET", "/api/missies", timeout=4.0)
        progress = d.get("voortgang", {})
        return [{"name": m.get("naam"), "error": m.get("fout"), "progress": progress.get(m.get("naam")),
                 **{k: v for k, v in m.items() if k not in ("naam", "fout")}} for m in d.get("missies", [])]

    def mission_start(self, name: str, resume: bool = False, max_kmh: float | None = None) -> dict:
        try:
            return robot_link.start_mission(self.url, name, resume, max_kmh)
        except (RuntimeError, OSError) as e:
            raise RobotError(str(e)) from e

    def mission_stop(self) -> dict:
        return self._call("POST", "/api/missie/stop", {}, timeout=3.0)

    def send(self, kaart=None, name=None, mission=None, waypoints=None) -> dict:
        try:
            return robot_link.send(self.url, kaart, name, mission, waypoints)
        except (RuntimeError, OSError) as e:
            raise RobotError(str(e)) from e


ADAPTERS: dict[str, type[RobotAdapter]] = {}


def register(kind: str, cls: type[RobotAdapter]) -> None:
    ADAPTERS[kind] = cls


def make(kind: str, url: str) -> RobotAdapter:
    try:
        return ADAPTERS[kind](url)
    except KeyError:
        raise ValueError(f"onbekende soort robot '{kind}' (bekend: {', '.join(sorted(ADAPTERS))})") from None


register(OpenAgbotAdapter.kind, OpenAgbotAdapter)
