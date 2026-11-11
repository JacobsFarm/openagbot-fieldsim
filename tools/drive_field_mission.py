"""Een veldmissie laten rijden door de ECHTE OpenAgbot in de sim, zonder UI, en meten hoe goed hij hem volgt.

  .venv\\Scripts\\python tools\\drive_field_mission.py --test-field 50x35 --minutes 15 --out result.json
  .venv\\Scripts\\python tools\\drive_field_mission.py --world example --field driehoek --minutes 10

`--test-field BxL` maakt een tijdelijke wereld met één rechthoekig veld (sloot langs de lange zijde, hek langs een
korte), zodat een hele missie in een paar minuten past. `--mode missie` (standaard): de robot krijgt kaart.json en
de veldmissie en rijdt hem met zijn eigen missievolger en geofence. `--mode waypoints`: als waypoints met pure
pursuit van punt naar punt (de oude manier, om te vergelijken). Uitvoer: afwijking van het geplande pad per soort segment (rondje, baan,
bocht, overgang), hoeveel van de route gereden is, en of de robot ergens buiten het toegestane gebied kwam.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shapely.geometry import LineString, Point  # noqa: E402

from openagbot_fieldsim.agbot import robot_link  # noqa: E402
from openagbot_fieldsim.missions.field_plan import plan_field, to_veldmissie, to_waypoints  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_test_world(root: str, width: float, length: float) -> str:
    """Wereld met één rechthoekig veld van width x length m (lange zijde noord-zuid)."""
    from openagbot_fieldsim.geo import LocalFrame
    from openagbot_fieldsim.world.model import Ditch, Fence, Field, Pose, World
    lat0, lon0 = 52.0, 4.0
    f = LocalFrame(lat0, lon0, 0.0)
    m = 30.0

    def ll(pts):
        return [f.to_latlon(x, y) for x, y in pts]

    area = ll([(-m, -m), (width + m, -m), (width + m, length + m), (-m, length + m)])
    w = World(name="testveld", label=f"testveld {width:g} x {length:g} m", origin=(lat0, lon0, 0.0), area=area,
              margin_m=20.0, terrain={"type": "flat"},
              fields=[Field("testveld", "Testveld", ll([(0, 0), (width, 0), (width, length), (0, length)]), "gras")],
              ditches=[Ditch(ll([(width + 1.5, -10), (width + 1.5, length + 10)]), 2.0, 0.8, 0.3)],
              fences=[Fence(ll([(-5, length + 0.8), (width + 5, length + 0.8)]), 1.2, 4.0)],
              start_pose=Pose(*f.to_latlon(-6.0, -6.0), 0.0))
    folder = os.path.join(root, "testveld")
    w.save(folder)
    return "testveld"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default=None)
    ap.add_argument("--field", default=None)
    ap.add_argument("--test-field", default="40x30")
    ap.add_argument("--settings", default="{}")
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--lookahead", type=float, default=2.0)
    ap.add_argument("--mode", choices=("missie", "waypoints"), default="missie")
    ap.add_argument("--out", default=None)
    ap.add_argument("--agbot-dir", default=os.environ.get("FIELDSIM_AGBOT_DIR", r"F:\Agbot\Bruut_OpenAgbot"))
    a = ap.parse_args(argv)

    from openagbot_fieldsim.app import SimApp
    from openagbot_fieldsim.settings import Settings
    home = tempfile.mkdtemp(prefix="fieldsim_drive_")
    if a.world:
        worlds_root, world_name, field_id = os.path.join(ROOT, "worlds"), a.world, a.field
    else:
        worlds_root = os.path.join(home, "worlds")
        wd, ln = (float(v) for v in a.test_field.lower().split("x"))
        world_name, field_id = make_test_world(worlds_root, wd, ln), "testveld"
    s = Settings(home=home, agbot_dir=a.agbot_dir, agbot_port=free_port(), gnss_port=0, steering_port=0, can_port=0,
                 worlds_root=worlds_root, ui_port=free_port())
    app = SimApp(s)
    app.load(world_name)
    result = {"world": world_name, "field": field_id}
    try:
        end = time.time() + 60
        while time.time() < end:
            try:
                if app.agbot_request("/api/status").get("fix") == 4:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("geen RTK fix van OpenAgbot")
        sim = app.sim
        plan = plan_field(sim.world, field_id, json.loads(a.settings), sim.params)
        wps = to_waypoints(plan, sim.world)
        segs = plan["segments"]
        print(f"plan: {len(segs)} segmenten, {len(wps)} waypoints, {plan['stats']['length_m']} m, "
              f"dekking {plan['stats']['coverage_allowed_pct']} %, waarschuwingen {plan['warnings']}", flush=True)
        sent = app.send_to_agbot("testmissie", to_veldmissie(plan, sim.world, field_id), wps)
        print("naar de robot:", json.dumps(sent, ensure_ascii=True), flush=True)
        x0, y0, th = segs[0].pts[0]
        h = (90.0 - math.degrees(th)) % 360.0
        sim.reset_pose(x0 - 2.0 * math.sin(math.radians(h)), y0 - 2.0 * math.cos(math.radians(h)), h)
        time.sleep(1.5)
        speed = plan["settings"]["speed_kmh"]
        if a.mode == "missie":
            r = robot_link.start_mission(app.agbot.url, "testmissie").get("status")
        else:
            r = app.agbot_request("/api/nav/start_pure_pursuit", {"target_speed_kmh": speed,
                                                                   "lookahead_distance": a.lookahead, "gain": 1.0})
        print("start:", r, flush=True)
        result["mode"] = a.mode

        # Gepland pad per segment als lijnen, om de afwijking per soort te meten
        lines = [(sg.type, LineString([(p[0], p[1]) for p in sg.pts])) for sg in segs if len(sg.pts) >= 2]
        full = LineString([(p[0], p[1]) for sg in segs for p in sg.pts])
        drive_ok = plan["allowed"].buffer(0.05)
        half = max(plan["settings"]["work_width_m"] / 2.0, plan["settings"]["robot_half_width_m"])
        devs: dict[str, list[float]] = {}
        track = []
        outside = 0
        t_end = time.time() + a.minutes * 60.0
        last_print = 0.0
        progress_s = 0.0
        while time.time() < t_end:
            st = sim.vehicle.s
            p = Point(st.x, st.y)
            track.append((round(st.x, 3), round(st.y, 3)))
            # dichtstbijzijnde segment in de buurt van de voortgang (zoeken rond de laatst bekende plek)
            s_now = full.project(p)
            if abs(s_now - progress_s) < 25.0:
                progress_s = max(progress_s, s_now)
            d_best, kind = 1e9, None
            for k, ln in lines:
                d = ln.distance(p)
                if d < d_best:
                    d_best, kind = d, k
            devs.setdefault(kind, []).append(d_best)
            if not drive_ok.contains(p.buffer(half - 0.15)):
                outside += 1
            try:
                nav = app.agbot_request("/api/status", timeout=1.0)
            except Exception:
                nav = {}
            if time.time() - last_print > 20:
                last_print = time.time()
                print(f"{progress_s:7.1f}/{full.length:.0f} m  "
                      f"{nav.get('missie_state') if a.mode == 'missie' else nav.get('nav_state', '')}  "
                      f"{(nav.get('missie_message') if a.mode == 'missie' else nav.get('nav_message')) or ''}  "
                      f"geofence {(nav.get('geofence') or {}).get('toestand')}", flush=True)
            if progress_s > full.length - 2.0 and abs(st.v) < 0.05:
                break
            if a.mode == "missie" and nav.get("missie_state") in ("FOUT", "KLAAR") and abs(st.v) < 0.05:
                result["end_state"] = nav.get("missie_state")
                result["end_message"] = nav.get("missie_message")
                break
            time.sleep(0.2)
        app.agbot_request("/api/stop", {})

        def summ(v):
            v = sorted(v)
            return {"n": len(v), "mean_m": round(sum(v) / len(v), 3), "p95_m": round(v[int(0.95 * (len(v) - 1))], 3),
                    "max_m": round(v[-1], 3)} if v else {}

        result.update({
            "planned_m": round(full.length, 1), "driven_along_m": round(progress_s, 1),
            "done_pct": round(100.0 * progress_s / full.length, 1),
            "deviation": {k: summ(v) for k, v in devs.items()},
            "samples_outside_allowed": outside, "samples": len(track),
            "plan_stats": {k: v for k, v in plan["stats"].items() if k not in ("missed_geom", "by_type")},
            "warnings": plan["warnings"],
            "agbot_log_tail": app.agbot.status()["log"][-8:],
        })
        try:
            result["geofence"] = app.agbot_request("/api/geofence")
        except Exception:      # noqa: BLE001 - oude robotsoftware
            pass
    finally:
        app.shutdown()
    txt = json.dumps(result, indent=1, ensure_ascii=False)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(txt)
    print(json.dumps(result, indent=1, ensure_ascii=True))      # Windows-console kan geen emoji


if __name__ == "__main__":
    main()
