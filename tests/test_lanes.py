"""Rijpaden: vaste rijlijnen met een zone. Bochten afronden, het netwerk, oppakken op de startplek, weigeren van een
bocht die niet in de zone past, en wat er naar de robot gaat."""
import math

import pytest

shapely = pytest.importorskip("shapely")

from shapely.geometry import LineString, Point  # noqa: E402

from openagbot_fieldsim.missions.farm_route import FarmGraph, farm_view, plan_farm, to_veldmissie_farm  # noqa: E402
from openagbot_fieldsim.missions.field_plan import PlanSettings  # noqa: E402
from openagbot_fieldsim.missions.lanes import fillet, lane_corridor  # noqa: E402
from openagbot_fieldsim.missions.safety import hazards  # noqa: E402
from openagbot_fieldsim.world.model import Passage  # noqa: E402
from test_farm import ll_of, make_world  # noqa: E402

FAST = {"headland_rounds": 1}


def lane(world, name, pts, width=5.0, **kw):
    return Passage(ll_of(world, pts), width, "rijpad", id=name, name=name, **kw)


def lane_world(width=5.0, extra=(), **kw):
    """De testboerderij (twee velden, sloot ertussen, erf eronder) met een rijpad over het erf en een aftakking
    naar elk veld. De robot staat op (-60, -20): op het erfpad."""
    w = make_world()
    w.passages += [lane(w, "erfpad", [(-80, -20), (80, -20)], width, **kw),
                   lane(w, "naar_west", [(-50, -20), (-50, 12)], width),
                   lane(w, "naar_oost", [(50, -20), (50, 12)], width), *extra]
    w.__post_init__()
    return w


def transit(plan):
    return [sg for sg in plan["segments"] if sg.type in ("transport", "doorgang")]


# ---------------------------------------------------------------------------------------------------- meetkunde
def test_fillet_rounds_a_corner_on_the_turning_circle():
    pts, bad = fillet([(0, 0), (10, 0), (10, 10)], 2.0, 0.2)
    assert not bad and pts[0] == (0, 0) and pts[-1] == (10, 10)
    line = LineString(pts)
    assert line.distance(Point(10, 0)) == pytest.approx(2.0 * (math.sqrt(2) - 1), abs=0.02)   # hoek afgesneden
    assert all(Point(p).distance(Point(8, 2)) >= 2.0 - 1e-6 for p in pts)                     # nooit binnen de boog
    assert line.length == pytest.approx(16.0 + math.pi, abs=0.05)
    # De boog raakt aan beide benen: de koers verspringt nergens meer dan een stapje
    heads = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in zip(pts, pts[1:])]
    assert max(abs(b - a) for a, b in zip(heads, heads[1:])) < 0.12


def test_fillet_refuses_what_does_not_fit():
    _, bad = fillet([(0, 0), (1, 0), (1, 10)], 2.0)              # been van 1 m, 2 m nodig
    assert bad and bad[0]["need_m"] == 2.0 and bad[0]["room_m"] == 1.0
    _, bad = fillet([(0, 0), (10, 0), (0, 0.5)], 2.0)            # keren op de plaats
    assert bad and bad[0]["need_m"] is None
    pts, bad = fillet([(0, 0), (10, 0), (20, 0.1)], 2.0)         # knikje van een halve graad: recht door
    assert not bad and len(pts) == 3
    _, bad = fillet([(0, 0), (6, 0), (6, 3), (12, 3)], 2.0)      # twee bochten delen een been van 3 m: past niet
    assert len(bad) == 2
    _, bad = fillet([(0, 0), (6, 0), (6, 4), (12, 4)], 2.0)      # met 4 m wel
    assert not bad


# ---------------------------------------------------------------------------------------------------- netwerk
def test_lanes_replace_free_driving_and_form_a_network():
    w = lane_world()
    g = FarmGraph(w, PlanSettings())
    assert g.strict and {r.kind for r in g.regions} == {"veld"}            # geen vrij rijgebied meer op het erf
    assert set(g.lanes) == {"erfpad", "naar_west", "naar_oost"} and not g.lane_notes
    # erfpad is geknipt op de twee T-splitsingen; de aftakkingen op de rand van hun veld
    assert sorted(k for k in g.piece_lane if k.startswith("erfpad")) == ["erfpad#0", "erfpad#1", "erfpad#2"]
    view = {p["id"]: p for p in g.view()["passages"]}
    assert view["naar_west"]["connects"] == ["West"] and view["naar_oost"]["connects"] == ["Oost"]
    assert view["erfpad"]["usable"] and view["erfpad"]["connects"] == []
    assert view["hek_west"]["usable"]                                      # het hek sluit nu op het rijpad aan


