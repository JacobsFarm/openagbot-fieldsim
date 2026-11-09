# OpenAgbot FieldSim - plan voor een simulatiewereld voor de Bruut OpenAgbot

Doel: een Windows-programma (.exe) waarin je

1. echte velden op RTK-nauwkeurigheid inlaadt (perceelgrens, luchtfoto, hoogte),
2. missies maakt (AB-lijnen, waypoints, onkruidroutes) die de robot daarna **ongewijzigd** in het echt rijdt,
3. de **echte OpenAgbot-software** laat rijden tegen een gesimuleerde robot, zodat je software test zonder veld,
4. ritlogs uit het veld terugspeelt en vergelijkt met de simulatie,
5. aanbouwdelen (vloeibare-kunstmeststrooier, ridderzuringboor, ...) in het veld probeert, ook met taakkaarten
   (hier meer liters, daar minder).

## Stand van zaken (2026-11-09)

| Fase | Stand |
| --- | --- |
| 0 Fundament | klaar: repo, venv (Python 3.12, uv), geo-module met tests |
| 1 SIL-kern | klaar: voertuigmodel, GNSS-, stuur- en VESC-emulators, `simcan`, OpenAgbot als subproces; integratietest rijdt een AB-baan binnen 10 cm |
| 2 Sim-UI | grotendeels: 3D in FreeCAD-stijl, panelen, minikaart, storingen, console, echte webinterface in een venster, rijden met de pijltjes. Nog niet: schermvergelijking met `field_run_simple_refill.png`, 2D-kaart met luchtfoto |
| 3 Missies | AB per veld (opslaan in `ab_line.json`, starten, pad uit OpenAgbot getekend), punten zetten en als `waypoints.json` exporteren of naar de robot sturen, BRP-percelen in echte werelden. Nog niet: `ab_line.json` direct naar de robot (OpenAgbot heeft daar geen API voor) |
| 4 Replay | ritlog afspelen met kerngetallen, route narijden in de sim. Nog niet: ijken van het voertuigmodel, ruwe GNSS terugspelen |
| 5 Wereld | fantasiewereld `example` (5 velden, boerderij, sloten, reliëf) en wereldbouwer voor een echte plek (BGT, BRP, AHN, 3D BAG; `eigen_bedrijf` gebouwd). Nog niet: bomen uit BGT/TOP10NL, ontvangstmodel bij gebouwen, glb uit FreeCAD (maatverschil robot, zie README) |
| 6 Aanbouwdelen | 3 werktuigen uit FreeCAD (messenbalk, ridderzuringboor v8, injecteur V2) met werkstand/geheven, strook, liters, boor-planten; nog niet: taakkaarten/variabele dosis, werktuigsturing vanuit OpenAgbot (stoppen bij een plant, pomp) |
| 7 Onkruidroutes | basis: punten importeren of maken, kortste route, rijden in de sim, exporteren en naar de robot sturen. Nog niet: cyclustijd van de boor per plant, Dubins-bochten |
| 8 .exe | werkt: `packaging/build_exe.py --with-agbot`, getest (missie gereden vanuit de .exe). Nog niet: GitHub Actions, installer getest |

Antwoord op "kan dit": ja. De OpenAgbot-software praat met de hardware via drie smalle kanalen, en precies daar
kun je de echte wereld vervangen door een simulatie:

| Kanaal in de robot | Code | Protocol | Wat de simulator nabootst |
| --- | --- | --- | --- |
| GNSS (ZED-X20D, dubbele antenne) | `app/hardware/gps_logic.py` | serieel: NMEA GGA/THS/VTG/RMC + UBX NAV-DAHEADING, 25 Hz | positie, heading, snelheid, pitch, fix-soort, correctieleeftijd, ruis, uitval |
| Sturing (Arduino + 2 NEMA34) | `app/hardware/stepper_logic.py` | serieel tekst: `POS:`, `STA:`, `ERR:` ... | stuurhoeken met looptijd, limieten, stapverlies |
| Aandrijving (2 VESC) | `app/hardware/vesc_can.py` | CAN 500 kbit, VESC status-berichten | eRPM, stroom, accu, tacho, temperatuur, slip, stall |

Alles daarboven (navigator, Stanley/pure pursuit, kopakkerbochten, logger, webinterface) draait dan echt.
Dat heet **software-in-the-loop (SIL)**: je test de code die op de Jetson draait, niet een kopie.

---

## 1. Gekozen aanpak

### Kernkeuzes

