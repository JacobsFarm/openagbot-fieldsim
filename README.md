# OpenAgbot FieldSim

Simulatiewereld voor de Bruut OpenAgbot. De **echte robotsoftware** (OpenAgbot) rijdt in de sim alsof hij op
de Jetson staat: de sim bootst de GNSS-ontvanger, de stuur-Arduino en de twee VESC's na op hun eigen protocol, dus
alles boven de drivers (navigator, Stanley, kopakkerbochten, veiligheid, logger, webinterface) draait ongewijzigd.
Plan en achtergrond: [PLAN.md](PLAN.md).

## Starten

```bash
.venv\Scripts\python -m openagbot_fieldsim
```

Opties: `--world NAAM`, `--browser` (gewone browser in plaats van een eigen venster), `--headless` (alleen de
server), `--no-agbot` (alleen de wereld bekijken), `--make-example` (fantasiewereld opnieuw genereren).

De sim zoekt OpenAgbot in `F:\Agbot\Bruut_OpenAgbot`; anders `FIELDSIM_AGBOT_DIR` of `agbot_dir` in
`settings.json` naast de werelden. OpenAgbot heeft daarvoor `serial_for_url`, `AGBOT_CONFIG` en
`data/config.sim.json` nodig (staat erin sinds 2026-11-08).

### In de sim

- **Missie**: kies een veld (of dubbelklik erop), stel werkbreedte en kopakker in, *Plan* toont de banen,
  *Opslaan + start* zet de missie in `runtime/agbot/data/ab_line.json` en start hem via de API van de robot.
  OpenAgbot rekent zelf de volgorde en de bochten; de sim tekent het pad dat de robot zelf plant.
- **Rijden**: pijltjes of WASD (via `POST /api/nav/manual_drive`, dus met de echte dodemansknop), spatie = stop.
- **Storingen**: GNSS-fix (fixed/float/DGPS/geen), heading weg, ontvanger stil, Arduino antwoordt niet,
  stappenmotor vast, CAN weg, VESC-foutcode. Zo zie je wat de software doet als het in het veld misgaat.
- **Robot-UI**: de echte webinterface van de robot in een venster (zelfde als op de telefoon in het veld).
- **Console**: uitvoer van de OpenAgbot-backend en gebeurtenissen van de sim (sloot, botsing).
- **Replay**: ritlog kiezen (uit de sim, uit `data/logs` van de robot of uit `worlds/<naam>/logs`), afspelen met
  koers, stuurhoek, snelheid en afwijking, en *Route in de sim narijden* (waypoints + pure pursuit van de robot).
- **Wereld**: werelden openen, een echte plek toevoegen en bouwen, lagen aan/uit.
- **Punten**: GPS-punten zetten (dubbelklik), onkruid importeren (CSV `lat, lon, naam` of GeoJSON-punten) of
  testplanten in een veld maken, *Volgorde optimaliseren* (kortste route vanaf de robot), *Rijden in de sim*
  (waypoints + pure pursuit van de echte software), *Download waypoints.json* of *Stuur* naar de echte robot
  (vervangt alleen de waypoints daar, start niets). Punten worden bewaard in `worlds/<naam>/missions/punten.json`.

Ritlogs van gesimuleerde ritten komen in `runtime/agbot/data/logs/` (zelfde CSV als op de robot).

## Werelden

Een wereld is een map `worlds/<naam>/` met `world.json` (lat/lon, ETRS89). Zie PLAN.md hoofdstuk 4.

| Wereld | Wat |
| --- | --- |
| `example` | Fantasieboerderij: erf met woonhuis, ligboxenstal, werktuigenloods, kapschuur, mest- en voersilo, sleufsilo; vijf velden (Achterkamp, Lange akker, Driehoek, Weide, Zuidkavel), sloten met dammen, weg, laanbomen, boomgaard, afrastering, zacht reliëf. Gemaakt door `openagbot_fieldsim/world/fantasy.py`. |
| `eigen_bedrijf` | Gebouwd uit kaarten: 26 BRP-percelen, 272 BGT-vlakken (erf, verharding, bermen, 162 waterdelen), 59 gebouwen met hoogte uit 3D BAG, AHN-reliëf op 1 m. Niet in git. |

