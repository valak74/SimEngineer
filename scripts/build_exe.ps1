# Construit dist\simengineer\simengineer.exe (dossier autonome) avec PyInstaller.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\python.exe -m pip install pyinstaller
& .\.venv\Scripts\pyinstaller.exe --noconfirm --distpath dist --workpath build packaging\simengineer.spec
Write-Host "Exécutable : dist\simengineer\simengineer.exe"
Write-Host "Copier simengineer.toml et le dossier models\ à côté de l'exécutable."
