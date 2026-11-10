"""Boerderij: farm.json-overlay, nieuwe objecten in de planner, doorgangen en routegraaf, veiligheid, import/export."""
import copy
import json
import math
import os

import pytest

from conftest import ROOT

shapely = pytest.importorskip("shapely")

from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from openagbot_fieldsim.geo import LocalFrame  # noqa: E402
from openagbot_fieldsim.missions.boundary import field_area  # noqa: E402
from openagbot_fieldsim.missions.farm_route import (FarmGraph, farm_view, plan_farm, suggest_passages,  # noqa: E402
                                                    to_veldmissie_farm)
from openagbot_fieldsim.missions.field_plan import PlanSettings, plan_field, to_veldmissie, to_waypoints  # noqa: E402
from openagbot_fieldsim.missions.safety import Geofence, check_path, footprint_points, hazards  # noqa: E402
from openagbot_fieldsim.world import interchange as ix  # noqa: E402
from openagbot_fieldsim.world.farm import (anchor_of, apply_farm, empty_farm, list_backups, load_farm,  # noqa: E402
                                           save_farm, set_start_pose)
from openagbot_fieldsim.world.model import (Ditch, Dike, Field, Hole, Marker, Nest, NoGo, Passage, Pose, Tree,  # noqa: E402
                                            World, Zone, ensure_ids, load_world)

LAT0, LON0 = 52.5, 5.5


def make_world(**layers) -> World:
    """Twee velden van 80 x 60 m naast elkaar met een sloot ertussen, en een erf (rijzone) eronder."""
    f = LocalFrame(LAT0, LON0, 0.0)

    def ll(pts):
        return [f.to_latlon(x, y) for x, y in pts]

    def box(x1, y1, x2, y2):
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

    base = dict(
        fields=[Field("west", "West", ll(box(-90, 0, -10, 60)), "gras"),
                Field("oost", "Oost", ll(box(10, 0, 90, 60)), "gras")],
        ditches=[Ditch(ll([(0, -5), (0, 70)]), 3.0, 0.9, 0.3, "sloot_midden")],
        zones=[Zone(ll(box(-90, -30, 90, -8)), "alleen_rijden", "erf", "Erf")],
        passages=[Passage(ll([(-50, -12), (-50, 6)]), 5.0, "hek", id="hek_west", name="Hek west"),
                  Passage(ll([(-14, 30), (14, 30)]), 5.0, "dam", id="dam", name="Dam")],
    )
    for k, v in layers.items():
        base[k] = [*base.get(k, []), *v] if isinstance(v, list) else v
    return World(name="t", label="t", origin=(LAT0, LON0, 0.0), area=ll(box(-110, -50, 110, 90)), margin_m=20.0,
                 terrain={"type": "flat"}, start_pose=Pose(*f.to_latlon(-60, -20), 90.0), **base)


def ll_of(world, pts):
    return [world.frame.to_latlon(x, y) for x, y in pts]


# ---------------------------------------------------------------------------------------------------- datamodel
def test_ids_are_stable_and_from_geometry():
    d = {"ditches": [{"line": [[52.0, 4.0], [52.001, 4.0]]}, {"line": [[52.0, 4.1], [52.001, 4.1]]}],
         "fields": [{"id": "perceel_1", "polygon": [[52, 4], [52, 4.001], [52.001, 4.001]]}]}
    a, b = copy.deepcopy(d), copy.deepcopy(d)
    b["ditches"].reverse()                       # andere volgorde na een nieuwe bouw: zelfde id's
    ensure_ids(a)
    ensure_ids(b)
    assert {x["id"] for x in a["ditches"]} == {x["id"] for x in b["ditches"]}
    assert a["ditches"][0]["id"].startswith("sloot_") and a["fields"][0]["id"] == "perceel_1"


def test_world_roundtrip_with_new_layers(tmp_path):
    w = make_world(dikes=[Dike(ll_of(make_world(), [(-90, 75), (90, 75)]), 2.0, 1.5, 2.5, "doorgang", "dijk1", "Dijk")],
                   nests=[Nest(*LocalFrame(LAT0, LON0).to_latlon(-50, 30), 6.0, "03-15", "08-15", "nest1", "Kievit")],
                   markers=[Marker(*LocalFrame(LAT0, LON0).to_latlon(40, 20), "put", 0.5, "put", "m1", "Put")])
    w.fields[0].holes = [Hole(ll_of(w, [(-60, 20), (-50, 20), (-50, 30), (-60, 30)]), True, "poel")]
    w.__post_init__()
    w.save(str(tmp_path))
    again = load_world(str(tmp_path))
    assert again.to_dict() == json.loads(json.dumps(w.to_dict()))
    assert again.dikes[0].mode == "doorgang" and again.fields[0].holes[0].passable
    assert again.nests[0].active("2026-05-01") and not again.nests[0].active("2026-11-10")