Een eigen plek maken: tab **Wereld**, naam en hoekpunten (lat, lon) invullen, *Maak*, dan *Bouw*. Of:

```bash
.venv\Scripts\python -m openagbot_fieldsim.world.builder eigen_bedrijf
```

De bouwer haalt BGT en BRP in CRS84 bij PDOK (PDOK rekent RD correct om), AHN (DTM 0,5 m) en 3D BAG in RD, en
koppelt RD aan de sim met een affiene fit op dezelfde BGT-objecten in beide stelsels (rest-afwijking < 1 mm).
Water uit de BGT wordt in het terrein uitgegraven en als no-go gebruikt. Downloads blijven in
`worlds/<naam>/cache/`, dus opnieuw bouwen kan offline. Startplek: het erf dat het dichtst bij het midden ligt,
of zelf zetten (dubbelklik op de grond, dan *Hier = startplek* in Storingen).

`worlds/*` staat in `.gitignore` behalve `worlds/example/`: een echte plek is de eigen locatie.

## Aanbouwdelen

Tab **Werktuig**: messenbalk (3 balken, 3,25 m), ridderzuringboor v8 en de vloeibare-mestinjecteur advanced V2
(5 rijen). Het model komt uit de FreeCAD-bouwscripts in `F:\veldrobot\aanbouwdelen`, in werkstand en geheven.

- **Stand:** *auto* = omlaag zodra de robot in een veld is, omhoog daarbuiten; of vast omlaag/omhoog.
  OpenAgbot stuurt nog geen werktuig aan, dus de sim doet dat zelf.
- **Strook** (messenbalk, injecteur): bewerkte strook in de kleur van het werktuig, hectares, liters (dosis in
  l/ha), energie van het werktuig (gaat ook van de accu af).
- **Boor:** de punten uit de tab Punten zijn de planten. Per plant: *geboord* (de robot stond met de spil boven de
  plant de hele cyclus van 21,8 s stil), *te snel voorbij* (binnen de X-slag van ±50 cm, maar niet gestopt) of
  *buiten bereik*. De X-slede schuift naar de plant; de planten kleuren mee.

Modellen opnieuw exporteren na een wijziging in een werktuig (headless FreeCAD, schrijft niets in de
aanbouwdelen-map):

```bash
"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tools\export_implements.py
```

Een werktuig toevoegen: een `ImplementSpec` in `openagbot_fieldsim/implements/catalog.py` en een recept (welke standen)
in `tools/export_implements.py`. Montage gaat per as: het werktuig houdt zijn afstand tot de vooras of achteras
van het FreeCAD-model, ook nu de sim-robot (config) een andere wielbasis heeft.

## .exe bouwen

```bash
.venv\Scripts\python packaging\build_exe.py --with-agbot F:\Agbot\Bruut_OpenAgbot
```

Resultaat: `dist/openagbot-fieldsim/openagbot-fieldsim.exe` met de voorbeeldwereld en (met `--with-agbot`) een kopie van de
OpenAgbot-code zonder eigen config, ritlogs of gewichten. `--installer` maakt er met Inno Setup een setup.exe van.
De OpenAgbot-backend draait als tweede proces van dezelfde .exe (`openagbot-fieldsim.exe --agbot-runner`).

## Bestanden

