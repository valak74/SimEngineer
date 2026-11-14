# Spécification PyInstaller (dossier autonome, sans installation de Python sur le PC).
# Construire sous Windows :  scripts\build_exe.ps1
# NON TESTÉ sous Windows depuis l'environnement de développement (voir docs/RUN_ON_PC.md).
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], ["irsdk", "yaml"]
for pkg in ("faster_whisper", "ctranslate2", "piper", "onnxruntime", "sounddevice"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["launcher.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pandas", "torch"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="simengineer", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="simengineer")
