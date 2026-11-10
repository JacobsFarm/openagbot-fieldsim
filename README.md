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

- **Missie**, twee soorten:
  - *Veldmissie*: het hele veld als één rijpad. Eerst een aantal rondjes langs de rand (of laatst, of geen), dan
    het hart in rechte banen, met bochten die de robot echt kan rijden (Dubins, draaicirkel uit de config).
    Per soort rand (sloot, weg, hek, gebouw) hoe ver hij eruit blijft. *Plan* toont het pad, de randen in kleur en
    de cijfers (dekking, tijd, rendement); *Rijden in de sim* rijdt het met de echte software als waypoint-route;
    `veldmissie.json` / `waypoints.json` downloaden of *Stuur* naar de echte robot. Zie PLAN.md 5a.
  - *Boerderij*: meer velden achter elkaar. De planner kiest de volgorde (steeds het dichtstbijzijnde veld), plant
    elk veld en rijdt ertussen als transport, **alleen via doorgangen** (dam, duiker, brug, hek, oversteek). Een
    veld zonder doorgang komt niet in de missie en staat er met de reden bij. Zie "Wereld bewerken" hieronder.
  - *AB-lijn*: werkbreedte en kopakker instellen, *Opslaan + start* zet de missie in
    `runtime/agbot/data/ab_line.json` en start hem via de API van de robot. OpenAgbot rekent zelf de volgorde en de
    bochten; de sim tekent het pad dat de robot zelf plant.
- **Veiligheid**: elke geplande route wordt vooraf met de breedte van robot en werktuig tegen sloten, water,
  verboden gebieden, nesten (in hun periode), dijken, gebouwen en wegen gelegd; een stuk dat er een raakt wordt rood
  en de sim rijdt die missie niet (en stuurt hem niet naar de robot). Tijdens het rijden test de bewaking 20 keer per
  seconde de hoeken van robot en werktuig: bij een verboden gebied of in de **slootzone** pauzeert de sim, krijgt
  de sim-robot een stop en komt er een melding.
- **Slootzone**: een verboden strook langs elke sloot en elk water, als boerderij-instelling (*Wereld bewerken* ->
  Boerderij, standaard 2 m; oranje in de editor, de minikaart en 3D). Wie erin komt stopt. Missies worden er vanzelf
  buiten gepland: de vrije ruimte tot water is minstens de slootzone plus de planmarge (0,3 m), ook als een missie
  minder opgeeft. Bij een doorgang is de zone open, met de aanloop naar het veld erbij.
- **Kopakker**: alleen het buitenste rondje volgt de slingerende rand; rondje 2 en verder worden recht getrokken
  (bochtjes tot de ingestelde maat, standaard 1 m), zodat een bocht langs de sloot door één werkgang loopt en de
  banen op een rechte kopakker eindigen. Een recht rondje overlapt het buitenste hooguit die maat en laat geen strook
  liggen. Uit te zetten per missie. Banen overslaan kan tot 6.
- **Max overlap laatste baan** (0-100 %, standaard 100 = uit): de laatste baan past zelden precies en schuift dan
  over zijn buur. Overlapt hij meer dan dit percentage van de werkbreedte, dan vervalt hij en blijft de smalle strook
  liggen aan de kant van het hart die het dichtst bij de sloot ligt (geen sloot: aan het einde van de banen). Scheelt
  een bijna dubbele werkgang, kost die strook.
- **Luchtfoto**: onder *Weergave* legt *Luchtfoto (PDOK)* de foto over het terrein in 3D (in tegels van 2048
  pixels, gecachet in `worlds/<naam>/cache/luchtfoto`). Alleen het kader van de wereld gaat naar PDOK.
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
| `boerderij_agopengps` | Tweede boerderij: 12 velden uit AgOpenGPS (in `farm.json`), kaart eromheen gebouwd. Niet in git. Nog zonder doorgangen: die teken je zelf (of *Voorstel doorgangen*). |
| `example` | Met doorgangen: hekken naar de velden, een dam over de sloot tussen Driehoek en Lange akker, een dam naar de Weide en een oversteek over de Polderweg naar de Zuidkavel. Fantasieboerderij: erf met woonhuis, ligboxenstal, werktuigenloods, kapschuur, mest- en voersilo, sleufsilo; vijf velden (Achterkamp, Lange akker, Driehoek, Weide, Zuidkavel), sloten met dammen, weg, laanbomen, boomgaard, afrastering, zacht reliëf. Gemaakt door `openagbot_fieldsim/world/fantasy.py`. |
| `eigen_bedrijf` | Gebouwd uit kaarten: 26 BRP-percelen, 272 BGT-vlakken (erf, verharding, bermen, 162 waterdelen), 59 gebouwen met hoogte uit 3D BAG, AHN-reliëf op 1 m. Niet in git. |