def test_farm_overlay_survives_rebuild(tmp_path):
    folder = str(tmp_path)
    make_world().save(folder)
    base = json.load(open(os.path.join(folder, "world.json"), encoding="utf-8"))
    ensure_ids(base)
    west = next(f for f in base["fields"] if f["id"] == "west")
    oost = next(f for f in base["fields"] if f["id"] == "oost")
    farm = empty_farm()
    changed = copy.deepcopy(west)
    changed["name"] = "West (ingemeten)"
    changed["replaces"] = {"id": "west", "anchor": anchor_of("fields", west)}
    farm["objects"]["fields"] = [changed]
    farm["objects"]["no_go"] = [{"name": "Natte plek", "action": "stoppen", "polygon": oost["polygon"][:3]}]
    farm["hidden"] = [{"layer": "fields", "id": "oost", "anchor": anchor_of("fields", oost)}]
    save_farm(folder, farm)

    w = load_world(folder)
    assert [f.name for f in w.fields] == ["West (ingemeten)"] and w.fields[0].source == "overschreven"
    assert w.no_go[0].source == "eigen" and w.overlay
    with pytest.raises(RuntimeError):            # de samengevoegde wereld mag world.json niet overschrijven
        w.save(folder)

    # "Bouw" draait opnieuw: de percelen krijgen andere id's en een andere volgorde. De overlay vindt ze op het anker.
    rebuilt = copy.deepcopy(base)
    rebuilt["fields"] = [dict(oost, id="perceel_1"), dict(west, id="perceel_2")]
    json.dump(rebuilt, open(os.path.join(folder, "world.json"), "w", encoding="utf-8"))
    w2 = load_world(folder)
    assert [f.name for f in w2.fields] == ["West (ingemeten)"] and w2.fields[0].id == "perceel_2"
    assert load_world(folder, overlay=False).fields[0].id == "perceel_1"       # de bouwer ziet alleen de brondata

    # Het bronperceel verdwijnt helemaal: je versie blijft als eigen object, met een melding
    rebuilt["fields"] = [dict(oost, id="perceel_1")]
    json.dump(rebuilt, open(os.path.join(folder, "world.json"), "w", encoding="utf-8"))
    w3 = load_world(folder)
    assert [f.name for f in w3.fields] == ["West (ingemeten)"] and w3.fields[0].source == "eigen"
    assert any("bestaat niet meer" in r for r in w3.overlay_report)


def test_farm_save_makes_backup_and_rejects_bad_geometry(tmp_path):
    folder = str(tmp_path)
    make_world().save(folder)
    farm = empty_farm()
    farm["objects"]["nests"] = [{"name": "n", "lat": LAT0, "lon": LON0}]
    save_farm(folder, farm)
    farm["objects"]["nests"].append({"name": "n2", "lat": LAT0 + 0.0001, "lon": LON0})
    save_farm(folder, farm)
    assert len(list_backups(folder)) == 1 and len(load_farm(folder)["objects"]["nests"]) == 2
    bad = copy.deepcopy(farm)
    bad["objects"]["no_go"] = [{"polygon": [[LAT0, LON0], [LAT0, LON0 + 0.001]]}]
    with pytest.raises(ValueError):
        save_farm(folder, bad)
    assert len(load_farm(folder)["objects"]["nests"]) == 2            # het oude bestand staat er nog
    set_start_pose(folder, LAT0 + 0.0002, LON0, 45.0)
    assert load_world(folder).start_pose.heading_deg == 45.0


# ---------------------------------------------------------------------------------------------------- planner
def test_no_go_and_nest_shrink_allowed_area():
    plain = field_area(make_world(), "west").allowed.area
    w = make_world(no_go=[NoGo(ll_of(make_world(), [(-60, 20), (-40, 20), (-40, 40), (-60, 40)]), "stoppen", "ng", "x")])
    assert field_area(w, "west").allowed.area < plain - 400
    w = make_world(nests=[Nest(*LocalFrame(LAT0, LON0).to_latlon(-50, 30), 8.0, "03-15", "08-15", "n1", "nest")])
    in_season = field_area(w, "west", date="2026-05-01").allowed.area
    assert in_season < plain - math.pi * 8 * 8 * 0.9
    assert field_area(w, "west", date="2026-10-01").allowed.area == pytest.approx(plain, rel=1e-6)


def test_tree_with_own_clearance():
    lat, lon = LocalFrame(LAT0, LON0).to_latlon(-50, 30)
    small = field_area(make_world(trees=[Tree(lat, lon, 8.0, "ball", "t1", 0.5)]), "west").allowed.area
    big = field_area(make_world(trees=[Tree(lat, lon, 8.0, "ball", "t1", 6.0)]), "west").allowed.area
    assert small - big > 100


def test_dike_modes():
    line = ll_of(make_world(), [(-90, 40), (-10, 40)])
    plain = field_area(make_world(), "west").allowed.area
    avoid = make_world(dikes=[Dike(line, 2.0, 1.0, 2.0, "ontwijken", "d", "Dijk")])
    ignore = make_world(dikes=[Dike(line, 2.0, 1.0, 2.0, "negeren", "d", "Dijk")])
    assert field_area(avoid, "west").allowed.area < plain - 300
    assert field_area(ignore, "west").allowed.area == pytest.approx(plain, rel=1e-6)
    assert field_area(avoid, "west", mission_dike_mode="negeren").allowed.area == pytest.approx(plain, rel=1e-6)
    avoid.farm_settings = {"dike_mode": "negeren"}                 # boerderij-instelling gaat voor de dijk zelf
    assert field_area(avoid, "west").allowed.area == pytest.approx(plain, rel=1e-6)


def test_holes_passable_or_not():
    w = make_world()
    ring = ll_of(w, [(-60, 22), (-40, 22), (-40, 38), (-60, 38)])
    w.fields[0].holes = [Hole(ring, False, "poel")]
    w.__post_init__()
    hard = plan_field(w, "west", {"headland_rounds": 1})
    hole = Polygon([(-60, 22), (-40, 22), (-40, 38), (-60, 38)])
    for sg in hard["segments"]:                                    # nergens door het gat, ook niet met werktuig omhoog
        assert LineString([(p[0], p[1]) for p in sg.pts]).distance(hole.buffer(-0.5)) > 0
    w.fields[0].holes = [Hole(ring, True, "leiding")]
    w.__post_init__()
    soft = plan_field(w, "west", {"headland_rounds": 1})
    down = [sg for sg in soft["segments"] if sg.implement == "omlaag"]
    for sg in down:                                                # wel erdoor rijden, niet werken
        assert LineString([(p[0], p[1]) for p in sg.pts]).intersection(hole.buffer(-0.2)).length < 0.5
    assert any("niet werken" in sg.label for sg in soft["segments"])
    assert soft["stats"]["worked_m2"] < hard["stats"]["allowed_m2"] + 400


