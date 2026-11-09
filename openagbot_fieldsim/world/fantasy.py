"""De fantasiewereld `example`: een boerderij met erf en vijf velden, sloten, een weg, bomen en zacht reliëf.

Verzonnen, maar op Nederlandse maat: een gemengd bedrijf in een polder, velden van 2,5 tot 5 ha, sloten van 2-3 m
breed, een erf met woonhuis, ligboxenstal, werktuigenloods, mestsilo en voersilo. De plek (52,0 N / 4,0 O) ligt in
zee, zodat er nooit verwarring is met een echt perceel.

Opnieuw maken:  python -m openagbot_fieldsim.world.fantasy  (schrijft worlds/example/world.json)
"""
from __future__ import annotations

import os

from openagbot_fieldsim.geo import LocalFrame
from openagbot_fieldsim.world.geometry import circle, rectangle
from openagbot_fieldsim.world.model import Building, Ditch, Fence, Field, Pose, Roof, Surface, Tree, World

ORIGIN = (52.0, 4.0, 0.0)


def build() -> World:
    f = LocalFrame(*ORIGIN)

    def ll(pts):
        return [f.to_latlon(x, y) for x, y in pts]

    def box(x1, y1, x2, y2):
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

    # ---------------------------------------------------------------- velden
    fields = [
        Field("achterkamp", "Achterkamp", ll(box(-260, 130, -10, 300)), "wintertarwe"),
        Field("lange_akker", "Lange akker", ll(box(10, 230, 420, 300)), "suikerbieten"),
        Field("driehoek", "Driehoek", ll([(10, 131), (330, 131), (250, 215), (10, 215)]), "maïs"),
        Field("weide", "Weide", ll(box(-260, -125, -62, 106)), "grasland"),
        Field("zuidkavel", "Zuidkavel", ll([(-200, -163), (220, -163), (240, -330), (-180, -312)]), "aardappelen"),
    ]

    # ---------------------------------------------------------------- vlakken
    yard = box(-50, -120, 50, -30)
    surfaces = [
        Surface("road", ll(box(-300, -153, 460, -147)), "Polderweg"),
        Surface("yard", ll(yard), "Erf"),
        Surface("paved", ll(box(-4, -147, 4, -120)), "Inrit"),
        Surface("track", ll(box(-3, -30, 3, 310)), "Kavelpad noord"),
        Surface("track", ll(box(-265, 115, 335, 125)), "Kavelpad dwars"),
        Surface("track", ll(box(94, -163, 106, -153)), "Dam zuidkavel"),
        Surface("paved", ll(box(-10, -30, 40, -18)), "Voerpad"),
    ]

    # ---------------------------------------------------------------- sloten (met dammen waar paden kruisen)
    ditches = [
        Ditch(ll([(-300, -143.5), (-6, -143.5)]), 2.5, 0.9),
        Ditch(ll([(6, -143.5), (460, -143.5)]), 2.5, 0.9),
        Ditch(ll([(-300, -157.5), (92, -157.5)]), 2.5, 0.9),
        Ditch(ll([(108, -157.5), (460, -157.5)]), 2.5, 0.9),
        Ditch(ll([(-190, -338), (250, -338)]), 2.0, 0.8),
        Ditch(ll([(-268, -138), (-268, 307), (430, 307), (430, 128)]), 2.5, 0.9),
        Ditch(ll([(-56, -122), (-56, 108)]), 2.0, 0.7),
        Ditch(ll([(12, 222.5), (424, 222.5)]), 2.0, 0.7),
        Ditch(ll([(338, 128), (430, 128)]), 2.0, 0.7),
    ]

    # ---------------------------------------------------------------- gebouwen (lekker simpel)
    buildings = [
        Building("house", "Woonhuis", "house", ll(rectangle(-35, -105, 12, 10)), 3.5, Roof("gable", 8.5, "long")),
        Building("barn", "Ligboxenstal", "barn", ll(rectangle(10, -60, 60, 28)), 4.5, Roof("gable", 11.0, "long")),
        Building("shed", "Werktuigenloods", "shed", ll(rectangle(-30, -55, 24, 16)), 5.5, Roof("gable", 8.0, "short")),
        Building("lean_to", "Kapschuur", "shed", ll(rectangle(25, -108, 12, 8)), 3.5, Roof("flat")),
        Building("manure_silo", "Mestsilo", "silo", ll(circle(38, -95, 8.0, 32)), 4.0, Roof("flat")),
        Building("feed_silo", "Voersilo", "silo", ll(circle(-8, -92, 2.0, 16)), 11.0, Roof("cone", 12.5)),
        Building("bunker", "Sleufsilo", "wall", ll(rectangle(-10, -24, 40, 10)), 2.2, Roof("flat")),
    ]

    # ---------------------------------------------------------------- bomen
    trees: list[Tree] = []

    def tree(x, y, h, crown="ball"):
        lat, lon = f.to_latlon(x, y)
        trees.append(Tree(lat, lon, h, crown))

    for x in range(-290, -60, 12):                 # laanbomen langs de weg
        tree(x, -139.0, 14.0)
    for x in range(60, 450, 12):
        tree(x, -139.0, 14.0)
    for x in range(-45, 46, 9):                    # erfbeplanting achter de stal
        tree(x, -12.0, 11.0, "cone")
    for y in range(-115, -30, 9):                  # bomenrij tussen erf en sloot langs de weide
        tree(-52.5, y, 12.0)
    for ix in range(10):                           # boomgaard oost van het erf
        for iy in range(13):
            tree(80.0 + ix * 9.0, -100.0 + iy * 9.0, 4.5)
    tree(-20.0, -118.0, 16.0)                      # oude linde bij het huis
    tree(-48.0, -98.0, 15.0)

    # ---------------------------------------------------------------- afrastering om de weide
    fences = [Fence(ll([(-256, -121), (-256, 102), (-66, 102), (-66, -60)]), 1.2, 4.0),
              Fence(ll([(-66, -80), (-66, -121), (-256, -121)]), 1.2, 4.0)]

    # ---------------------------------------------------------------- reliëf
    terrain = {
        "type": "procedural",
        "cell_m": 1.0,
        "base_m": 0.0,
        "slope_pct": [0.03, -0.08],
        "bumps": [
            {**dict(zip(("lat", "lon"), f.to_latlon(20, -245))), "radius_m": 170, "height_m": 0.9},
            {**dict(zip(("lat", "lon"), f.to_latlon(-150, 215))), "radius_m": 110, "height_m": -0.45},
            {**dict(zip(("lat", "lon"), f.to_latlon(250, 265))), "radius_m": 140, "height_m": 0.5},
        ],
        "noise": {"amplitude_m": 0.12, "wavelength_m": 70, "seed": 7},
        "flatten": [{"polygon": [list(p) for p in ll(yard)], "edge_m": 8.0}],
    }

    # Noordkant van het erf, neus naar het oosten: vrij naar het kavelpad (x 0), niet tegen de stal of loods
    lat, lon = f.to_latlon(-40, -38)
    return World(
        name="example",
        label="Fantasieboerderij (voorbeeld)",
        origin=ORIGIN,
        area=ll(box(-300, -350, 460, 320)),
        margin_m=40.0,
        terrain=terrain,
        surfaces=surfaces,
        fields=fields,
        buildings=buildings,
        trees=trees,
        ditches=ditches,
        fences=fences,
        start_pose=Pose(lat, lon, 90.0),
        sources={"generator": "openagbot_fieldsim.world.fantasy", "note": "verzonnen wereld, geen echte plek"},
    )


def main(root: str | None = None) -> str:
    root = root or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "worlds")
    return build().save(os.path.join(root, "example"))


if __name__ == "__main__":
    print(main())