def test_mission_drives_only_on_lanes_between_fields():
    w = lane_world()
    p = plan_farm(w, None, FAST)
    g = p["graph"]
    assert p["order"] == ["west", "oost"] and not p["unreachable"] and p["safety"]["ok"]
    assert any("pakt de lijn" in t for t in p["warnings"])                 # start op het erfpad
    lanes_used = [sg for sg in p["segments"] if sg.meta.get("soort") == "rijpad"]
    assert lanes_used and all(sg.ok for sg in lanes_used)
    fields = [r.geom.buffer(g.half + 0.1) for r in g.regions]
    for sg in transit(p):                                                  # buiten de velden alleen in de zones
        if sg.type == "doorgang" and sg.meta.get("soort") != "rijpad":
            continue                                                       # de dam tussen de velden
        swept = LineString([(q[0], q[1]) for q in sg.pts]).buffer(g.half)
        assert g.lane_ok.covers(swept), sg.label
        if sg.type == "transport":
            assert any(f.covers(swept) for f in fields), sg.label
    # De lijn wordt strikt gevolgd: buiten de afgeronde bochten ligt de route OP de getekende lijn
    drawn = LineString([(-80, -20), (80, -20)]).union(LineString([(-50, -20), (-50, 12)])).union(
        LineString([(50, -20), (50, 12)]))
    for sg in lanes_used:
        assert drawn.distance(Point(sg.pts[0][0], sg.pts[0][1])) < 0.01    # begint en eindigt op de lijn
        assert drawn.distance(Point(sg.pts[-1][0], sg.pts[-1][1])) < 0.01
        assert max(drawn.distance(Point(q[0], q[1])) for q in sg.pts) < g.R * (math.sqrt(2) - 1) + 0.05
    assert "erfpad" in p["passages_used"] and "naar_oost" in p["passages_used"]


def test_field_to_field_takes_the_shortest_way_to_the_lane():
    w = lane_world()
    w.passages = [q for q in w.passages if q.id != "dam"]                  # alleen nog via de rijpaden
    w.__post_init__()
    p = plan_farm(w, ["oost"], FAST, start_xy=(-50.0, 30.0), return_home=False)   # robot staat in West
    assert p["order"] == ["oost"]
    first = transit(p)[0]
    assert first.type == "transport" and first.meta["gebied"] == "West"
    end = first.pts[-1]
    assert abs(end[0] + 50.0) < 0.05                                       # recht naar de lijn van naar_west
    names = [sg.label for sg in transit(p) if sg.meta.get("soort") == "rijpad"]
    assert names == ["rijpad naar_west > erfpad > naar_oost"]


# ---------------------------------------------------------------------------------------------------- oppakken
def test_start_outside_the_zone_is_refused():
    w = lane_world()
    with pytest.raises(ValueError, match="met de hand"):
        plan_farm(w, None, FAST, start_xy=(-70.0, -12.0))                  # op het erf, 8 m naast het rijpad
    g = FarmGraph(w, PlanSettings(), start_xy=(-70.0, -12.0))
    assert g.start is None and "erfpad" in g.start_error and "8.0 m" in g.start_error


def test_start_inside_the_zone_picks_up_the_line():
    w = lane_world()
    p = plan_farm(w, ["west"], FAST, start_xy=(-70.0, -18.2))              # 1,8 m naast de lijn, zone is 2,5 m
    first = transit(p)[0]
    assert first.meta.get("soort") == "rijpad"
    assert first.pts[0][0] == pytest.approx(-70.0, abs=0.01) and first.pts[0][1] == pytest.approx(-20.0, abs=0.01)
    last = transit(p)[-1]                                                  # en hij komt daar ook weer terug
    assert last.pts[-1][0] == pytest.approx(-70.0, abs=0.01)


def test_start_in_a_field_is_always_fine():
    w = lane_world()
    p = plan_farm(w, ["west"], FAST, start_xy=(-50.0, 30.0), return_home=False)
    assert p["order"] == ["west"] and not any(sg.type == "doorgang" for sg in p["segments"])


