[CmdletBinding()]
param(
  [string]$ProjectRoot = '',
  [string]$RuntimeRoot = '',
  [string]$PythonExe = '',
  [int]$RuntimePort = 8766,
  [string]$AgentCommand = '',
  [string]$ServerName = 'gw-ap-debug-vnext',
  [ValidateSet('CodeArts', 'OpenCode', 'ClaudeCompatible', 'AgentCompatible')]
  [string]$TargetClient = 'CodeArts',
  [ValidateSet('Project', 'User')]
  [string]$SkillScope = 'Project',
  [ValidateSet('Auto', 'CodeArtsNative', 'CodeArtsClaude', 'OpenCodeV1', 'OpenCodeV2', 'None')]
  [string]$McpSchema = 'Auto',
  [switch]$SkipPythonInstall,
  [switch]$BuildFrontend,
  [switch]$SkipStart,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ProjectRoot) { $ProjectRoot = $RepositoryRoot }
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
  New-Item -ItemType Directory -Force -Path $ProjectRoot | Out-Null
}
if ($RuntimePort -lt 1 -or $RuntimePort -gt 65535) { throw "Invalid Runtime port: $RuntimePort" }
if ($ServerName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
  throw 'ServerName must be 1-64 safe letters, numbers, dots, underscores or hyphens.'
}
if (-not $RuntimeRoot) {
  if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; pass -RuntimeRoot.' }
  $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$RuntimeUrl = "http://127.0.0.1:$RuntimePort"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function ConvertTo-MutableValue($Value) {
  if ($null -eq $Value) { return $null }
  if ($Value -is [System.Collections.IDictionary]) {
    $Map = [ordered]@{}
    foreach ($Key in $Value.Keys) { $Map[[string]$Key] = ConvertTo-MutableValue $Value[$Key] }
    return $Map
  }
  if ($Value.GetType().FullName -eq 'System.Management.Automation.PSCustomObject') {
    $Map = [ordered]@{}
    foreach ($Property in $Value.PSObject.Properties) {
      $Map[$Property.Name] = ConvertTo-MutableValue $Property.Value
    }
    return $Map
  }
  if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
    return @($Value | ForEach-Object { ConvertTo-MutableValue $_ })
  }
  return $Value
}

function Read-JsonMap([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return [ordered]@{} }
  $Raw = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
  if (-not $Raw.Trim()) { return [ordered]@{} }
  try { return ConvertTo-MutableValue ($Raw | ConvertFrom-Json) }
  catch { throw "Refusing to overwrite an unreadable JSON config: $Path. Use the generated Skill/CLI fallback or repair the file first." }
}

function Test-EquivalentJson($Left, $Right) {
  function Test-DeepValue($A, $B) {
    if ($null -eq $A -or $null -eq $B) { return $null -eq $A -and $null -eq $B }
    if ($A -is [System.Collections.IDictionary] -and $B -is [System.Collections.IDictionary]) {
      $AKeys = @($A.Keys | ForEach-Object { [string]$_ } | Sort-Object)
      $BKeys = @($B.Keys | ForEach-Object { [string]$_ } | Sort-Object)
      if ($AKeys.Count -ne $BKeys.Count) { return $false }
      for ($Index = 0; $Index -lt $AKeys.Count; $Index++) {
        if ($AKeys[$Index] -cne $BKeys[$Index]) { return $false }
        if (-not (Test-DeepValue $A[$AKeys[$Index]] $B[$BKeys[$Index]])) { return $false }
      }
      return $true
    }
    $AIsList = $A -is [System.Collections.IEnumerable] -and $A -isnot [string]
    $BIsList = $B -is [System.Collections.IEnumerable] -and $B -isnot [string]
    if ($AIsList -or $BIsList) {
      if (-not ($AIsList -and $BIsList)) { return $false }
      $AItems = @($A)
      $BItems = @($B)
      if ($AItems.Count -ne $BItems.Count) { return $false }
      for ($Index = 0; $Index -lt $AItems.Count; $Index++) {
        if (-not (Test-DeepValue $AItems[$Index] $BItems[$Index])) { return $false }
      }
      return $true
    }
    return [string]$A -ceq [string]$B
  }
  return Test-DeepValue $Left $Right
}

