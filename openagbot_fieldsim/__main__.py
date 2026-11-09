"""OpenAgbot FieldSim starten.

  python -m openagbot_fieldsim                    venster (pywebview) met de sim, laadt de wereld uit settings (example)
  python -m openagbot_fieldsim --world NAAM       andere wereld
  python -m openagbot_fieldsim --browser          in de gewone browser in plaats van een eigen venster
  python -m openagbot_fieldsim --headless         alleen de server (tests, CI)
  python -m openagbot_fieldsim --no-agbot         zonder OpenAgbot-backend (alleen de wereld bekijken)
  python -m openagbot_fieldsim --make-example     fantasiewereld opnieuw genereren
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--agbot-runner":           # in de .exe: subproces voor de OpenAgbot-backend
        from openagbot_fieldsim.agbot.runner import main as runner_main
        runner_main(argv[1:])
        return

    ap = argparse.ArgumentParser(prog="openagbot-fieldsim", description="Simulatiewereld voor de Bruut OpenAgbot")
    ap.add_argument("--world")
    ap.add_argument("--agbot-dir")
    ap.add_argument("--port", type=int)
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-agbot", action="store_true")
    ap.add_argument("--make-example", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    from openagbot_fieldsim.settings import load_settings
    settings = load_settings()
    if args.make_example:
        from openagbot_fieldsim.world.fantasy import main as make
        print(make(settings.worlds_dir))
        return
    if args.agbot_dir:
        settings.agbot_dir = args.agbot_dir
    if args.port:
        settings.ui_port = args.port
    if args.world:
        settings.world = args.world
    if args.no_agbot:
        settings.start_agbot = False

    import uvicorn

    from openagbot_fieldsim.api.server import create_app
    from openagbot_fieldsim.app import SimApp

    sim_app = SimApp(settings)
    sim_app.load()
    app = create_app(sim_app)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.ui_port, log_level="warning"))
    url = f"http://127.0.0.1:{settings.ui_port}/"
    try:
        if args.headless:
            print(f"OpenAgbot FieldSim draait op {url} (OpenAgbot: {sim_app.agbot.url})")
            server.run()
            return
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(0.05)
        if args.browser:
            webbrowser.open(url)
            while True:
                time.sleep(1)
        try:
            import webview
        except ImportError:
            webbrowser.open(url)
            while True:
                time.sleep(1)
        webview.create_window("OpenAgbot FieldSim - Bruut OpenAgbot", url, width=1440, height=900, min_size=(900, 600))
        webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        sim_app.shutdown()


if __name__ == "__main__":
    main()
