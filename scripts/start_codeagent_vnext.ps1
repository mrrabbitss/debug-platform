[CmdletBinding()]
param(
  [string]$RepositoryRoot = '',
  [string]$RuntimeRoot = '',
  [string]$PythonExe = '',
  [int]$Port = 8766,
  [string[]]$WorkspaceRoots = @()
)

$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid Runtime port: $Port" }
if (-not $RepositoryRoot) { $RepositoryRoot = Join-Path $PSScriptRoot '..' }
$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot 'backend\app'))) {
  throw "RepositoryRoot does not contain backend\app: $RepositoryRoot"
}
if (-not $RuntimeRoot) {
  if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; pass -RuntimeRoot.' }
  $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
if (-not $PythonExe) { $PythonExe = Join-Path $RuntimeRoot 'venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe)) {
  throw "vNext Python was not found at $PythonExe. Run scripts\setup_codeagent_vnext.bat first."
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path

$DataRoot = Join-Path $RuntimeRoot 'data'
$StorageRoot = Join-Path $RuntimeRoot 'storage'
$LogsRoot = Join-Path $RuntimeRoot 'logs'
$ProcessRoot = Join-Path $RuntimeRoot 'runtime'
New-Item -ItemType Directory -Force -Path $DataRoot, $StorageRoot, $LogsRoot, $ProcessRoot | Out-Null
$PidFile = Join-Path $ProcessRoot 'agent-runtime.pid'
$StartResultFile = Join-Path $ProcessRoot 'start-result.json'
$OutFile = Join-Path $LogsRoot 'agent-runtime.out.log'
$ErrFile = Join-Path $LogsRoot 'agent-runtime.err.log'
$RuntimeUrl = "http://127.0.0.1:$Port"

$ExistingHealth = $null
try { $ExistingHealth = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/health" -TimeoutSec 2 } catch {}
if ($ExistingHealth -and $ExistingHealth.status -eq 'ok') {
  if (-not (Test-Path -LiteralPath $PidFile)) {
    throw "A GW/AP Debug-compatible service is already healthy at $RuntimeUrl, but it is not managed by $RuntimeRoot. Refusing to reuse a possible main instance."
  }
  $ManagedPid = (Get-Content -Raw -LiteralPath $PidFile).Trim()
  if ($ManagedPid -notmatch '^\d+$' -or -not (Get-Process -Id ([int]$ManagedPid) -ErrorAction SilentlyContinue)) {
    throw "A service is healthy at $RuntimeUrl, but the vNext PID file is stale or invalid: $PidFile"
  }
  $AgentStatus = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/system/agent-runtime" -TimeoutSec 2
  $ExistingResult = [ordered]@{
    started = $false
    reason = 'already_healthy'
    runtime_url = $RuntimeUrl
    agent_runtime_endpoint = $null -ne $AgentStatus
    pid_file = $PidFile
  }
  $ExistingJson = $ExistingResult | ConvertTo-Json -Depth 4
  [System.IO.File]::WriteAllText($StartResultFile, $ExistingJson, (New-Object System.Text.UTF8Encoding($false)))
  $ExistingJson
  return
}

if (Test-Path -LiteralPath $PidFile) {
  $ExistingPidText = (Get-Content -Raw -LiteralPath $PidFile).Trim()
  if ($ExistingPidText -match '^\d+$') {
    $ExistingProcess = Get-Process -Id ([int]$ExistingPidText) -ErrorAction SilentlyContinue
    if ($ExistingProcess) {
      throw "PID file points to a running process ($ExistingPidText), but Runtime health failed. Inspect $OutFile and $ErrFile; refusing to start a duplicate."
    }
  }
  Remove-Item -LiteralPath $PidFile -Force
}

if ($WorkspaceRoots.Count -eq 0) { $WorkspaceRoots = @($RepositoryRoot) }
$ResolvedWorkspaceRoots = @()
foreach ($WorkspaceRoot in $WorkspaceRoots) {
  if (-not (Test-Path -LiteralPath $WorkspaceRoot)) { throw "Workspace root does not exist: $WorkspaceRoot" }
  $ResolvedWorkspaceRoots += (Resolve-Path -LiteralPath $WorkspaceRoot).Path
}
$DatabasePath = (Join-Path $DataRoot 'gw_ap_debug.db').Replace('\', '/')
$FrontendDist = Join-Path $RepositoryRoot 'frontend\dist'
$EnvironmentNames = @(
  'PYTHONPATH', 'APP_ENV', 'AUTH_MODE', 'AGENT_MODE', 'AGENT_RUNTIME_HOST',
  'AGENT_RUNTIME_PORT', 'SERVE_FRONTEND', 'FRONTEND_DIST', 'DATA_ROOT_PATH',
  'DATABASE_URL', 'STORAGE_ROOT', 'MODEL_ROOTS', 'WORKSPACE_ROOTS',
  'CORS_ORIGINS', 'GWAP_RUNTIME_URL', 'GWAP_RUNTIME_ROOT'
)
$PreviousEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
  $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}

$RuntimeProcess = $null
$StartResult = $null
try {
  $env:PYTHONPATH = Join-Path $RepositoryRoot 'backend'
  $env:APP_ENV = 'dev'
  $env:AUTH_MODE = 'local'
  $env:AGENT_MODE = 'external'
  $env:AGENT_RUNTIME_HOST = '127.0.0.1'
  $env:AGENT_RUNTIME_PORT = "$Port"
  $env:SERVE_FRONTEND = if (Test-Path -LiteralPath $FrontendDist) { 'true' } else { 'false' }
  $env:FRONTEND_DIST = $FrontendDist
  $env:DATA_ROOT_PATH = $DataRoot
  $env:DATABASE_URL = "sqlite:///$DatabasePath"
  $env:STORAGE_ROOT = $StorageRoot
  $env:MODEL_ROOTS = Join-Path $RepositoryRoot 'models'
  $env:WORKSPACE_ROOTS = $ResolvedWorkspaceRoots -join [System.IO.Path]::PathSeparator
  $env:CORS_ORIGINS = "http://127.0.0.1:$Port,http://localhost:$Port"
  $env:GWAP_RUNTIME_URL = $RuntimeUrl
  $env:GWAP_RUNTIME_ROOT = $RuntimeRoot

  $RuntimeProcess = Start-Process -FilePath $PythonExe `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port") `
    -WorkingDirectory $RepositoryRoot `
    -RedirectStandardOutput $OutFile `
    -RedirectStandardError $ErrFile `
    -WindowStyle Hidden `
    -PassThru
  [System.IO.File]::WriteAllText($PidFile, [string]$RuntimeProcess.Id, [System.Text.Encoding]::ASCII)

  $Deadline = (Get-Date).AddSeconds(45)
  $Ready = $false
  while ((Get-Date) -lt $Deadline -and -not $Ready) {
    if ($RuntimeProcess.HasExited) {
      $Tail = if (Test-Path -LiteralPath $ErrFile) { (Get-Content -LiteralPath $ErrFile -Tail 30) -join "`n" } else { '' }
      throw "Runtime exited during startup.`n$Tail"
    }
    try {
      $Health = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/health" -TimeoutSec 2
      if ($Health.status -eq 'ok') { $Ready = $true }
    } catch {}
    if (-not $Ready) { Start-Sleep -Milliseconds 300 }
  }
  if (-not $Ready) { throw "Runtime did not become healthy at $RuntimeUrl. Inspect $OutFile and $ErrFile." }
  $StartResult = [ordered]@{
    started = $true
    pid = $RuntimeProcess.Id
    runtime_url = $RuntimeUrl
    web_url = if ($env:SERVE_FRONTEND -eq 'true') { "$RuntimeUrl/ui/" } else { $null }
    runtime_root = $RuntimeRoot
    pid_file = $PidFile
    stdout_log = $OutFile
    stderr_log = $ErrFile
  }
} catch {
  if ($RuntimeProcess -and -not $RuntimeProcess.HasExited) {
    Stop-Process -Id $RuntimeProcess.Id -Force -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  throw
} finally {
  foreach ($Name in $EnvironmentNames) {
    [Environment]::SetEnvironmentVariable($Name, $PreviousEnvironment[$Name], 'Process')
  }
}
$StartResultJson = $StartResult | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($StartResultFile, $StartResultJson, (New-Object System.Text.UTF8Encoding($false)))
$StartResultJson