function Set-MapEntry([System.Collections.IDictionary]$Map, [string]$Key, $Value, [string]$ConfigPath) {
  if ($Map.Contains($Key) -and -not (Test-EquivalentJson $Map[$Key] $Value)) {
    if (-not $Force) {
      throw "MCP server '$Key' already exists with different settings in $ConfigPath. Pass -Force to replace only that entry after review."
    }
    $BackupPath = "$ConfigPath.backup-$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
    if (Test-Path -LiteralPath $ConfigPath) { Copy-Item -LiteralPath $ConfigPath -Destination $BackupPath }
  }
  $Map[$Key] = $Value
}

function Resolve-AgentExecutable([string]$Name) {
  if ($Name) {
    if ([System.IO.Path]::IsPathRooted($Name) -and (Test-Path -LiteralPath $Name)) {
      return (Resolve-Path -LiteralPath $Name).Path
    }
    $Explicit = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Explicit) { return $(if ($Explicit.Source) { $Explicit.Source } else { $Explicit.Definition }) }
    return $null
  }
  foreach ($Candidate in @('nga', 'codearts', 'codeagent', 'opencode')) {
    $Detected = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($Detected) { return $(if ($Detected.Source) { $Detected.Source } else { $Detected.Definition }) }
  }
  return $null
}

function Resolve-AutoSchema([string]$Executable) {
  if ($TargetClient -eq 'OpenCode') {
    $Major = 1
    if ($Executable) {
      try {
        $VersionText = (& $Executable --version 2>$null | Select-Object -First 1)
        if ([string]$VersionText -match '(\d+)\.') { $Major = [int]$Matches[1] }
      } catch {}
    }
    if ($Major -ge 2) { return 'OpenCodeV2' }
    return 'OpenCodeV1'
  }
  $NativeJson = Join-Path $ProjectRoot '.codeartsdoer\codearts_cli.json'
  $NativeJsonc = Join-Path $ProjectRoot '.codeartsdoer\codearts_cli.jsonc'
  if ((Test-Path -LiteralPath $NativeJsonc) -and -not (Test-Path -LiteralPath $NativeJson)) {
    return 'CodeArtsClaude'
  }
  return 'CodeArtsNative'
}

$ResolvedAgent = Resolve-AgentExecutable $AgentCommand
if ($AgentCommand -and -not $ResolvedAgent) {
  Write-Warning "Agent command '$AgentCommand' is not available in this terminal. Setup will continue; the probe report will remain partial."
}
if ($McpSchema -eq 'Auto') { $McpSchema = Resolve-AutoSchema $ResolvedAgent }

$DataRoot = Join-Path $RuntimeRoot 'data'
$StorageRoot = Join-Path $RuntimeRoot 'storage'
$LogsRoot = Join-Path $RuntimeRoot 'logs'
$ProcessRoot = Join-Path $RuntimeRoot 'runtime'
$PackagesRoot = Join-Path $RuntimeRoot 'packages'
New-Item -ItemType Directory -Force -Path $RuntimeRoot, $DataRoot, $StorageRoot, $LogsRoot, $ProcessRoot, $PackagesRoot | Out-Null
$Venv = Join-Path $RuntimeRoot 'venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

