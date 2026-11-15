# Lanceur de SimEngineer : mise à jour automatique puis attente d'iRacing.
# Utilisé par les raccourcis du Bureau et du démarrage de Windows.
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Se = Join-Path $Root ".venv\Scripts\simengineer.exe"
New-Item -ItemType Directory -Force -Path "data\logs" | Out-Null
$UpdateLog = "data\logs\mise-a-jour.log"

# Mise à jour (silencieuse ; en cas d'échec on démarre la version installée)
if (Get-Command git -ErrorAction SilentlyContinue) {
    $before = (git rev-parse HEAD 2>$null)
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    git pull --ff-only --quiet origin $branch *>> $UpdateLog
    $after = (git rev-parse HEAD 2>$null)
    if ($before -and $after -and $before -ne $after) {
        "$(Get-Date -Format s) mise à jour $before -> $after" | Add-Content $UpdateLog
        & $Py -m pip install -e ".[live,llm,voice,perf]" --quiet *>> $UpdateLog
    }
}

& $Se run --source live