# ---------------------------------------------------------------------------------------------------- veiligheid
def test_precheck_flags_path_through_ditch_and_no_go():
    w = make_world(no_go=[NoGo(ll_of(make_world(), [(30, 20), (50, 20), (50, 40), (30, 40)]), "stoppen", "ng", "Nat")])
    assert check_path(w, [(-80, 50), (-20, 50)], 0.6) == []
    kinds = {v["kind"] for v in check_path(w, [(-40, 50), (60, 30)], 0.6)}
    assert kinds == {"water", "verboden"}
    assert check_path(w, [(-12, 30), (12, 30)], 0.6) == []            # over de dam mag het wel
    assert {v["kind"] for v in check_path(w, [(-12, 36), (12, 36)], 0.6)} == {"water"}    # naast de dam niet
    assert {v["kind"] for v in check_path(w, [(80, 50), (200, 50)], 0.6)} == {"buiten"}


def test_field_plan_never_touches_hazards():
    w = make_world(no_go=[NoGo(ll_of(make_world(), [(30, 20), (50, 20), (50, 40), (30, 40)]), "stoppen", "ng", "Nat")])
    p = plan_field(w, "oost", {"headland_rounds": 2})
    assert p["safety"]["ok"] and all(sg.ok for sg in p["segments"])
    vm = to_veldmissie(p, w, "Oost")
    assert vm["versie"] == 2 and vm["veiligheid"]["ok"]
    assert {v["soort"] for v in vm["geofence"]["verboden"]} >= {"water", "verboden"}
    assert vm["geofence"]["toegestaan"] and len(to_waypoints(p, w)) > 20


def test_geofence_stops_before_ditch_and_in_no_go():
    w = make_world(no_go=[NoGo(ll_of(make_world(), [(30, 20), (50, 20), (50, 40), (30, 40)]), "stoppen", "ng", "Nat"),
                          NoGo(ll_of(make_world(), [(60, 45), (70, 45), (70, 55), (60, 55)]), "mijden", "zacht", "Zacht")])
    g = Geofence(w)
    assert g.check(footprint_points(-50, 30, 90.0, 1.2, 1.0)) == []
    # naar het oosten rijden, op de sloot af: stop voordat een wiel in het water staat (rand 0,3 m)
    hit = None
    x = -20.0
    while x < 0 and not hit:
        hit = g.check(footprint_points(x, 50, 90.0, 1.2, 1.0))
        x += 0.07
    assert hit and hit[0]["kind"] == "water" and hit[0]["level"] == "stop"
    front = x - 0.07 + 1.2 + 0.25
    assert front <= -1.5 + 0.1                     # insteek ligt op x = -1,5: neus stopt daarvoor
    assert g.check(footprint_points(x, 50, 90.0, 1.2, 1.0)) == []       # niet elke tick opnieuw melden
    g.reset()
    assert g.check(footprint_points(40, 30, 0.0, 1.2, 1.0))[0]["kind"] == "verboden"
    soft = Geofence(w).check(footprint_points(65, 50, 0.0, 1.2, 1.0))
    assert soft and soft[0]["level"] == "mijden"
    # over de dam: geen alarm
    g2 = Geofence(w)
    assert all(g2.check(footprint_points(x / 10.0, 30, 90.0, 1.2, 1.0)) == [] for x in range(-140, 130, 2))


def test_simulation_pauses_on_geofence(agbot_config):
    from openagbot_fieldsim.engine import DT, Ports, Simulation
    w = make_world(no_go=[NoGo(ll_of(make_world(), [(-60, 20), (-40, 20), (-40, 40), (-60, 40)]), "stoppen", "ng", "Nat")])
    sim = Simulation(w, agbot_config, Ports(0, 0, 0))
    try:
        stopped = []
        sim.on_geofence = stopped.append
        sim.reset_pose(-50.0, 12.0, 0.0)             # onder het verboden gebied, neus naar het noorden
        for v in sim.vescs.values():
            v.online = True
        for _ in range(4000):
            sim.vehicle.s.y += 0.004                 # met de hand vooruit schuiven (geen aandrijving nodig)
            sim._gf_tick = 10
            sim._check_geofence()
            if sim.paused:
                break
        assert sim.paused and stopped and stopped[0]["kind"] == "verboden"
        assert sim.snapshot()["geofence"]["alarm"]["kind"] == "verboden"
        assert sim.vehicle.s.y + 1.2 + 0.25 < 20.2    # gestopt op de rand, niet middenin
        _ = DT
    finally:
        sim.stop()


# ---------------------------------------------------------------------------------------------------- doorgangen
def test_fields_only_connected_through_passages():
    w = make_world()
    g = FarmGraph(w, PlanSettings())
    assert {r.kind for r in g.regions} == {"veld", "rijgebied"} and not g.unusable
    p = plan_farm(w, None, {"headland_rounds": 1})
    assert p["order"] == ["west", "oost"] and not p["unreachable"] and p["safety"]["ok"]
    types = [sg.type for sg in p["segments"]]
    assert types.count("doorgang") >= 3            # hek in, dam over, en terug
    ditch = LineString([(0, -5), (0, 70)]).buffer(1.5, cap_style="flat")
    dam = LineString([(-14, 30), (14, 30)]).buffer(2.5, cap_style="flat")
    for sg in p["segments"]:                       # de route kruist de sloot alleen in de dam
        line = LineString([(q[0], q[1]) for q in sg.pts])
        if line.intersects(ditch):
            assert sg.type == "doorgang" and line.intersection(ditch).within(dam.buffer(0.01))
    vm = to_veldmissie_farm(p, w)
    assert vm["soort"] == "boerderij" and [d["id"] for d in vm["doorgangen"]] == ["hek_west", "dam"]
    assert any(s["type"] == "doorgang" and s["doorgang"] == "dam" for s in vm["segmenten"])
    assert json.dumps(farm_view(p)) and len(to_waypoints(p, w)) > 50


