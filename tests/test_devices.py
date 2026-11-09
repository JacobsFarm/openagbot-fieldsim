"""De emulators getest tegen de ECHTE drivers van OpenAgbot (gps_logic, stepper_logic, vesc_can)."""
import time

import pytest

from conftest import load_agbot_module

from openagbot_fieldsim.devices import nmea, ubx
from openagbot_fieldsim.devices.can_link import SimCanBus, register
from openagbot_fieldsim.devices.gnss import GnssEmulator, GnssTruth
from openagbot_fieldsim.devices.steering import SteeringEmulator
from openagbot_fieldsim.devices.vesc import STATUS_1, STATUS_5, VescEmulator, WheelLoad


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_nmea_checksums_pass_agbot_parser():
    gl = load_agbot_module("app/hardware/gps_logic.py", "agbot_gps_logic")
    for raw in (nmea.gga(time.time(), 52.1, 4.2, 4, 20, 0.5, 1.0, 1.2), nmea.ths(123.45), nmea.vtg(10.0, 3.0),
                nmea.rmc(time.time(), 52.1, 4.2, 3.0, 10.0)):
        assert gl.GpsSystem._checksum_ok(raw.decode().strip())
    lat = gl.GpsSystem._nmea_naar_decimaal("5206.12345678", "N")
    assert abs(lat - (52 + 6.12345678 / 60)) < 1e-12


def test_ubx_frame_matches_agbot():
    gl = load_agbot_module("app/hardware/gps_logic.py", "agbot_gps_logic")
    assert ubx.frame(0x06, 0x8A, b"\x01\x02") == gl.GpsSystem._ubx_frame(0x06, 0x8A, b"\x01\x02")


def test_gnss_emulator_with_real_driver():
    gl = load_agbot_module("app/hardware/gps_logic.py", "agbot_gps_logic")
    emu = GnssEmulator(0, heading_offset_deg=1.1, pitch_offset_deg=1.1)
    cfg = {"hardware": {"gps_port": emu.port.url}, "ntrip": {"host": ""},
           "gnss": {"heading_offset_deg": 1.1, "pitch_offset_deg": 1.1, "antenna_offset_forward_m": 0.0,
                    "antenna_offset_right_m": 0.0, "daheading": True, "latency_compensation": False}}
    gps = gl.GpsSystem(cfg)
    try:
        assert wait_for(lambda: emu.port.connected)
        for _ in range(30):
            emu.emit(GnssTruth(time.time(), 52.0001, 4.0002, 1.0, rel_n=0.70, rel_e=0.70, rel_d=0.0,
                               speed_mps=0.0, cog_deg=45.0))
            time.sleep(0.04)
        assert wait_for(lambda: gps.antenne_positie["fix"] == 4)
        assert abs(gps.antenne_positie["lat"] - 52.0001) < 1e-6
        # Montagefout erin, correctie in de driver eruit: netto weer 45 graden
        assert abs(((gps.current_heading - 45.0 + 180) % 360) - 180) < 0.5
        assert gps.heading_bron == "THS"
        assert wait_for(lambda: gps.dah is not None)
        assert gps.dah["oplossing"] == "fixed" and abs(gps.dah["pitch_deg"]) < 0.05
        assert abs(gps.dah["baseline_m"] - 0.99) < 0.01
        assert wait_for(lambda: "06-8A" in emu.rx_messages)    # CFG-VALSET kwam aan en kreeg een ACK
    finally:
        gps.close()
        emu.close()