| Keuze | Besluit | Waarom |
| --- | --- | --- |
| Koppeling met de robotsoftware | Op apparaatniveau (serieel + CAN), niet via een nep-`VehicleController` | Test de echte parsers, timing en veiligheidslogica (GNSS-niveaus, link-timeout, stall). Zelfde code in sim en veld |
| Taal simulator-kern | Python 3.12 | Zelfde taal als OpenAgbot en de aanbouwdelen (`<p>_params.py`, `<p>_calc.py` direct herbruikbaar) |
| Fysica | Eigen voertuigmodel (kinematisch Ackermann + achterwiel-differentieel, slip, rampen, stuurvertraging), terrein uit hoogtekaart | Bij 1-5 km/h is een rigid-body engine overbodig; een eigen model is te ijken op de echte ritlogs |
| Interface | Web-UI (Svelte + MapLibre/Leaflet voor 2D, Three.js/Threlte voor 3D) in een eigen venster via **pywebview** (WebView2) | Zelfde stack als de robot-frontend (Svelte + Leaflet). Geen tweede ecosysteem |
| Vormgeving | De FreeCAD-stijl van de veldanimaties (`field_run_simple.gif`), zie hoofdstuk 3 | Sim, animaties en documentatie zien er hetzelfde uit; modellen en kleuren komen rechtstreeks uit FreeCAD |
| .exe | **PyInstaller** (onedir) + **Inno Setup** installer, gebouwd door GitHub Actions bij een tag, download via GitHub Releases | Bewezen route voor Python + webview op Windows |
| 3D-modellen | FreeCAD 1.1 -> glTF (`.glb`), per bewegend sub-Part een eigen node | De aanbouwdelen hebben al bewegende sub-Parts (zie `AGENTS.md`), die worden zo animeerbare nodes |
| Coördinaten | Intern lokaal ENU (meters) rond een veld-oorsprong; opslag in WGS84/ETRS89 lat/lon; conversie met **pyproj** | Meter-rekenwerk zonder afrondingsfouten, en exacte omrekening naar RD/UTM voor kaarten |

### Overwogen en afgewezen

| Alternatief | Waarom niet (nu) |
| --- | --- |
| Gazebo / ROS 2 | Sterk, maar Linux-gericht en niet als één .exe te leveren. OpenAgbot draait nog niet op ROS 2. Wel: later een ROS 2-brug (fase 8) |
| Godot / Unity als hele simulator | Mooie 3D en makkelijke .exe, maar dan zit de fysica en GIS in een andere taal dan de robot en de aanbouwdelen. Dubbel onderhoud |
| Isaac Sim / Webots | Zwaar (NVIDIA GPU / eigen installer) en overkill voor 1-5 km/h veldwerk |
| De navigator los in de simulator nabouwen | Dan test je de kopie, niet de robot. Fouten in parsers en timing vallen weg |

---

## 2. Architectuur

```
+---------------------------- openagbot-fieldsim.exe (Windows) ----------------------------+
|                                                                                  |
|  UI (pywebview venster, Svelte)                                                  |
|   - Kaart 2D: luchtfoto PDOK/Esri, BRP-percelen, taakkaarten, onkruidpunten      |
|   - 3D veld/wereld: reliëf, gebouwen, robot + aanbouwdeel (glb), sporen, dosis   |
|   - Missie-editor, log-replay, grafieken (afwijking, snelheid, dosis)            |
|          |  HTTP/WebSocket                                                       |
|  sim-server (FastAPI, Python)                                                    |
|   world/      wereld + veld, reliëf, gebouwen, onkruid, taakkaarten, coördinaten |
|   vehicle/    voertuigmodel Bruut (params uit agbot_params) - stap 100-200 Hz    |
|   implements/ plugins per aanbouwdeel (lfa, dwd7, cbr, ovd, ...)                 |
|   devices/    emulators: GNSS (NMEA+UBX) | Arduino-stuur | VESC-CAN              |
|   missions/   AB-planner, waypoints, onkruidroute, export/import robotformaat    |
|   replay/     ritlog-CSV inlezen, vergelijken, ijken voertuigmodel               |
|          |  socket://127.0.0.1:5101 (GNSS)   socket://...:5102 (stuur)           |
|          |  sim-CAN (TCP)                                                        |
|  OpenAgbot-backend (echte code, subproces, config = data/config.sim.json)        |
|   run.py -> GpsSystem / StepperSteering / MotorController / navigators / logger  |
|   webinterface op http://localhost:8000 (precies zoals in het veld)              |
+----------------------------------------------------------------------------------+
         ^  missie (ab_line.json / waypoints.json) / ritlogs (rit_*.csv)
         v  via USB-stick, git, of de API van de robot over wifi
                     echte robot op de Jetson
```

### Kleine aanpassingen in OpenAgbot (gedaan op 2026-11-08, nog niet gecommit)

1. **Gedaan**: `serial.Serial(port, ...)` -> `serial.serial_for_url(port, ...)` in `gps_logic.py` en
   `stepper_logic.py`. Echte poorten (`/dev/serial/by-id/...`, `COM4`) blijven werken; in de sim wordt het
   `socket://127.0.0.1:5101` (GNSS) en `socket://127.0.0.1:5102` (stuur-Arduino).
