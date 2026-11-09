"""Start de OpenAgbot-backend in dit proces, tegen de sim (wordt door AgbotProcess als subproces gestart).

Doet hetzelfde als `run.py` van OpenAgbot, met drie verschillen:
  - de python-can interface `simcan` wordt eerst aangemeld (CAN over TCP naar de sim);
  - de frontend wordt met een absoluut pad gekoppeld, want de werkmap is de sim-werkmap (runtime/agbot);
  - de server luistert alleen op 127.0.0.1 (geen firewallvraag, niet bereikbaar van buiten).
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="openagbot_fieldsim.agbot.runner")
    ap.add_argument("--agbot-dir", required=True)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    # OpenAgbot print emoji's; in de .exe negeert Python PYTHONIOENCODING, dus de pijp expliciet op UTF-8 zetten.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    from openagbot_fieldsim.devices.can_link import register
    register()

    agbot_dir = os.path.abspath(args.agbot_dir)
    sys.path.insert(0, agbot_dir)
    print(f"[sim] OpenAgbot uit {agbot_dir}, werkmap {os.getcwd()}, config {os.environ.get('AGBOT_CONFIG')}",
          flush=True)

    import uvicorn
    from fastapi.staticfiles import StaticFiles

    from app import create_app  # noqa: E402  (pas na sys.path en register)

    app = create_app()
    dist = os.path.join(agbot_dir, "frontend", "dist")
    if os.path.isdir(dist) and not any(getattr(r, "name", "") == "frontend" for r in app.routes):
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    uvicorn.run(app, host="127.0.0.1", port=args.port, reload=False, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
