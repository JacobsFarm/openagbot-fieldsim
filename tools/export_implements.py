"""Exporteert de aanbouwdelen uit de FreeCAD-bouwscripts (F:/veldrobot/aanbouwdelen) naar .glb voor de sim.

Draaien (headless, schrijft niets in de aanbouwdelen-map):
    "C:/Program Files/FreeCAD 1.1/bin/freecadcmd.exe" tools/export_implements.py
of één werktuig:  FIELDSIM_IMPLEMENTS=sickle_bar_mower  freecadcmd tools/export_implements.py

Wat het doet, per werktuig uit openagbot_fieldsim/implements/catalog.py:
  1. het bouwscript importeren (zelfde manier als zijn run_in_freecad.py) en bouwen zonder op te slaan;
  2. per stand (werk / geheven) de vlakken tesselleren in globale coördinaten, kleur uit COLOR (add_shape wordt
     afgeluisterd, want zonder GUI heeft een object geen ViewObject); het robotmodel wordt niet meegenomen;
  3. schrijven naar openagbot_fieldsim/implements/assets/<id>/<stand>.glb.

Het glb blijft in de FreeCAD-assen (x rechts, y vooruit, z op) maar in meters, oorsprong = FreeCAD-oorsprong.
De sim zet het op de robot met `mount` uit de catalogus. Delen die los bewegen (de X-slede van de boor) komen in een
eigen node (`layers`), zodat de UI die kan verschuiven.
"""
import json
import os
import struct
import sys
import traceback

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import FreeCAD as App  # noqa: E402

from openagbot_fieldsim.implements.catalog import CATALOG, SOURCE_ROOT  # noqa: E402

OUT = os.path.join(ROOT, "openagbot_fieldsim", "implements", "assets")
LINEAR_DEFLECTION = 1.5      # mm


# ---------------------------------------------------------------------------------------------- recepten (FreeCAD-kant)
def _sbm(m):
    def work():
        return m.build(save=False, show_robot=None, step=False)

    def raised():
        doc = work()
        m.set_transport(doc, 1.0)
        doc.recompute()
        return doc
    return {"work": work, "raised": raised}


def _dwd8(m):
    P = m.P
    return {"work": lambda: m.build(x=0.0, z=-P.depth_std, save_path=None, show_robot=False),
            "raised": lambda: m.build(x=0.0, z=P.drive_z, save_path=None, show_robot=False)}


def _lfa2(m):
    return {"work": lambda: m.build(lift=0.0, save_path=None, show_robot=False),
            "raised": lambda: m.build(lift=m.P.lift_height, save_path=None, show_robot=False)}


RECIPES = {"sbm": _sbm, "dwd8": _dwd8, "lfa2": _lfa2}


def setup_lfa2(folder):
    import lfa2_params
    lfa2_params.setup_path()


SETUP = {"lfa2": setup_lfa2}


def probe_dwd8(m, doc):
    """Spilhart (midden van de frees) en de cyclustijd per plant uit dwd8_calc."""
    import dwd8_calc
    bb = App.BoundBox()
    for o in doc.getObject("Tool").OutListRecursive:
        if hasattr(o, "Shape") and not o.Shape.isNull() and o.TypeId == "Part::Feature":
            s = o.Shape.copy()
            s.Placement = o.getGlobalPlacement()
            bb.add(s.BoundBox)
    return {"tool_x_mm": round(bb.Center.x, 1), "tool_y_mm": round(bb.Center.y, 1),
            "cycle_s": round(dwd8_calc.cycle_time(), 1)}


def probe_lfa2(m, doc):
    import lfa2_calc
    return {"dose_l_ha": m.P.dose_l_ha, "disc_y_mm": m.P.disc_y, "pump_rpm_at_1ms": round(lfa2_calc.pump_rpm(), 1)}


PROBES = {"dwd8": probe_dwd8, "lfa2": probe_lfa2}


# ---------------------------------------------------------------------------------------------- glb
def _pad(b, fill=b"\x00"):
    return b + fill * ((4 - len(b) % 4) % 4)


def write_glb(path, layers):
    """layers: {node_name: {(r,g,b,a): (positions[float], indices[int])}} -> glb met één node per laag."""
    gltf = {"asset": {"version": "2.0", "generator": "openagbot_fieldsim export_implements"},
            "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
            "accessors": [], "bufferViews": [], "buffers": []}
    blob = bytearray()
    mats = {}
    for layer, groups in layers.items():
        prims = []
        for rgba, (pos, idx) in groups.items():
            if not idx:
                continue
            if rgba not in mats:
                mats[rgba] = len(gltf["materials"])
                m = {"pbrMetallicRoughness": {"baseColorFactor": list(rgba), "metallicFactor": 0.0,
                                              "roughnessFactor": 1.0}, "doubleSided": True}
                if rgba[3] < 1.0:
                    m["alphaMode"] = "BLEND"
                gltf["materials"].append(m)
            pb = struct.pack("<%df" % len(pos), *pos)
            ib = struct.pack("<%dI" % len(idx), *idx)
            xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
            for data, target in ((pb, 34962), (ib, 34963)):
                gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data),
                                            "target": target})
                blob += _pad(data)
            nv = len(gltf["bufferViews"])
            gltf["accessors"].append({"bufferView": nv - 2, "componentType": 5126, "count": len(pos) // 3,
                                      "type": "VEC3", "min": [min(xs), min(ys), min(zs)],
                                      "max": [max(xs), max(ys), max(zs)]})
            gltf["accessors"].append({"bufferView": nv - 1, "componentType": 5125, "count": len(idx),
                                      "type": "SCALAR"})
            na = len(gltf["accessors"])
            prims.append({"attributes": {"POSITION": na - 2}, "indices": na - 1, "material": mats[rgba]})
        if not prims:
            continue
        gltf["meshes"].append({"name": layer, "primitives": prims})
        gltf["nodes"].append({"name": layer, "mesh": len(gltf["meshes"]) - 1})
        gltf["scenes"][0]["nodes"].append(len(gltf["nodes"]) - 1)
    gltf["buffers"].append({"byteLength": len(blob)})
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        fh.write(struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))