2. **Gedaan**: `app/__init__.py` leest de omgevingsvariabele `AGBOT_CONFIG`. Zonder die variabele blijft het
   `config.json` -> `config.example.json`. Wijst hij naar een bestand dat niet bestaat, dan stopt de backend (geen
   stille terugval op de config met echte poorten).
3. **Gedaan**: `data/config.sim.json` (staat via `!data/config.sim.json` in `.gitignore` wel in git): gelijk aan
   `config.example.json`, met de socket-poorten, `can_interface: "simcan"`, `can_channel: "127.0.0.1:5103"` en een
   lege NTRIP-host (`_start_ntrip` stopt dan meteen; de GNSS-emulator meldt zelf RTK fixed/float/dgps).
4. **Geen wijziging in `vesc_can.py`**: die geeft `can_interface`/`can_channel` door aan `can.Bus()`. OpenAgbot FieldSim
   levert een python-can plugin `simcan` (aangemeld via entry point `can.interface`) die CAN-frames over TCP naar
   de VESC-emulator stuurt. Nodig omdat `socketcan` niet op Windows bestaat en `virtual` maar binnen één proces werkt.
5. Optioneel, later: ruwe GNSS-stroom (NMEA/UBX) meeloggen in het veld (`data/logs/*.ubx`). Daarmee kan de sim een
   rit **exact** terugspelen naar de software (fase 4b).
6. Later voor de taakkaarten: een implement-kanaal (pomp/secties) in OpenAgbot, zie fase 6.

Starten tegen de sim: `AGBOT_CONFIG=data/config.sim.json python run.py` (PowerShell: `$env:AGBOT_CONFIG=...`).

### Tijd

Fase 1 draait **real-time (1x)**, want OpenAgbot gebruikt `time.time()` en threads. Sneller dan real-time
(bijv. 10x om een hele dag strooien te testen) vraagt een klok-abstractie in OpenAgbot; dat staat onder "Later" in de fasering.
Missies zonder de echte software (alleen planning + voertuigmodel met een ingebouwde regelaar) kunnen wel meteen
versneld, als "snelle voorvertoning".

---

## 3. Vormgeving: de FreeCAD-stijl van de veldanimaties

De sim krijgt **dezelfde look als de veldanimaties** van de precisie-applicator, met als referentie
`F:\veldrobot\aanbouwdelen\liquid fertilizer applicator\precision_applicator\animations\previews\field_run_simple.gif`
(stilstaande beelden: `field_run_simple_title.png`, `_refill.png`, `_done.png`). Die beelden zijn in FreeCAD
gerenderd (`animate_fld.py`, `view.saveImage(..., "White")`) en daarna voorzien van panelen met PIL
(`make_video_fld.py`). De sim bouwt die look na in Three.js, zodat sim, animaties en documentatie er hetzelfde
uitzien. Dit is de standaardweergave van de sim, niet een optie.

### 3D-scène (zoals de FreeCAD-viewport in "Flat Lines")

| Element | Stijl | Bron van de waarde |
| --- | --- | --- |
| Onderdelen | Vlakke, matte kleuren per materiaal, geen texturen, geen glans | `COLOR` in `build_<p>.py` (bijv. frame (0.18, 0.36, 0.62), rubber (0.10, 0.10, 0.10), funnel (0.95, 0.75, 0.10)) |
| Randen | Dunne zwarte lijnen langs **alle B-rep-randen** (de typische FreeCAD-omlijning) | Geëxporteerd uit FreeCAD: per vorm de randen (`Shape.Edges` gediscretiseerd) als lijnen in de `.glb`. Niet benaderen met een hoek-drempel, dan verdwijnen de ronde randen |
| Doorzichtig | Tanks halfdoorzichtig, vloeistof erin zichtbaar | `TRANSPARENT` in `build_<p>.py` (tank 45 %) |
| Licht | FreeCAD-headlight: één lamp vanuit de camera, plus zwak omgevingslicht. Geen schaduwen | Coin3D-standaard |
| Achtergrond | Wit / gebroken wit, geen lucht, geen horizon-effecten | `saveImage(..., "White")` |
| Camera | Perspectief, beeldhoek 40°, schuin van boven achter de robot, volgt de robot | `track_camera()` (`heightAngle` 40°), `CAM_*`-poses |
| Veld | Vlak groen, banen als lichte lijnen, kopakker lichter, behandelde strook donkerder | `COLOR`: field (0.47, 0.63, 0.31), headland (0.60, 0.70, 0.44), lines (0.66, 0.78, 0.50), treated (0.33, 0.50, 0.24) |
| Sporen en paden | Geplande route als witte streepjeslijn, sleuven/sporen in de grond in bruin | path (0.96, 0.96, 0.92), slot (0.22, 0.14, 0.08), closed (0.72, 0.60, 0.40) |
| Omgeving | Afrastering met bruine palen en grijze draad, verder leeg | post (0.45, 0.33, 0.20), wire (0.75, 0.75, 0.75) |
| Statuslampje | Baken op de robot: groen / oranje / rood | `BEACON` in `animate_fld.py` |