def test_field_without_passage_is_reported_not_driven():
    w = make_world()
    w.passages = [p for p in w.passages if p.id != "dam"]
    p = plan_farm(w, None, {"headland_rounds": 1})
    assert p["order"] == ["west"]
    assert p["unreachable"][0]["field"] == "oost" and "doorgang" in p["unreachable"][0]["reason"]
    assert all(LineString([(q[0], q[1]) for q in sg.pts]).bounds[2] < -1.5 for sg in p["segments"])


def test_narrow_or_one_way_passage():
    w = make_world()
    next(p for p in w.passages if p.id == "dam").width_m = 2.0        # smaller dan het werktuig van 3 m
    g = FarmGraph(w, PlanSettings(work_width_m=3.0))
    assert "te smal" in g.unusable["dam"]
    assert plan_farm(w, None, {"headland_rounds": 1})["order"] == ["west"]
    assert plan_farm(w, None, {"headland_rounds": 1, "work_width_m": 1.5})["order"] == ["west", "oost"]
    w = make_world()
    next(p for p in w.passages if p.id == "dam").direction = "terug"   # alleen van oost naar west
    p = plan_farm(w, None, {"headland_rounds": 1})
    assert p["order"] == ["west"] and p["unreachable"][0]["field"] == "oost"


def test_passage_with_confirm_and_speed_limit():
    w = make_world()
    dam = next(p for p in w.passages if p.id == "dam")
    dam.confirm, dam.max_speed_kmh = True, 1.5
    vm = to_veldmissie_farm(plan_farm(w, None, {"headland_rounds": 1}), w)
    seg = next(s for s in vm["segmenten"] if s.get("doorgang") == "dam")
    assert seg["bevestigen"] is True and seg["snelheid_kmh"] == 1.5 and seg["werktuig"] == "omhoog"


def test_suggest_passages_only_where_nothing_blocks():
    w = make_world()
    w.passages = []
    names = {tuple(sorted(s["connects"])) for s in suggest_passages(w)}
    assert ("West", "rijgebied 1") in names and ("Oost", "rijgebied 1") in names
    assert ("Oost", "West") not in names            # daar ligt de sloot tussen


def test_example_world_whole_farm():
    w = load_world(os.path.join(ROOT, "worlds", "example"), overlay=False)
    p = plan_farm(w, None, {"headland_rounds": 1})
    assert sorted(p["order"]) == ["achterkamp", "driehoek", "lange_akker", "weide", "zuidkavel"]
    assert not p["unreachable"] and p["safety"]["ok"]
    cross = [sg for sg in p["segments"] if sg.meta.get("doorgang") == "oversteek_polderweg"]
    assert cross and all(sg.meta["bevestigen"] for sg in cross)       # de weg over: eerst stoppen
    water = [h.geom for h in hazards(w) if h.kind in ("water", "weg")]
    for sg in p["segments"]:
        line = LineString([(q[0], q[1]) for q in sg.pts])
        assert not any(line.intersects(g) for g in water), sg.label


def test_terrain_dike_and_dam():
    from openagbot_fieldsim.world.terrain import build_terrain
    w = make_world(dikes=[Dike(ll_of(make_world(), [(-90, 75), (90, 75)]), 2.0, 1.5, 2.0, "ontwijken", "d", "Dijk")],
                   passages=[Passage(ll_of(make_world(), [(40, 68), (40, 82)]), 4.0, "hek", id="coupure", name="c")])
    t = build_terrain(w)
    assert t.height(-40, 75) == pytest.approx(1.5, abs=0.05) and t.height(-40, 77.5) == pytest.approx(0.75, abs=0.3)
    assert t.height(40, 75) == pytest.approx(0.0, abs=0.05)            # doorgestoken bij de doorgang
    assert t.height(0, 50) < -0.5 and t.height(0, 30) == pytest.approx(0.0, abs=0.05)     # sloot, en de dam
    assert w.surface_at(0, 50) == "water" and w.surface_at(0, 30) != "water"


# ---------------------------------------------------------------------------------------------------- import / export
def aog_files(e0=0.0, n0=0.0, number=lambda v, nd: f"{v:.{nd}f}"):
    """Een AgOpenGPS-veldmap: veld van 120 x 80 m met een gat, en drie vlaggen."""
    def ring(pts):
        return [f"{number(e, 3)},{number(n, 3)},{number(0.5, 5)}" for e, n in pts]

    outer = [(e0 + x, n0 + y) for x, y in ((0, 0), (40, 0), (80, 0), (120, 0), (120, 80), (60, 80), (0, 80), (0, 40))]
    hole = [(e0 + x, n0 + y) for x, y in ((50, 30), (70, 30), (70, 50), (50, 50))]
    return {
        "Field.txt": "2025-June-12 09:04:00 PM\n$FieldDir\nFieldNew\n$Offsets\n0,0\nConvergence\n0\nStartFix\n"
                     "52.2123343333334,4.59296250000119\n",
        "Boundary.txt": "\n".join(["$Boundary", "False", "8", *ring(outer), "True", "4", *ring(hole)]) + "\n",
        "Flags.txt": "$Flags\n3\n52.2125,4.5932,10,10,0,1,1,vogelnest kievit\n52.2126,4.5933,20,20,0,0,2,put, riool\n"
                     "52.2127,4.5934,30,30,2,3,oude boom\n",
    }


