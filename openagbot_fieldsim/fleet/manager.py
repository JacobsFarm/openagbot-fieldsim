"""FleetManager: de robots van de vloot, hun verbindingen en hun stand in de geladen wereld.

Per robot draait één thread (`RobotLink`) die zijn telemetrie blijft ophalen. Valt een robot weg, dan blijft zijn
laatste stand staan als 'offline' en probeert de verbinding het rustig opnieuw; de andere robots merken daar
niets van. De lijst staat in `fleet.json` naast settings.json (bevat adressen: niet in git).
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import asdict

from openagbot_fieldsim.fleet import adapters
from openagbot_fieldsim.fleet.adapters import RobotAdapter, RobotError
from openagbot_fieldsim.fleet.model import PALETTE, RobotConfig, Telemetry, clean_url, slug

log = logging.getLogger(__name__)

POLL_S = 0.2              # telemetrie ophalen zolang de robot antwoordt (5 Hz)
RETRY_S = 2.0             # opnieuw proberen zolang hij weg is
OFFLINE_AFTER_S = 2.5     # zo lang geen antwoord = offline
TRAIL_STEP_M = 0.25       # afstand tussen twee punten van het spoor
TRAIL_MAX = 20000         # ca. 5 km; daarna vallen de oudste punten af


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Afstand tussen twee (lat, lon) over korte afstand."""
    dy = (b[0] - a[0]) * 111_320.0
    dx = (b[1] - a[1]) * 111_320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


class RobotLink:
    """De verbinding met één robot: blijft meten en houdt zijn laatste stand en spoor bij."""

    def __init__(self, config: RobotConfig, on_event=None, adapter: RobotAdapter | None = None):
        self.config = config
        self.adapter = adapter or adapters.make(config.kind, config.url)
        self.on_event = on_event or (lambda kind, text, robot_id: None)
        self.telemetry: Telemetry | None = None
        self.info: dict | None = None
        self.online = False
        self.error: str | None = None
        self.last_seen: float | None = None          # time.time() van het laatste antwoord
        self.latency_ms: float | None = None
        self.trail: list[tuple[float, float]] = []   # (lat, lon): geldig in elke wereld
        self.trail_dropped = 0                       # punten die er aan de voorkant al af zijn
        self.trail_gen = 0                           # hoger = spoor gewist, de UI begint opnieuw
        self._last_ok = 0.0                          # monotonic
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ levensloop
    def start(self) -> None:
        if self._thread is None and self.config.enabled:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name=f"fleet-{self.config.id}")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=4.0)
        with self._lock:
            self.online = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(POLL_S if self.online else RETRY_S)

    def poll_once(self) -> None:
        """Eén meting (ook los te roepen in tests)."""
        t0 = time.perf_counter()
        try:
            tel = self.adapter.telemetry()
        except RobotError as e:
            with self._lock:
                self.error = str(e)
                lost = self.online and time.monotonic() - self._last_ok > OFFLINE_AFTER_S
                if lost:
                    self.online = False
            if lost:
                self.on_event("fout", f"{self.config.name}: verbinding weg", self.config.id)
            return
        except Exception as e:      # noqa: BLE001 - een fout in een adapter mag de thread niet stoppen
            log.exception("telemetrie van %s", self.config.name)
            with self._lock:
                self.error = f"adapterfout: {e}"
                self.online = False
            return
        now = time.monotonic()
        with self._lock:
            came_online = not self.online
            new_fault = tel.fault and tel.fault != (self.telemetry.fault if self.telemetry else None)
            self.telemetry = tel
            self.online = True
            self.error = None
            self.last_seen = time.time()
            self.latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            self._last_ok = now
            if tel.has_position:
                p = (tel.lat, tel.lon)
                if not self.trail or _dist_m(self.trail[-1], p) >= TRAIL_STEP_M:
                    self.trail.append(p)
                    if len(self.trail) > TRAIL_MAX:
                        cut = TRAIL_MAX // 10
                        del self.trail[:cut]
                        self.trail_dropped += cut
        if came_online:
            self._fetch_info()
            self.on_event("vloot", f"{self.config.name}: verbonden", self.config.id)
        if new_fault:
            self.on_event("fout", f"{self.config.name}: {tel.fault}", self.config.id)

    def _fetch_info(self) -> None:
        try:
            self.info = self.adapter.info()
        except RobotError:
            self.info = {}          # oude software zonder /api/info: volgen werkt ook zonder

    def clear_trail(self) -> None:
        with self._lock:
            self.trail = []
            self.trail_dropped = 0
            self.trail_gen += 1

    # ------------------------------------------------------------------ uitlezen
    def snapshot(self, frame=None, terrain=None, cursor: tuple[int, int] | None = None) -> dict:
        """Stand voor de UI. `frame`/`terrain` van de geladen wereld zetten lat/lon om naar lokale meters;
        `cursor` = (trail_gen, total) van wat deze kijker al heeft, zodat alleen het nieuwe spoor meegaat."""
        with self._lock:
            tel, c = self.telemetry, self.config
            out = {
                **c.to_dict(), "label": self.adapter.label, "capabilities": list(self.adapter.capabilities),
                "online": self.online, "seen": self.last_seen is not None, "error": self.error,
                "age_s": None if self.last_seen is None else round(time.time() - self.last_seen, 2),
                "latency_ms": self.latency_ms, "info": self.info, "pose": None,
                "telemetry": None if tel is None else {k: v for k, v in asdict(tel).items() if k != "extra"},
            }
            total = self.trail_dropped + len(self.trail)
            start = 0 if cursor is None or cursor[0] != self.trail_gen else min(cursor[1], total)
            new = self.trail[max(0, start - self.trail_dropped):]
            start = max(start, self.trail_dropped)
            gen = self.trail_gen
        if frame is not None:
            if tel is not None and tel.has_position:
                x, y = frame.to_xy(tel.lat, tel.lon)
                out["pose"] = {"x": round(x, 3), "y": round(y, 3), "heading": tel.heading or 0.0,
                               "in_world": _in_terrain(terrain, x, y)}
            pts = [[round(v, 2) for v in frame.to_xy(lat, lon)] for lat, lon in new]
            out["trail"] = {"gen": gen, "from": start, "points": pts, "total": total}
        return out