Gevolgen voor de rest van het plan:

- **Luchtfoto en terrein** zijn lagen die je aan kunt zetten (bijvoorbeeld om een veld uit te lijnen in de
  missie-editor), maar de standaardweergave is het vlakke FreeCAD-veld. Een perceel uit BRP of een RTK-grens wordt in
  de veldkleuren getekend. Hoogte (AHN) verandert de vorm van het veld, niet de stijl.
- **Modellen** komen uit de gegenereerde FCStd's met kleur, transparantie en randen erbij. De export (`export_glb.py`
  in de aanbouwdelen-repo) leest `ShapeColor`/`Transparency` van de objecten, zodat er geen tweede kleurtabel ontstaat.

### Panelen over het beeld (zoals `make_video_fld.py`)

| Paneel | Positie | Inhoud |
| --- | --- | --- |
| Titel + status | linksboven | vette titel van de missie; regels "Werkrobot: ..." / "Bijvuller: ..." (grijs label, zwarte tekst) |
| Tijd | rechtsboven | "2.0× real time" (vet) en "veldtijd 1:10" |
| Minikaart + meters | rechtsonder | bovenaanzicht van het veld met genummerde banen en de robot als pijltje; balken voor tank (blauw), bijvuller (oranje), accu; regel met kerngetallen (l toegediend, pomp l/min, l/ha, hefhoogte) |
| Baanvolgorde | linksonder | "Baanvolgorde: 1 7 2 8 ...", gedaan in groen, huidige baan groot en oranje |
| Titelkaart | vol beeld, bij start van een missie/replay | vervaagd veld als achtergrond, ondertitel grijs, titel vet, uitleg in gewone tekst |

Stijlwaarden (één bestand `ui/src/theme.js`, overgenomen uit `make_video_fld.py`):

| Token | Waarde |
| --- | --- |
| Lettertype | Segoe UI (vet voor titels), terugval Arial; 20 vet / 16 / 13 / 10 px bij 1280×720, mee schalend |
| Tekst | ink `#1E1E1E`, muted `#5F5F5F` |
| Paneel | wit met 87 % dekking (`rgba(255,255,255,0.87)`), afgeronde hoeken 10 px, marge 14 px tot de rand, geen schaduw |
| Accenten | blauw `#1E64C8` (vloeistof/tank), groen `#3C8232` (gedaan), oranje `#E66E14` (huidig/bijvuller), paars `#7846AA` (tweede vloeistof) |
| Balken | grijze baan `#E6E6E6`, afgerond 3 px, label links, waarde rechts erboven |

Ook de bedienschermen van de sim (missie-editor, log-replay, instellingen) gebruiken dezelfde panelen, kleuren en
letters: licht, rustig, geen donker "dashboard"-thema.

**Controle**: zet in de sim de AB-missie en de camerapose van `field_run_simple` neer en leg een screenshot naast
`field_run_simple_refill.png`. Kleuren, randen, licht en panelen moeten op het oog gelijk zijn. Dat is een vaste
acceptatie van fase 2 en wordt bij elke release opnieuw gedaan.

---

## 4. Kaarten, speelwereld en GPS-nauwkeurigheid

| Bron | Wat | Gebruik |
| --- | --- | --- |
| PDOK BRP Gewaspercelen (OGC API / WFS) | perceelgrenzen NL, per jaar | veld kiezen met één klik |
| PDOK Luchtfoto (8 cm RGB / 25 cm) | ondergrond | 2D-kaart en schakelbare laag in 3D, offline tegels zoals `data/kaarttegels` |
| AHN4/AHN5 DTM 0,5 m (PDOK) | maaiveldhoogte zonder gebouwen en bomen | reliëf van de wereld, hellingen, sloottaluds, pitch/roll in de sim |
| BGT (PDOK, OGC API) | grootschalige topografie op dm-niveau: wegen, erf, water/sloten, grasland, bouwland, bermen | vlakken van de speelwereld, sloten als no-go-zone |
| 3D BAG (3dbag.nl) | alle gebouwen NL met hoogte (LoD 1.2 blokken, LoD 2.2 daken) | simpele gebouwen in de speelwereld |
| TOP10NL / BGT vegetatie | bomen, bomenrijen, heggen | simpele bomen (kegel/bol) |
| OpenStreetMap + Copernicus DEM 30 m | gebouwen, wegen, water, reliëf | terugval buiten Nederland (grover) |
| Eigen RTK-metingen | grenzen, obstakels, A/B-punten | **leidend** boven de luchtfoto (`RTK_Waypoint_Logger.py`, `RTK_points_maker.py`) |
| Import | GeoJSON, KML, Shapefile, ISOXML (TASKDATA.XML), CSV | grenzen, taakkaarten, onkruidpunten |