if (-not $SkipPythonInstall) {
  if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Created = $false
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
      & $PyLauncher.Source -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) {
        & $PyLauncher.Source -3.12 -m venv $Venv
        if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed with exit code $LASTEXITCODE" }
        $Created = $true
      }
    }
    if (-not $Created) {
      $SystemPython = Get-Command python -ErrorAction SilentlyContinue
      if (-not $SystemPython) { throw 'Python 3.11+ was not found. Python 3.12 is recommended.' }
      & $SystemPython.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
      if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ was not found. Python 3.12 is recommended.' }
      & $SystemPython.Source -m venv $Venv
      if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed with exit code $LASTEXITCODE" }
    }
  }
  $PythonExe = $VenvPython
  $Constraints = Join-Path $RepositoryRoot 'backend\constraints.lock'
  & $PythonExe -m pip install --upgrade --constraint $Constraints pip setuptools wheel
  if ($LASTEXITCODE -ne 0) { throw "Python bootstrap install failed with exit code $LASTEXITCODE" }
  & $PythonExe -m pip install --constraint $Constraints -e (Join-Path $RepositoryRoot 'backend')
  if ($LASTEXITCODE -ne 0) { throw "Backend install failed with exit code $LASTEXITCODE" }
} else {
  if (-not $PythonExe) { $PythonExe = $VenvPython }
  if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "SkipPythonInstall was requested, but PythonExe does not exist: $PythonExe"
  }
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path

$PreviousPythonPath = $env:PYTHONPATH
try {
  $env:PYTHONPATH = Join-Path $RepositoryRoot 'backend'
  & $PythonExe -c "import app; import app.agent_runtime.mcp_server"
  if ($LASTEXITCODE -ne 0) { throw "vNext backend import failed with exit code $LASTEXITCODE" }
} finally {
  $env:PYTHONPATH = $PreviousPythonPath
}

if ($BuildFrontend) {
  $Npm = Get-Command npm -ErrorAction SilentlyContinue
  if (-not $Npm) { throw 'npm was not found. Install Node.js 20.19+ or 22.12+ to build the optional Web UI.' }
  Push-Location (Join-Path $RepositoryRoot 'frontend')
  try {
    & $Npm.Source ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
    $PreviousPublicBase = $env:VITE_PUBLIC_BASE
    $env:VITE_PUBLIC_BASE = '/ui/'
    try {
      & $Npm.Source run build
      if ($LASTEXITCODE -ne 0) { throw "frontend build failed with exit code $LASTEXITCODE" }
    } finally { $env:VITE_PUBLIC_BASE = $PreviousPublicBase }
  } finally { Pop-Location }
}

$SourceSkill = Join-Path $RepositoryRoot '.claude\skills\gw-ap-debug'
if (-not (Test-Path -LiteralPath (Join-Path $SourceSkill 'SKILL.md'))) { throw "Missing Skill source: $SourceSkill" }
$ScopeRoot = if ($SkillScope -eq 'Project') { $ProjectRoot } else { $HOME }
switch ($TargetClient) {
  'CodeArts' { $SkillParent = Join-Path $ScopeRoot '.codeartsdoer\skills' }
  'OpenCode' {
    $SkillParent = if ($SkillScope -eq 'Project') { Join-Path $ScopeRoot '.opencode\skills' } else { Join-Path $ScopeRoot '.config\opencode\skills' }
  }
  'ClaudeCompatible' { $SkillParent = Join-Path $ScopeRoot '.claude\skills' }
  'AgentCompatible' { $SkillParent = Join-Path $ScopeRoot '.agents\skills' }
}
$SkillTarget = Join-Path $SkillParent 'gw-ap-debug'
New-Item -ItemType Directory -Force -Path $SkillTarget | Out-Null
if (-not ([System.IO.Path]::GetFullPath($SourceSkill) -eq [System.IO.Path]::GetFullPath($SkillTarget))) {
  Copy-Item -Path (Join-Path $SourceSkill '*') -Destination $SkillTarget -Recurse -Force
}
$RuntimeConfig = [ordered]@{
  runtime_url = $RuntimeUrl
  runtime_root = $RuntimeRoot
  python_exe = $PythonExe
  backend_root = Join-Path $RepositoryRoot 'backend'
  repository_root = $RepositoryRoot
  workspace_roots = @($ProjectRoot)
  runtime_port = $RuntimePort
  mcp_server_name = $ServerName
}
[System.IO.File]::WriteAllText(
  (Join-Path $SkillTarget 'runtime-config.json'),
  ($RuntimeConfig | ConvertTo-Json -Depth 5),
  $Utf8NoBom
)

