"""Veldmissie als plaatje (SVG, en PNG via ffmpeg als die er is), om een plan te bekijken zonder de sim.

  .venv\\Scripts\\python tools\\render_field_plan.py example zuidkavel out.svg [instellingen als json]

Kleuren: perceel grijs, toegestaan gebied lichtgroen, rand per soort (sloot blauw, weg zwart, hek bruin, erf grijs,
buurperceel geel, open groen), rondjes blauw, banen groen, bochten oranje, overgangen paars, gemist rood.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openagbot_fieldsim.missions.field_plan import plan_field, plan_view  # noqa: E402
from openagbot_fieldsim.world.model import load_world  # noqa: E402

EDGE = {"water": "#2f7fd6", "weg": "#222", "verhard": "#888", "hek": "#8a5a2b", "gebouw": "#c0392b",
        "boom": "#2e7d32", "perceel": "#e0b000", "open": "#6ab04c"}
SEG = {"rondje": "#1f5fbf", "baan": "#2e8b3a", "bocht": "#f08c00", "overgang": "#8e44ad", "aanrijden": "#555"}


def svg(view: dict, field_xy, size=1100) -> str:
    xs = [p[0] for p in field_xy]
    ys = [p[1] for p in field_xy]
    x0, x1, y0, y1 = min(xs) - 5, max(xs) + 5, min(ys) - 5, max(ys) + 5
    sc = size / max(x1 - x0, y1 - y0)
    W, H = (x1 - x0) * sc, (y1 - y0) * sc + 70

    def P(p):
        return f"{(p[0] - x0) * sc:.1f},{(y1 - p[1]) * sc:.1f}"

    def path(pts, closed=False):
        return "M" + " L".join(P(p) for p in pts) + (" Z" if closed else "")

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
           f'viewBox="0 0 {W:.0f} {H:.0f}" font-family="Segoe UI, Arial" font-size="14">',
           '<rect width="100%" height="100%" fill="#fff"/>',
           f'<path d="{path(field_xy, True)}" fill="#e9ece6" stroke="#999" stroke-width="1"/>']
    for poly in view["allowed"]:
        d = " ".join(path(r, True) for r in poly)
        out.append(f'<path d="{d}" fill="#d6efc7" fill-rule="evenodd" stroke="none"/>')
    for poly in view["missed"]:
        d = " ".join(path(r, True) for r in poly)
        out.append(f'<path d="{d}" fill="#e74c3c" fill-opacity="0.55" fill-rule="evenodd" stroke="none"/>')
    w = view["settings"]["work_width_m"] * sc
    for sg in view["segments"]:
        if sg["implement"] == "omlaag":
            out.append(f'<path d="{path(sg["pts"])}" fill="none" stroke="{SEG[sg["type"]]}" stroke-opacity="0.13" '
                       f'stroke-width="{w:.1f}" stroke-linecap="butt" stroke-linejoin="round"/>')
    for sg in view["segments"]:
        dash = '' if sg["ok"] else ' stroke-dasharray="4 3"'
        col = SEG[sg["type"]] if sg["ok"] else "#d00"
        out.append(f'<path d="{path(sg["pts"])}" fill="none" stroke="{col}" stroke-width="1.4"{dash}/>')
    for e in view["edges"]:
        out.append(f'<path d="{path(e["pts"])}" fill="none" stroke="{EDGE.get(e["kind"], "#000")}" '
                   f'stroke-width="4" stroke-linecap="round"/>')
    if view["segments"]:
        p = view["segments"][0]["pts"][0]
        out.append(f'<circle cx="{P(p).split(",")[0]}" cy="{P(p).split(",")[1]}" r="6" fill="#000"/>')
    st = view["stats"]
    y = (y1 - y0) * sc + 22
    txt = (f'{view["rounds"]} rondjes ({view["settings"]["headland_order"]}), banen {view["swath_heading_deg"]}°, '
           f'R {view["turn_radius_m"]} m, werkbreedte {view["settings"]["work_width_m"]} m  |  '
           f'dekking {st["coverage_allowed_pct"]}% van toegestaan, {st["coverage_field_pct"]}% van perceel, '
           f'{st["time_min"]} min, rendement {st["efficiency_pct"]}%')
    out.append(f'<text x="8" y="{y:.0f}">{txt}</text>')
    leg = "  ".join(f'<tspan fill="{c}">■ {k}</tspan>' for k, c in {**SEG, **{"rand " + k: v for k, v in EDGE.items()}}.items())
    out.append(f'<text x="8" y="{y + 24:.0f}" font-size="12">{leg}</text>')
    out.append("</svg>")
    return "\n".join(out)


EDGE_EXE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def to_png(svg_path: str, png_path: str, svg_text: str) -> bool:
    """SVG -> PNG met Edge of Chrome headless (Windows); geeft False als er geen browser is."""
    import re
    import tempfile
    exe = next((e for e in EDGE_EXE if os.path.exists(e)), None) or shutil.which("chrome") or shutil.which("chromium")
    if not exe:
        return False
    m = re.search(r'width="(\d+)" height="(\d+)"', svg_text)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1200, 1200)
    import time
    if os.path.exists(png_path):
        os.remove(png_path)
    prof = tempfile.mkdtemp(prefix="fieldplan_edge_")
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                    f"--user-data-dir={prof}", f"--window-size={w},{h}", f"--screenshot={os.path.abspath(png_path)}",
                    "file:///" + os.path.abspath(svg_path).replace("\\", "/")], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    for _ in range(100):                 # Edge start een tweede proces en komt direct terug
        if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
            time.sleep(0.3)
            break
        time.sleep(0.2)
    shutil.rmtree(prof, ignore_errors=True)
    return os.path.exists(png_path)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    world_name, field_id, out_path = a[0], a[1], a[2]
    settings = json.loads(a[3]) if len(a) > 3 else {}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    w = load_world(os.path.join(here, "worlds", world_name))
    plan = plan_field(w, field_id, settings)
    view = plan_view(plan)
    s = svg(view, w.field_by_id(field_id).xy)
    svg_path = out_path if out_path.endswith(".svg") else os.path.splitext(out_path)[0] + ".svg"
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(s)
    if out_path.endswith(".png"):
        to_png(svg_path, out_path, s)
    print(json.dumps({k: v for k, v in view["stats"].items() if k != "by_type"}, ensure_ascii=False))
    for wmsg in view["warnings"]:
        print("let op:", wmsg)


if __name__ == "__main__":
    main()
