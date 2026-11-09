"""De echte OpenAgbot-backend naast de sim draaien, in een eigen werkmap.

OpenAgbot leest en schrijft relatief aan zijn werkmap (`data/config*.json`, `data/ab_line.json`, `data/logs/`).
De sim start hem daarom in `runtime/agbot/` met een eigen `data/`: missies uit de sim en ritlogs van gesimuleerde
ritten komen daar, en de echte `data/` van de robot blijft onaangeroerd. De code zelf komt ongewijzigd uit de
OpenAgbot-map (`FIELDSIM_AGBOT_DIR`).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque

log = logging.getLogger(__name__)


def load_sim_config(agbot_dir: str) -> dict:
    """data/config.sim.json van OpenAgbot, anders config.example.json (met de sim-verbindingen erin gezet)."""
    for name in ("config.sim.json", "config.example.json"):
        path = os.path.join(agbot_dir, "data", name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError(f"geen data/config.sim.json of config.example.json in {agbot_dir}")


def runtime_config(base: dict, gnss_port: int, steering_port: int, can_port: int) -> dict:
    cfg = json.loads(json.dumps(base))
    hw = cfg.setdefault("hardware", {})
    hw["gps_port"] = f"socket://127.0.0.1:{gnss_port}"
    hw["arduino_stepper_port"] = f"socket://127.0.0.1:{steering_port}"
    vesc = cfg.setdefault("vesc", {})
    vesc["can_interface"] = "simcan"
    vesc["can_channel"] = f"127.0.0.1:{can_port}"
    cfg["ntrip"] = {**cfg.get("ntrip", {}), "host": ""}
    cfg.setdefault("system", {})["os"] = "sim"
    return cfg


class AgbotProcess:
    def __init__(self, agbot_dir: str, workdir: str, port: int = 8000):
        self.agbot_dir = os.path.abspath(agbot_dir)
        self.workdir = os.path.abspath(workdir)
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.lines: deque = deque(maxlen=400)
        self.started_at: float | None = None

    @property
    def data_dir(self) -> str:
        return os.path.join(self.workdir, "data")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def write_config(self, cfg: dict) -> str:
        os.makedirs(os.path.join(self.data_dir, "logs"), exist_ok=True)
        path = os.path.join(self.data_dir, "config.sim.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        return path

    def start(self, cfg: dict) -> None:
        if self.running:
            return
        self.write_config(cfg)
        env = dict(os.environ)
        env["AGBOT_CONFIG"] = os.path.join("data", "config.sim.json")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--agbot-runner"]
        else:
            cmd = [sys.executable, "-m", "openagbot_fieldsim.agbot.runner"]
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        cmd += ["--agbot-dir", self.agbot_dir, "--port", str(self.port)]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, cwd=self.workdir, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, creationflags=flags)
        self.started_at = time.time()
        threading.Thread(target=self._pump, daemon=True, name="agbot-log").start()
        log.info("OpenAgbot gestart (pid %s) op %s", self.proc.pid, self.url)

    def _pump(self):
        proc = self.proc
        logfile = os.path.join(self.workdir, "agbot_console.log")
        with open(logfile, "a", encoding="utf-8") as out:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                self.lines.append(line)
                out.write(line + "\n")
                out.flush()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 5.0) -> None:
        if not self.running:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def status(self) -> dict:
        return {"running": self.running, "url": self.url, "pid": self.proc.pid if self.proc else None,
                "returncode": self.proc.poll() if self.proc else None, "agbot_dir": self.agbot_dir,
                "workdir": self.workdir, "log": list(self.lines)[-60:]}