if ($TargetClient -eq 'CodeArts' -and $SkillScope -eq 'Project') {
  $StatusFile = Join-Path $SkillParent 'ProjectSkillStatus.txt'
  $StatusLines = if (Test-Path -LiteralPath $StatusFile) { @(Get-Content -Encoding UTF8 -LiteralPath $StatusFile) } else { @() }
  $StatusLines = @($StatusLines | Where-Object { $_ -notmatch '^gw-ap-debug=' }) + @('gw-ap-debug=true')
  [System.IO.File]::WriteAllLines($StatusFile, [string[]]$StatusLines, $Utf8NoBom)
}

$McpConfigPath = $null
if ($McpSchema -ne 'None') {
  $CommonEnvironment = [ordered]@{
    PYTHONPATH = Join-Path $RepositoryRoot 'backend'
    GWAP_RUNTIME_URL = $RuntimeUrl
    GWAP_AGENT_ROLE = 'ENGINEER'
    NO_PROXY = '127.0.0.1,localhost,::1'
  }
  if ($McpSchema -eq 'CodeArtsNative') {
    $JsoncPath = Join-Path $ProjectRoot '.codeartsdoer\codearts_cli.jsonc'
    $McpConfigPath = Join-Path $ProjectRoot '.codeartsdoer\codearts_cli.json'
    if ((Test-Path -LiteralPath $JsoncPath) -and -not (Test-Path -LiteralPath $McpConfigPath)) {
      throw "Existing JSONC config detected at $JsoncPath. Rerun with -McpSchema CodeArtsClaude to preserve its comments, or merge the generated entry manually."
    }
    $Config = Read-JsonMap $McpConfigPath
    if (-not $Config.Contains('mcp')) { $Config['mcp'] = [ordered]@{} }
    $Entry = [ordered]@{
      type = 'local'
      command = @($PythonExe, '-m', 'app.agent_runtime.mcp_server')
      environment = $CommonEnvironment
      enabled = $true
      timeout = 100000
    }
    Set-MapEntry $Config['mcp'] $ServerName $Entry $McpConfigPath
  } elseif ($McpSchema -eq 'CodeArtsClaude') {
    $McpConfigPath = Join-Path $ProjectRoot '.codeartsdoer\mcp\mcp_settings.json'
    $Config = Read-JsonMap $McpConfigPath
    if (-not $Config.Contains('mcpServers')) { $Config['mcpServers'] = [ordered]@{} }
    $Entry = [ordered]@{
      transportType = 'stdio'
      disabled = $false
      timeout = 100000
      command = $PythonExe
      args = @('-m', 'app.agent_runtime.mcp_server')
      env = $CommonEnvironment
    }
    Set-MapEntry $Config['mcpServers'] $ServerName $Entry $McpConfigPath
  } elseif ($McpSchema -eq 'OpenCodeV1') {
    $McpConfigPath = Join-Path $ProjectRoot 'opencode.json'
    $Config = Read-JsonMap $McpConfigPath
    if (-not $Config.Contains('$schema')) { $Config['$schema'] = 'https://opencode.ai/config.json' }
    if (-not $Config.Contains('mcp')) { $Config['mcp'] = [ordered]@{} }
    $Entry = [ordered]@{
      type = 'local'
      command = @($PythonExe, '-m', 'app.agent_runtime.mcp_server')
      enabled = $true
      timeout = 100000
      environment = $CommonEnvironment
    }
    Set-MapEntry $Config['mcp'] $ServerName $Entry $McpConfigPath
  } elseif ($McpSchema -eq 'OpenCodeV2') {
    $McpConfigPath = Join-Path $ProjectRoot 'opencode.json'
    $Config = Read-JsonMap $McpConfigPath
    if (-not $Config.Contains('$schema')) { $Config['$schema'] = 'https://opencode.ai/config.json' }
    if (-not $Config.Contains('mcp')) { $Config['mcp'] = [ordered]@{} }
    if (-not $Config['mcp'].Contains('servers')) { $Config['mcp']['servers'] = [ordered]@{} }
    $Entry = [ordered]@{
      type = 'local'
      command = @($PythonExe, '-m', 'app.agent_runtime.mcp_server')
      disabled = $false
      timeout = 100000
      environment = $CommonEnvironment
    }
    Set-MapEntry $Config['mcp']['servers'] $ServerName $Entry $McpConfigPath
  } else {
    throw "Unsupported McpSchema: $McpSchema"
  }
  $McpConfigParent = Split-Path -Parent $McpConfigPath
  New-Item -ItemType Directory -Force -Path $McpConfigParent | Out-Null
  [System.IO.File]::WriteAllText($McpConfigPath, ($Config | ConvertTo-Json -Depth 20), $Utf8NoBom)
}

