"""Nagebootste robot om de vloot te proberen zonder hardware: geeft de antwoorden van de OpenAgbot-API en rijdt
een rondje rond een punt.

  python tools/demo_robot.py                                  rondje bij het erf van de voorbeeldwereld, poort 8101
  python tools/demo_robot.py --port 8102 --lat 52.0008 --lon 4.0012 --radius 25 --speed 4

Voeg daarna in de sim (tab Vloot) http://127.0.0.1:8101 toe. Stop en missie starten/stoppen werken: de robot
staat dan stil of rijdt verder. Alleen de standaardbibliotheek, dus hij draait ook los van de sim.
"""
from __future__ import annotations

import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html><meta charset="utf-8"><title>Demo-robot</title>
<body style="font:16px sans-serif;margin:40px;color:#223">
<h1>Demo-robot {name}</h1><p>Hier staat bij een echte robot zijn eigen webinterface.</p></body>"""


class DemoRobot:
    def __init__(self, name: str, lat: float, lon: float, radius: float, speed_kmh: float):
        self.name, self.lat0, self.lon0, self.radius, self.cruise = name, lat, lon, radius, speed_kmh
        self.angle = 0.0                # plaats op de cirkel (rad, 0 = noord, met de klok mee)
        self.driving = True
        self.speed = 0.0                # km/h, loopt op en af
        self.t = time.monotonic()
        self.lock = threading.Lock()

    def step(self) -> None:
        with self.lock:
            now = time.monotonic()
            dt, self.t = now - self.t, now
            want = self.cruise if self.driving else 0.0
            self.speed += max(-2.0 * dt, min(1.5 * dt, want - self.speed))
            self.angle = (self.angle + (self.speed / 3.6) * dt / self.radius) % (2 * math.pi)

    def status(self) -> dict:
        self.step()
        with self.lock:
            north, east = self.radius * math.cos(self.angle), self.radius * math.sin(self.angle)
            lat = self.lat0 + north / 111_320.0
            lon = self.lon0 + east / (111_320.0 * math.cos(math.radians(self.lat0)))
            heading = (math.degrees(self.angle) + 90.0) % 360.0
            moving = self.speed > 0.05
            return {"lat": lat, "lon": lon, "fix": 4, "fix_soort": "RTK fixed", "hdop": 0.6, "sats": 27,
                    "heading": heading, "heading_geldig": True, "speed_kmh": round(self.speed, 2),
                    "correctie_leeftijd_s": 1.0, "navigator_active": False, "ab_active": False,
                    "missie_actief": self.driving, "missie_state": "RIJDEN" if moving else "WACHT",
                    "missie_message": "demo: rondje" if self.driving else "", "drive_fault": None,
                    "geofence": {"vooruit": True, "achteruit": True, "reden": ""}, "accu_v": 54.6}

    def mission(self) -> dict:
        with self.lock:
            pct = 100.0 * self.angle / (2 * math.pi)
            left = (2 * math.pi - self.angle) * self.radius / max(0.3, self.cruise / 3.6) / 60.0
            return {"naam": "demo_rondje", "actief": self.driving, "procent": round(pct, 1),
                    "segment_label": "rondje", "resterend_min": round(left, 1), "afwijking_m": 0.01}

    def steer(self) -> float:
        return math.degrees(math.atan(1.2 / self.radius))       # wielbasis 1,2 m


def serve(robot: DemoRobot, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, data, code=200):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/status":
                self._json(robot.status())
            elif path == "/api/info":
                self._json({"naam": f"Demo-robot {robot.name}", "api": 2, "simulatie": True,
                            "config": {"vehicle": {"wheelbase_m": 1.2, "track_width_m": 1.0}}})
            elif path == "/api/steering/status":
                a = robot.steer()
                self._json({"status": "success", "steering": {"position": {"left": a, "right": a * 0.8}}})
            elif path == "/api/motors":
                self._json({"battery": {"voltage_v": 54.6, "current_a": 6.0, "soc_pct": 72}})
            elif path == "/api/missies":
                self._json({"missies": [{"naam": "demo_rondje"}], "voortgang": {"demo_rondje": {"s_m": 1.0}}})
            elif path == "/api/missie/live":
                self._json({"missie": robot.mission()})
            elif path == "/":
                body = PAGE.format(name=robot.name).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"detail": "niet in de demo-robot"}, 404)

        def do_POST(self):
            self._read()
            if self.path == "/api/missie/start":
                robot.driving = True
                self._json({"status": "started"})
            elif self.path in ("/api/stop", "/api/missie/stop", "/api/stop_nav"):
                robot.driving = False
                self._json({"status": "stopped"})
            else:
                self._json({"detail": "niet in de demo-robot"}, 404)

        def do_PUT(self):
            self._read()
            self._json({"status": "ok", "kaart": {"demo": True}, "missie": {"demo": True}, "total": 0})

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name=f"demo-robot-{port}").start()
    return server


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Nagebootste robot voor de vloot van OpenAgbot FieldSim")
    ap.add_argument("--port", type=int, default=8101)
    ap.add_argument("--name", default="1")
    ap.add_argument("--lat", type=float, default=52.0003, help="midden van het rondje (standaard: voorbeeldwereld)")
    ap.add_argument("--lon", type=float, default=4.0003)
    ap.add_argument("--radius", type=float, default=18.0, help="straal van het rondje in meters")
    ap.add_argument("--speed", type=float, default=3.0, help="km/h")
    args = ap.parse_args(argv)
    serve(DemoRobot(args.name, args.lat, args.lon, args.radius, args.speed), args.port)
    print(f"Demo-robot {args.name} rijdt op http://127.0.0.1:{args.port} (Ctrl+C stopt)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