# ---------------------------------------------------------------------------------------------- export
MAX_TRIS_PER_OBJECT = 1500   # schroefdraad, veren en slangen anders miljoenen driehoeken


def tessellate(shape):
    """Grover naarmate het onderdeel groter is, en grover tot het onder het budget blijft."""
    tol = max(LINEAR_DEFLECTION, shape.BoundBox.DiagonalLength / 150.0)
    while True:
        pts, tris = shape.tessellate(tol, True)
        if len(tris) <= MAX_TRIS_PER_OBJECT or tol > 40.0:
            return pts, tris
        tol *= 2.0


def ancestors(obj):
    seen, todo = set(), [obj]
    while todo:
        o = todo.pop()
        for p in o.InList:
            if p.Name not in seen:
                seen.add(p.Name)
                todo.append(p)
    return seen


def export(spec):
    folder = os.path.join(SOURCE_ROOT, spec.source)
    for k in [k for k in sys.modules if k.startswith(spec.prefix)]:
        del sys.modules[k]
    if folder in sys.path:
        sys.path.remove(folder)
    sys.path.insert(0, folder)
    if spec.prefix in SETUP:
        SETUP[spec.prefix](folder)
    m = __import__(spec.module)

    record = {}
    original = m.add_shape

    def add_shape(doc, parent, name, label, shape, color, visible=True, *a, **kw):
        obj = original(doc, parent, name, label, shape, color, visible, *a, **kw)
        tr = kw.get("transparency", a[0] if a else None)
        if tr is None:
            tr = getattr(m, "TRANSPARENT", {}).get(color, 0)
        record[obj.Name] = (m.COLOR[color], visible, tr)
        return obj
    m.add_shape = add_shape

    out = os.path.join(OUT, spec.id)
    os.makedirs(out, exist_ok=True)
    info = {}
    for pose, make in RECIPES[spec.prefix](m).items():
        record.clear()
        doc = make()
        doc.recompute()
        if pose == "work" and spec.prefix in PROBES:
            info["meta"] = PROBES[spec.prefix](m, doc)
        layers = {name: {} for name in ["frame"] + list(spec.layers)}
        ntri = 0
        bb = App.BoundBox()
        for obj in doc.Objects:
            if obj.Name not in record:
                continue
            col, visible, tr = record[obj.Name]
            if not visible or tr >= 90 or obj.Shape.isNull():
                continue
            anc = ancestors(obj)
            layer = next((ln for ln, parts in spec.layers.items() if anc & set(parts)), "frame")
            shape = obj.Shape.copy()
            shape.Placement = obj.getGlobalPlacement()
            bb.add(shape.BoundBox)
            rgba = (round(col[0], 4), round(col[1], 4), round(col[2], 4), round(1.0 - tr / 100.0, 3))
            pos, idx = layers[layer].setdefault(rgba, ([], []))
            try:
                pts, tris = tessellate(shape)
            except Exception:
                continue
            base = len(pos) // 3
            for p in pts:
                pos += (p.x / 1000.0, p.y / 1000.0, p.z / 1000.0)
            for t in tris:
                idx += (base + t[0], base + t[1], base + t[2])
            ntri += len(tris)
        path = os.path.join(out, pose + ".glb")
        write_glb(path, layers)
        info[pose] = {"triangles": ntri, "bytes": os.path.getsize(path),
                      "bbox_mm": [round(v, 1) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]}
        App.closeDocument(doc.Name)
        print(spec.id, pose, info[pose], flush=True)
    m.add_shape = original
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(info, fh, indent=1)
    return info


def main():
    only = [s for s in os.environ.get("FIELDSIM_IMPLEMENTS", "").split(",") if s]
    report = {}
    for spec in CATALOG:
        if only and spec.id not in only:
            continue
        try:
            report[spec.id] = export(spec)
        except Exception:
            traceback.print_exc()
            report[spec.id] = {"error": traceback.format_exc()}
    with open(os.path.join(OUT, "export_report.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1)
    print("klaar", json.dumps({k: list(v) for k, v in report.items()}))


main()
