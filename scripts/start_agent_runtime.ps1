param([int]$Port = 8765)
$ErrorActionPreference='Stop'
$Root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimeRoot=Join-Path $env:LOCALAPPDATA 'GWAPDebug'
$VenvPython=Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) { throw 'Agent runtime is not installed. Run scripts\install_agent_runtime.bat first.' }
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeRoot 'data'),(Join-Path $RuntimeRoot 'storage'),(Join-Path $RuntimeRoot 'logs'),(Join-Path $RuntimeRoot 'runtime') | Out-Null
$Db=(Join-Path $RuntimeRoot 'data\gw_ap_debug.db').Replace('\','/')
$env:APP_ENV='dev'
$env:AUTH_MODE='local'
$env:AGENT_MODE='external'
$env:AGENT_RUNTIME_HOST='127.0.0.1'
$env:AGENT_RUNTIME_PORT="$Port"
$env:SERVE_FRONTEND='true'
$env:FRONTEND_DIST=(Join-Path $Root 'frontend\dist')
$env:DATA_ROOT_PATH=(Join-Path $RuntimeRoot 'data')
$env:DATABASE_URL="sqlite:///$Db"
$env:STORAGE_ROOT=(Join-Path $RuntimeRoot 'storage')
$env:MODEL_ROOTS=(Join-Path $Root 'models')
$env:CORS_ORIGINS="http://127.0.0.1:$Port,http://localhost:$Port"
$env:GWAP_RUNTIME_URL="http://127.0.0.1:$Port"
$PidFile=Join-Path $RuntimeRoot 'runtime\agent-runtime.pid'
$LogFile=Join-Path $RuntimeRoot 'logs\agent-runtime.out.log'
$ErrFile=Join-Path $RuntimeRoot 'logs\agent-runtime.err.log'
try {
  $r=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2
  if ($r.status -eq 'ok') { Write-Host "Runtime already healthy at http://127.0.0.1:$Port"; exit 0 }
} catch {}
$proc=Start-Process -FilePath $VenvPython -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $Root -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -WindowStyle Hidden -PassThru
Set-Content -Path $PidFile -Value $proc.Id -Encoding ASCII
$deadline=(Get-Date).AddSeconds(45)
do {
  if ($proc.HasExited) { throw "Runtime exited during startup. See $LogFile and $ErrFile" }
  try { $r=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2; if ($r.status -eq 'ok') { Write-Host "Runtime: http://127.0.0.1:$Port  Web: http://127.0.0.1:$Port/ui/"; exit 0 } } catch {}
  Start-Sleep -Milliseconds 400
} while ((Get-Date) -lt $deadline)
throw "Runtime did not become healthy. See $LogFile and $ErrFile"