def _in_terrain(terrain, x: float, y: float) -> bool:
    if terrain is None:
        return True
    return (terrain.x0 <= x <= terrain.x0 + (terrain.nx - 1) * terrain.cell
            and terrain.y0 <= y <= terrain.y0 + (terrain.ny - 1) * terrain.cell)


class FleetManager:
    def __init__(self, path: str, autostart: bool = True):
        self.path = path
        self.autostart = autostart
        self.events: deque[dict] = deque(maxlen=50)
        self._links: dict[str, RobotLink] = {}
        self._lock = threading.RLock()
        self._event_seq = 0
        self._load()

    # ------------------------------------------------------------------ bewaren
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            log.exception("fleet.json lezen")
            return
        for d in data.get("robots", []):
            try:
                self._attach(RobotConfig.from_dict(d))
            except (KeyError, ValueError) as e:
                log.warning("robot in fleet.json overgeslagen: %s", e)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self._lock:
            data = {"robots": [link.config.to_dict() for link in self._links.values()]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _attach(self, config: RobotConfig) -> RobotLink:
        link = RobotLink(config, self._event)
        self._links[config.id] = link
        if self.autostart:
            link.start()
        return link

    def _event(self, kind: str, text: str, robot_id: str | None = None) -> None:
        with self._lock:
            self._event_seq += 1
            self.events.append({"seq": self._event_seq, "t": time.time(), "kind": kind, "text": text,
                                "robot": robot_id})

    # ------------------------------------------------------------------ beheren
    def get(self, robot_id: str) -> RobotLink:
        try:
            return self._links[robot_id]
        except KeyError:
            raise KeyError(f"robot '{robot_id}' staat niet in de vloot") from None

    def links(self) -> list[RobotLink]:
        with self._lock:
            return list(self._links.values())

    def add(self, name: str, url: str, kind: str = "openagbot", color: str | None = None,
            enabled: bool = True) -> RobotLink:
        url = clean_url(url)
        name = (name or "").strip() or "Robot"
        adapters.make(kind, url)                     # onbekende soort: nu melden, niet in de thread
        with self._lock:
            if any(l.config.url == url for l in self._links.values()):
                raise ValueError(f"{url} staat al in de vloot")
            base = slug(name)
            robot_id, n = base, 2
            while robot_id in self._links:
                robot_id, n = f"{base}-{n}", n + 1
            used = {l.config.color for l in self._links.values()}
            color = color or next((c for c in PALETTE if c not in used), PALETTE[len(self._links) % len(PALETTE)])
            link = self._attach(RobotConfig(robot_id, name, url, kind, color, enabled))
        self.save()
        return link

    def update(self, robot_id: str, **changes) -> RobotLink:
        """Naam, kleur, adres, soort of aan/uit wijzigen. Een ander adres of soort begint een nieuwe verbinding."""
        with self._lock:
            old = self.get(robot_id)
            c = RobotConfig(**old.config.to_dict())
            for key in ("name", "color", "url", "kind", "enabled"):
                if changes.get(key) is not None:
                    setattr(c, key, changes[key])
            c.url = clean_url(c.url)
            c.name = c.name.strip() or old.config.name
            if any(l.config.url == c.url for l in self._links.values() if l is not old):
                raise ValueError(f"{c.url} staat al in de vloot")
            reconnect = (c.url, c.kind, c.enabled) != (old.config.url, old.config.kind, old.config.enabled)
            if not reconnect:
                old.config = c
                link = old
            else:
                adapters.make(c.kind, c.url)
                link = RobotLink(c, self._event)
                link.trail, link.trail_dropped, link.trail_gen = old.trail, old.trail_dropped, old.trail_gen
                self._links[robot_id] = link
        if reconnect:
            old.stop()
            if self.autostart:
                link.start()
        self.save()
        return link

    def remove(self, robot_id: str) -> None:
        with self._lock:
            link = self.get(robot_id)
            del self._links[robot_id]
        link.stop()
        self.save()

    def shutdown(self) -> None:
        for link in self.links():
            link._stop.set()
        for link in self.links():
            link.stop()

    # ------------------------------------------------------------------ commando's
    def stop_all(self) -> dict:
        """Alle bereikbare robots tegelijk laten stoppen (elk in zijn eigen thread: één trage robot houdt de
        andere niet op). Geeft per robot 'stopped' of de foutmelding."""
        results: dict[str, str] = {}

        def run(link: RobotLink):
            try:
                link.adapter.stop()
                results[link.config.id] = "stopped"
            except Exception as e:      # noqa: BLE001
                results[link.config.id] = f"mislukt: {e}"

        threads = [threading.Thread(target=run, args=(l,), daemon=True) for l in self.links() if l.config.enabled]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        self._event("vloot", "stop naar alle robots gestuurd")
        return results

    # ------------------------------------------------------------------ uitlezen
    def snapshot(self, frame=None, terrain=None, cursors: dict | None = None) -> dict:
        """Hele vloot voor de UI. `cursors` ({id: (gen, total)}) wordt bijgewerkt voor de volgende keer."""
        robots = []
        for link in self.links():
            s = link.snapshot(frame, terrain, None if cursors is None else cursors.get(link.config.id))
            if cursors is not None and "trail" in s:
                cursors[link.config.id] = (s["trail"]["gen"], s["trail"]["total"])
            robots.append(s)
        with self._lock:
            events = list(self.events)[-10:]
        return {"robots": robots, "events": events, "kinds": [{"kind": k, "label": a.label}
                                                              for k, a in sorted(adapters.ADAPTERS.items())]}
