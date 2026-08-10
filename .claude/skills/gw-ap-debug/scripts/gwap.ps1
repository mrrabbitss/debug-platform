[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CliArgs)

$ErrorActionPreference = 'Stop'
$SkillRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ConfigPath = Join-Path $SkillRoot 'runtime-config.json'
$Config = $null
if (Test-Path -LiteralPath $ConfigPath) {
  $Config = Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json
}

function Get-ConfiguredValue([string]$EnvironmentName, [string]$PropertyName, [string]$DefaultValue = '') {
  $EnvironmentValue = [Environment]::GetEnvironmentVariable($EnvironmentName, 'Process')
  if ($EnvironmentValue) { return $EnvironmentValue }
  if ($Config -and $Config.PSObject.Properties.Name -contains $PropertyName) {
    $Value = [string]$Config.$PropertyName
    if ($Value) { return $Value }
  }
  return $DefaultValue
}

$RuntimeUrl = Get-ConfiguredValue 'GWAP_RUNTIME_URL' 'runtime_url' 'http://127.0.0.1:8765'
$PythonExe = Get-ConfiguredValue 'GWAP_PYTHON_EXE' 'python_exe'
$BackendRoot = Get-ConfiguredValue 'PYTHONPATH' 'backend_root'
$RuntimeRoot = Get-ConfiguredValue 'GWAP_RUNTIME_ROOT' 'runtime_root'
$RepositoryRoot = Get-ConfiguredValue 'GWAP_REPOSITORY_ROOT' 'repository_root'
$ConfiguredWorkspaceRoots = @()
if ($Config -and $Config.PSObject.Properties.Name -contains 'workspace_roots') {
  $ConfiguredWorkspaceRoots = @($Config.workspace_roots | ForEach-Object { [string]$_ } | Where-Object { $_ })
}
$RuntimePort = ([Uri]$RuntimeUrl).Port

if (-not $BackendRoot) {
  $CandidateRepository = [System.IO.Path]::GetFullPath((Join-Path $SkillRoot '..\..\..'))
  if (Test-Path -LiteralPath (Join-Path $CandidateRepository 'backend\app')) {
    $RepositoryRoot = $CandidateRepository
    $BackendRoot = Join-Path $CandidateRepository 'backend'
  }
}
if (-not $RuntimeRoot -and $env:LOCALAPPDATA) {
  foreach ($Name in @('GWAPDebugVNext', 'GWAPDebug')) {
    $Candidate = Join-Path $env:LOCALAPPDATA $Name
    if (Test-Path -LiteralPath (Join-Path $Candidate 'venv\Scripts\python.exe')) {
      $RuntimeRoot = $Candidate
      break
    }
  }
}
if (-not $PythonExe -and $RuntimeRoot) {
  $Candidate = Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $Candidate) { $PythonExe = $Candidate }
}

$EnvironmentNames = @(
  'PYTHONPATH', 'GWAP_RUNTIME_URL', 'GWAP_RUNTIME_ROOT', 'APP_ENV', 'AUTH_MODE',
  'AGENT_MODE', 'AGENT_RUNTIME_HOST', 'AGENT_RUNTIME_PORT', 'SERVE_FRONTEND',
  'FRONTEND_DIST', 'DATA_ROOT_PATH', 'DATABASE_URL', 'STORAGE_ROOT', 'MODEL_ROOTS',
  'WORKSPACE_ROOTS', 'CORS_ORIGINS'
)
$PreviousEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
  $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}

try {
  $env:GWAP_RUNTIME_URL = $RuntimeUrl
  if ($BackendRoot) { $env:PYTHONPATH = $BackendRoot }
  if ($RuntimeRoot) {
    $DataRoot = Join-Path $RuntimeRoot 'data'
    $DatabasePath = (Join-Path $DataRoot 'gw_ap_debug.db').Replace('\', '/')
    $env:GWAP_RUNTIME_ROOT = $RuntimeRoot
    $env:APP_ENV = 'dev'
    $env:AUTH_MODE = 'local'
    $env:AGENT_MODE = 'external'
    $env:AGENT_RUNTIME_HOST = '127.0.0.1'
    $env:AGENT_RUNTIME_PORT = "$RuntimePort"
    $env:DATA_ROOT_PATH = $DataRoot
    $env:DATABASE_URL = "sqlite:///$DatabasePath"
    $env:STORAGE_ROOT = Join-Path $RuntimeRoot 'storage'
    $env:CORS_ORIGINS = "http://127.0.0.1:$RuntimePort,http://localhost:$RuntimePort"
  }
  if ($RepositoryRoot) {
    $env:MODEL_ROOTS = Join-Path $RepositoryRoot 'models'
    $env:WORKSPACE_ROOTS = if ($ConfiguredWorkspaceRoots.Count) {
      $ConfiguredWorkspaceRoots -join [System.IO.Path]::PathSeparator
    } else {
      $RepositoryRoot
    }
    $FrontendDist = Join-Path $RepositoryRoot 'frontend\dist'
    $env:FRONTEND_DIST = $FrontendDist
    $env:SERVE_FRONTEND = if (Test-Path -LiteralPath $FrontendDist) { 'true' } else { 'false' }
  }

  if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
    & $PythonExe -m app.agent_runtime.cli --runtime-url $RuntimeUrl @CliArgs
  } else {
    $Gwap = Get-Command gwap -ErrorAction SilentlyContinue
    if (-not $Gwap) {
      throw 'Neither runtime-config.json/GWAP_PYTHON_EXE nor a gwap command was found. Run scripts\setup_codeagent_vnext.bat first.'
    }
    & $Gwap.Source --runtime-url $RuntimeUrl @CliArgs
  }
  $ExitCode = $LASTEXITCODE
} finally {
  foreach ($Name in $EnvironmentNames) {
    [Environment]::SetEnvironmentVariable($Name, $PreviousEnvironment[$Name], 'Process')
  }
}
exit $ExitCode
