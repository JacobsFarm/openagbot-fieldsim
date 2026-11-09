"""Instellingen en paden. Volgorde: omgevingsvariabele > settings.json naast de werelden > standaard.

  FIELDSIM_AGBOT_DIR   map van de OpenAgbot-software (Bruut_OpenAgbot), standaard F:/Agbot/Bruut_OpenAgbot
  FIELDSIM_HOME        map met worlds/ en runtime/, standaard de projectmap (of naast de .exe)
  FIELDSIM_PORT        poort van de sim-UI/API, standaard 8765
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_home() -> str:
    if getattr(sys, "frozen", False):            # PyInstaller: naast de .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(PACKAGE_DIR)


@dataclass
class Settings:
    home: str
    agbot_dir: str
    ui_port: int = 8765
    agbot_port: int = 8000
    gnss_port: int = 5101
    steering_port: int = 5102
    can_port: int = 5103
    world: str = "example"
    start_agbot: bool = True
    worlds_root: str | None = None     # andere map met werelden (standaard <home>/worlds)

    @property
    def worlds_dir(self) -> str:
        return self.worlds_root or os.path.join(self.home, "worlds")

    @property
    def runtime_dir(self) -> str:
        return os.path.join(self.home, "runtime")

    @property
    def static_dir(self) -> str:
        return os.path.join(PACKAGE_DIR, "static")

    def save(self) -> None:
        os.makedirs(self.home, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k != "home" and v is not None}
        with open(os.path.join(self.home, "settings.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)


def load_settings() -> Settings:
    home = os.environ.get("FIELDSIM_HOME") or _default_home()
    data = {}
    path = os.path.join(home, "settings.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    # Meegeleverde OpenAgbot (naast de .exe, zie packaging/build_exe.py --with-agbot) gaat voor de vaste map
    bundled = os.path.join(home, "agbot")
    default_agbot = bundled if os.path.isdir(os.path.join(bundled, "app")) else r"F:\Agbot\Bruut_OpenAgbot"
    s = Settings(home=home, agbot_dir=data.get("agbot_dir") or default_agbot)
    for key in ("ui_port", "agbot_port", "gnss_port", "steering_port", "can_port", "world", "start_agbot",
                "worlds_root"):
        if key in data:
            setattr(s, key, data[key])
    if os.environ.get("FIELDSIM_AGBOT_DIR"):
        s.agbot_dir = os.environ["FIELDSIM_AGBOT_DIR"]
    if os.environ.get("FIELDSIM_PORT"):
        s.ui_port = int(os.environ["FIELDSIM_PORT"])
    return s