# ---------------------------------------------------------------------------------------------------- weigeren
def test_turn_that_leaves_the_zone_is_refused():
    w = lane_world(width=3.2)                                              # machine 3,0 m breed: recht past net
    w.passages = [q for q in w.passages if q.id != "hek_west"]
    w.__post_init__()
    g = FarmGraph(w, PlanSettings(transport_width_m=3.0), start_xy=(-60.0, -20.0))
    assert set(g.lanes) == {"erfpad", "naar_west", "naar_oost"}            # recht rijden past
    a = next(k for k, n in g.nodes.items() if k[0].startswith("erfpad") and isinstance(n["region"], str)
             and abs(n["xy"][0] + 50) < 0.1 and k[1] == 1)
    b = next(k for k, n in g.nodes.items() if k[0].startswith("naar_west") and isinstance(n["region"], str))
    assert not g._turn_ok(a, b)                                            # de bocht van 90 graden niet
    with pytest.raises(ValueError, match="geen enkel veld"):
        plan_farm(w, None, {**FAST, "transport_width_m": 3.0})
    # Zelfde bocht in een bredere zone, of met een smallere machine: wel
    assert plan_farm(lane_world(width=5.0), ["west"], {**FAST, "transport_width_m": 3.0})["order"] == ["west"]
    assert plan_farm(w, ["west"], {**FAST, "transport_width_m": 1.4})["order"] == ["west"]


def test_sharp_corner_inside_a_lane_is_reported():
    w = make_world()
    w.passages += [lane(w, "haaks", [(-80, -20), (-50, -20), (-50.5, -19), (-50, 12)])]
    w.__post_init__()
    g = FarmGraph(w, PlanSettings())
    assert "haaks" in g.lane_notes and "bocht" in g.lane_notes["haaks"]
    assert any(t.startswith("rijpad haaks") for t in g.warnings)
    assert "haaks#0" in g.unusable and "haaks#0" not in g.passages          # het stuk met de knik valt af
    with pytest.raises(ValueError, match="geen enkel veld"):
        plan_farm(w, ["west"], FAST, start_xy=(-70.0, -20.0))


def test_lane_never_opens_a_ditch():
    w = make_world()
    w.passages += [lane(w, "over_sloot", [(-30, 50), (30, 50)])]
    w.__post_init__()
    g = FarmGraph(w, PlanSettings())
    assert "sloot" in g.unusable["over_sloot"] and "over_sloot" not in g.lanes
    water = [h for h in hazards(w) if h.kind == "water"]
    assert any(h.geom.covers(Point(0, 50)) for h in water)                 # de sloot is daar niet weggehaald
    assert any(not h.geom.covers(Point(0, 30)) for h in water)             # bij de dam wel


def test_narrow_zone_and_one_way():
    w = lane_world(extra=())
    next(q for q in w.passages if q.id == "naar_oost").width_m = 2.0
    g = FarmGraph(w, PlanSettings(transport_width_m=3.0))
    assert "te smal" in g.unusable["naar_oost"]
    w = lane_world(direction="terug")                                      # erfpad alleen van oost naar west
    w.passages = [q for q in w.passages if q.id not in ("dam", "hek_west")]
    w.__post_init__()
    p = plan_farm(w, ["west"], FAST, start_xy=(50.0, 30.0), return_home=False)     # van Oost naar West: mag
    assert p["order"] == ["west"]
    p = plan_farm(w, ["oost", "west"], FAST, start_xy=(-50.0, 30.0), return_home=False, order="gegeven")
    assert p["order"] == ["west"] and p["unreachable"][0]["field"] == "oost"       # tegen de richting in: niet


def test_transit_mode_free_keeps_the_yard():
    w = lane_world()
    w.farm_settings = {"transit_mode": "vrij"}
    g = FarmGraph(w, PlanSettings())
    assert not g.strict and "rijgebied" in {r.kind for r in g.regions} and g.lanes
    w = make_world()
    w.farm_settings = {"transit_mode": "rijpaden"}                         # strikt zonder rijpaden: alleen de dam
    assert FarmGraph(w, PlanSettings()).strict