Een eigen plek maken: tab **Wereld**, naam en hoekpunten (lat, lon) invullen, *Maak*, dan *Bouw*. Of:

```bash
.venv\Scripts\python -m openagbot_fieldsim.world.builder eigen_bedrijf
```

Perceelgrenzen komen uit de BRP-gewaspercelen van RVO (via PDOK), dus de grenzen zoals ze bij RVO staan. De bouwer
haalt ook de kadastrale percelen (BRK) en de BGT-scheidingen (hek, damwand, walbescherming) op; hekken worden
afrastering in de sim, de rest gebruikt de missieplanner. Zonder BRP-percelen worden de kadastrale percelen in
gras- of bouwland de velden. Alleen deze lagen bijwerken in een al gebouwde wereld:

```bash
.venv\Scripts\python -m openagbot_fieldsim.world.builder eigen_bedrijf --boundaries
```

De bouwer haalt BGT en BRP in CRS84 bij PDOK (PDOK rekent RD correct om), AHN (DTM 0,5 m) en 3D BAG in RD, en
koppelt RD aan de sim met een affiene fit op dezelfde BGT-objecten in beide stelsels (rest-afwijking < 1 mm).
Water uit de BGT wordt in het terrein uitgegraven en als no-go gebruikt. Downloads blijven in
`worlds/<naam>/cache/`, dus opnieuw bouwen kan offline. Startplek: het erf dat het dichtst bij het midden ligt,
of zelf zetten (dubbelklik op de grond, dan *Hier = startplek* in Storingen).

`worlds/*` staat in `.gitignore` behalve `worlds/example/`: een echte plek is de eigen locatie.

## Wereld bewerken (hoofdmenu)

**Wereld bewerken** opent de gekozen wereld in een bovenaanzicht (noorden boven, meters). Wat je tekent of wijzigt
komt in `worlds/<naam>/farm.json`; `world.json` blijft de brondata uit de kaart. *Bouw* kan dus opnieuw draaien
zonder dat je werk verdwijnt. Elk object heeft een vast id; een gewijzigd bronobject is "overschreven", een
weggehaald bronobject "verborgen" (terug te halen). Bij elke keer bewaren gaat de vorige versie naar `backups/`.

| Object | Wat het doet |
| --- | --- |
| Veld | grens, gaten (doorrijdbaar of niet), standaard aantal kopakkerrondjes en werkbreedte |
| Sloot | breedte, diepte, waterpeil; niet over te steken behalve bij een doorgang |
| Dijk | kruinbreedte, hoogte, talud; modus `ontwijken`, `negeren` of `doorgang` (alleen over bij een doorgang). Verhoogt het terrein |
| Verboden gebied | `stoppen` (robot stopt zodra hij erin komt) of `mijden` (planner rijdt eromheen, alleen een waarschuwing) |
| Rijzone | `werken`, `alleen_rijden` (transport, werktuig omhoog), `niet_bewerken` of `verboden`. **Niet bewerken** (paars) is een stuk van een veld dat de missie overslaat, zoals een punt of uitstulping: rondjes en banen volgen de rest van het veld, de robot mag er wel rijden en keren. Een **alleen rijden**-zone in een hoek of langs de rand haalt de veldmissie ook uit het werkgebied: rondjes en banen volgen de zonelijn, dus met een schuine zone over een scherpe hoek krijg je een ruime bocht in plaats van rare banen. Knipt de zone het veld in stukken (pad dwars door het veld), dan lopen de banen erdoorheen met het werktuig omhoog |
| Doorgang | rijlijn met breedte, soort (dam, duiker, brug, hek, oversteek, oprit), richting, maximumsnelheid en "eerst stoppen en wachten op vrijgave". De enige plek waar de robot een sloot, dijk, hek of weg over mag, en van veld naar veld |
| Vogelnest | punt met straal en periode (bv. 03-15 tot 08-15); daarbuiten telt het niet |
| Marker | steen, paal, put, kabel: een punt om omheen te rijden |
| Boom, hek | boom met eigen vrije ruimte; hek als lijn |

