import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

AGBOT_DIR = os.environ.get("FIELDSIM_AGBOT_DIR", r"F:\Agbot\Bruut_OpenAgbot")


def load_agbot_module(rel_path: str, name: str):
    """Laad één bestand van OpenAgbot zonder het pakket `app` te importeren (dat start de hele backend)."""
    path = os.path.join(AGBOT_DIR, rel_path)
    if not os.path.exists(path):
        pytest.skip(f"OpenAgbot niet gevonden: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def agbot_config():
    import json
    path = os.path.join(AGBOT_DIR, "data", "config.sim.json")
    if not os.path.exists(path):
        pytest.skip("data/config.sim.json van OpenAgbot ontbreekt")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
