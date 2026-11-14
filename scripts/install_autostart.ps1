# Démarrage automatique : crée un raccourci dans le dossier « Démarrage » de Windows.
# SimEngineer se lance réduit à l'ouverture de session et attend iRacing (sondage toutes
# les 2 s, CPU négligeable). Il se remet en attente après chaque session.
# Désinstaller : powershell -File scripts\install_autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "SimEngineer.lnk"

if ($Remove) {
    if (Test-Path $link) { Remove-Item $link; Write-Host "Démarrage automatique supprimé." }
    exit 0
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $root "scripts\simengineer.bat"
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7   # fenêtre réduite
$shortcut.Description = "SimEngineer - ingénieur de course iRacing"
$shortcut.Save()
Write-Host "Raccourci créé : $link"
