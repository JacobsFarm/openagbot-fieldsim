"""AgOpenGPS-velden als (tweede) boerderij in de sim zetten, en de omrekening controleren.

  python tools/import_agopengps.py "C:/pad/naar/Fields" mijn_boerderij            nieuwe wereld uit de velden
  python tools/import_agopengps.py "C:/pad/naar/Fields" mijn_boerderij --build    + sloten, gebouwen, hoogte (PDOK)
  python tools/import_agopengps.py "C:/pad/naar/Fields" --check                   alleen controleren, niets schrijven

De map bevat een submap per veld (Field.txt, Boundary.txt, Flags.txt, Field.kml), zoals AgOpenGPS ze bewaart.

Wat er gebeurt:
  1. Elke veldmap wordt ingelezen: de buitengrens wordt een veld, binnengrenzen worden gaten, vlaggen worden markers.
  2. Controle: de omgerekende grens wordt vergeleken met de Field.kml die AgOpenGPS zelf schreef (lat/lon), en het
     veld gaat heen en terug (import -> export -> import). Beide moeten binnen een paar centimeter kloppen.
  3. De velden komen als EIGEN objecten in worlds/<naam>/farm.json; world.json krijgt alleen het gebied.
  4. Met --build haalt de wereldbouwer de kaart erbij (BGT, AHN, 3D BAG). Percelen uit de BRP die over een
     AgOpenGPS-veld heen liggen worden verborgen: jouw gemeten grens telt.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openagbot_fieldsim.api.editor_api import import_files  # noqa: E402
from openagbot_fieldsim.world import interchange as ix  # noqa: E402
from openagbot_fieldsim.world.farm import anchor_of, load_farm, save_farm  # noqa: E402

WANTED = ("field.txt", "boundary.txt", "flags.txt", "field.kml")


def read_folder(root: str) -> dict[str, dict[str, str]]:
    """{veldnaam: {bestandsnaam: tekst}} voor elke submap met een Field.txt (of de map zelf)."""
    out = {}
    cands = [root] + [os.path.join(root, d) for d in sorted(os.listdir(root))]
    for d in cands:
        if not os.path.isfile(os.path.join(d, "Field.txt")):
            continue
        files = {}
        for fn in os.listdir(d):
            if fn.lower() in WANTED:
                with open(os.path.join(d, fn), encoding="utf-8-sig", errors="replace") as fh:
                    files[fn] = fh.read()
        out[os.path.basename(os.path.normpath(d))] = files
    return out


def _m(a, b) -> float:
    k = math.cos(math.radians(a[0])) * 111320.0
    return math.hypot((a[0] - b[0]) * 111320.0, (a[1] - b[1]) * k)


def check(folders: dict[str, dict[str, str]]) -> bool:
    """Import tegen de KML van AgOpenGPS zelf, en heen-en-terug. True als alles binnen 3 cm klopt."""
    ok = True
    print(f"{'veld':22s} {'punten':>6s} {'tov KML':>9s} {'heen-terug':>11s}")
    for name, files in folders.items():
        imp = ix.aog_to_objects(files, name)
        flds = imp["objects"].get("fields", [])
        if not flds:
            print(f"{name:22s} geen grens ({len(imp['objects'].get('markers', []))} vlaggen)")
            continue
        ring = flds[0]["polygon"]
        kml_dev = None
        if "Field.kml" in files:
            kring = ix.kml_to_objects(files["Field.kml"])["objects"].get("fields", [{}])[0].get("polygon", [])
            if kring:
                kml_dev = max(min(_m(p, q) for q in kring) for p in ring[:: max(1, len(ring) // 150)])
        again = ix.aog_to_objects(ix.export_aog(flds[0], start_fix=tuple(imp["start_fix"]), spacing_m=0), name)
        ring2 = again["objects"]["fields"][0]["polygon"]
        rt = max(_m(p, q) for p, q in zip(ring, ring2)) if len(ring) == len(ring2) else float("inf")
        good = rt < 0.003 and (kml_dev is None or kml_dev < 0.03)
        ok &= good
        print(f"{name:22s} {len(ring):6d} {'-' if kml_dev is None else f'{kml_dev * 100:6.2f} cm':>9s} "
              f"{rt * 1000:8.2f} mm {'' if good else '  <-- AFWIJKING'}")
    return ok


def hide_overlapping_source_fields(folder: str, min_share: float = 0.4) -> list[str]:
    """Percelen uit de kaart (BRP) verbergen als ze grotendeels onder een eigen veld liggen."""
    import json
    from shapely.geometry import Polygon
    from openagbot_fieldsim.world.model import ensure_ids
    with open(os.path.join(folder, "world.json"), encoding="utf-8") as fh:
        base = json.load(fh)
    ensure_ids(base)
    farm = load_farm(folder)
    lat0 = base["origin"]["lat"]
    k = math.cos(math.radians(lat0)) * 111320.0

    def poly(ring):
        g = Polygon([(p[1] * k, p[0] * 111320.0) for p in ring])
        return g if g.is_valid else g.buffer(0)

    own = [poly(f["polygon"]) for f in farm["objects"].get("fields", []) if not f.get("replaces")]
    already = {(h["layer"], h["id"]) for h in farm["hidden"]}
    hidden = []
    for f in base.get("fields", []):
        g = poly(f["polygon"])
        if g.area <= 0 or ("fields", f["id"]) in already:
            continue
        share = sum(g.intersection(o).area for o in own) / g.area
        if share >= min_share:
            farm["hidden"].append({"layer": "fields", "id": f["id"], "anchor": anchor_of("fields", f)})
            hidden.append(f"{f.get('name', f['id'])} ({share * 100:.0f}% onder een eigen veld)")
    if hidden:
        save_farm(folder, farm)
    return hidden


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="map met AgOpenGPS-velden (een submap per veld)")
    ap.add_argument("world", nargs="?", help="naam van de nieuwe wereld (kleine letters, cijfers, _)")
    ap.add_argument("--label", default="", help="naam in het menu")
    ap.add_argument("--simplify", type=float, default=0.03, help="grens vereenvoudigen (m); 0 = alle punten")
    ap.add_argument("--build", action="store_true", help="kaart erbij halen (PDOK: BGT, AHN, 3D BAG)")
    ap.add_argument("--check", action="store_true", help="alleen de omrekening controleren")
    ap.add_argument("--force", action="store_true", help="bestaande farm.json van die wereld vervangen")
    args = ap.parse_args(argv)

    folders = read_folder(args.source)
    if not folders:
        print(f"geen AgOpenGPS-velden in {args.source} (verwacht submappen met Field.txt)")
        return 2
    good = check(folders)
    print("omrekening:", "klopt" if good else "WIJKT AF, kijk de regels hierboven na")
    if args.check or not args.world:
        return 0 if good else 1

    from openagbot_fieldsim.settings import load_settings
    folder = os.path.join(load_settings().worlds_dir, args.world)
    files = {f"{name}/{fn}": text for name, fs in folders.items() for fn, text in fs.items() if fn.lower() != "field.kml"}
    r = import_files(files, "agopengps", simplify_m=args.simplify)
    for line in r["report"]:
        print(" ", line)
    exists = os.path.exists(os.path.join(folder, "world.json"))
    if exists and os.path.exists(os.path.join(folder, "farm.json")) and not args.force:
        print(f"wereld {args.world} heeft al een farm.json; gebruik --force om hem te vervangen (er komt een back-up)")
        return 2
    world, farm = ix.world_from_objects(args.world, args.label or args.world.replace("_", " "), r["objects"])
    if not exists:
        world.save(folder)
    save_farm(folder, farm)
    print(f"wereld {args.world}: {len(r['objects'].get('fields', []))} velden, "
          f"{len(r['objects'].get('markers', []))} markers -> {folder}")
    if args.build:
        from openagbot_fieldsim.world.builder import Builder
        Builder(folder).build()
        for line in hide_overlapping_source_fields(folder):
            print("  verborgen (kaart):", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