def test_steering_emulator_with_real_driver():
    sl = load_agbot_module("app/hardware/stepper_logic.py", "agbot_stepper_logic")
    emu = SteeringEmulator(0)
    cfg = {"hardware": {"arduino_stepper_port": emu.port.url},
           "steering": {"max_angle_degrees": 30.0, "invert_left": True, "invert_right": True,
                        "min_step_delay_us": 300, "start_step_delay_us": 1200, "ramp_steps": 400}}
    st = sl.StepperSteering(cfg)
    try:
        assert emu.max_angle == 30.0 and emu.min_delay_us == 300
        st.set_angles(10.0, 12.0)
        # motorframe: omgekeerd; afgerond op hele stappen (44,4 per graad) zoals de firmware
        assert emu.targets() == pytest.approx((-10.0, -12.0), abs=0.02)
        for _ in range(200):
            emu.step(0.005)
        assert st.get_positions() == pytest.approx((10.0, 12.0), abs=0.02)   # terug in het voertuigframe
        status = st.get_status()
        assert status["moving"] is False and status["max_angle_degrees"] == 30.0
        st.set_angles(45.0, 45.0, force=True)            # driver begrenst zelf al op 30
        assert emu.targets() == pytest.approx((-30.0, -30.0), abs=0.02)
    finally:
        st.close()
        emu.close()


def test_steering_motion_time():
    emu = SteeringEmulator(0)
    try:
        emu.process("A:30")
        t = 0.0
        while emu.motors[0].current != emu.motors[0].target and t < 5:
            emu.step(0.005)
            t += 0.005
        # 1333 stappen: ramp op en af (2 x 400 stappen, gemiddeld 750 us) + 533 op 300 us = ca. 0,76 s
        assert 0.6 < t < 0.9
    finally:
        emu.close()


def test_vesc_frames_with_real_driver():
    vc = load_agbot_module("app/hardware/vesc_can.py", "agbot_vesc_can")
    vesc = VescEmulator(1, "links", erpm_per_mps=2349.3)
    vesc.handle(3, (2000).to_bytes(4, "big", signed=True), now=0.0)
    for i in range(200):
        vesc.step(0.005, i * 0.005, WheelLoad(100.0), 54.0, 0.6)
    node = vc.VescNode(bus=None, vesc_id=1, name="links")
    for cmd, _id, data in vesc.status_frames(54.0):
        node.handle(cmd, data, 0.0)
    assert abs(node.erpm - 2000) < 5 and node.v_in == 54.0
    assert node.motor_current > 0
    assert STATUS_1 in (9,) and STATUS_5 == 27


def test_vesc_below_min_erpm_coasts():
    vesc = VescEmulator(1, "links", erpm_per_mps=2349.3)
    vesc.erpm = 1500.0
    vesc.handle(3, (500).to_bytes(4, "big", signed=True), now=0.0)
    for i in range(100):
        vesc.step(0.005, i * 0.005, WheelLoad(100.0), 54.0, 0.6)
    # 0,5 s uitrollen met 0,6 m/s2 = 0,3 m/s = ca. 700 eRPM minder, niet naar 500 geregeld
    assert vesc.erpm == pytest.approx(1500.0 - 0.3 * 2349.3, abs=20)
    for i in range(100, 400):
        vesc.step(0.005, i * 0.005, WheelLoad(100.0), 54.0, 0.6)
    assert vesc.erpm == 0.0


def test_simcan_roundtrip():
    import can
    from openagbot_fieldsim.devices.can_link import CanHub
    got = []
    hub = CanHub(0, lambda arb, data: got.append((arb, data)))
    register()
    bus = can.Bus(interface="simcan", channel=f"127.0.0.1:{hub.port.port}", bitrate=500000)
    try:
        assert wait_for(lambda: hub.port.connected)
        bus.send(can.Message(arbitration_id=(3 << 8) | 1, data=b"\x00\x00\x07\xd0", is_extended_id=True))
        assert wait_for(lambda: got)
        assert got[0] == ((3 << 8) | 1, b"\x00\x00\x07\xd0")
        hub.send((9 << 8) | 1, b"\x01\x02\x03\x04\x05\x06\x07\x08")
        msg = bus.recv(timeout=2.0)
        assert msg.arbitration_id == (9 << 8) | 1 and bytes(msg.data) == b"\x01\x02\x03\x04\x05\x06\x07\x08"
    finally:
        bus.shutdown()
        hub.close()
