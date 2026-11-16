"""Gegevens van de vloot: wat je van een robot bewaart en wat hij live meldt."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# Kleuren waarin robots op de kaart en in 3D staan; een nieuwe robot krijgt de eerste die nog vrij is.
PALETTE = ("#4fb3ff", "#ffb23e", "#c873ff", "#40e6d9", "#ff7a59", "#f2e24a", "#ff6fb1", "#9be37a")


def slug(text: str) -> str:
    """Id voor in een URL: kleine letters, cijfers en streepjes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "robot"


def clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("robot-adres moet met http:// beginnen, bv. http://192.168.4.1:8000")
    return url


@dataclass
class RobotConfig:
    """Eén robot in fleet.json."""
    id: str
    name: str
    url: str
    kind: str = "openagbot"          # sleutel in fleet.adapters.ADAPTERS
    color: str = PALETTE[0]
    enabled: bool = True             # uit = bewaard, maar er wordt niet mee verbonden

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RobotConfig":
        return cls(id=str(d["id"]), name=str(d.get("name") or d["id"]), url=clean_url(d["url"]),
                   kind=str(d.get("kind") or "openagbot"), color=str(d.get("color") or PALETTE[0]),
                   enabled=bool(d.get("enabled", True)))


@dataclass
class Telemetry:
    """Wat een robot op één moment meldt, los van zijn soort. None = meldt hij niet."""
    lat: float | None = None
    lon: float | None = None
    heading: float | None = None             # graden, 0 = noord, met de klok mee
    heading_valid: bool = True
    speed_kmh: float = 0.0
    fix: int = 0                             # GGA-kwaliteit: 4 = RTK fixed, 5 = float, 0 = geen
    fix_label: str = ""
    sats: int | None = None
    correction_age_s: float | None = None
    wheel_left_deg: float | None = None
    wheel_right_deg: float | None = None
    battery_v: float | None = None
    battery_soc: float | None = None         # 0..1
    mode: str = "idle"                       # idle | manual | waypoints | ab | mission
    state: str = ""                          # toestand van de actieve navigator
    message: str = ""
    mission: dict | None = None              # {name, percent, segment_label, remaining_min, xte_m}
    fault: str | None = None                 # aandrijving of sturing meldt een fout
    geofence: str | None = None              # reden waarom de bewaking een rijrichting dicht houdt
    extra: dict = field(default_factory=dict)

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None and bool(self.fix) and (self.lat, self.lon) != (0.0, 0.0)