$PackagePath = Join-Path $PackagesRoot 'gw-ap-debug-codeagent-skill.zip'
& (Join-Path $PSScriptRoot 'package_codeagent_skill.ps1') -OutputPath $PackagePath -Force | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Skill packaging failed with exit code $LASTEXITCODE" }

$RuntimeStart = $null
if (-not $SkipStart) {
  & (Join-Path $PSScriptRoot 'start_codeagent_vnext.ps1') `
    -RepositoryRoot $RepositoryRoot `
    -RuntimeRoot $RuntimeRoot `
    -PythonExe $PythonExe `
    -Port $RuntimePort `
    -WorkspaceRoots @($ProjectRoot) | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Runtime start failed with exit code $LASTEXITCODE" }
  $StartResultPath = Join-Path $RuntimeRoot 'runtime\start-result.json'
  if (-not (Test-Path -LiteralPath $StartResultPath)) { throw "Runtime start result was not written: $StartResultPath" }
  $RuntimeStart = Get-Content -Raw -Encoding UTF8 -LiteralPath $StartResultPath | ConvertFrom-Json
  $RuntimeHealth = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/health" -TimeoutSec 3
  if ($RuntimeHealth.status -ne 'ok') { throw "Runtime did not remain healthy at $RuntimeUrl" }
}

$ProbePath = Join-Path $LogsRoot 'codeagent-compatibility.json'
$ProbeArguments = @{
  ProjectRoot = $ProjectRoot
  RuntimeUrl = $RuntimeUrl
  PythonExe = $PythonExe
  ServerName = $ServerName
  ClientFamily = $TargetClient
  ReportPath = $ProbePath
  Quiet = $true
}
if ($AgentCommand) { $ProbeArguments['AgentCommand'] = $AgentCommand }
& (Join-Path $PSScriptRoot 'probe_codeagent_compatibility.ps1') @ProbeArguments
if ($LASTEXITCODE -ne 0) { throw "Compatibility probe failed with exit code $LASTEXITCODE" }
$Probe = Get-Content -Raw -Encoding UTF8 -LiteralPath $ProbePath | ConvertFrom-Json

[ordered]@{
  status = 'PASS'
  repository_root = $RepositoryRoot
  project_root = $ProjectRoot
  runtime_root = $RuntimeRoot
  runtime_url = $RuntimeUrl
  python_exe = $PythonExe
  target_client = $TargetClient
  skill_scope = $SkillScope
  skill_path = $SkillTarget
  skill_package = $PackagePath
  mcp_schema = $McpSchema
  mcp_config = $McpConfigPath
  mcp_server_name = $ServerName
  runtime_start = $RuntimeStart
  compatibility_status = $Probe.status
  compatibility_report = $ProbePath
  next_checks = @(
    'In CodeArts TUI run /skills and confirm gw-ap-debug.',
    'If MCP was configured, run /mcps or codearts mcp list and confirm the configured server is connected.',
    'Run the synthetic prompt documented in docs/codeagent-compatibility.md before using company data.'
  )
} | ConvertTo-Json -Depth 8