| Pad | Wat |
| --- | --- |
| `openagbot_fieldsim/geo.py` | lat/lon <-> lokaal ENU (exact via ECEF), kompaskoersen |
| `openagbot_fieldsim/world/model.py` | wereldformaat (`world.json`): velden, vlakken, sloten, gebouwen, bomen, hekken |
| `openagbot_fieldsim/world/terrain.py` | hoogteraster (vlak, procedureel of AHN-raster), sloten uitgraven, erf vlak maken |
| `openagbot_fieldsim/world/fantasy.py` | de fantasiewereld `example` |
| `openagbot_fieldsim/world/builder.py` | wereldbouwer voor een echte plek (BGT, BRP, AHN, 3D BAG via PDOK) |
| `openagbot_fieldsim/world/sources/` | downloaden met cache, AHN-GeoTIFF lezen (eigen decoder voor de float-predictor) |
| `openagbot_fieldsim/replay/ride_log.py` | ritlogs inlezen, kerngetallen (zoals extract_ride_report.py), route als waypoints |
| `openagbot_fieldsim/vehicle/params.py` | robotmaten uit de OpenAgbot-config (één bron) plus `SimExtras` (geschat) |
| `openagbot_fieldsim/vehicle/model.py` | voertuigmodel: Ackermann-voorwielen, VESC-achterwielen, slip, terrein, sloot/gebouw |
| `openagbot_fieldsim/devices/gnss.py` | ZED-X20D: GGA/RMC/VTG/THS + UBX NAV-DAHEADING op 25 Hz, ruis per fix-soort, montagefouten |
| `openagbot_fieldsim/devices/steering.py` | stuur-Arduino: hetzelfde protocol en dezelfde stap-ramp als de firmware |
| `openagbot_fieldsim/devices/vesc.py` | VESC: SET_RPM/BRAKE/fault-poll, STATUS 1-5, min-eRPM en timeout zoals de echte |
| `openagbot_fieldsim/devices/can_link.py` | CAN over TCP + python-can interface `simcan` |
| `openagbot_fieldsim/devices/transport.py` | TCP-poorten voor `socket://` (pyserial) |
| `openagbot_fieldsim/engine.py` | de lus (200 Hz): sturing, aandrijving, voertuig, GNSS-epochs, storingen, snapshot |
| `openagbot_fieldsim/agbot/` | OpenAgbot als subproces in `runtime/agbot` (eigen `data/`) |
| `openagbot_fieldsim/missions/ab.py` | AB-missie per veld in het formaat van `ab_line.json` |
| `openagbot_fieldsim/missions/points.py` | puntmissies: import, testonkruid, kortste route (nearest neighbour + 2-opt), `waypoints.json` |
| `openagbot_fieldsim/implements/` | aanbouwdelen: catalogus (bron, montage, breedte), werking (strook / punten), `assets/` (glb uit FreeCAD) |
| `tools/export_implements.py` | FreeCAD-bouwscripts -> glb per stand + `meta.json` (spilpositie, cyclustijd, dosis) |
| `openagbot_fieldsim/api/` | HTTP/WebSocket-API en scene-export voor de UI |
| `ui/` | Svelte + three.js, FreeCAD-stijl (`ui/src/theme.js`); `npm run build` -> `openagbot_fieldsim/static` |
| `packaging/` | PyInstaller-spec, build-script, Inno Setup |
| `tests/` | unit-tests en integratietests tegen de echte OpenAgbot-drivers |

## Testen

```bash
.venv\Scripts\python -m pytest -q                 # alles, ook de integratietests (ca. 1 min)
.venv\Scripts\python -m pytest -q -m "not integration"
```

De device-tests sturen de emulators aan met de **echte** `gps_logic.py`, `stepper_logic.py` en `vesc_can.py` van
OpenAgbot. De integratietest start de hele backend, rijdt handmatig (en checkt de dodemansknop) en rijdt baan 0
van een AB-missie: afwijking < 10 cm.

## Geschat, niet gemeten

- Voertuig: massa 180 kg, rolweerstand 0,06, yaw-efficiëntie 0,97, VESC-reactietijd 0,15 s, stroom per kracht
  (ca. 9 A bij 150 N per wiel). Te ijken op de ritlogs (PLAN.md fase 4).
- GNSS-ruis per fix-soort (fixed 8 mm, float 12 cm, DGPS 45 cm, GPS 1,5 m) en heading-ruis 0,08°.
- Werktuigen: vermogen messenbalk 2,2 kW (sbm_calc, bij 1 m/s in 3 t ds/ha), injecteur 150 W, boor 600 W tijdens
  het boren; de boor moet binnen 5 cm voor/achter de plant stilstaan.
- Antenne-baseline 0,997 m (uit de ritlog van 2 nov 2026).
- Accu: lineaire rustspanning 3,30-4,15 V per cel.
- Robotmaten in 3D: van de OpenAgbot-config (wielbasis 1,20 m, spoor 1,00 m); het FreeCAD-model in
  `agbots/agbot design` is kleiner (1,00 x 0,75 m). Welke klopt voor de 2WD in het veld moet nog bevestigd.
