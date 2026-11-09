"""Bouw openagbot-fieldsim als Windows-programma: dist/openagbot-fieldsim/openagbot-fieldsim.exe (+ installer als Inno Setup er is).

  python packaging/build_exe.py                         UI bouwen, PyInstaller, voorbeeldwereld erbij
  python packaging/build_exe.py --with-agbot DIR        OpenAgbot-software (app/, frontend/dist, data/*.example.json,
                                                        data/config.sim.json) meeleveren in dist/openagbot-fieldsim/agbot
  python packaging/build_exe.py --installer             ook een setup.exe maken met Inno Setup (iscc op PATH)

De .exe start de sim in een eigen venster (WebView2, zit in Windows 10/11). Zonder meegeleverde OpenAgbot kijkt hij
naar settings.json (agbot_dir) naast de .exe.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
APP = os.path.join(DIST, "openagbot-fieldsim")


def run(cmd, cwd=ROOT):
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=cwd, shell=(os.name == "nt" and cmd[0] in ("npm", "npx")))


def build_ui():
    ui = os.path.join(ROOT, "ui")
    if not os.path.isdir(os.path.join(ui, "node_modules")):
        run(["npm", "ci"], cwd=ui)
    run(["npm", "run", "build"], cwd=ui)


def pyinstaller():
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", os.path.join("packaging", "openagbot_fieldsim.spec")])


def copy_tree(src, dst, ignore=None):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def add_worlds():
    copy_tree(os.path.join(ROOT, "worlds", "example"), os.path.join(APP, "worlds", "example"))
    for name in ("README.md", "PLAN.md"):
        shutil.copy2(os.path.join(ROOT, name), APP)


def add_agbot(agbot_dir: str):
    """Alleen wat de backend nodig heeft; geen eigen config.json, ritlogs of gewichten (privé/groot)."""
    dst = os.path.join(APP, "agbot")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    copy_tree(os.path.join(agbot_dir, "app"), os.path.join(dst, "app"),
              ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copy_tree(os.path.join(agbot_dir, "frontend", "dist"), os.path.join(dst, "frontend", "dist"))
    os.makedirs(os.path.join(dst, "data"), exist_ok=True)
    for name in ("config.sim.json", "config.example.json", "ab_line.example.json", "waypoints.example.json"):
        src = os.path.join(agbot_dir, "data", name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, "data", name))
    for name in ("LICENSE", "README.md"):
        if os.path.exists(os.path.join(agbot_dir, name)):
            shutil.copy2(os.path.join(agbot_dir, name), dst)


def installer():
    iss = os.path.join(ROOT, "packaging", "installer.iss")
    exe = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if not os.path.exists(exe):
        print("Inno Setup niet gevonden; installer overgeslagen (dist/openagbot-fieldsim kan ook als zip)")
        return
    run([exe, iss])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-agbot", metavar="DIR")
    ap.add_argument("--installer", action="store_true")
    ap.add_argument("--skip-ui", action="store_true")
    args = ap.parse_args()
    if not args.skip_ui:
        build_ui()
    pyinstaller()
    add_worlds()
    if args.with_agbot:
        add_agbot(args.with_agbot)
    if args.installer:
        installer()
    print(f"klaar: {os.path.join(APP, 'openagbot-fieldsim.exe')}")


if __name__ == "__main__":
    main()
