[CmdletBinding()]
param(
  [string]$ProjectRoot = '',
  [string]$RuntimeRoot = '',
  [string]$NgaCommand = 'nga',
  [string[]]$NgaArguments = @(),
  [switch]$Repair,
  [switch]$RefreshPythonDependencies,
  [switch]$RebuildFrontend,
  [switch]$NoTui
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ProjectRoot) { $ProjectRoot = $RepositoryRoot }
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
  throw "ProjectRoot does not exist: $ProjectRoot"
}
if (-not $RuntimeRoot) {
  if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; pass -RuntimeRoot.' }
  $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)

$RuntimePort = 8766
$RuntimeUrl = "http://127.0.0.1:$RuntimePort"
$ServerName = 'gw-ap-debug-vnext'
$PythonExe = Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
$SkillPath = Join-Path $ProjectRoot '.codeartsdoer\skills\gw-ap-debug\SKILL.md'
$OpenCodeConfigPath = Join-Path $ProjectRoot 'opencode.json'
$FrontendIndex = Join-Path $RepositoryRoot 'frontend\dist\index.html'

function Resolve-LaunchCommand([string]$Name) {
  if ([System.IO.Path]::IsPathRooted($Name) -and (Test-Path -LiteralPath $Name)) {
    return (Resolve-Path -LiteralPath $Name).Path
  }
  $Command = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $Command) { return $null }
  if ($Command.Source) { return $Command.Source }
  return $Command.Definition
}

function Test-CompanyMcpConfig([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  try {
    $Config = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    if (-not $Config.mcp) { return $false }
    $ServerProperty = $Config.mcp.PSObject.Properties[$ServerName]
    if (-not $ServerProperty) { return $false }
    $Server = $ServerProperty.Value
    if (-not $Server.environment) { return $false }
    return (
      [string]$Server.environment.GWAP_RUNTIME_URL -eq $RuntimeUrl -and
      [string]$Server.environment.NO_PROXY -eq '127.0.0.1,localhost,::1'
    )
  } catch {
    return $false
  }
}

function Merge-NoProxy([string]$Current) {
  $Items = @()
  if ($Current) { $Items += @($Current -split ',') }
  $Items += @('127.0.0.1', 'localhost', '::1')
  $Clean = @($Items | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
  return $Clean -join ','
}

$NgaExecutable = Resolve-LaunchCommand $NgaCommand
if (-not $NgaExecutable) {
  throw "The tested company launcher '$NgaCommand' was not found. Ensure nga is on PATH or pass -NgaCommand with its absolute path."
}

$SetupReasons = @()
if ($Repair) { $SetupReasons += 'repair requested' }
if (-not (Test-Path -LiteralPath $PythonExe)) { $SetupReasons += 'isolated Python missing' }
if (-not (Test-Path -LiteralPath $SkillPath)) { $SetupReasons += 'CodeArts project Skill missing' }
if (-not (Test-CompanyMcpConfig $OpenCodeConfigPath)) { $SetupReasons += 'OpenCode V1 MCP config missing or stale' }
if (-not (Test-Path -LiteralPath $FrontendIndex)) { $SetupReasons += 'frontend build missing' }

if ($SetupReasons.Count -gt 0) {
  Write-Host "[INFO] Synchronizing the tested Huawei CodeAgent profile: $($SetupReasons -join ', ')"
  $SetupArguments = @{
    ProjectRoot = $ProjectRoot
    RuntimeRoot = $RuntimeRoot
    RuntimePort = $RuntimePort
    AgentCommand = $NgaExecutable
    ServerName = $ServerName
    TargetClient = 'CodeArts'
    SkillScope = 'Project'
    McpSchema = 'OpenCodeV1'
    Force = $true
  }
  if ((Test-Path -LiteralPath $PythonExe) -and -not $RefreshPythonDependencies) {
    $SetupArguments['SkipPythonInstall'] = $true
  }
  if ($RebuildFrontend -or -not (Test-Path -LiteralPath $FrontendIndex)) {
    $SetupArguments['BuildFrontend'] = $true
  }
  & (Join-Path $PSScriptRoot 'setup_codeagent_vnext.ps1') @SetupArguments | Write-Output
} else {
  & (Join-Path $PSScriptRoot 'start_codeagent_vnext.ps1') `
    -RepositoryRoot $RepositoryRoot `
    -RuntimeRoot $RuntimeRoot `
    -PythonExe $PythonExe `
    -Port $RuntimePort `
    -WorkspaceRoots @($ProjectRoot) | Write-Output
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
  throw "The isolated Python was not created: $PythonExe"
}

$PreviousPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
try {
  $env:PYTHONPATH = Join-Path $RepositoryRoot 'backend'
  $StatusJson = & $PythonExe -c (
    "import json; from app.agent_runtime.client import RuntimeClient; " +
    "print(json.dumps(RuntimeClient('$RuntimeUrl').status()))"
  )
  if ($LASTEXITCODE -ne 0) { throw 'RuntimeClient status verification failed.' }
  $Status = $StatusJson | ConvertFrom-Json
  if ($Status.health.status -ne 'ok') { throw 'Runtime did not return health.status=ok.' }
  Write-Host "[PASS] Runtime data plane is direct and healthy at $RuntimeUrl (agent_mode=$($Status.agent_runtime.agent_mode))."
} finally {
  [Environment]::SetEnvironmentVariable('PYTHONPATH', $PreviousPythonPath, 'Process')
}

if ($NoTui) {
  Write-Host '[PASS] Company profile is ready; -NoTui skipped the nga launch.'
  return
}

$PreviousNoProxy = [Environment]::GetEnvironmentVariable('NO_PROXY', 'Process')
$PreviousRuntimeUrl = [Environment]::GetEnvironmentVariable('GWAP_RUNTIME_URL', 'Process')
$TuiExitCode = 0
try {
  $env:NO_PROXY = Merge-NoProxy $PreviousNoProxy
  $env:GWAP_RUNTIME_URL = $RuntimeUrl
  Write-Host "[INFO] Launching nga in $ProjectRoot. The vNext Runtime remains on $RuntimeUrl after TUI exit."
  Push-Location $ProjectRoot
  try {
    & $NgaExecutable @NgaArguments
    if ($null -ne $LASTEXITCODE) { $TuiExitCode = [int]$LASTEXITCODE }
  } finally {
    Pop-Location
  }
} finally {
  [Environment]::SetEnvironmentVariable('NO_PROXY', $PreviousNoProxy, 'Process')
  [Environment]::SetEnvironmentVariable('GWAP_RUNTIME_URL', $PreviousRuntimeUrl, 'Process')
}

if ($TuiExitCode -ne 0) {
  Write-Warning "nga exited with code $TuiExitCode. The Runtime was left running for inspection."
  exit $TuiExitCode
}