### Speelwereld van een echte plek

De sim opent op twee manieren:

| Modus | Wat je ziet | Waarvoor |
| --- | --- | --- |
| **Veld** | Eén perceel, vlak of met reliëf, in de stijl van `field_run_simple` | Een missie of aanbouwdeel testen, zoals de veldanimaties nu |
| **Wereld** (speelwereld) | Een heel gebied van de echte kaart: percelen, erf, schuren, wegen, sloten, bomen en het reliëf | Vrij rondrijden (handmatig of op missie), van het erf naar het veld, meerdere velden achter elkaar, de bijvuller laten aanrijden |

Een veld is een uitsnede van een wereld: in de wereld klik je op een perceel en kies je "open als veld".

**Wereld maken** (wereldbouwer, `openagbot_fieldsim/world/builder.py`):

1. Gebied kiezen: een veelhoek van punten (lat/lon), getekend op de kaart of geplakt als coördinaten, bijv. de
   vier hoeken van het eigen bedrijf (zie hieronder).
2. Downloaden (eenmalig, daarna offline): AHN-DTM, BGT, 3D BAG, BRP, TOP10NL en luchtfoto-tegels voor het gebied
   plus een rand van 50 m.
3. Bouwen in de FreeCAD-stijl van hoofdstuk 3:
   - **Reliëf**: AHN-DTM 0,5 m naar een terreinmesh, fijn (0,5 m) bij sloten en taluds, grover (2-4 m) op vlakke
     percelen, zodat het licht blijft. Gaten in AHN (onder gebouwen, water) worden opgevuld.
   - **Vlakken**: BGT-vlakken over het terrein gelegd, elk in een vaste vlakke kleur: bouwland en grasland in de
     veldgroenen, erf en verharding lichtgrijs, weg middengrijs, water lichtblauw, berm lichter groen.
   - **Gebouwen**: simpel. Footprint uit de BAG, opgetrokken tot de 3D BAG-hoogte (LoD 1.2: blok met plat dak; met
     een schakelaar LoD 2.2 voor eenvoudige zadeldaken). Muren gebroken wit, daken grijs of rood-bruin, zwarte randen.
     Geen ramen, deuren of texturen.
   - **Bomen**: stam plus kegel of bol, hoogte uit AHN-DSM als die er is.
   - **Randlijnen** alleen op perceelgrenzen, slootkanten, wegkanten en gebouwranden, **niet** op de driehoeken van
     het terrein (dat wordt een zwart raster). Zo blijft het de rustige FreeCAD-look.
4. Opslaan als **wereldmap** `worlds/<naam>/`:

