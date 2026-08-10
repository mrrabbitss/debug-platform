param(
  [switch]$InstallLocalModels,
  [switch]$ConfigureMcp,
  [switch]$SkipFrontendBuild
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebug'
$Venv = Join-Path $RuntimeRoot 'venv'
$SkillTarget = Join-Path $HOME '.claude\skills\gw-ap-debug'
New-Item -ItemType Directory -Force -Path $RuntimeRoot, (Join-Path $RuntimeRoot 'data'), (Join-Path $RuntimeRoot 'storage'), (Join-Path $RuntimeRoot 'logs'), (Join-Path $RuntimeRoot 'runtime') | Out-Null

$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
  if ($LASTEXITCODE -eq 0) { $Python = @('py','-3.12') }
}
if (-not $Python -and (Get-Command python -ErrorAction SilentlyContinue)) {
  & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
  if ($LASTEXITCODE -eq 0) { $Python = @('python') }
}
if (-not $Python) { throw 'Python 3.11+ was not found. Python 3.12 is recommended.' }
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
  if ($Python.Count -eq 2) { & $Python[0] $Python[1] -m venv $Venv } else { & $Python[0] -m venv $Venv }
}
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Constraints = Join-Path $Root 'backend\constraints.lock'
& $VenvPython -m pip install --upgrade --constraint $Constraints pip setuptools wheel
$Target = Join-Path $Root 'backend'
if ($InstallLocalModels) { & $VenvPython -m pip install --constraint $Constraints -e "${Target}[local-models]" } else { & $VenvPython -m pip install --constraint $Constraints -e $Target }

if (-not $SkipFrontendBuild) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm was not found. Install Node.js 20.19+ or 22.12+ to build the optional Web UI.' }
  Push-Location (Join-Path $Root 'frontend')
  try {
    npm ci
    $env:VITE_PUBLIC_BASE='/ui/'
    npm run build
  } finally { Pop-Location }
}

if (Test-Path $SkillTarget) { Remove-Item -Recurse -Force $SkillTarget }
New-Item -ItemType Directory -Force -Path (Split-Path $SkillTarget) | Out-Null
Copy-Item -Recurse -Force (Join-Path $Root '.claude\skills\gw-ap-debug') $SkillTarget

$Bin = Join-Path $Venv 'Scripts'
$UserPath = [Environment]::GetEnvironmentVariable('Path','User')
if (($UserPath -split ';') -notcontains $Bin) {
  [Environment]::SetEnvironmentVariable('Path', (($UserPath.TrimEnd(';') + ';' + $Bin).Trim(';')), 'User')
}

& (Join-Path $Root 'scripts\configure_agent_integrations.ps1') -VenvPython $VenvPython -ConfigureClaude:$ConfigureMcp
Write-Host "Installed GW/AP Debug Agent Runtime. Runtime data: $RuntimeRoot"
Write-Host "Open a new terminal, then run: gwap start ; gwap doctor"
