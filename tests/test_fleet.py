"""Vloot: adapters, verbindingen, bewaren en de API, tegen een nagebootste robot (een kleine HTTP-server die
de antwoorden van de OpenAgbot-API geeft). Heeft OpenAgbot zelf niet nodig."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from openagbot_fieldsim.fleet import FleetManager, RobotAdapter, RobotError, Telemetry, adapters, manager, register
from openagbot_fieldsim.fleet.adapters import OpenAgbotAdapter
from openagbot_fieldsim.fleet.manager import RobotLink
from openagbot_fieldsim.fleet.model import RobotConfig
from openagbot_fieldsim.geo import LocalFrame

LAT0, LON0 = 52.5, 5.5


class FakeRobot:
    """OpenAgbot nagebootst: `state` is wat /api/status meldt, `posts` wat hij binnenkreeg."""

    def __init__(self):
        self.state = {"lat": LAT0, "lon": LON0, "fix": 4, "fix_soort": "RTK fixed", "heading": 90.0,
                      "heading_geldig": True, "speed_kmh": 0.0, "sats": 24, "accu_v": 55.1,
                      "navigator_active": False, "ab_active": False, "missie_actief": False,
                      "geofence": {"vooruit": True, "achteruit": True, "reden": ""}, "drive_fault": None}
        self.posts: list[tuple[str, dict]] = []
        self.puts: list[str] = []
        robot = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/status":
                    self._send(robot.state)
                elif path == "/api/info":
                    self._send({"naam": "Bruut OpenAgbot", "api": 2, "simulatie": False,
                                "config": {"vehicle": {"wheelbase_m": 1.2, "track_width_m": 1.0}}})
                elif path == "/api/steering/status":
                    self._send({"status": "success", "steering": {"position": {"left": 4.0, "right": 3.5}}})
                elif path == "/api/motors":
                    self._send({"battery": {"voltage_v": 54.8, "soc_pct": 80}})
                elif path == "/api/missies":
                    self._send({"missies": [{"naam": "achterkamp", "lengte_m": 812.0}],
                                "voortgang": {"achterkamp": {"s_m": 100.0}}})
                elif path == "/api/missie/live":
                    self._send({"missie": {"naam": "achterkamp", "procent": 41.5, "segment_label": "baan 3",
                                           "resterend_min": 12.0, "afwijking_m": 0.02}})
                else:
                    self._send({"detail": "not found"}, 404)

            def do_POST(self):
                body = self._body()
                robot.posts.append((self.path, body))
                if self.path == "/api/missie/start":
                    self._send({"status": "started"} if body["naam"] == "achterkamp"
                               else {"status": "error", "msg": "missie bestaat niet"})
                else:
                    self._send({"status": "stopped"})

            def do_PUT(self):
                self._body()
                robot.puts.append(self.path)
                self._send({"status": "ok", "kaart": {"zones": 3}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def robot():
    r = FakeRobot()
    yield r
    r.close()


@pytest.fixture
def fleet(tmp_path):
    f = FleetManager(str(tmp_path / "fleet.json"), autostart=False)
    yield f
    f.shutdown()


# ---------------------------------------------------------------------- adapter
def test_adapter_telemetry(robot):
    a = OpenAgbotAdapter(robot.url)
    t = a.telemetry()
    assert (t.lat, t.lon, t.fix, t.heading) == (LAT0, LON0, 4, 90.0)
    assert t.has_position and t.mode == "idle"
    assert (t.wheel_left_deg, t.wheel_right_deg) == (4.0, 3.5)
    assert t.battery_v == 54.8 and t.battery_soc == pytest.approx(0.8)
    assert a.info()["robot"]["wheelbase_m"] == 1.2


def test_adapter_modes(robot):
    a = OpenAgbotAdapter(robot.url)
    robot.state.update(missie_actief=True, missie_state="RIJDEN", missie_message="baan 3")
    t = a.telemetry()
    assert (t.mode, t.state, t.message) == ("mission", "RIJDEN", "baan 3")
    assert t.mission["percent"] == 41.5
    robot.state.update(missie_actief=False, ab_active=True, ab_state="TRACKING")
    assert a.telemetry().mode == "ab"
    robot.state.update(ab_active=False, speed_kmh=2.0)
    assert a.telemetry().mode == "manual"
    robot.state.update(geofence={"vooruit": False, "achteruit": True, "reden": "sloot"}, drive_fault="stall links")
    t = a.telemetry()
    assert t.geofence == "sloot" and t.fault == "stall links"


def test_adapter_no_fix_has_no_position(robot):
    robot.state.update(fix=0, lat=0.0, lon=0.0)
    assert not OpenAgbotAdapter(robot.url).telemetry().has_position


def test_adapter_commands(robot):
    a = OpenAgbotAdapter(robot.url)
    a.stop()
    assert robot.posts[-1][0] == "/api/stop"
    assert a.missions()[0]["name"] == "achterkamp"
    a.mission_start("achterkamp", resume=True, max_kmh=2.5)
    assert robot.posts[-1] == ("/api/missie/start", {"naam": "achterkamp", "hervat": True, "max_kmh": 2.5})
    with pytest.raises(RobotError, match="bestaat niet"):
        a.mission_start("onbekend")
    a.send({"type": "openagbot.kaart"})
    assert robot.puts == ["/api/kaart"]


def test_adapter_unreachable():
    with pytest.raises(RobotError):
        OpenAgbotAdapter("http://127.0.0.1:9", timeout=0.3).telemetry()


# ---------------------------------------------------------------------- verbinding
def test_link_online_trail_and_offline(robot, monkeypatch):
    events = []
    link = RobotLink(RobotConfig("r1", "Bruut 1", robot.url), lambda k, t, r: events.append((k, t)))
    link.poll_once()
    assert link.online and link.info["api"] == 2
    assert events == [("vloot", "Bruut 1: verbonden")]
    for i in range(1, 6):                       # 5 x 1 m naar het noorden
        robot.state["lat"] = LAT0 + i / 111_320.0
        link.poll_once()
    assert len(link.trail) == 6
    link.poll_once()                            # stilstaan: geen nieuw punt
    assert len(link.trail) == 6

    frame = LocalFrame(LAT0, LON0)
    s = link.snapshot(frame)
    assert s["pose"]["y"] == pytest.approx(5.0, abs=0.02) and s["pose"]["heading"] == 90.0
    assert s["trail"]["total"] == 6 and len(s["trail"]["points"]) == 6
    s = link.snapshot(frame, cursor=(s["trail"]["gen"], 4))      # deze kijker heeft er al 4
    assert s["trail"]["from"] == 4 and len(s["trail"]["points"]) == 2
    link.clear_trail()
    s = link.snapshot(frame, cursor=(0, 6))
    assert s["trail"] == {"gen": 1, "from": 0, "points": [], "total": 0}

    robot.close()
    monkeypatch.setattr(manager, "OFFLINE_AFTER_S", 0.0)
    link.adapter.timeout = 0.3
    link.poll_once()
    assert not link.online and link.error
    assert events[-1] == ("fout", "Bruut 1: verbinding weg")
    assert link.snapshot(frame)["pose"] is not None           # laatste stand blijft staan


def test_link_fault_event(robot):
    events = []
    link = RobotLink(RobotConfig("r1", "Bruut 1", robot.url), lambda k, t, r: events.append(t))
    link.poll_once()
    robot.state["drive_fault"] = "stall links"
    link.poll_once()
    link.poll_once()                            # zelfde fout: geen tweede melding
    assert events.count("Bruut 1: stall links") == 1


def test_link_thread_polls(robot, monkeypatch):
    monkeypatch.setattr(manager, "POLL_S", 0.02)
    link = RobotLink(RobotConfig("r1", "Bruut 1", robot.url))
    link.start()
    try:
        for _ in range(100):
            if link.online:
                break
            threading.Event().wait(0.02)
        assert link.online
    finally:
        link.stop()
    assert not link.online


# ---------------------------------------------------------------------- manager
def test_manager_add_persist_reload(fleet, tmp_path):
    a = fleet.add("Bruut 1", "http://192.168.4.1:8000/")
    b = fleet.add("Bruut 1", "http://192.168.4.2:8000")
    assert (a.config.id, b.config.id) == ("bruut-1", "bruut-1-2")
    assert a.config.url == "http://192.168.4.1:8000" and a.config.color != b.config.color
    with pytest.raises(ValueError, match="al in de vloot"):
        fleet.add("Dubbel", "http://192.168.4.1:8000")
    with pytest.raises(ValueError, match="http://"):
        fleet.add("Fout", "192.168.4.3:8000")
    with pytest.raises(ValueError, match="onbekende soort"):
        fleet.add("Fout", "http://192.168.4.3:8000", kind="tractor")

    again = FleetManager(str(tmp_path / "fleet.json"), autostart=False)
    assert [l.config.to_dict() for l in again.links()] == [a.config.to_dict(), b.config.to_dict()]

    fleet.update("bruut-1", name="Voorste", color="#ffffff")
    assert fleet.get("bruut-1").config.name == "Voorste"
    with pytest.raises(ValueError, match="al in de vloot"):
        fleet.update("bruut-1", url="http://192.168.4.2:8000")
    fleet.remove("bruut-1-2")
    assert [l.config.id for l in FleetManager(str(tmp_path / "fleet.json"), autostart=False).links()] == ["bruut-1"]
    with pytest.raises(KeyError):
        fleet.get("bruut-1-2")


def test_manager_update_url_keeps_trail(fleet, robot):
    link = fleet.add("Bruut 1", "http://192.168.4.1:8000")
    link.trail = [(LAT0, LON0)]
    new = fleet.update("bruut-1", url=robot.url)
    assert new is not link and new.trail == [(LAT0, LON0)]
    new.poll_once()
    assert new.online


def test_manager_snapshot_and_stop_all(fleet, robot):
    fleet.add("Bruut 1", robot.url).poll_once()
    fleet.add("Weg", "http://127.0.0.1:9").adapter.timeout = 0.3
    terrain = SimpleNamespace(x0=-10.0, y0=-10.0, cell=1.0, nx=21, ny=21)
    cursors = {}
    s = fleet.snapshot(LocalFrame(LAT0, LON0), terrain, cursors)
    r1, r2 = s["robots"]
    assert r1["online"] and r1["pose"]["in_world"] and r1["telemetry"]["fix"] == 4
    assert not r2["online"] and r2["pose"] is None and not r2["seen"]
    assert cursors["bruut-1"] == (0, 1)
    assert {"kind": "openagbot", "label": "Bruut OpenAgbot"} in s["kinds"]
    far = fleet.snapshot(LocalFrame(LAT0 + 1.0, LON0), terrain)["robots"][0]
    assert far["pose"]["in_world"] is False                  # robot staat buiten de geladen wereld

    result = fleet.stop_all()
    assert result["bruut-1"] == "stopped" and result["weg"].startswith("mislukt")
    assert ("/api/stop", {}) in robot.posts


def test_register_other_robot_kind(fleet):
    class Tractor(RobotAdapter):
        kind, label, capabilities = "tractor", "Testtractor", ("stop",)

        def telemetry(self):
            return Telemetry(lat=LAT0, lon=LON0, fix=4, heading=10.0)

        def info(self):
            return {"name": "T"}

    register("tractor", Tractor)
    try:
        link = fleet.add("Trekker", "http://10.0.0.5", kind="tractor")
        link.poll_once()
        s = link.snapshot(LocalFrame(LAT0, LON0))
        assert s["online"] and s["label"] == "Testtractor" and s["capabilities"] == ["stop"]
        with pytest.raises(RobotError):
            link.adapter.missions()
    finally:
        del adapters.ADAPTERS["tractor"]


# ---------------------------------------------------------------------- API
@pytest.fixture
def client(fleet):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openagbot_fieldsim.api import fleet_api
    app = FastAPI()
    sim = SimpleNamespace(world=SimpleNamespace(frame=LocalFrame(LAT0, LON0)),
                          terrain=SimpleNamespace(x0=-50.0, y0=-50.0, cell=1.0, nx=101, ny=101))
    fleet_api.register(app, SimpleNamespace(fleet=fleet, sim=sim, kaart=lambda: {"type": "openagbot.kaart"}))
    return TestClient(app)


def test_api(client, fleet, robot):
    assert client.post("/api/fleet/probe", json={"url": robot.url}).json()["pose"]["in_world"] is True
    assert client.post("/api/fleet/probe", json={"url": "http://127.0.0.1:9"}).status_code == 502
    assert client.post("/api/fleet", json={"name": "Bruut 1", "url": "geen-url"}).status_code == 400
    r = client.post("/api/fleet", json={"name": "Bruut 1", "url": robot.url}).json()
    assert r["robot"]["id"] == "bruut-1"
    fleet.get("bruut-1").poll_once()

    d = client.get("/api/fleet").json()
    assert d["robots"][0]["online"] and "trail" not in d["robots"][0]
    assert client.put("/api/fleet/bruut-1", json={"name": "Voorste"}).json()["robot"]["name"] == "Voorste"
    assert client.put("/api/fleet/onbekend", json={"name": "x"}).status_code == 404

    assert client.post("/api/fleet/bruut-1/stop").status_code == 200
    assert client.post("/api/fleet/stop").json()["robots"] == {"bruut-1": "stopped"}
    assert client.get("/api/fleet/bruut-1/missions").json()["missions"][0]["name"] == "achterkamp"
    # Starten laat de echte robot rijden: zonder bevestiging gebeurt er niets
    assert client.post("/api/fleet/bruut-1/mission/start", json={"name": "achterkamp"}).status_code == 400
    assert not any(p == "/api/missie/start" for p, _ in robot.posts)
    assert client.post("/api/fleet/bruut-1/mission/start",
                       json={"name": "achterkamp", "confirm": True}).json()["status"] == "started"
    assert client.post("/api/fleet/bruut-1/mission/start",
                       json={"name": "onbekend", "confirm": True}).status_code == 502
    assert client.post("/api/fleet/bruut-1/mission/stop").status_code == 200
    assert client.post("/api/fleet/bruut-1/map").json()["sent"]["kaart"] == {"zones": 3}
    assert client.post("/api/fleet/bruut-1/trail/clear").status_code == 200

    assert client.delete("/api/fleet/bruut-1").status_code == 200
    assert client.get("/api/fleet").json()["robots"] == []