def test_aog_plane_matches_exact_geodesy():
    # AgOpenGPS rekent met vaste meters-per-graad; over een veld moet dat op de cm kloppen met de exacte omrekening
    lat0, lon0 = 52.2123343333334, 4.59296250000119
    frame = LocalFrame(lat0, lon0)
    for e, n in ((0, 0), (100, 50), (-250, 300), (600, -400)):
        lat, lon = ix.aog_to_latlon(e, n, lat0, lon0)
        x, y = frame.to_xy(lat, lon)
        assert math.hypot(x - e, y - n) < 0.05 + 2e-4 * math.hypot(e, n)
        e2, n2 = ix.latlon_to_aog(lat, lon, lat0, lon0)
        assert abs(e2 - e) < 1e-6 and abs(n2 - n) < 1e-6


def test_aog_import_roundtrip():
    r = ix.aog_to_objects(aog_files(), "testveld")
    fld = r["objects"]["fields"][0]
    assert fld["id"] == "testveld" and len(fld["polygon"]) == 8 and fld["holes"][0]["passable"] is True
    assert len(r["objects"]["nests"]) == 1 and len(r["objects"]["trees"]) == 1
    assert r["objects"]["markers"][0]["kind"] == "put" and "riool" in r["objects"]["markers"][0]["name"]
    out = ix.export_aog(fld, start_fix=tuple(r["start_fix"]), spacing_m=0)
    again = ix.aog_to_objects(out, "testveld")["objects"]["fields"][0]
    for a, b in zip(fld["polygon"], again["polygon"]):
        assert abs(a[0] - b[0]) < 2e-8 and abs(a[1] - b[1]) < 2e-8          # ~1 mm
    assert again["holes"][0]["passable"] is True
    # met een eigen StartFix en verdichte punten blijft het veld hetzelfde vlak
    dense = ix.aog_to_objects(ix.export_aog(fld), "testveld")["objects"]["fields"][0]["polygon"]
    assert len(dense) > 100
    f = LocalFrame(*r["start_fix"])
    a, b = Polygon([f.to_xy(*p) for p in fld["polygon"]]), Polygon([f.to_xy(*p) for p in dense])
    assert a.symmetric_difference(b).area < 0.5 and a.area == pytest.approx(9600.0, rel=1e-3)
    assert len(ix.aog_to_objects(aog_files(), "v", simplify_m=0.05)["objects"]["fields"][0]["polygon"]) == 4


def test_aog_thousands_separator_bug():
    # .NET "N3" schrijft 1234.5 als 1,234.500: de komma botst met het scheidingsteken
    n3 = lambda v, nd: f"{v:,.{nd}f}"            # noqa: E731
    plain = ix.aog_to_objects(aog_files(1500.0, -2300.0), "v")["objects"]["fields"][0]
    broken = ix.aog_to_objects(aog_files(1500.0, -2300.0, n3), "v")["objects"]["fields"][0]
    assert broken["polygon"] == plain["polygon"] and broken["holes"] == plain["holes"]
    assert ix.parse_numbers("1,234.500,-12,345.678,0.50000", 3) == [1234.5, -12345.678, 0.5]
    assert ix.parse_numbers("-65.398,-50.118,4.88962", 3) == [-65.398, -50.118, 4.88962]
    with pytest.raises(ValueError):
        ix.parse_numbers("abc,def", 3)


def test_aog_old_format_and_missing_boundary():
    files = aog_files()
    files["Boundary.txt"] = files["Boundary.txt"].replace("False\n8", "False\nFalse\n8")     # oude versie: 2 regels
    assert len(ix.aog_to_objects(files, "v")["objects"]["fields"][0]["polygon"]) == 8
    files["Boundary.txt"] = "$Boundary\n"
    r = ix.aog_to_objects(files, "v")
    assert "fields" not in r["objects"] and any("geen grens" in x for x in r["report"])
    with pytest.raises(ValueError):
        ix.aog_to_objects({"Boundary.txt": "$Boundary\n"}, "v")


def test_geojson_and_kml_keep_everything():
    w = make_world(dikes=[Dike(ll_of(make_world(), [(-90, 75), (90, 75)]), 2.0, 1.5, 2.5, "doorgang", "dijk1", "Dijk")],
                   nests=[Nest(*LocalFrame(LAT0, LON0).to_latlon(-50, 30), 6.0, "04-01", "07-15", "nest1", "Kievit")],
                   no_go=[NoGo(ll_of(make_world(), [(30, 20), (50, 20), (50, 40), (30, 40)]), "mijden", "ng", "Nat")])
    w.fields[0].holes = [Hole(ll_of(w, [(-60, 20), (-50, 20), (-50, 30), (-60, 30)]), True, "poel")]
    d = w.to_dict()
    objs = {k: d[k] for k in ("fields", "ditches", "dikes", "zones", "passages", "nests", "no_go")}
    back = ix.geojson_to_objects(json.dumps(ix.to_geojson(objs)))["objects"]
    assert back == json.loads(json.dumps(objs))
    kml = ix.kml_to_objects(ix.to_kml(objs, "t"))["objects"]
    assert {k: len(v) for k, v in kml.items()} == {k: len(v) for k, v in objs.items()}
    assert kml["fields"][0]["holes"] and kml["dikes"][0]["mode"] == "doorgang" and kml["nests"][0]["start"] == "04-01"
    for a, b in zip(kml["fields"][0]["polygon"], objs["fields"][0]["polygon"]):
        assert abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7
    # een KML uit Google Earth zonder onze eigenschappen: het soort komt uit de naam
    plain = ('<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Sloot achter</name><LineString>'
             '<coordinates>5.5,52.5,0 5.501,52.5,0</coordinates></LineString></Placemark><Placemark><name>nest 3</name>'
             '<Point><coordinates>5.5,52.5,0</coordinates></Point></Placemark></Document></kml>')
    got = ix.kml_to_objects(plain)["objects"]
    assert len(got["ditches"]) == 1 and len(got["nests"]) == 1