```
worlds/<naam>/
  world.json          naam, gebiedsveelhoek (ETRS89 lat/lon), oorsprong lokaal ENU, bronnen + downloaddatum + licenties
  terrain.glb         reliëf met vlakken (gegenereerd)
  buildings.glb       gebouwen en bomen (gegenereerd)
  layers/*.geojson    BGT, BRP, water/no-go, eigen RTK-punten
  tiles/              luchtfoto-tegels (offline)
  missions/           missies in robotformaat (ab_line.json, waypoints.json)
  weeds/, taskmaps/   onkruidpunten, taakkaarten
  logs/               ritlogs voor replay
```

   De gegenereerde bestanden zijn altijd opnieuw te maken uit `world.json` (zoals de FCStd's uit de scripts);
   alleen `world.json`, `layers/` met eigen metingen, missies en taakkaarten zijn echte invoer.

Wat de speelwereld extra test:

- **Sloten en water** uit de BGT gaan als no-go-zone in de geofence. Een missie die er te dicht langs gaat wordt
  rood gemarkeerd, en in de sim rijdt de robot er echt in (kantelt, stopt) als de software het niet tegenhoudt.
- **Reliëf**: taluds, bolle percelen en drempels geven pitch en roll aan de GNSS-emulator, precies zoals de echte
  NAV-DAHEADING, zodat de pitch-correctie en de ruwheidsregeling getest worden.
- **Ontvangst**: gebouwen en bomen blokkeren satellieten. In een eenvoudig model (lijn naar de lucht onder een
  paar hoeken) zakt de fix bij de schuur naar RTK float of dgps, zodat je ziet wat de robot dan doet.
- **Handmatig rijden** met toetsenbord of gamepad gaat via `POST /api/nav/manual_drive` van de echte software, dus
  inclusief de dodemansknop (`manual_timeout_s`).
- **Terug naar FreeCAD**: `world_to_freecad.py` zet een uitsnede (terrein + gebouwen) in een FreeCAD-document, zodat
  de animaties van de aanbouwdelen ook op de echte plek kunnen spelen.

Eerste wereld: **eigen bedrijf**, opgeslagen in `worlds/eigen_bedrijf/world.json`. Vier hoekpunten:

| Hoek | lat | lon |
| --- | --- | --- |
| noordwest | 52.21470641961879 | 4.5889437679918235 |
| noordoost | 52.215009031989986 | 4.605753330989602 |
| zuidoost | 52.20949907691641 | 4.606383476714949 |
| zuidwest | 52.20932166170363 | 4.587836755231078 |

Ongeveer 1,27 km (oost-west) bij 0,60 km (noord-zuid), 72,6 ha. De ritlog van 2 nov 2026 ligt erbinnen, dus die
rit kan meteen in deze wereld teruggespeeld worden.

**Privacy**: werelden van een echte plek bevatten de eigen locatie, net als de ritlogs. `worlds/` staat daarom in
`.gitignore`; alleen een voorbeeldwereld (`worlds/example/`) gaat in git.

Let op voor "GPS-nauwkeurig":

- **Datum**: Centipede-RTK levert ETRS89-coördinaten; "WGS84" van Esri/Google ligt in 2026 in NL ca. 0,8 m daarnaast
  (ITRF-drift). PDOK (RD/ETRS89) past bij Centipede. De sim slaat alles op in ETRS89 en zet kaarten met pyproj om;
  per kaartlaag is een handmatige verschuiving in te stellen.
- **Luchtfoto's** zelf zijn niet RTK-nauwkeurig (orthofoto ca. 0,1-0,5 m). Dus: RTK-gemeten punten gaan voor, de
  luchtfoto is achtergrond. In de UI: "lijn de kaart uit op je gemeten A-B-punten".
- Rekenen gebeurt in lokaal ENU per veld (meters, double precision), zoals `AB_mission_maker.py` nu ook in meters werkt.

---

## 5. Missies: van sim naar veld en terug

**Eén bron voor de formaten**: de sim schrijft exact `data/ab_line.json` en `data/waypoints.json` zoals de robot ze
leest (velden `veldnaam`, `lat_a`, `lon_a`, `werkbreedte_m`, `baan_volgorde`, ...). De planlogica van
`AB_mission_maker.py` wordt een importeerbare module die zowel het losse script als de sim gebruikt, zodat er
één implementatie is.

Stroom:

1. Veld kiezen (BRP of RTK-grens) -> AB-lijn, werkbreedte, kopakker, volgorde (skip-pass) instellen.
2. **Simuleren** met de echte software: kopakkerbochten, afwijking, tijd, accu-verbruik, overlap/gemiste stroken.
3. Exporteren: bestand, of rechtstreeks naar de robot via zijn API (`POST /api/waypoints`, of `ab_line.json`
   kopiëren). De sim zet een **missie-hash + simulatieresultaat** in de missie, zodat je in het veld ziet dat precies
   deze missie getest is. Versturen naar de robot vraagt altijd een bevestiging en gebeurt nooit tijdens het rijden.
4. In het veld rijden; ritlog (`data/logs/rit_*.csv`) terug naar de sim.
5. **Log-replay**: geplande lijn, gesimuleerde rit en echte rit over elkaar, met afwijking, snelheid, stuurhoek en
   motorstromen in de tijd (de CSV heeft al 60 kolommen). De verschillen gebruik je om het voertuigmodel te ijken
   (slip, stuurvertraging, heading-offset), zodat de sim steeds beter het veld voorspelt.

### Onkruidmissies

1. Onkruidpunten inladen: uit de detectielogger van de robot (`data/detections`, `/api/weeds`), uit een
   drone-kaart, of handmatig gemeten met RTK.
2. Route berekenen: volgorde over alle planten (TSP) met de draaicirkel van de robot (Dubins-bochten), of per baan
   langsrijden en bij elke plant stoppen (zoals ridderzuringboor v7: de robot stuurt naar de plant).
3. Simuleren met het aanbouwdeel: cyclustijd per plant (boren, Z-as), totale tijd, gemiste planten.
4. Exporteren als waypoint-missie; na de rit vergelijken: welke planten zijn behandeld, hoe ver ernaast.

---

## 6. Aanbouwdelen in de sim

Elk aanbouwdeel uit `F:\veldrobot\aanbouwdelen` wordt een **plugin** met:

| Onderdeel | Bron |
| --- | --- |
| 3D-model `.glb` met bewegende nodes | geëxporteerd uit het gegenereerde FCStd (script `export_glb.py` in de aanbouwdelen-repo, met kleuren, transparantie en randen, zie hoofdstuk 3) |
| Montage, werkbreedte, secties, massa, zwaartepunt | `<p>_params.py` en `mass_properties()` (geen getallen overtypen) |
| Procesmodel | `<p>_calc.py` (bijv. `lfa_calc.py`: debiet per sectie bij snelheid x werkbreedte) |
| Gedrag in de sim | klein Python-bestand `sim_<p>.py`: `update(dt, robot_state, taakkaart) -> {dosis, krachten, animatie-standen}` |

Effecten op het voertuig: massa en zwaartepunt (aslast, tractie, accu), trekkracht (cambridge roll, getrokken via
de trekhaak: hitch-kinematica), zijdelingse krachten. Gebruik van de bestaande calc-modules houdt sim, animatie en
rapport op dezelfde getallen, net als nu tussen animatie en rapport.

### Taakkaarten (variabel doseren, bijv. vloeibare kunstmest)

- Inladen: ISOXML TASKDATA (ISO 11783-10), Shapefile/GeoJSON met dosis per zone (l/ha), of een raster (GeoTIFF,
  bijv. uit NDVI of bodemscan).
- De sim rekent per sectie per tijdstap: gewenste dosis onder de sectie (met voorkijk-tijd voor de pomp- en
  leidingvertraging) -> gewenst debiet = dosis x werkbreedte_sectie x snelheid -> pomptoerental.
- Resultaat: **toegediende-dosiskaart** naast de voorschriftkaart, met fout per zone en totaalverbruik.
- Voor het echte veld moet OpenAgbot een implement-regelaar krijgen (pomp-PWM of toerental via Arduino/CAN, secties
  aan/uit op kopakker en overlap). Die wordt in de sim **eerst** gebouwd en getest met een geëmuleerde pomp-Arduino,
  precies zoals de stuur-Arduino. Daarna gaat dezelfde code naar de robot.

---

## 7. Fasering

Elke fase levert iets bruikbaars op en eindigt met een controleerbare acceptatie.

| Fase | Inhoud | Klaar als |
| --- | --- | --- |
| **0. Fundament** | Repo `openagbot-fieldsim` (git), Python-project (uv/pyproject), mappenstructuur, coördinaten-module (ETRS89 <-> ENU <-> RD) met tests | Tests: rondreis lat/lon -> ENU -> lat/lon < 1 mm |
| **1. SIL-kern** | Voertuigmodel; emulators GNSS (NMEA+UBX, 25 Hz), stuur-Arduino, VESC-CAN; de 3 kleine OpenAgbot-aanpassingen; `config.sim.json`; start-script dat sim + OpenAgbot opstart | De echte OpenAgbot rijdt `ab_line.example.json` op een plat veld; de webinterface op :8000 werkt; er komt een normale `rit_*.csv` uit |
| **2. Sim-UI in FreeCAD-stijl** | pywebview-venster; 3D-veld in de stijl van hoofdstuk 3 (vlak veld, robot-`.glb` met randen, volgcamera, panelen); `export_glb.py` voor de robot; 2D-kaart als tweede tabblad; start/stop; storingen injecteren (RTK float, GNSS weg, CAN weg, wiel stall) | Screenshot naast `field_run_simple_refill.png` ziet er hetzelfde uit; elke ingeschoten storing laat de robot correct reageren (langzaam/stop) |
| **3. Veld & missie-editor** | BRP-percelen, RTK-grens importeren, AB-planner als gedeelde module, waypoints, export naar robotformaat, push naar robot via API | Missie gemaakt in de sim rijdt op de robot zonder handwerk |
| **4. Log-replay & ijken** | CSV-replay, vergelijking plan/sim/echt, ijken voertuigmodel op de rit van 2 nov 2026; (4b) ruwe GNSS-log terugspelen naar de software | Sim voorspelt de afwijking van de echte rit binnen enkele cm (doel vaststellen na eerste vergelijking) |
| **5. Speelwereld, reliëf & aanbouwdelen-modellen** | Wereldbouwer (AHN, BGT, 3D BAG, BRP, bomen) in FreeCAD-stijl, wereldmappen opslaan/openen, modus Veld en Wereld, sloten als no-go, eenvoudig ontvangstmodel bij gebouwen, aanbouwdelen-`.glb` via `export_glb.py`, pitch/roll uit terrein naar GNSS-emulator | Wereld `eigen_bedrijf` gebouwd en offline te openen; ritlog van 2 nov 2026 speelt erin af; pitch uit het AHN-reliëf komt overeen met de gelogde pitch van die rit |
| **6. Aanbouwdelen & taakkaarten** | Plugin-interface, eerste plugins: vloeibare kunstmest (lfa, met VRA) en ridderzuringboor v7/v8; implement-regelaar in OpenAgbot + pomp-emulator | Taakkaart in, toegediende-dosiskaart uit, fout per zone < 5 % (sim) |
| **7. Onkruidroutes** | Onkruidpunten importeren, route-optimalisatie, simuleren met boor-cyclustijd, export | Route voor 200 planten gepland, gesimuleerd en als missie geëxporteerd |
| **8. .exe & release** | PyInstaller onedir + Inno Setup, GitHub Actions bij tag `v*`, installer op GitHub Releases, OpenAgbot meegeleverd als versie of map kiezen | Schone Windows-pc: installer draaien, sim start, demo-missie rijdt |
| **Later** | Camera-simulatie (gerenderde beelden naar YOLO), sneller dan real-time (klok-abstractie), ROS 2/Foxglove-brug (MCAP-export), meerdere robots, XL-robot | - |

De .exe staat als fase 8, maar de build wordt al vanaf fase 2 elke release meegebouwd, zodat er geen verrassingen
aan het eind komen (bijv. WebView2, pyproj-data, python-can plugins in PyInstaller).

---

## 8. Mappenstructuur openagbot-fieldsim

```
openagbot-fieldsim/
  PLAN.md                  dit plan
  README.md                gebruik
  pyproject.toml
  openagbot_fieldsim/
    app.py                 start sim-server + venster + OpenAgbot-subproces
    clock.py
    world/                 geo.py (pyproj), field.py, terrain.py (AHN), maps.py (PDOK/BRP), weeds.py, taskmap.py,
                           builder.py (wereldbouwer: AHN/BGT/3D BAG/bomen -> glb), buildings.py, receptie.py,
                           world_to_freecad.py
    vehicle/               bruut_model.py (params uit agbot_params), tire.py (slip)
    devices/               gnss_emulator.py (NMEA/UBX), stepper_emulator.py, vesc_emulator.py, sim_can.py
    missions/              ab_planner.py (gedeeld met AB_mission_maker), waypoints.py, weed_route.py, export.py
    replay/                ride_log.py, compare.py, calibrate.py
    implements/            base.py, lfa.py, dwd.py, ... (gebruiken <p>_params/<p>_calc uit aanbouwdelen)
    api/                   FastAPI-routes + WebSocket voor de UI
  ui/                      Svelte + MapLibre/Leaflet + Threlte, build -> openagbot_fieldsim/static; src/theme.js = stijl (H3)
  assets/                  glb-modellen (gegenereerd, niet in git), offline kaarttegels
  configs/                 config.sim.json, scenario's (veld + weer + storingen)
  worlds/                  wereldmappen (world.json + gegenereerde glb/tegels); niet in git behalve worlds/example/
  tests/                   unit-tests + "scenario-tests": missie rijden en afwijking controleren
  packaging/               pyinstaller.spec, installer.iss
  .github/workflows/       build-exe.yml
```

Scenario-tests (fase 1 en verder) kunnen in CI zonder UI draaien: "rijd deze AB-missie, afwijking op de lijn
moet < 5 cm zijn, geen stop door GNSS". Zo wordt elke wijziging aan de navigator automatisch getest.

---

## 9. Risico's en open punten

| Risico | Maatregel |
| --- | --- |
| Sim is te optimistisch (geen slip, perfecte GNSS) | Ruis, latentie, uitval en slip instelbaar; ijken op echte ritlogs (fase 4) |
| Echte robot rijdt een missie die in de sim goed ging maar het veld is anders (sloot, paal) | Geofence + obstakels uit RTK-metingen in de missie; sim controleert of de hele route binnen de grens blijft; altijd toezicht en noodstop (zie disclaimer OpenAgbot) |
| `ultralytics`/torch maakt de .exe groot | Vision is al optioneel in `endpoints.py`; sim-build zonder torch, apart "vision-pakket" later |
| OpenAgbot verandert | Sim draait tegen een gekozen OpenAgbot-map of git-versie; scenario-tests vangen breuk op |
| Datum-fouten (ETRS89 vs WGS84) | Alles intern ETRS89, kaartlagen met bekende datum, uitlijnfunctie op RTK-punten |
| Taakkaart-formaten verschillen per leverancier | Begin met GeoJSON/Shapefile + ISOXML; andere via conversie |
| Eigen locatie lekt via git | `worlds/` in `.gitignore`, alleen `worlds/example/` met een verzonnen gebied in git |
| Wereld wordt te zwaar (AHN 0,5 m = miljoenen punten) | Adaptieve terreinmesh (fijn bij sloten, grof op percelen), tegels per 250 m, alleen de zichtbare in het geheugen |
| Brondata verouderd of fout (BGT/3D BAG loopt achter, nieuwe schuur) | Downloaddatum in `world.json`; eigen RTK-metingen en zelf getekende gebouwen gaan voor |
| Licenties van kaartdata | PDOK, AHN, BGT, 3D BAG: open (CC0/CC-BY); OSM: ODbL met naamsvermelding; Esri-luchtfoto niet in de .exe meeleveren |

Beslissingen die nodig zijn van jou vóór fase 1:

1. Akkoord met de drie kleine aanpassingen in OpenAgbot (`serial_for_url`, sim-CAN, sim-config)?
2. `openagbot-fieldsim` als eigen GitHub-repo (zoals de implements), open source onder AGPL-3.0 zoals OpenAgbot?
