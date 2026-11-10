# PyInstaller-spec voor openagbot-fieldsim (onedir: snel opstarten, makkelijk bij te werken).
# Bouwen: python packaging/build_exe.py
# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# De OpenAgbot-backend draait als subproces van dezelfde .exe (--agbot-runner) en importeert zijn eigen code uit de
# agbot-map. Alles wat DIE code nodig heeft moet dus in de bundel zitten, ook al importeert openagbot_fieldsim het zelf niet.
hidden = (
    collect_submodules("openagbot_fieldsim")
    + collect_submodules("serial")
    + collect_submodules("can.interfaces")
    + collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + collect_submodules("shapely")
    + ["fastapi.staticfiles", "fastapi.middleware.cors", "starlette.staticfiles", "pydantic", "multipart",
       "webview", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr_loader", "pythonnet"]
)

datas = [(os.path.join(ROOT, "openagbot_fieldsim", "static"), os.path.join("openagbot_fieldsim", "static"))]
datas += [(os.path.join(ROOT, "openagbot_fieldsim", "implements", "assets"), os.path.join("openagbot_fieldsim", "implements", "assets"))]
datas += collect_data_files("webview")

a = Analysis(
    [os.path.join(ROOT, "openagbot_fieldsim", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "torch", "ultralytics", "cv2", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="openagbot-fieldsim",
    console=False,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="openagbot-fieldsim")
