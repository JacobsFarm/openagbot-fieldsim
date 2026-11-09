import math

from openagbot_fieldsim.geo import LocalFrame, heading_deg, heading_to_unit, norm180


def test_roundtrip_mm():
    f = LocalFrame(52.2121, 4.5972, 0.0)
    for lat, lon in [(52.2147, 4.5889), (52.2093, 4.6064), (52.30, 4.70)]:
        x, y = f.to_xy(lat, lon)
        lat2, lon2 = f.to_latlon(x, y)
        # 1e-8 graad = ca. 1 mm
        assert abs(lat2 - lat) < 1e-8 and abs(lon2 - lon) < 1e-8


def test_distance_matches_known():
    f = LocalFrame(52.0, 4.0)
    x, y = f.to_xy(52.0 + 1e-3, 4.0)
    # 0,001 graad noord bij 52 graden = 111,26 m
    assert abs(y - 111.26) < 0.05 and abs(x) < 1e-6
    x, y = f.to_xy(52.0, 4.0 + 1e-3)
    assert abs(x - 68.67) < 0.05


def test_heading():
    assert heading_deg(0, 1) == 0.0
    assert abs(heading_deg(1, 0) - 90.0) < 1e-9
    e, n = heading_to_unit(90.0)
    assert abs(e - 1.0) < 1e-12 and abs(n) < 1e-12
    assert norm180(350.0) == -10.0
    assert math.isclose(norm180(190.0), -170.0)
