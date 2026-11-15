# Installation complète de SimEngineer (Windows 10/11). Rejouable sans risque.
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
# Options : -Unattended (aucune question : pas de clés, pas de bouton, pas de son)
#           -Python <chemin de python.exe 3.12>   -NoShortcuts
param(
    [switch]$Unattended,
    [string]$Python = "",
    [switch]$NoShortcuts
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Find-Python312 {
    try {
        $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    } catch {}
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     "C:\Program Files\Python312\python.exe")) {
        if (Test-Path $c) { return $c }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $v = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
        if ($v -eq "3.12") { return $cmd.Source }
    }
    return $null
}

if (-not $Python) { $Python = Find-Python312 }
if (-not $Python) { throw "Python 3.12 introuvable. Lance d'abord scripts\bootstrap.ps1 (il l'installe)." }
Write-Host "Python : $Python"

Step "Environnement Python (.venv)"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $Python -m venv .venv }
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Se = Join-Path $Root ".venv\Scripts\simengineer.exe"
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -e ".[live,llm,voice,perf]" --quiet
if ($LASTEXITCODE -ne 0) { throw "Échec de l'installation des dépendances." }

Step "Configuration"
if (-not (Test-Path "simengineer.toml")) {
    Copy-Item "simengineer.example.toml" "simengineer.toml"
    & $Se config-set source kind live | Out-Null
    Write-Host "simengineer.toml créé (source : iRacing en direct)."
} else { Write-Host "simengineer.toml conservé." }

Step "Voix (téléchargement unique, ~130 Mo)"
& $Se voices
if ($LASTEXITCODE -ne 0) { Write-Warning "Voix non téléchargées : relance plus tard « simengineer voices »." }
& $Se say-test --synth-only
if ($LASTEXITCODE -ne 0) { Write-Warning "La synthèse vocale ne fonctionne pas encore (voir le diagnostic)." }

if (-not $Unattended) {
    Step "Clés d'API (facultatif : Entrée pour passer, tout marche sans)"
    $envFile = Join-Path $Root ".env"
    $lines = @()
    if (Test-Path $envFile) { $lines = Get-Content $envFile }
    foreach ($k in @("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")) {
        if ($lines -match "^$k=") { Write-Host "$k déjà renseignée."; continue }
        $v = Read-Host "  $k"
        if ($v) { $lines += "$k=$v" }
    }
    if ($lines.Count -gt 0) { Set-Content -Path $envFile -Value $lines -Encoding UTF8 }

    Step "Bouton pour parler à l'ingénieur"
    Write-Host "Appuie sur le bouton de ton volant (ou une touche) dans les 30 secondes."
    & $Se ptt-detect --timeout 30
    if ($LASTEXITCODE -ne 0) { Write-Host "Ignoré : tu pourras relancer « simengineer ptt-detect »." }

    Step "Test du son (tu dois entendre 4 phrases)"
    & $Se say-test
}

if (-not $NoShortcuts) {
    Step "Raccourcis (Bureau + démarrage avec Windows)"
    $shell = New-Object -ComObject WScript.Shell
    $target = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnkArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$Root\scripts\start.ps1`""
    $places = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"))
    foreach ($dir in $places) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "SimEngineer.lnk"))
        $lnk.TargetPath = $target
        $lnk.Arguments = $lnkArgs
        $lnk.WorkingDirectory = $Root
        $lnk.WindowStyle = 7
        $lnk.Description = "SimEngineer - ingénieur de course iRacing"
        $lnk.Save()
    }
    Write-Host "SimEngineer démarrera avec Windows et attendra iRacing (sans consommer de CPU)."
}

Step "Terminé"
Write-Host "Lance iRacing et roule : l'interface s'ouvrira toute seule."
Write-Host "Après ta première séance, un fichier « SimEngineer-diagnostic-....zip » apparaîtra"
Write-Host "sur ton Bureau : envoie-le dans la conversation."
