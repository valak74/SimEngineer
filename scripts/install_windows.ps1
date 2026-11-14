# Installation de SimEngineer sur le PC de jeu (Windows 10/11, Python 3.12).
# Usage : clic droit > « Exécuter avec PowerShell », ou :  powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

py -3.12 --version
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[live,llm,voice,perf]"

if (-not (Test-Path "simengineer.toml")) {
    Copy-Item "simengineer.example.toml" "simengineer.toml"
    (Get-Content "simengineer.toml") -replace 'kind = "synthetic"', 'kind = "live"' | Set-Content "simengineer.toml"
    Write-Host "simengineer.toml créé (source = live)."
}
New-Item -ItemType Directory -Force -Path "models" | Out-Null
Write-Host ""
Write-Host "Installation terminée. Étapes suivantes : docs\RUN_ON_PC.md"
Write-Host "  1. Télécharger les voix Piper dans le dossier models\"
Write-Host "  2. Définir les clés d'API (variables d'environnement ou fichier .env)"
Write-Host "  3. .\.venv\Scripts\simengineer.exe say-test"
