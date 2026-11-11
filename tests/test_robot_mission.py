"""Kaart en missie naar de robot: de geofence van OpenAgbot zelf (data/kaart.json) en zijn missievolger.

  - zonder backend: de kaart die de sim exporteert, en de poort van de GeofenceBewaker met een nagemaakte GNSS;
  - met de echte backend in de sim (pytest -m integration): handmatig naar de sloot rijden stopt de robot zelf,
    achteruit kan hij er weer uit, en een veldmissie rijdt met de missievolger, stopt en hervat.
"""
import math
import os
import socket
import sys
import time

import pytest

from conftest import AGBOT_DIR, ROOT, load_agbot_module

sys.path.insert(0, os.path.join(ROOT, "tools"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def test_world(tmp_path_factory):
    """Veld van 40 x 30 m met een sloot langs de oostkant (water x 40,5-42,5; slootzone vanaf x 38,5)."""
    from drive_field_mission import make_test_world
    from openagbot_fieldsim.world.model import load_world
    root = str(tmp_path_factory.mktemp("worlds"))
    name = make_test_world(root, 40.0, 30.0)
    return root, name, load_world(os.path.join(root, name))


class FakeGps:
    """Positie in lokale meters van de testwereld, als de GNSS van OpenAgbot."""

    def __init__(self, world):
        self.world = world
        self.x, self.y, self.heading, self.kmh, self.fix = 0.0, 0.0, 0.0, 0.0, 4

    def get_current_position(self):
        lat, lon = self.world.frame.to_latlon(self.x, self.y)
        return {"lat": lat, "lon": lon, "fix": self.fix, "heading": self.heading, "heading_geldig": True,
                "speed_kmh": self.kmh, "positie_leeftijd_s": 0.02}

    def wacht_op_epoch(self, vorige, timeout=0.1):
        time.sleep(timeout)
        return vorige


def test_kaart_export(test_world):
    from openagbot_fieldsim.missions.safety import kaart_export
    _, _, world = test_world
    kaart = kaart_export(world)
    assert kaart["format"] == "openagbot.kaart" and kaart["slootzone_m"] == 2.0
    sloot = [z for z in kaart["verboden"] if z["soort"] == "water"]
    assert len(sloot) == 1 and sloot[0]["actie"] == "stoppen"
    assert any(z["soort"] == "hek" and z["actie"] == "mijden" for z in kaart["verboden"])
    assert kaart["velden"][0]["id"] == "testveld" and len(kaart["werkgebied"]) >= 4
    # de slootzone (2 m) zit in de veelhoek: de westrand ligt op x = 38,5
    xs = [world.frame.to_xy(lat, lon)[0] for lat, lon in sloot[0]["polygon"]]
    assert min(xs) == pytest.approx(38.5, abs=0.05) and max(xs) == pytest.approx(44.5, abs=0.05)


def test_geofence_gate(test_world, agbot_config, tmp_path, monkeypatch):
    """De poort: vrij in het veld, vooruit dicht voor de sloot (achteruit open), en open in de overbrugging."""
    from openagbot_fieldsim.missions.safety import kaart_export
    _, _, world = test_world
    gf = load_agbot_module(os.path.join("app", "services", "geofence.py"), "agbot_geofence")
    monkeypatch.chdir(tmp_path)                      # data/kaart.json van deze test, niet die van de robot
    gps = FakeGps(world)
    bewaker = gf.GeofenceBewaker(gps, agbot_config)
    bewaker.running = False                          # de test rekent zelf, zonder de thread
    assert bewaker.poort() == gf.OPEN and bewaker.kaart is None

    with pytest.raises(ValueError):
        bewaker.bewaar_kaart({"format": "iets anders"})
    info = bewaker.bewaar_kaart(kaart_export(world))
    assert info["stop_zones"] == 1 and os.path.exists(os.path.join("data", "kaart.json"))

    def poort(x, y, heading, kmh=0.0, fix=4):
        gps.x, gps.y, gps.heading, gps.kmh, gps.fix = x, y, heading, kmh, fix
        bewaker._stap()
        return bewaker.poort()

    assert poort(20, 15, 90) == gf.OPEN              # midden in het veld
    # Neus (1,45 m voor het regelpunt) op 0,2 m van de slootzone: vooruit dicht, achteruit open
    p = poort(36.85, 15, 90)
    assert not p.vooruit and p.achteruit and "sloot" in p.reden
    assert bewaker.toestand == "dicht" and bewaker.stops == 1
    assert poort(36.85, 15, 270) == gf.OPEN          # zelfde plek, neus van de sloot af: niets aan de hand
    p = poort(38.0, 15, 270)                         # achterkant (0,25 m achter het regelpunt) vlak voor de zone
    assert p.vooruit and not p.achteruit
    # Harder rijden = verder vooruit kijken (remweg)
    assert poort(36.0, 15, 90, kmh=0.0).vooruit and not poort(36.0, 15, 90, kmh=5.0).vooruit
    # Zonder RTK fixed 1 m extra afstand
    assert poort(35.8, 15, 90).vooruit and not poort(35.8, 15, 90, fix=5).vooruit
    # Langs de sloot rijden (evenwijdig, robot 0,65 m breed vanaf het midden) mag tot vlak voor de zone
    assert poort(37.6, 15, 0) == gf.OPEN
    assert not poort(38.0, 15, 0).vooruit
    # Er helemaal in: beide dicht, tot de overbrugging (langzaam)
    p = poort(41.5, 15, 0)
    assert not p.vooruit and not p.achteruit
    bewaker.overbrug(5)
    p = poort(41.5, 15, 0)
    assert p.vooruit and p.achteruit and p.max_kmh == bewaker.overbrug_kmh
    bewaker.stop_overbrugging()
    assert not poort(41.5, 15, 0).vooruit
    # Buiten het werkgebied van de kaart
    assert not poort(200, 15, 90).vooruit

    # Grenzen van een missie: binnen het veld blijven, pas scherp als de robot er helemaal in staat
    veld = {"toegestaan": [{"polygon": [list(world.frame.to_latlon(x, y)) for x, y in
                                        ((0, 0), (38, 0), (38, 30), (0, 30))]}], "halve_breedte_m": 1.5}
    bewaker.zet_missie(veld)
    assert poort(-3, 15, 90) == gf.OPEN              # aanloop van buiten het veld
    assert poort(10, 15, 90) == gf.OPEN and bewaker._missie_scherp
    assert poort(10, 28.2, 90) == gf.OPEN              # werktuig (1,5 m) blijft 0,3 m binnen de noordrand
    assert not poort(10, 29.0, 90).vooruit           # werktuig (1,5 m) steekt aan de noordkant 0,5 m uit
    bewaker.zet_missie(None)
    assert poort(10, 29.0, 90) == gf.OPEN
    bewaker.wis_kaart()
    assert bewaker.poort() == gf.OPEN


# ---------------------------------------------------------------------------------------------------- met de backend
@pytest.fixture(scope="module")
def sim_app(test_world, tmp_path_factory):
    if not os.path.isdir(AGBOT_DIR):
        pytest.skip("OpenAgbot niet gevonden")
    from openagbot_fieldsim.app import SimApp
    from openagbot_fieldsim.settings import Settings
    root, name, _ = test_world
    s = Settings(home=str(tmp_path_factory.mktemp("home")), agbot_dir=AGBOT_DIR, agbot_port=free_port(),
                 gnss_port=0, steering_port=0, can_port=0, worlds_root=root, ui_port=free_port())
    app = SimApp(s)
    app.load(name)
    end = time.time() + 40
    while time.time() < end:
        try:
            if app.agbot_request("/api/status").get("fix") == 4:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        pytest.fail("OpenAgbot kreeg geen RTK fix: " + "\n".join(app.agbot.status()["log"][-20:]))
    yield app
    app.shutdown()


def drive(app, kmh, seconds):
    r = None
    for _ in range(int(seconds / 0.1)):
        r = app.agbot_request("/api/nav/manual_drive", {"speed_kmh": kmh, "steering_percentage": 0})
        time.sleep(0.1)
    return r


@pytest.mark.integration
def test_robot_stops_itself_for_the_ditch(sim_app):
    """Handmatig recht op de sloot af: de robot stopt zelf voor de slootzone; de sim hoeft niet in te grijpen."""
    sim = sim_app.sim
    gf = sim_app.agbot_request("/api/geofence")
    assert gf["kaart"]["stop_zones"] == 1            # kaart.json stond er al bij het opstarten
    sim.reset_pose(33.0, 15.0, 90.0)
    time.sleep(1.0)
    r = drive(sim_app, 3.0, 8.0)
    assert r["status"] == "blocked" and "sloot" in r["msg"]
    x = sim.vehicle.s.x
    assert abs(sim.vehicle.s.v) < 0.05
    assert 35.8 < x < 37.05                           # neus (x + 1,45) blijft voor de zone op x = 38,5
    assert sim.geofence_alarm is None                 # de bewaking van de sim zelf is niet afgegaan
    st = sim_app.agbot_request("/api/status")["geofence"]
    assert st["toestand"] == "dicht" and not st["vooruit"] and st["achteruit"]
    # Achteruit kan hij er gewoon weer uit, en daarna mag vooruit weer
    assert drive(sim_app, -2.0, 3.0)["status"] == "driving"
    assert sim.vehicle.s.x < x - 0.8
    time.sleep(2.5)
    assert sim_app.agbot_request("/api/status")["geofence"]["toestand"] == "vrij"
    # Overbruggen: dan rijdt hij (langzaam) wel door de grens
    sim.reset_pose(36.9, 15.0, 90.0)
    time.sleep(1.0)
    assert drive(sim_app, 3.0, 1.0)["status"] == "blocked"
    sim_app.agbot_request("/api/geofence/overbruggen", {"seconden": 10})
    drive(sim_app, 3.0, 2.0)
    # ... tot de bewaking van de SIM hem pauzeert zodra de neus de zone raakt (x = 37,05)
    assert sim.vehicle.s.x > 37.0 and abs(sim.vehicle.s.v) <= 1.6 / 3.6
    assert sim.geofence_alarm is not None and sim.geofence_alarm["kind"] == "water"
    sim_app.agbot_request("/api/stop", {})
    sim.paused = False                                # de sim pauzeert zelf zodra hij de zone raakt


@pytest.mark.integration
def test_mission_follower_drives_stops_and_resumes(sim_app):
    from openagbot_fieldsim.agbot import robot_link
    from openagbot_fieldsim.missions.field_plan import plan_field, to_veldmissie, to_waypoints
    sim = sim_app.sim
    sim.paused = False
    plan = plan_field(sim.world, "testveld", {"headland_rounds": 1}, sim.params)
    sent = sim_app.send_to_agbot("test", to_veldmissie(plan, sim.world, "Testveld"), to_waypoints(plan, sim.world))
    assert not sent["old_software"] and sent["mission"]["segmenten"] == len(plan["segments"])
    assert sent["waypoints"] > 10 and sent["mission"]["afgekeurd"] is None
    missies = sim_app.agbot_request("/api/missies")["missies"]
    assert [m["naam"] for m in missies] == ["test"]

    # Te ver van het startpunt: de robot weigert
    sim.reset_pose(20.0, -25.0, 0.0)
    time.sleep(1.0)
    with pytest.raises(RuntimeError, match="startpunt"):
        robot_link.start_mission(sim_app.agbot.url, "test")

    x0, y0, th = plan["segments"][0].pts[0]
    h = (90.0 - math.degrees(th)) % 360.0
    sim.reset_pose(x0 - 2.0 * math.sin(math.radians(h)), y0 - 2.0 * math.cos(math.radians(h)), h)
    time.sleep(1.0)
    robot_link.start_mission(sim_app.agbot.url, "test")
    time.sleep(25.0)
    live = sim_app.agbot_request("/api/missie/live")
    m = live["missie"]
    assert m["actief"] and m["state"] == "RIJDEN" and m["opgepakt"]
    assert m["s_m"] > 10.0 and abs(m["afwijking_m"]) < 0.15
    assert m["werktuig_omlaag"] and live["geofence"]["missie_grenzen"]
    assert len(live["spoor"]["punten"]) > 20
    s_stop = m["s_m"]

    sim_app.agbot_request("/api/missie/stop", {})
    time.sleep(1.5)
    assert abs(sim.vehicle.s.v) < 0.05
    st = sim_app.agbot_request("/api/missies")
    assert st["voortgang"]["test"]["s_m"] >= s_stop - 1.0
    assert not sim_app.agbot_request("/api/geofence")["missie_grenzen"]     # grenzen van de missie zijn weer weg

    r = sim_app.agbot_request("/api/missie/start", {"naam": "test", "hervat": True})
    assert r["status"] == "started"
    time.sleep(10.0)
    m = sim_app.agbot_request("/api/missie/live")["missie"]
    assert m["state"] == "RIJDEN" and m["s_m"] > s_stop + 3.0
    sim_app.agbot_request("/api/stop", {})