Tekenen: vlak, lijn en punt; punten verslepen, toevoegen (bolletje op een rand) en verwijderen; snappen aan
hoekpunten, randen en een raster (Alt = even niet); meetlat; ongedaan maken en opnieuw. Per veld: splitsen met een
lijn, samenvoegen, gat tekenen, de rand een stuk naar binnen of buiten schuiven, vereenvoudigen. Lagenpaneel met
zichtbaar en vergrendeld, eigenschappenpaneel, luchtfoto van PDOK als achtergrond. Onder *Boerderij*: dijkmodus voor
alle dijken, de vrije ruimte per soort obstakel en de instellingen van de bewaking. *Voorstel doorgangen* zoekt
plekken waar twee gebieden zonder sloot, hek of weg ertussen aan elkaar grenzen (bij een dam is de sloot in de
kaart onderbroken); *Controleer* laat zien welke velden geen bruikbare doorgang hebben en waar de robot mag rijden.

### AgOpenGPS, KML en GeoJSON

*Import / export* leest een AgOpenGPS-veldmap (of de map met al je velden), KML en GeoJSON. De buitengrens wordt
een veld, binnengrenzen worden gaten ("doorrijdbaar" blijft doorrijdbaar), vlaggen worden markers ("nest" of
"boom" in de notitie maakt er een nest of boom van). De omrekening van AgOpenGPS' lokale meters naar lat/lon is
dezelfde als in AgOpenGPS zelf (`world/interchange.py`); duizendtal-komma's van nieuwere versies (`1,234.500`)
worden goed gelezen. Terug naar AgOpenGPS: `Field.txt`, `Boundary.txt`, `Flags.txt` en `Field.kml` per veld (zip);
sloten, dijken, verboden gebieden en nesten in een veld gaan mee als gat, want AgOpenGPS kent ze niet. GeoJSON
bevat alle lagen met alle eigenschappen: daarmee kopieer je een boerderij naar een andere wereld (of *Neem over*).

Een tweede boerderij uit AgOpenGPS-velden, met controle van de omrekening tegen de `Field.kml` die AgOpenGPS zelf
schreef en een heen-en-terug-test:

```bash
.venv\Scripts\python tools/import_agopengps.py "C:/pad/naar/Fields" --check
.venv\Scripts\python tools/import_agopengps.py "C:/pad/naar/Fields" mijn_boerderij --build
```

Op 12 echte velden: grens binnen 0,7 cm van de KML van AgOpenGPS (die op 7 decimalen afrondt), heen en terug
binnen 0,1 mm. Met `--build` komt de kaart erbij en worden BRP-percelen die onder een eigen veld liggen verborgen.

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
| `openagbot_fieldsim/world/model.py` | wereldformaat (`world.json`): velden (met gaten), vlakken, sloten, gebouwen, bomen, hekken, dijken, verboden gebieden, rijzones, doorgangen, nesten, markers; vaste id's |
| `openagbot_fieldsim/world/farm.py` | `farm.json`: eigen aanpassingen over de brondata (verborgen, overschreven, eigen objecten), back-ups |
| `openagbot_fieldsim/world/interchange.py` | AgOpenGPS-veldmap, KML en GeoJSON in en uit |
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
| `openagbot_fieldsim/missions/boundary.py` | perceelrand: wat ligt ernaast (sloot, weg, hek, gebouw, buurperceel) en het toegestane werkgebied |
| `openagbot_fieldsim/missions/dubins.py` | Dubins-paden (kortste vooruit-pad met minimale draaicirkel) voor bochten en overgangen |
| `openagbot_fieldsim/missions/field_plan.py` | veldmissie: kopakkerrondjes, banen, volgorde, bochten, dekking; uitvoer `veldmissie.json` en `waypoints.json` |
| `openagbot_fieldsim/missions/safety.py` | gevaren (stop / mijden), controle van een route vooraf, bewaking tijdens het rijden, geofence voor de robot |
| `openagbot_fieldsim/missions/farm_route.py` | routegraaf (velden, rijgebieden, doorgangen), boerderijmissie, voorstellen voor doorgangen |
| `openagbot_fieldsim/api/field_api.py` | API voor veldmissies (plannen, downloaden, rijden in de sim, naar de robot) |
| `openagbot_fieldsim/api/farm_api.py` | API voor de boerderijmissie, de routegraaf en de veiligheid |
| `openagbot_fieldsim/api/editor_api.py` | API van de editor: toestand, bewaren, splitsen/samenvoegen/verschuiven, import/export, luchtfoto |
| `tools/import_agopengps.py` | AgOpenGPS-velden controleren en als nieuwe wereld neerzetten |
| `tools/render_field_plan.py` | veldmissie als SVG/PNG, zonder sim |
| `tools/drive_field_mission.py` | veldmissie door de echte OpenAgbot laten rijden (zonder UI) en de afwijking meten |
| `tools/check_boundaries.py` | perceelgrenzen controleren: BRP (RVO) tegen Kadaster en water |
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
