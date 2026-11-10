"""SimApp: houdt de wereld, de simulatie en de OpenAgbot-backend bij elkaar. De API en de tests praten hiermee."""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

from openagbot_fieldsim.agbot.runtime import AgbotProcess, load_sim_config, runtime_config
from openagbot_fieldsim.engine import Ports, Simulation
from openagbot_fieldsim.settings import Settings
from openagbot_fieldsim.world.model import World, load_world

log = logging.getLogger(__name__)


class SimApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_config = load_sim_config(settings.agbot_dir)
        self.agbot = AgbotProcess(settings.agbot_dir, os.path.join(settings.runtime_dir, "agbot"),
                                  settings.agbot_port)
        self.sim: Simulation | None = None
        self.world: World | None = None
        self._lock = threading.RLock()
        self.missions: dict[str, dict] = {}      # laatst geplande missie per veld (voor de UI)
        self.implement_id: str | None = None     # gekozen aanbouwdeel, blijft bij een andere wereld
        self.build_status: dict = {"running": False, "world": None, "log": [], "error": None}

    # ------------------------------------------------------------------ werelden
    def list_worlds(self) -> list[dict]:
        out = []
        root = self.settings.worlds_dir
        for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            path = os.path.join(root, name, "world.json")
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        d = json.load(fh)
                    out.append({"name": name, "label": d.get("label", name),
                                "fields": len(d.get("fields", [])), "buildings": len(d.get("buildings", []))})
                except (OSError, ValueError) as e:
                    out.append({"name": name, "label": name, "error": str(e)})
        return out

    def load(self, name: str | None = None, start_agbot: bool | None = None) -> None:
        name = name or self.settings.world
        with self._lock:
            self.agbot.stop()
            if self.sim is not None:
                self.sim.stop()
            self.world = load_world(os.path.join(self.settings.worlds_dir, name))
            s = self.settings
            self.sim = Simulation(self.world, self.base_config, Ports(s.gnss_port, s.steering_port, s.can_port))
            if self.implement_id:
                self.sim.set_implement(self.implement_id)
            self.sim.on_geofence = self._on_geofence
            self.sim.start()
            self.missions.clear()
            self.settings.world = name
            if self.settings.start_agbot if start_agbot is None else start_agbot:
                self.start_agbot()

    def _on_geofence(self, violation: dict) -> None:
        """Stop-gebied geraakt: ook de sim-robot stoppen, zodat hij na 'verder' niet doorrijdt."""
        if self.sim is None or not self.sim.geofence.settings.get("stop_robot", True):
            return

        def stop():
            try:
                self.agbot_request("/api/stop", {})
            except Exception:      # noqa: BLE001 - OpenAgbot draait niet: de sim staat al op pauze
                pass

        threading.Thread(target=stop, daemon=True, name="geofence-stop").start()

    def start_agbot(self) -> None:
        p = self.sim.ports
        self.agbot.start(runtime_config(self.base_config, p.gnss, p.steering, p.can))

    def restart_agbot(self) -> None:
        self.agbot.stop()
        self.start_agbot()

    def shutdown(self) -> None:
        self.agbot.stop()
        if self.sim:
            self.sim.stop()

    # ------------------------------------------------------------------ OpenAgbot-API
    def agbot_request(self, path: str, body: dict | None = None, timeout: float = 3.0):
        url = self.agbot.url + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # ------------------------------------------------------------------ wereldbouwer
    def build_world(self, name: str) -> None:
        """Wereld uit kaarten bouwen (PDOK) in een achtergrondthread; voortgang in build_status."""
        if self.build_status["running"]:
            raise RuntimeError("er wordt al een wereld gebouwd")
        from openagbot_fieldsim.world.builder import Builder
        st = self.build_status = {"running": True, "world": name, "log": [], "error": None}

        def run():
            try:
                Builder(os.path.join(self.settings.worlds_dir, name), progress=st["log"].append).build()
                if self.settings.world == name:
                    self.load(name)
            except Exception as e:          # noqa: BLE001 - de UI moet de fout zien
                log.exception("wereld bouwen")
                st["error"] = str(e)
            finally:
                st["running"] = False

        threading.Thread(target=run, daemon=True, name="world-builder").start()
