# SimEngineer — installation en une commande (Windows 10/11) :
#   irm https://raw.githubusercontent.com/valak74/SimEngineer/ccr-13bdc1b6-mxglbc/scripts/bootstrap.ps1 | iex
# Installe si besoin Python 3.12 et Git (winget), récupère SimEngineer dans
# %USERPROFILE%\SimEngineer, puis lance l'installation complète.
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/valak74/SimEngineer.git"
$Branch = if ($env:SIMENGINEER_BRANCH) { $env:SIMENGINEER_BRANCH } else { "ccr-13bdc1b6-mxglbc" }
$Dir = if ($env:SIMENGINEER_DIR) { $env:SIMENGINEER_DIR } else { Join-Path $HOME "SimEngineer" }

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Winget-Install($id) {
    if (-not (Has "winget")) { throw "winget absent : installe « App Installer » depuis le Microsoft Store." }
    winget install -e --id $id --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

Write-Host "SimEngineer — installation automatique" -ForegroundColor Cyan

Step "Python 3.12"
$py = $null
try { $py = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null) } catch {}
if (-not $py) {
    Winget-Install "Python.Python.3.12"
    $cand = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path $cand) { $py = $cand } else { $py = (& py -3.12 -c "import sys; print(sys.executable)") }
}
$py = "$py".Trim()
Write-Host "OK : $py"

Step "Git"
if (-not (Has "git")) { Winget-Install "Git.Git" }
if (-not (Has "git")) { $env:Path += ";$env:ProgramFiles\Git\cmd" }
Write-Host "OK : $((git --version) -join ' ')"

Step "SimEngineer ($Branch) dans $Dir"
if (Test-Path (Join-Path $Dir ".git")) {
    git -C $Dir fetch --quiet origin $Branch
    git -C $Dir checkout --quiet $Branch
    git -C $Dir pull --quiet --ff-only origin $Branch
} else {
    git clone --quiet --branch $Branch $Repo $Dir
}

& (Join-Path $Dir "scripts\install_windows.ps1") -Python $py