def test_real_agopengps_fields_if_present():
    """Op de eigen AgOpenGPS-map (niet in git): import klopt met de KML van AgOpenGPS zelf en gaat heen en terug."""
    root = os.environ.get("FIELDSIM_AOG_DIR", os.path.expanduser(r"~\OneDrive\Bureaublad\Fields Agopengps"))
    if not os.path.isdir(root):
        pytest.skip("geen AgOpenGPS-map (zet FIELDSIM_AOG_DIR)")
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from import_agopengps import check, read_folder
    folders = read_folder(root)
    assert folders and check(folders)


# ---------------------------------------------------------------------------------------------------- editor-API
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from openagbot_fieldsim.api.server import create_app
    from openagbot_fieldsim.app import SimApp
    from openagbot_fieldsim.settings import Settings
    if not os.path.exists(os.path.join(r"F:\Agbot\Bruut_OpenAgbot", "data", "config.sim.json")) and \
            not os.environ.get("FIELDSIM_AGBOT_DIR"):
        pytest.skip("OpenAgbot niet gevonden")
    make_world().save(str(tmp_path / "worlds" / "t"))
    s = Settings(home=str(tmp_path), agbot_dir=os.environ.get("FIELDSIM_AGBOT_DIR", r"F:\Agbot\Bruut_OpenAgbot"),
                 gnss_port=0, steering_port=0, can_port=0, world="t", start_agbot=False)
    app = SimApp(s)
    app.load("t", start_agbot=False)
    yield TestClient(create_app(app)), app
    app.shutdown()


def test_editor_save_reload_and_ops(client):
    c, app = client
    st = c.get("/api/editor/state").json()
    assert {o["id"] for o in st["layers"]["fields"]} == {"west", "oost"} and not st["has_farm"]
    west = next(o for o in st["layers"]["fields"] if o["id"] == "west")
    west["props"]["name"] = "West nieuw"
    west["props"]["headland_rounds"] = 4
    west["holes"] = [{"pts": [[-60, 20], [-50, 20], [-50, 30], [-60, 30]], "passable": True, "name": "leiding"}]
    body = {"objects": {"fields": [west],
                        "no_go": [{"id": "", "source": "eigen", "props": {"name": "Nat", "action": "stoppen"},
                                   "pts": [[30, 20], [50, 20], [50, 40], [30, 40]]}],
                        "nests": [{"source": "eigen", "props": {"name": "Kievit", "radius_m": 6}, "pts": [[40, 50]]}]},
            "hidden": [{"layer": "ditches", "id": "sloot_midden"}],
            "settings": {"clearance": {"water": 2.0}, "dike_mode": "negeren"}}
    r = c.put("/api/editor/state", json=body)
    assert r.status_code == 200, r.text
    st2 = r.json()
    f = next(o for o in st2["layers"]["fields"] if o["id"] == "west")
    assert f["source"] == "overschreven" and f["props"]["headland_rounds"] == 4 and f["holes"][0]["passable"]
    assert f["pts"] == west["pts"]                              # meters -> lat/lon -> meters op de mm
    assert st2["layers"]["no_go"][0]["source"] == "eigen" and not st2["layers"]["ditches"]
    assert st2["hidden"][0]["id"] == "sloot_midden" and st2["settings"]["clearance"]["water"] == 2.0
    w = app.world                                               # de sim draait nu met de nieuwe wereld
    assert w.no_go and not w.ditches and w.fields[0].headland_rounds == 4 and w.farm_settings["dike_mode"] == "negeren"
    assert app.sim.geofence is not None and any(z.kind == "verboden" for z in app.sim.geofence.zones)
    sc = c.get("/api/world").json()
    assert sc["no_go"] and sc["nests"] and sc["fields"][0]["holes"]
    # een kruisende grens wordt geweigerd met een leesbare melding, en er verandert niets
    bad = {"objects": {"no_go": [{"source": "eigen", "props": {"name": "kruis"},
                                  "pts": [[0, 0], [10, 10], [10, 0], [0, 10]]}]}}
    r = c.put("/api/editor/state", json=bad)
    assert r.status_code == 400 and "kruist" in r.json()["detail"]
    assert c.get("/api/editor/state").json()["layers"]["no_go"][0]["props"]["name"] == "Nat"

    sq = {"pts": [[0, 0], [100, 0], [100, 60], [0, 60]], "holes": []}
    parts = c.post("/api/editor/op", json={"op": "split", "polys": [sq], "line": [[40, 10], [40, 50]]}).json()["polys"]
    assert sorted(p["area_m2"] for p in parts) == [2400.0, 3600.0]
    merged = c.post("/api/editor/op", json={"op": "merge", "polys": parts}).json()["polys"]
    assert len(merged) == 1 and merged[0]["area_m2"] == pytest.approx(6000.0, abs=1.0)
    assert c.post("/api/editor/op", json={"op": "offset", "polys": [sq], "distance": -5}).json()["polys"][0]["area_m2"] == 4500.0
    far = {"pts": [[300, 0], [400, 0], [400, 60], [300, 60]], "holes": []}
    assert c.post("/api/editor/op", json={"op": "merge", "polys": [sq, far]}).status_code == 400


