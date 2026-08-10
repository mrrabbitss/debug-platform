param(
  [string]$PythonExe = "",
  [string]$Model = "opencode/deepseek-v4-flash-free",
  [switch]$KeepArtifacts
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OpenCode = Get-Command opencode -ErrorAction SilentlyContinue
if (-not $OpenCode) { throw 'OpenCode CLI was not found on PATH.' }

if (-not $PythonExe) {
  $Candidates = @()
  if ($env:VIRTUAL_ENV) { $Candidates += (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe') }
  if ($env:LOCALAPPDATA) { $Candidates += (Join-Path $env:LOCALAPPDATA 'GWAPDebug\venv\Scripts\python.exe') }
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) { $Candidates += $PythonCommand.Source }
  $PythonExe = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe)) {
  throw 'A Python environment with the vNext backend dependencies was not found. Pass -PythonExe explicitly.'
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path

$TempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$TempRoot = Join-Path $TempBase ("gwap-opencode-e2e-" + [Guid]::NewGuid().ToString('N'))
$ResolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot)
if (-not $ResolvedTempRoot.StartsWith($TempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to create test data outside the OS temp directory: $ResolvedTempRoot"
}

$Workspace = Join-Path $ResolvedTempRoot 'workspace'
$DataRoot = Join-Path $ResolvedTempRoot 'data'
$StorageRoot = Join-Path $ResolvedTempRoot 'storage'
$ModelRoot = Join-Path $ResolvedTempRoot 'models'
$ConfigDir = Join-Path $ResolvedTempRoot 'opencode-config-dir'
$XdgConfig = Join-Path $ResolvedTempRoot 'xdg-config'
$XdgData = Join-Path $ResolvedTempRoot 'xdg-data'
$XdgCache = Join-Path $ResolvedTempRoot 'xdg-cache'
$ConfigPath = Join-Path $ResolvedTempRoot 'opencode.json'
$EventsPath = Join-Path $ResolvedTempRoot 'opencode-events.jsonl'
$ServerOut = Join-Path $ResolvedTempRoot 'runtime.out.log'
$ServerErr = Join-Path $ResolvedTempRoot 'runtime.err.log'
$SampleLog = Join-Path $Root 'sample_data\collectDebuginfo_demo.zip'
$SampleRepository = Join-Path $Root 'sample_data\repository'
$SkillSource = Join-Path $Root '.claude\skills\gw-ap-debug'
$CaseTitle = 'OpenCode MCP E2E ' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$RuntimeProcess = $null

$EnvironmentNames = @(
  'PYTHONPATH', 'APP_ENV', 'AUTH_MODE', 'AGENT_MODE', 'AGENT_RUNTIME_PORT',
  'SERVE_FRONTEND', 'DATA_ROOT_PATH', 'DATABASE_URL', 'STORAGE_ROOT',
  'MODEL_ROOTS', 'WORKSPACE_ROOTS', 'GWAP_RUNTIME_URL', 'GWAP_AGENT_ROLE',
  'OPENCODE_CONFIG', 'OPENCODE_CONFIG_DIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
  'XDG_CACHE_HOME'
)
$PreviousEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
  $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}

try {
  New-Item -ItemType Directory -Force -Path $Workspace, $DataRoot, $StorageRoot, $ModelRoot, $ConfigDir, $XdgConfig, $XdgData, $XdgCache | Out-Null
  Copy-Item -Path (Join-Path $SampleRepository '*') -Destination $Workspace -Recurse -Force
  $SkillTargetParent = Join-Path $Workspace '.claude\skills'
  New-Item -ItemType Directory -Force -Path $SkillTargetParent | Out-Null
  Copy-Item -LiteralPath $SkillSource -Destination $SkillTargetParent -Recurse -Force

  $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $Listener.Start()
  $Port = ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
  $Listener.Stop()
  $RuntimeUrl = "http://127.0.0.1:$Port"
  $DatabasePath = (Join-Path $DataRoot 'opencode-e2e.db').Replace('\', '/')

  $Environment = [ordered]@{
    PYTHONPATH = (Join-Path $Root 'backend')
    APP_ENV = 'test'
    AUTH_MODE = 'local'
    AGENT_MODE = 'external'
    AGENT_RUNTIME_PORT = "$Port"
    SERVE_FRONTEND = 'false'
    DATA_ROOT_PATH = $DataRoot
    DATABASE_URL = "sqlite:///$DatabasePath"
    STORAGE_ROOT = $StorageRoot
    MODEL_ROOTS = $ModelRoot
    WORKSPACE_ROOTS = $ResolvedTempRoot
    GWAP_RUNTIME_URL = $RuntimeUrl
    GWAP_AGENT_ROLE = 'ENGINEER'
    OPENCODE_CONFIG = $ConfigPath
    OPENCODE_CONFIG_DIR = $ConfigDir
    XDG_CONFIG_HOME = $XdgConfig
    XDG_DATA_HOME = $XdgData
    XDG_CACHE_HOME = $XdgCache
  }
  foreach ($Entry in $Environment.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($Entry.Key, [string]$Entry.Value, 'Process')
  }

  $Config = [ordered]@{
    '$schema' = 'https://opencode.ai/config.json'
    model = $Model
    permission = [ordered]@{
      '*' = 'deny'
      read = 'allow'
      glob = 'allow'
      grep = 'allow'
      list = 'allow'
      skill = 'allow'
      'gw-ap-debug_*' = 'allow'
      'gw_ap_debug_*' = 'allow'
      edit = 'deny'
      bash = 'deny'
      task = 'deny'
      webfetch = 'deny'
      websearch = 'deny'
      external_directory = 'deny'
    }
    mcp = [ordered]@{
      'gw-ap-debug' = [ordered]@{
        type = 'local'
        command = @($PythonExe, '-m', 'app.agent_runtime.mcp_server')
        enabled = $true
        timeout = 30000
        environment = [ordered]@{
          PYTHONPATH = (Join-Path $Root 'backend')
          GWAP_RUNTIME_URL = $RuntimeUrl
          GWAP_AGENT_ROLE = 'ENGINEER'
        }
      }
    }
  }
  [System.IO.File]::WriteAllText($ConfigPath, ($Config | ConvertTo-Json -Depth 12), $Utf8NoBom)

  $RuntimeProcess = Start-Process -FilePath $PythonExe `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port") `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $ServerOut `
    -RedirectStandardError $ServerErr `
    -WindowStyle Hidden `
    -PassThru

  $Deadline = (Get-Date).AddSeconds(45)
  $Ready = $false
  while ((Get-Date) -lt $Deadline) {
    if ($RuntimeProcess.HasExited) {
      $Tail = if (Test-Path -LiteralPath $ServerErr) { (Get-Content -LiteralPath $ServerErr -Tail 30) -join "`n" } else { '' }
      throw "Runtime exited during startup.`n$Tail"
    }
    try {
      $Health = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/health" -TimeoutSec 2
      if ($Health.status -eq 'ok') { $Ready = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 250
  }
  if (-not $Ready) { throw "Runtime did not become ready at $RuntimeUrl" }

  $ErrorActionPreference = 'Continue'
  $McpOutput = @(& $OpenCode.Source mcp list --pure 2>&1 | ForEach-Object { [string]$_ })
  $McpExit = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  if ($McpExit -ne 0) { throw "opencode mcp list failed:`n$($McpOutput -join "`n")" }
  $McpText = $McpOutput -join "`n"
  if ($McpText -notmatch 'gw-ap-debug' -or $McpText -notmatch '(?i)connected') {
    throw "OpenCode did not connect to gw-ap-debug:`n$McpText"
  }

  $Prompt = @"
This is an isolated integration test using only synthetic repository data and a synthetic collectDebuginfo archive.
Load the gw-ap-debug skill and use the gw-ap-debug MCP tools as the diagnostic data plane. Do not use bash, edit, task, web, or any source-modification tool.

Complete every required step in order:
1. Call debug_status and verify External Agent Mode.
2. Call debug_create_case with title exactly "$CaseTitle", device_type AP, a short synthetic-test description, and confirm_write=true.
3. Using the returned case_id, call debug_ingest with local_file "$SampleLog", wait=true, timeout_seconds=180, and confirm_write=true.
4. Call debug_attach_workspace for "$Workspace", index=true, wait=true, timeout_seconds=180, and confirm_write=true.
5. Call debug_diagnose with wait=true, timeout_seconds=180, and confirm_write=true. This is the platform's deterministic evidence analysis, not your final reasoning.
6. Call debug_evidence_bundle with query "hostapd authentication failure", top_k=12, and max_hops=2.
7. Use your own OpenCode LLM reasoning over that evidence. Do not invent evidence IDs.

Your final answer must include all of the following:
OPENCODE_GWAP_E2E_PASS
CASE_ID=<the real case id>
At least one CONFIRMED statement with a real evidence ID
At least one PROBABLE statement with a real evidence ID
At least one UNKNOWN or missing-information statement
"@

  $ErrorActionPreference = 'Continue'
  $RawOutput = @(& $OpenCode.Source run --pure --auto --model $Model --format json --title $CaseTitle --dir $Workspace $Prompt 2>&1 | ForEach-Object { [string]$_ })
  $OpenCodeExit = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  [System.IO.File]::WriteAllLines($EventsPath, [string[]]$RawOutput, $Utf8NoBom)
  if ($OpenCodeExit -ne 0) {
    throw "opencode run failed with exit code $OpenCodeExit. Last output:`n$(($RawOutput | Select-Object -Last 30) -join "`n")"
  }

  $ToolCalls = New-Object System.Collections.Generic.List[string]
  $TextParts = New-Object System.Collections.Generic.List[string]
  foreach ($Line in $RawOutput) {
    try { $Event = $Line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
    if ($Event.type -eq 'text' -and $Event.part.text) { $TextParts.Add([string]$Event.part.text) }
    if ($Event.part.type -eq 'tool' -and $Event.part.tool) { $ToolCalls.Add([string]$Event.part.tool) }
  }
  $FinalText = $TextParts -join "`n"
  foreach ($RequiredTool in @('skill', 'debug_status', 'debug_create_case', 'debug_ingest', 'debug_attach_workspace', 'debug_diagnose', 'debug_evidence_bundle')) {
    if (-not ($ToolCalls | Where-Object { $_ -like "*$RequiredTool" })) {
      throw "OpenCode did not call required MCP tool $RequiredTool. Calls: $($ToolCalls -join ', ')"
    }
  }
  if ($FinalText -notmatch 'OPENCODE_GWAP_E2E_PASS') { throw 'OpenCode final response did not contain the success marker.' }
  if ($FinalText -notmatch 'EVT[-_][A-Za-z0-9-]+') { throw 'OpenCode final response did not cite a real EVT evidence ID.' }
  if ($FinalText -notmatch '(?i)CONFIRMED' -or $FinalText -notmatch '(?i)PROBABLE' -or $FinalText -notmatch '(?i)(UNKNOWN|missing information)') {
    throw 'OpenCode final response did not preserve the required CONFIRMED/PROBABLE/UNKNOWN distinction.'
  }

  $CaseIdMatch = [regex]::Match($FinalText, '(?im)CASE_ID\s*=\s*`?(CASE[-_][A-Za-z0-9_-]+)`?')
  if (-not $CaseIdMatch.Success) { throw 'OpenCode final response did not expose a parseable CASE_ID.' }
  $ReportedCaseId = $CaseIdMatch.Groups[1].Value
  $Case = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/cases/$ReportedCaseId" -TimeoutSec 10
  if (-not $Case -or $Case.id -ne $ReportedCaseId) { throw "The case reported by OpenCode was not persisted: $ReportedCaseId" }
  $Artifacts = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/cases/$($Case.id)/artifacts" -TimeoutSec 10
  $EventStats = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/cases/$($Case.id)/events/stats" -TimeoutSec 10
  $Analyses = Invoke-RestMethod -Uri "$RuntimeUrl/api/v1/cases/$($Case.id)/analyses" -TimeoutSec 10
  $ArtifactCount = if ($null -eq $Artifacts) { 0 } elseif ($Artifacts -is [System.Array]) { $Artifacts.Length } else { 1 }
  $AnalysisCount = if ($null -eq $Analyses) { 0 } elseif ($Analyses -is [System.Array]) { $Analyses.Length } else { 1 }
  if ($ArtifactCount -lt 1 -or $EventStats.total -lt 1 -or $AnalysisCount -lt 1) {
    throw "Runtime persistence verification failed: artifacts=$ArtifactCount, events=$($EventStats.total), analyses=$AnalysisCount"
  }
  $Analysis = if ($Analyses -is [System.Array]) { $Analyses[0] } else { $Analyses }
  if ($Analysis.provider -ne 'deterministic' -or $Analysis.model -ne 'rule+agentic-evidence') {
    throw "External-mode platform analysis attribution is incorrect: $($Analysis.provider)/$($Analysis.model)"
  }

  $ErrorActionPreference = 'Continue'
  $Version = (& $OpenCode.Source --version 2>$null | Select-Object -First 1).Trim()
  $ErrorActionPreference = 'Stop'
  $Summary = [ordered]@{
    status = 'PASS'
    opencode_version = $Version
    model = $Model
    mcp_connected = $true
    case_id = $Case.id
    case_title = $Case.title
    requested_case_title = $CaseTitle
    artifact_count = $ArtifactCount
    event_count = $EventStats.total
    analysis_count = $AnalysisCount
    analysis_provider = $Analysis.provider
    analysis_model = $Analysis.model
    external_llm_result_persisted = ([string]$Analysis.result_json).Contains('OPENCODE_GWAP_E2E_PASS')
    tool_calls = @($ToolCalls)
    final_excerpt = $FinalText.Substring(0, [Math]::Min(1600, $FinalText.Length))
    artifacts_path = if ($KeepArtifacts) { $ResolvedTempRoot } else { $null }
  }
  $Summary | ConvertTo-Json -Depth 6
} finally {
  if ($RuntimeProcess -and -not $RuntimeProcess.HasExited) {
    Stop-Process -Id $RuntimeProcess.Id -ErrorAction SilentlyContinue
    $StopDeadline = (Get-Date).AddSeconds(10)
    while (-not $RuntimeProcess.HasExited -and (Get-Date) -lt $StopDeadline) { Start-Sleep -Milliseconds 100 }
    if (-not $RuntimeProcess.HasExited) {
      Stop-Process -Id $RuntimeProcess.Id -Force -ErrorAction SilentlyContinue
      $RuntimeProcess.WaitForExit(10000) | Out-Null
    }
  }
  if ($RuntimeProcess) {
    $RuntimeProcess.WaitForExit(10000) | Out-Null
    $RuntimeProcess.Dispose()
  }
  foreach ($Name in $EnvironmentNames) {
    [Environment]::SetEnvironmentVariable($Name, $PreviousEnvironment[$Name], 'Process')
  }
  if (-not $KeepArtifacts -and (Test-Path -LiteralPath $ResolvedTempRoot)) {
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
      try {
        Remove-Item -LiteralPath $ResolvedTempRoot -Recurse -Force -ErrorAction Stop
        break
      } catch {
        if ($Attempt -eq 19) { throw }
        Start-Sleep -Milliseconds 200
      }
    }
  }
}
