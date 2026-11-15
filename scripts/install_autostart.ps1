# (Re)crée les raccourcis : Bureau + démarrage avec Windows. L'installateur le fait déjà.
# Désinstaller le démarrage automatique : powershell -File scripts\install_autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$startup = Join-Path ([Environment]::GetFolderPath("Startup")) "SimEngineer.lnk"
if ($Remove) {
    if (Test-Path $startup) { Remove-Item $startup; Write-Host "Démarrage automatique supprimé." }
    exit 0
}
& (Join-Path $PSScriptRoot "install_windows.ps1") -Unattended