def test_editor_import_export_and_farm_api(client):
    c, app = client
    r = c.post("/api/editor/import", json={"files": {f"veld a/{k}": v for k, v in aog_files().items()}}).json()
    kinds = sorted(o["layer"] for o in r["objects"])
    assert kinds == ["fields", "markers", "nests", "trees"] and any("LET OP" in x for x in r["report"])    # andere plek
    r = c.post("/api/editor/import_world", json={"world": "tweede", "label": "Tweede",
                                                 "files": {f"veld a/{k}": v for k, v in aog_files().items()}})
    assert r.status_code == 200 and r.json()["fields"] == 1
    w2 = load_world(os.path.join(app.settings.worlds_dir, "tweede"))
    assert w2.fields[0].source == "eigen" and w2.fields[0].name == "veld a" and w2.nests
    assert Polygon(w2.area_xy).contains(Polygon(w2.fields[0].xy))

    z = c.get("/api/editor/export?format=agopengps")
    assert z.status_code == 200
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert {"West/Field.txt", "West/Boundary.txt", "West/Flags.txt", "West/Field.kml", "Oost/Boundary.txt"} <= set(names)
    gj = c.get("/api/editor/export?format=geojson").json()
    assert {f["properties"]["layer"] for f in gj["features"]} == {"fields", "ditches", "zones", "passages"}
    assert "<Polygon>" in c.get("/api/editor/export?format=kml").text

    p = c.post("/api/farm/plan", json={"settings": {"headland_rounds": 1}})
    assert p.status_code == 200 and p.json()["order"] == ["west", "oost"] and p.json()["safety"]["ok"]
    vm = c.get("/api/farm/veldmissie.json").json()
    assert vm["versie"] == 2 and len(vm["doorgangen"]) == 2
    assert c.get("/api/farm/graph").json()["passages"][0]["usable"]
    assert c.post("/api/safety/check", json={"points": [[-40, 50], [40, 50]]}).json()["violations"][0]["kind"] == "water"
    assert any(h["kind"] == "water" for h in c.get("/api/safety").json()["hazards"])


# ---------------------------------------------------------------------------------------------------- slootzone, zones, kopakker
def test_ditch_zone_is_farm_setting_and_stops_robot():
    from openagbot_fieldsim.missions.boundary import effective_clearance
    w = make_world()
    assert effective_clearance(w)["water"] == pytest.approx(2.3)          # standaard: 2 m zone + 0,3 m marge
    near = make_world()                                                   # veld tot aan de slootkant (x = -1,5)
    near.fields[0] = Field("west", "West", ll_of(near, [(-90, 0), (-1.5, 0), (-1.5, 60), (-90, 60)]), "gras")
    near.__post_init__()
    edge = max(x for x, _ in field_area(near, "west").allowed.exterior.coords)
    assert edge == pytest.approx(-1.5 - 2.3, abs=0.05)                    # werktuig blijft 2,3 m van het water
    w.farm_settings = {"safety": {"ditch_zone_m": 3.0, "plan_margin_m": 0.5}}
    assert effective_clearance(w, {"water": 1.0})["water"] == pytest.approx(3.5)   # een missie kan er niet onder
    w.__dict__.pop("_hazard_cache", None)
    g = Geofence(w)
    # op 4 m van het water mag hij staan (neus op x = -5,5), op 2,9 m niet meer: stop in de zone, ver voor het water
    assert g.check(footprint_points(-6.95, 50, 90.0, 1.2, 1.0)) == []
    hit = g.check(footprint_points(-5.75, 50, 90.0, 1.2, 1.0))
    assert hit and hit[0]["kind"] == "water" and hit[0]["level"] == "stop"
    assert {v["kind"] for v in check_path(w, [(-40, 50), (-5, 50)], 0.6)} == {"water"}
    # de dam blijft open, ook de aanloop door de zone
    g2 = Geofence(w)
    assert all(g2.check(footprint_points(x / 10.0, 30, 90.0, 1.2, 1.0)) == [] for x in range(-200, 190, 2))
    p = plan_farm(w, None, {"headland_rounds": 1})
    assert p["order"] == ["west", "oost"] and p["safety"]["ok"]
    assert to_veldmissie_farm(p, w)["geofence"]["slootzone_m"] == 3.0


def test_skip_zone_is_left_out_but_drivable():
    corner = [(-90, 40), (-60, 40), (-60, 60), (-90, 60)]                 # hoek linksboven van veld West
    w = make_world(zones=[Zone(ll_of(make_world(), corner), "niet_bewerken", "hoek", "Hoek")])
    a = field_area(w, "west")
    plain = field_area(make_world(), "west")
    assert a.allowed.area < plain.allowed.area - 400 and a.full.area == pytest.approx(plain.allowed.area, rel=1e-6)
    p = plan_field(w, "west", {"headland_rounds": 2})
    zone = Polygon(corner).buffer(-0.3)
    for sg in p["segments"]:
        line = LineString([(q[0], q[1]) for q in sg.pts])
        if sg.implement == "omlaag":                                      # nergens werken in de zone
            assert line.buffer(1.5, cap_style="flat").intersection(zone).area < 1.0, sg.label
    assert p["safety"]["ok"] and all(sg.ok for sg in p["segments"])
    assert p["stats"]["coverage_allowed_pct"] > 97.0                      # van het werkgebied, zonder de hoek
    vm = to_veldmissie(p, w, "West")
    allowed = Polygon([w.frame.to_xy(*q) for q in vm["geofence"]["toegestaan"][0]["polygon"]])
    assert allowed.contains(Point(-80, 50))                              # de robot mag er wel komen


