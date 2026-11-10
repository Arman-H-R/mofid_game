# PyInstaller recipe for MarketPulse.exe. Build with: python tools/build_exe.py
from pathlib import Path

ROOT = Path(SPECPATH)
# The artwork the game loads; assets/documentation is only for people.
datas = [(str(p), str(p.parent.relative_to(ROOT))) for p in (ROOT / "assets").rglob("*")
         if p.is_file() and "documentation" not in p.parts]
datas.append((str(ROOT / "config" / "settings.json"), "config"))
datas.append((str(ROOT / "config" / "lidar.json"), "config"))  # the LiDAR tuning (input.lidar.settings_file)
if (ROOT / "config" / "camera_calibration.json").is_file():  # input.camera.calibration_file; F2 then K writes a new one next to the .exe
    datas.append((str(ROOT / "config" / "camera_calibration.json"), "config"))
# The player photo: YOLOv4-tiny and OpenCV's face finders (camera.model_dir).
datas += [(str(p), "models") for p in (ROOT / "models").glob("*") if p.is_file()]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["serial", "serial.tools.list_ports", "market_pulse.input.wallcam.source", "av"],  # imported only when the LiDAR / camera start
    excludes=["tkinter", "pytest", "fontTools", "uharfbuzz", "pymupdf", "PIL", "setuptools", "pydoc", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="MarketPulse",
    console=False,
    upx=False,
    icon=str(ROOT / "scripts" / "market_pulse.ico"),
)
