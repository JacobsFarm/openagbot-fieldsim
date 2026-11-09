"""De echte OpenAgbot-backend rijdt in de sim: handmatig rijden en een AB-missie op de fantasiewereld.

Traag (ca. 1 minuut) en heeft OpenAgbot nodig: pytest -m integration
"""
import math
import os
import socket
import time

import pytest

from conftest import AGBOT_DIR, ROOT

pytestmark = pytest.mark.integration


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def sim_app(tmp_path_factory):
    if not os.path.isdir(AGBOT_DIR):
        pytest.skip("OpenAgbot niet gevonden")
    from openagbot_fieldsim.app import SimApp
    from openagbot_fieldsim.settings import Settings
    home = str(tmp_path_factory.mktemp("home"))
    # werelden uit de repo, runtime (data/, logs) in de tijdelijke map
    s = Settings(home=home, agbot_dir=AGBOT_DIR, agbot_port=free_port(), gnss_port=0, steering_port=0, can_port=0,
                 worlds_root=os.path.join(ROOT, "worlds"))
    app = SimApp(s)
    app.load("example")
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


def test_manual_drive_and_deadman(sim_app):
    sim = sim_app.sim
    sim.reset_pose()
    x0, y0 = sim.vehicle.s.x, sim.vehicle.s.y
    for _ in range(30):
        r = sim_app.agbot_request("/api/nav/manual_drive", {"speed_kmh": 3.0, "steering_percentage": 0})
        time.sleep(0.1)
    assert r["status"] == "driving"
    moved = math.hypot(sim.vehicle.s.x - x0, sim.vehicle.s.y - y0)
    assert 1.0 < moved < 3.5
    # Dodemansknop: zonder herhaling stopt hij binnen manual_timeout_s (1,5 s) plus afremmen
    time.sleep(3.0)
    assert abs(sim.vehicle.s.v) < 0.05


def test_ab_mission_tracks_line(sim_app):
    from openagbot_fieldsim.missions.ab import plan_ab, save_ab_line, start_command
    sim = sim_app.sim
    plan = plan_ab(sim.world, "achterkamp", 3.0)
    save_ab_line(sim_app.agbot.data_dir, plan["entry"])
    h = math.radians(plan["heading_deg"])
    ax, ay = plan["a"]
    sim.reset_pose(ax - 4.0 * math.sin(h), ay - 4.0 * math.cos(h), plan["heading_deg"])
    time.sleep(1.0)
    r = sim_app.agbot_request("/api/nav/start_ab", start_command(plan["entry"]))
    assert r["status"] == "started", r
    dx, dy = math.sin(h), math.cos(h)
    errors = []
    t_end = time.time() + 25
    while time.time() < t_end:
        s = sim.vehicle.s
        along = (s.x - ax) * dx + (s.y - ay) * dy
        cross = (s.x - ax) * dy - (s.y - ay) * dx      # + = rechts van A->B
        if along > 6.0:
            errors.append(cross)
        time.sleep(0.1)
    sim_app.agbot_request("/api/stop", {})
    assert len(errors) > 50, "robot kwam niet op gang"
    worst = max(abs(e) for e in errors)
    assert worst < 0.10, f"afwijking op baan 0 tot {worst:.3f} m"
    # En hij reed echt in de baanrichting
    assert sim.vehicle.s.odometer_m > 12.0
