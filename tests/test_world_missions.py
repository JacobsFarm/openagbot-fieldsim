import json
import math
import os

import pytest

from conftest import ROOT
from openagbot_fieldsim.missions.ab import plan_ab, save_ab_line, start_command
from openagbot_fieldsim.replay.ride_log import load_log, stats, waypoints_from_log
from openagbot_fieldsim.world import fantasy
from openagbot_fieldsim.world.geometry import point_in_polygon, polygon_area
from openagbot_fieldsim.world.model import World, load_world
from openagbot_fieldsim.world.terrain import build_terrain


@pytest.fixture(scope="module")
def world():
    return fantasy.build()


@pytest.fixture(scope="module")
def terrain(world):
    return build_terrain(world)


def test_example_on_disk_matches_generator(world):
    """worlds/example/world.json is gegenereerd: na een wijziging in fantasy.py opnieuw draaien (--make-example)."""
    with open(os.path.join(ROOT, "worlds", "example", "world.json"), encoding="utf-8") as fh:
        assert json.load(fh) == json.loads(json.dumps(world.to_dict()))


def test_roundtrip(world, tmp_path):
    world.save(str(tmp_path))
    again = load_world(str(tmp_path))
    assert again.to_dict() == world.to_dict()
    assert len(again.fields) == 5 and len(again.buildings) >= 5


def test_fields_have_dutch_size(world):
    for f in world.fields:
        assert 2.0 < polygon_area(f.xy) / 1e4 < 7.0, f.name


def test_surfaces_and_hazards(world, terrain):
    assert world.surface_at(0, -75) == "yard"
    assert world.surface_at(-100, -143.5) == "water"          # sloot langs de weg
    assert world.surface_at(0, -143.5) != "water"             # dam bij de inrit
    assert world.surface_at(-150, 200) == "field"
    # erf is vlak, sloot is diep
    assert abs(terrain.height(-40, -110) - terrain.height(40, -40)) < 0.02
    assert terrain.height(-100, -143.5) < terrain.height(-100, -135) - 0.6


def test_ab_plans_stay_inside_fields(world):
    for f in world.fields:
        plan = plan_ab(world, f.id, 3.0, 8.0)
        e = plan["entry"]
        assert e["aantal_banen"] >= 10 and e["baanlengte_m"] > 80, f.name
        for a, b in plan["swaths"]:
            for p in (a, b):
                assert point_in_polygon(p[0], p[1], f.xy), f"{f.name}: baan buiten het veld"
        cmd = start_command(e)
        assert cmd["work_width_m"] == 3.0 and cmd["kant"] == "rechts"


def test_save_ab_line_replaces_same_field(world, tmp_path):
    e = plan_ab(world, "weide", 3.0)["entry"]
    save_ab_line(str(tmp_path), e)
    save_ab_line(str(tmp_path), {**e, "werkbreedte_m": 4.0})
    items = json.load(open(tmp_path / "ab_line.json", encoding="utf-8"))
    assert len(items) == 1 and items[0]["werkbreedte_m"] == 4.0


def test_ride_log(world, tmp_path):
    frame = world.frame
    path = tmp_path / "rit_test.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("Tijdstip,Modus,Lat,Lon,Heading_Echt,Stuurhoek,Doel_kmh,Echt_kmh,Afwijking_m,Pitch_deg,WP_Doel,Accu_V\n")
        for i in range(50):
            lat, lon = frame.to_latlon(0.0, i * 1.0)
            fh.write(f"10:00:{i:02d}.000,TRACKING,{lat},{lon},0,0,3,3,{0.01 * (i % 3)},0.1,0,55\n")
    d = load_log(str(path), frame)
    assert d["t"][-1] == 49.0 and abs(d["y"][-1] - 49.0) < 1e-3
    s = stats(d)
    assert s["distance_m"] == pytest.approx(49.0, abs=0.01) and s["xte_max_m"] == 0.02
    wps = waypoints_from_log(d, frame, 5.0)
    assert len(wps) == 10


def test_world_from_area_only():
    w = World.from_dict({"name": "x", "origin": {"lat": 52.2, "lon": 4.6},
                         "area": {"polygon": [[52.20, 4.59], [52.20, 4.60], [52.21, 4.60], [52.21, 4.59]]}})
    t = build_terrain(w)
    assert t.height(0, 0) == 0.0 and math.isclose(w.margin_m, 50.0)


def test_points_import_and_route(world):
    from openagbot_fieldsim.missions.points import parse_points_file, random_weeds, route_length, route_order, to_waypoints
    csv_data = b"lat,lon,naam\n52.001,4.001,a\n52.002,4.002,b\n"
    pts = parse_points_file("onkruid.csv", csv_data)
    assert [p["name"] for p in pts] == ["a", "b"] and pts[0]["lat"] == 52.001
    gj = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [4.0, 52.0]}, "properties": {"name": "x"}}]})
    assert parse_points_file("p.geojson", gj.encode())[0] == {"lat": 52.0, "lon": 4.0, "name": "x"}
    weeds = random_weeds(world, "achterkamp", 50)
    assert len(weeds) == 50
    f = world.field_by_id("achterkamp")
    xy = [world.frame.to_xy(p["lat"], p["lon"]) for p in weeds]
    assert all(point_in_polygon(x, y, f.xy) for x, y in xy)
    order = route_order(xy, (0.0, 0.0))
    assert sorted(order) == list(range(50))
    assert route_length(xy, order, (0.0, 0.0)) < 0.5 * route_length(xy, list(range(50)), (0.0, 0.0))
    wps = to_waypoints(weeds)
    assert set(wps[0]) == {"way_point_name", "lat", "lon"}
