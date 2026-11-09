import os
import struct

import pytest

from openagbot_fieldsim.implements.catalog import ASSETS, CATALOG, get
from openagbot_fieldsim.implements.state import ImplementState
from openagbot_fieldsim.vehicle.model import VehicleState

DT = 0.005


def drive(state, v, until_y, speed, stop=None):
    """Rijdt recht naar het noorden; stop(v) -> True laat de robot stilstaan."""
    while v.y < until_y:
        v.v = 0.0 if stop and stop(v) else speed
        v.y += v.v * DT
        state.step(DT, v)


def test_catalog_has_the_three_implements():
    ids = {s.id for s in CATALOG}
    assert ids == {"sickle_bar_mower", "dockweed_drill_v8", "liquid_fertilizer_v2"}
    assert get("sickle_bar_mower").width_m == pytest.approx(3.25)
    assert get("liquid_fertilizer_v2").width_m == pytest.approx(1.0)


@pytest.mark.parametrize("spec", CATALOG, ids=lambda s: s.id)
def test_exported_models_are_glb(spec):
    """tools/export_implements.py (freecadcmd) heeft per stand een geldig glb geschreven."""
    for pose in ("work", "raised"):
        path = os.path.join(ASSETS, spec.id, pose + ".glb")
        assert os.path.exists(path), f"{path} ontbreekt: draai tools/export_implements.py"
        with open(path, "rb") as fh:
            magic, version, length = struct.unpack("<III", fh.read(12))
        assert magic == 0x46546C67 and version == 2 and length == os.path.getsize(path)


def test_mounting_follows_the_axle():
    mower, lfa = get("sickle_bar_mower"), get("liquid_fertilizer_v2")
    # voorbalk 520 mm voor de vooras, ook als de sim-robot een andere wielbasis heeft
    assert mower.to_robot(0, 1020, 1.2)[1] == pytest.approx(1.72)
    assert mower.to_robot(0, 1020, 1.0)[1] == pytest.approx(1.52)
    # injecteur hangt aan de achteras: schijven 0,805 m erachter
    assert lfa.to_robot(0, -730, 1.2)[1] == pytest.approx(-0.805)


def test_strip_area_and_liquid():
    st = ImplementState(get("liquid_fertilizer_v2"), 1.2)
    v = VehicleState(surface="field")
    drive(st, v, 10.0, 0.75)
    snap = st.snapshot()
    assert snap["area_ha"] == pytest.approx(10.0 * 1.0 / 1e4, rel=1e-3)
    assert snap["liquid_l"] == pytest.approx(500.0 * 10.0 / 1e4, rel=1e-3)
    assert snap["strip"]["total"] > 30


def test_auto_mode_lifts_outside_the_field():
    st = ImplementState(get("sickle_bar_mower"), 1.2)
    v = VehicleState(surface="yard")
    drive(st, v, 5.0, 1.0)
    assert not st.lowered and st.area_m2 == 0.0
    st.mode = "down"
    drive(st, v, 10.0, 1.0)
    assert st.lowered and st.area_m2 == pytest.approx(5.0 * 3.25, rel=3e-3)


def test_drill_needs_the_robot_to_stop():
    st = ImplementState(get("dockweed_drill_v8"), 1.2)
    tool_y = st.sections[0][2]
    st.set_targets([(0.2, 5.0), (-0.1, 8.0), (0.9, 11.0), (30.0, 3.0)])
    v = VehicleState(surface="field")
    waited = {"t": 0.0}

    def stop_at_first(v):
        if abs(v.y + tool_y - 5.0) < 0.02 and waited["t"] < st.cycle_s + 1.0:
            waited["t"] += DT
            return True
        return False

    drive(st, v, 20.0, 0.4, stop_at_first)
    status = [t["status"] for t in st.targets]
    assert status == ["done", "missed_speed", "out_of_reach", "open"]   # de verre plant is nooit gepasseerd
    assert st.targets[0]["dx"] == pytest.approx(0.2, abs=0.01)