def test_drive_zone_cutting_a_corner_is_left_out_of_the_plan():
    """Een rijzone (alleen rijden) die een hoek afzet: rondjes en banen volgen de schuine kant, geen gekke banen."""
    cut = [(-95, 25), (-95, 65), (-55, 65)]                              # driehoek over de hoek linksboven van West
    w = make_world(zones=[Zone(ll_of(make_world(), cut), "alleen_rijden", "rij", "Rijpad hoek")])
    a = field_area(w, "west")
    plain = field_area(make_world(), "west")
    assert a.allowed.area < plain.allowed.area - 400 and a.no_work is None
    assert a.full.area == pytest.approx(plain.allowed.area, rel=1e-6)    # rijden en keren mag er wel
    p = plan_field(w, "west", {"headland_rounds": 2})
    zone = Polygon(cut).buffer(-0.3)
    for sg in p["segments"]:
        if sg.implement == "omlaag":
            line = LineString([(q[0], q[1]) for q in sg.pts])
            assert line.buffer(1.5, cap_style="flat").intersection(zone).area < 1.0, sg.label
    assert p["safety"]["ok"] and all(sg.ok for sg in p["segments"]) and not p["warnings"]
    assert p["stats"]["coverage_allowed_pct"] > 97.0


def test_drive_zone_across_the_field_stays_a_lift_strip():
    """Een rijpad dwars door het veld knipt het niet in twee: de banen lopen door, werktuig omhoog."""
    strip = [(-52, -5), (-48, -5), (-48, 65), (-52, 65)]
    w = make_world(zones=[Zone(ll_of(make_world(), strip), "alleen_rijden", "pad", "Pad")])
    a = field_area(w, "west")
    assert a.no_work is not None and a.allowed.area == pytest.approx(field_area(make_world(), "west").allowed.area)


def test_last_swath_with_too_much_overlap_is_dropped_at_the_ditch():
    """Max overlap laatste baan: een baan die te ver over zijn buur ligt vervalt, de rest-strook ligt aan de sloot."""
    from openagbot_fieldsim.world.model import Ditch
    w = make_world(ditches=[Ditch(ll_of(make_world(), [(-8, -5), (-8, 70)]), 2.0, 0.9, 0.3, "sloot_west")])

    def swath_x(pct):
        p = plan_field(w, "west", {"headland_rounds": 0, "swath_heading_deg": 0, "work_width_m": 3.0,
                                   "max_swath_overlap_pct": pct})
        return sorted({round((sg.pts[0][0] + sg.pts[-1][0]) / 2, 2) for sg in p["segments"] if " baan " in sg.label})

    full, cut = swath_x(100), swath_x(40)
    assert min(b - a for a, b in zip(full, full[1:])) < 1.5                # 100 %: laatste baan ligt half over zijn buur
    assert len(cut) == len(full) - 1
    assert min(b - a for a, b in zip(cut, cut[1:])) == pytest.approx(3.0 - 0.15, abs=0.02)
    assert cut[0] == pytest.approx(full[0]) and cut[-1] < full[-1] - 1.0   # de strook blijft aan de oostkant (sloot)


def wavy_world():
    """Veld van 120 x 80 m waarvan de oostrand slingert (30 cm uit het midden, golf van 16 m)."""
    f = LocalFrame(LAT0, LON0, 0.0)
    east = [(60 + 0.3 * math.sin(y / 16.0 * 2 * math.pi), y) for y in range(0, 81, 2)]
    ring = [(-60, 0), *east, (-60, 80)]
    ll = lambda pts: [f.to_latlon(x, y) for x, y in pts]      # noqa: E731
    return World(name="w", label="w", origin=(LAT0, LON0, 0.0), area=ll([(-90, -30), (90, -30), (90, 110), (-90, 110)]),
                 margin_m=20.0, terrain={"type": "flat"}, fields=[Field("golf", "Golf", ll(ring), "gras")])


def test_inner_headland_rounds_are_straight():
    w = wavy_world()

    def east_wiggle(p, name):
        sg = next(s for s in p["segments"] if s.label == name)
        xs = [q[0] for q in sg.pts if 15 < q[1] < 65 and q[0] > 40]
        return max(xs) - min(xs)

    wavy = plan_field(w, "golf", {"headland_rounds": 3, "headland_straight": False})
    straight = plan_field(w, "golf", {"headland_rounds": 3, "headland_straight": True})
    assert east_wiggle(wavy, "rondje 2") > 0.4 and east_wiggle(wavy, "rondje 3") > 0.4     # oud: slingert mee
    assert east_wiggle(straight, "rondje 1") > 0.4                        # het buitenste volgt de rand
    assert east_wiggle(straight, "rondje 2") < 0.1 and east_wiggle(straight, "rondje 3") < 0.1
    st = straight["stats"]
    assert st["coverage_allowed_pct"] > 99.0 and st["worked_outside_allowed_m2"] < 0.5
    assert all(sg.ok for sg in straight["segments"]) and straight["safety"]["ok"]
    # banen eindigen op een rechte kopakker: allemaal even lang aan de oostkant
    ends = [max(sg.pts[0][0], sg.pts[-1][0]) for sg in straight["segments"] if sg.type == "baan"]
    if straight["swath_heading_deg"] in (90.0, 270.0):
        assert max(ends) - min(ends) < 0.3


def test_skip_up_to_six_swaths():
    from openagbot_fieldsim.missions.field_plan import maak_baan_volgorde
    assert maak_baan_volgorde(14, 6) == [0, 7, 8, 1, 2, 9, 10, 3, 4, 11, 12, 5, 6, 13]
    p = plan_field(wavy_world(), "golf", {"headland_rounds": 2, "order": "overslaan_6", "swath_heading_deg": 0.0})
    assert {c["pattern"] for c in p["cells"]} == {"overslaan_6"}
    swaths = [sg for sg in p["segments"] if sg.type == "baan"]
    xs = sorted({round(sg.pts[0][0], 1) for sg in swaths})
    jumps = [abs(xs.index(round(b.pts[0][0], 1)) - xs.index(round(a.pts[0][0], 1))) for a, b in zip(swaths, swaths[1:])]
    assert max(jumps) == 7 and p["stats"]["coverage_allowed_pct"] > 98.0
