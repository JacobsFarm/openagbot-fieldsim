"""Veldmissie: randen, kopakkerrondjes, banen, bochten en de uitvoer voor de robot (op de fantasiewereld)."""
import math
import os
import random

import pytest

from conftest import ROOT

shapely = pytest.importorskip("shapely")

from shapely.geometry import LineString, Point  # noqa: E402

from openagbot_fieldsim.missions.boundary import field_area  # noqa: E402
from openagbot_fieldsim.missions.dubins import dubins_all  # noqa: E402
from openagbot_fieldsim.missions.field_plan import (PlanSettings, maak_baan_volgorde, plan_field, plan_view,  # noqa: E402
                                                    robot_turn_radius, to_veldmissie, to_waypoints)
from openagbot_fieldsim.world.model import load_world  # noqa: E402


@pytest.fixture(scope="module")
def world():
    return load_world(os.path.join(ROOT, "worlds", "example"), overlay=False)


def test_dubins_reaches_goal_pose():
    rnd = random.Random(3)
    for _ in range(300):
        a = (rnd.uniform(-10, 10), rnd.uniform(-10, 10), rnd.uniform(-3, 3))
        b = (rnd.uniform(-10, 10), rnd.uniform(-10, 10), rnd.uniform(-3, 3))
        paths = dubins_all(a, b, 2.5)
        assert paths
        for p in paths:
            x, y, th = p.sample(0.2)[-1]
            assert math.hypot(x - b[0], y - b[1]) < 1e-6
            assert abs((th - b[2] + math.pi) % (2 * math.pi) - math.pi) < 1e-6


def test_dubins_u_turn_is_half_circle_when_jump_is_2r():
    p = dubins_all((0, 0, math.pi / 2), (6, 0, -math.pi / 2), 3.0)[0]
    assert p.length == pytest.approx(math.pi * 3.0, rel=1e-6)


def test_turn_radius_matches_robot():
    # OpenAgbot: binnenste voorwiel op 30 graden, wielbasis 1,2, spoor 1,0 -> 2,078 + 0,5
    assert robot_turn_radius(1.2, 1.0, 30.0) == pytest.approx(2.578, abs=1e-3)


def test_baan_volgorde_same_as_robot():
    assert maak_baan_volgorde(8, 1) == [0, 2, 4, 6, 7, 5, 3, 1]
    assert maak_baan_volgorde(5, 0) == [0, 1, 2, 3, 4]


def test_boundary_finds_ditch_and_keeps_clear(world):
    # Zuidkavel ligt aan een sloot: die moet als water herkend worden, en het toegestane gebied blijft
    # minstens de vrije ruimte van het water af
    a = field_area(world, "zuidkavel", {"water": 1.0})
    kinds = {p.kind for p in a.pieces}
    assert "water" in kinds
    water = [d for d in world.ditches]
    for d in water:
        line = LineString(d.xy).buffer(d.width_m / 2.0, cap_style="flat")
        assert a.allowed.distance(line) >= 1.0 - 0.02 or not a.allowed.intersects(line.buffer(1.0))
    assert a.allowed.within(a.field_poly)


def test_clearance_moves_boundary_inward(world):
    near = field_area(world, "weide", {"hek": 0.2}).allowed.area
    far = field_area(world, "weide", {"hek": 2.0}).allowed.area
    assert far < near


@pytest.mark.parametrize("field", ["achterkamp", "lange_akker", "driehoek", "zuidkavel"])
def test_headland_first_covers_field_and_stays_inside(world, field):
    p = plan_field(world, field, {"work_width_m": 3.0, "headland_rounds": 3})
    st = p["stats"]
    assert st["coverage_allowed_pct"] > 99.0
    assert st["worked_outside_allowed_m2"] < 0.5
    assert all(sg.ok for sg in p["segments"]), [sg.label for sg in p["segments"] if not sg.ok]
    types = [sg.type for sg in p["segments"]]
    # eerst drie rondjes, dan banen met bochten ertussen
    assert types[0] == "rondje" and types.count("rondje") == 3
    first_swath = types.index("baan")
    assert "rondje" not in types[first_swath:]
    assert types.count("bocht") == types.count("baan") - len(p["cells"])


def test_headland_last_ends_with_rounds(world):
    p = plan_field(world, "lange_akker", {"headland_rounds": 2, "headland_order": "laatst"})
    types = [sg.type for sg in p["segments"]]
    assert types[0] == "baan" and types[-1] == "rondje"
    assert all(sg.ok for sg in p["segments"])


def test_no_headland_leaves_turning_strip(world):
    with_hl = plan_field(world, "lange_akker", {"headland_rounds": 3})["stats"]
    without = plan_field(world, "lange_akker", {"headland_rounds": 0})
    assert not [sg for sg in without["segments"] if sg.type == "rondje"]
    # kopakker overslaan: de draaistrook blijft onbewerkt, dus minder dekking, maar het werktuig blijft binnen
    assert without["stats"]["coverage_allowed_pct"] < with_hl["coverage_allowed_pct"]
    assert without["stats"]["worked_outside_allowed_m2"] < 0.5


def test_turns_respect_turn_radius(world):
    p = plan_field(world, "driehoek", {"headland_rounds": 2})
    R = p["turn_radius_m"]
    for sg in p["segments"]:
        if sg.type != "bocht":
            continue
        pts = sg.pts
        for a, b in zip(pts, pts[1:]):
            ds = math.hypot(b[0] - a[0], b[1] - a[1])
            dth = abs((b[2] - a[2] + math.pi) % (2 * math.pi) - math.pi)
            if ds > 1e-6:
                assert dth / ds <= 1.01 / R          # koorde iets korter dan de boog


def test_fixed_heading_and_pattern(world):
    p = plan_field(world, "achterkamp", {"swath_heading_deg": 0.0, "order": "overslaan_2"})
    assert p["swath_heading_deg"] == 0.0
    assert {c["pattern"] for c in p["cells"]} == {"overslaan_2"}


def test_exports(world):
    p = plan_field(world, "driehoek", PlanSettings(headland_rounds=2))
    vm = to_veldmissie(p, world, "Driehoek")
    assert vm["format"] == "openagbot.veldmissie" and vm["versie"] == 2
    assert len(vm["segmenten"]) == len(p["segments"])
    s0 = vm["segmenten"][0]
    assert s0["werktuig"] in ("omlaag", "omhoog") and len(s0["punten"][0]) == 2
    wps = to_waypoints(p, world)
    assert len(wps) > 50 and set(wps[0]) == {"way_point_name", "lat", "lon"}
    # opeenvolgende waypoints nooit verder dan een baanstuk uit elkaar, en de route volgt het plan
    f = world.frame
    xy = [f.to_xy(w["lat"], w["lon"]) for w in wps]
    path = LineString([(q[0], q[1]) for sg in p["segments"] for q in sg.pts])
    assert max(path.distance(Point(x, y)) for x, y in xy) < 0.05
    assert max(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:])) <= 25.5
    v = plan_view(p)
    assert v["kind"] == "veldmissie" and v["segments"] and v["edges"]