# ---------------------------------------------------------------------------------------------------- naar de robot
def test_export_gives_the_robot_line_and_zone():
    w = lane_world()
    p = plan_farm(w, None, FAST)
    vm = to_veldmissie_farm(p, w)
    by_id = {d["id"]: d for d in vm["doorgangen"]}
    assert by_id["erfpad"]["soort"] == "rijpad" and by_id["erfpad"]["breedte_m"] == 5.0
    segs = [s for s in vm["segmenten"] if s["type"] == "doorgang" and s.get("doorgang") in ("erfpad", "naar_west",
                                                                                         "naar_oost")]
    assert segs                               # de robot bewaakt op zo'n segment de breedte van de zone
    # Het toegestane gebied van de geofence bevat de zones: daarbuiten stopt hij
    f = w.frame
    allowed = [shapely.geometry.Polygon([f.to_xy(lat, lon) for lat, lon in a["polygon"]]) for a in
               vm["geofence"]["toegestaan"]]
    union = shapely.ops.unary_union(allowed)
    zone = lane_corridor(next(q for q in w.passages if q.id == "erfpad"))
    assert union.buffer(0.2).covers(zone.intersection(LineString([(-50, -20), (50, -20)]).buffer(2.4)))
    assert not union.covers(Point(0, -12))    # het erf naast het rijpad hoort er niet bij
    view = farm_view(p)
    assert view["graph"]["strict"] and any(s["meta"].get("soort") == "rijpad" for s in view["segments"])


def test_fillet_merges_hand_drawn_corner():
    """Een bocht getekend als rijtje knikjes krapper dan de draaicirkel wordt één bocht die wel past."""
    from openagbot_fieldsim.missions.lanes import fillet
    pts = [(0, 0), (0, 25), (0.2, 25.5), (0.6, 25.8), (1.0, 26.0), (1.4, 26.0), (40, 26)]
    smooth, bad = fillet(pts, 3.0)
    assert not bad
    assert smooth[0] == (0.0, 0.0) and smooth[-1] == (40.0, 26.0)
    from shapely.geometry import LineString, Point
    assert max(LineString(pts).distance(Point(q)) for q in smooth) < 1.0


def test_transport_width_not_work_width():
    """Een brede werkbreedte (camera, boom) maakt de machine onderweg niet breder: de transportbreedte telt."""
    from openagbot_fieldsim.missions.farm_route import FarmGraph
    from openagbot_fieldsim.missions.field_plan import PlanSettings
    w = lane_world(width=2.0)
    assert FarmGraph(w, PlanSettings(work_width_m=3.0)).half == 0.6
    ok = FarmGraph(w, PlanSettings(work_width_m=3.0)).view()
    assert all(p["usable"] for p in ok["passages"] if p["kind"] == "rijpad")
    wide = FarmGraph(w, PlanSettings(work_width_m=3.0, transport_width_m=3.0)).view()
    assert any("zone te smal" in (p["reason"] or "") for p in wide["passages"] if p["kind"] == "rijpad")


def test_bend_width_widens_only_the_bends():
    """`bend_width_m`: de zone is alleen rond knikken en aan de einden breder; de bocht past dan in een smal pad."""
    from shapely.geometry import Point
    from openagbot_fieldsim.missions.lanes import bend_pieces, lane_corridor
    from openagbot_fieldsim.world.model import Passage
    p = Passage([], 2.0, "rijpad", id="x")
    p.xy = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0)]
    assert bend_pieces(p) == []
    narrow = lane_corridor(p)
    p.bend_width_m = 6.0
    zone = lane_corridor(p)
    assert len(bend_pieces(p)) == 3                                        # begin, knik, eind
    assert zone.covers(Point(38.0, 2.5)) and not narrow.covers(Point(38.0, 2.5))     # in de bocht breder
    assert not zone.covers(Point(20.0, 2.5)) and zone.covers(Point(20.0, 0.9))      # rechte stuk blijft smal
    d = p.to_dict()
    assert d["bend_width_m"] == 6.0

    w = lane_world(width=2.0)
    s = {**FAST, "transport_width_m": 1.2}
    with pytest.raises(ValueError):
        plan_farm(w, ["west"], s)
    for q in w.passages:
        if q.kind == "rijpad":
            q.bend_width_m = 6.0
    assert plan_farm(w, ["west"], s)["order"] == ["west"]
