[CmdletBinding()]
param(
  [string]$AgentCommand = '',
  [string]$ProjectRoot = '',
  [string]$RuntimeUrl = 'http://127.0.0.1:8766',
  [string]$PythonExe = '',
  [string]$ServerName = 'gw-ap-debug-vnext',
  [ValidateSet('Auto', 'CodeArts', 'OpenCode', 'ClaudeCompatible', 'AgentCompatible')]
  [string]$ClientFamily = 'Auto',
  [int[]]$ScanPorts = @(8765, 8766),
  [int]$CommandTimeoutSeconds = 15,
  [string]$ReportPath = '',
  [switch]$SkipClientCommands,
  [switch]$Strict,
  [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ProjectRoot) { $ProjectRoot = $RepositoryRoot }
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$BackendRoot = Join-Path $RepositoryRoot 'backend'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Resolve-ClientCommand([string]$Name) {
  if (-not $Name) { return $null }
  if ([System.IO.Path]::IsPathRooted($Name) -and (Test-Path -LiteralPath $Name)) {
    return (Resolve-Path -LiteralPath $Name).Path
  }
  $Resolved = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $Resolved) { return $null }
  if ($Resolved.Source) { return $Resolved.Source }
  return $Resolved.Definition
}

function Protect-CommandOutput([string]$Value) {
  if (-not $Value) { return '' }
  $Protected = $Value
  $Protected = $Protected -replace '(?im)(api[_-]?key|token|password|authorization|secret)\s*[:=]\s*[^\s,;]+', '$1=<redacted>'
  $Protected = $Protected -replace '(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer <redacted>'
  if ($Protected.Length -gt 4000) { return $Protected.Substring(0, 4000) + "`n<truncated>" }
  return $Protected
}

function Invoke-ClientCommand([string]$Executable, [string[]]$Arguments) {
  $StdoutPath = [System.IO.Path]::GetTempFileName()
  $StderrPath = [System.IO.Path]::GetTempFileName()
  try {
    $LaunchExecutable = $Executable
    $LaunchArguments = @($Arguments)
    if ([System.IO.Path]::GetExtension($Executable) -eq '.ps1') {
      $CmdShim = [System.IO.Path]::ChangeExtension($Executable, '.cmd')
      if (Test-Path -LiteralPath $CmdShim) {
        $LaunchExecutable = $CmdShim
      } else {
        $PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
        $LaunchExecutable = $PowerShell
        $LaunchArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $Executable + '"')) + $Arguments
      }
    }
    $StartArguments = @{
      FilePath = $LaunchExecutable
      ArgumentList = $LaunchArguments
      WorkingDirectory = $ProjectRoot
      RedirectStandardOutput = $StdoutPath
      RedirectStandardError = $StderrPath
      PassThru = $true
      WindowStyle = 'Hidden'
    }
    $Process = Start-Process @StartArguments
    $Completed = $Process.WaitForExit($CommandTimeoutSeconds * 1000)
    if (-not $Completed) {
      Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
      $Process.WaitForExit(5000) | Out-Null
    } else {
      $Process.WaitForExit()
    }
    $ExitCode = $null
    if ($Completed) {
      $Process.Refresh()
      $ExitCode = [int]$Process.ExitCode
    }
    $Stdout = if (Test-Path -LiteralPath $StdoutPath) { Get-Content -Raw -ErrorAction SilentlyContinue -LiteralPath $StdoutPath } else { '' }
    $Stderr = if (Test-Path -LiteralPath $StderrPath) { Get-Content -Raw -ErrorAction SilentlyContinue -LiteralPath $StderrPath } else { '' }
    return [ordered]@{
      arguments = @($Arguments)
      exit_code = $ExitCode
      timed_out = -not $Completed
      stdout = Protect-CommandOutput ([string]$Stdout)
      stderr = Protect-CommandOutput ([string]$Stderr)
    }
  } catch {
    return [ordered]@{
      arguments = @($Arguments)
      exit_code = $null
      timed_out = $false
      stdout = ''
      stderr = Protect-CommandOutput $_.Exception.Message
    }
  } finally {
    Remove-Item -LiteralPath $StdoutPath, $StderrPath -Force -ErrorAction SilentlyContinue
  }
}

function Get-JsonPropertyNames($Value) {
  if ($null -eq $Value) { return @() }
  if ($Value -is [System.Collections.IDictionary]) { return @($Value.Keys) }
  return @($Value.PSObject.Properties.Name)
}

function Inspect-ConfigFile([string]$Path, [string]$Kind) {
  $Result = [ordered]@{
    path = $Path
    kind = $Kind
    exists = Test-Path -LiteralPath $Path
    schema = 'missing'
    server_names = @()
    contains_expected_server = $false
    loopback_runtime_urls = @()
    parse_error = $null
  }
  if (-not $Result.exists) { return $Result }
  $Raw = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
  $Result.contains_expected_server = $Raw -match [regex]::Escape($ServerName)
  $Result.loopback_runtime_urls = @(
    [regex]::Matches($Raw, 'http://(?:127\.0\.0\.1|localhost):\d+') |
      ForEach-Object { $_.Value } |
      Sort-Object -Unique
  )
  try {
    $Parsed = $Raw | ConvertFrom-Json
    $RootNames = Get-JsonPropertyNames $Parsed
    if ($RootNames -contains 'mcpServers') {
      $Result.schema = 'claude-compatible'
      $Result.server_names = @(Get-JsonPropertyNames $Parsed.mcpServers)
    } elseif ($RootNames -contains 'mcp') {
      $McpNames = Get-JsonPropertyNames $Parsed.mcp
      if ($McpNames -contains 'servers') {
        $Result.schema = 'opencode-v2'
        $Result.server_names = @(Get-JsonPropertyNames $Parsed.mcp.servers)
      } else {
        $Result.schema = 'codearts-or-opencode-v1'
        $Result.server_names = @($McpNames)
      }
    } else {
      $Result.schema = 'json-without-mcp'
    }
  } catch {
    $Result.parse_error = 'JSONC or invalid JSON; values were not emitted.'
    if ($Raw -match '"mcpServers"\s*:') {
      $Result.schema = 'claude-compatible-jsonc'
    } elseif ($Raw -match '"mcp"\s*:\s*\{[\s\S]*?"servers"\s*:') {
      $Result.schema = 'opencode-v2-jsonc'
    } elseif ($Raw -match '"mcp"\s*:') {
      $Result.schema = 'codearts-or-opencode-v1-jsonc'
    } else {
      $Result.schema = 'unrecognized-jsonc'
    }
  }
  return $Result
}

function Inspect-Skill([string]$Path, [string]$Client, [string]$Scope) {
  $Exists = Test-Path -LiteralPath $Path
  $Valid = $false
  $HasScripts = $false
  if ($Exists) {
    $Raw = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
    $Valid = $Raw -match '(?ms)^---\s*.*?^name:\s*gw-ap-debug\s*$.*?^description:\s*.+?^---\s*$'
    $HasScripts = Test-Path -LiteralPath (Join-Path (Split-Path $Path -Parent) 'scripts\gwap.ps1')
  }
  return [ordered]@{
    client = $Client
    scope = $Scope
    path = $Path
    exists = $Exists
    valid_frontmatter = $Valid
    cli_fallback_present = $HasScripts
  }
}

function Invoke-RuntimeProbe([string]$BaseUrl) {
  $Result = [ordered]@{
    url = $BaseUrl.TrimEnd('/')
    healthy = $false
    agent_runtime_endpoint = $false
    agent_mode = $null
    error = $null
  }
  try {
    $Health = Invoke-RestMethod -Uri "$($Result.url)/api/v1/health" -TimeoutSec 2
    $Result.healthy = $Health.status -eq 'ok'
    if ($Result.healthy) {
      try {
        $Runtime = Invoke-RestMethod -Uri "$($Result.url)/api/v1/system/agent-runtime" -TimeoutSec 2
        $Result.agent_runtime_endpoint = $true
        if ($Runtime.agent_mode) { $Result.agent_mode = [string]$Runtime.agent_mode }
        elseif ($Runtime.runtime.agent_mode) { $Result.agent_mode = [string]$Runtime.runtime.agent_mode }
      } catch {
        $Result.error = 'Health endpoint responded, but the Agent Runtime endpoint did not.'
      }
    }
  } catch {
    $Result.error = 'No GW/AP Debug health response.'
  }
  return $Result
}

function Invoke-McpHandshake([string]$Executable) {
  $Result = [ordered]@{
    attempted = $false
    passed = $false
    server_name = $null
    server_version = $null
    protocol_version = $null
    transport = 'stdio'
    tcp_port = $null
    tool_count = 0
    tool_names = @()
    missing_required_tools = @()
    error = $null
  }
  if (-not $Executable -or -not (Test-Path -LiteralPath $Executable)) {
    $Result.error = 'Python executable not found; pass -PythonExe.'
    return $Result
  }
  $Result.attempted = $true
  $Process = $null
  $InputPath = [System.IO.Path]::GetTempFileName()
  $OutputPath = [System.IO.Path]::GetTempFileName()
  $ErrorPath = [System.IO.Path]::GetTempFileName()
  $PreviousPythonPath = $env:PYTHONPATH
  $PreviousRuntimeUrl = $env:GWAP_RUNTIME_URL
  $PreviousAgentRole = $env:GWAP_AGENT_ROLE
  try {
    $Messages = @(
      [ordered]@{
        jsonrpc = '2.0'; id = 1; method = 'initialize'
        params = [ordered]@{
          protocolVersion = '2025-06-18'; capabilities = [ordered]@{}
          clientInfo = [ordered]@{name = 'gwap-codeagent-probe'; version = '1.0'}
        }
      },
      [ordered]@{jsonrpc = '2.0'; method = 'notifications/initialized'; params = [ordered]@{}},
      [ordered]@{jsonrpc = '2.0'; id = 2; method = 'tools/list'; params = [ordered]@{}}
    )
    $MessageLines = @($Messages | ForEach-Object { $_ | ConvertTo-Json -Depth 8 -Compress })
    [System.IO.File]::WriteAllLines($InputPath, [string[]]$MessageLines, $Utf8NoBom)
    $env:PYTHONPATH = $BackendRoot
    $env:GWAP_RUNTIME_URL = $RuntimeUrl
    $env:GWAP_AGENT_ROLE = 'ENGINEER'
    $Process = Start-Process -FilePath $Executable `
      -ArgumentList @('-m', 'app.agent_runtime.mcp_server') `
      -WorkingDirectory $RepositoryRoot `
      -RedirectStandardInput $InputPath `
      -RedirectStandardOutput $OutputPath `
      -RedirectStandardError $ErrorPath `
      -WindowStyle Hidden `
      -PassThru
    if (-not $Process.WaitForExit(10000)) {
      Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
      throw 'MCP handshake timed out after 10 seconds.'
    }
    $Process.WaitForExit()
    $Output = Get-Content -Raw -Encoding UTF8 -LiteralPath $OutputPath
    $ErrorText = Get-Content -Raw -Encoding UTF8 -LiteralPath $ErrorPath
    $Responses = @()
    foreach ($Line in ($Output -split "`r?`n")) {
      if (-not $Line.Trim()) { continue }
      try { $Responses += ($Line | ConvertFrom-Json) } catch {}
    }
    $Initialize = $Responses | Where-Object { $_.id -eq 1 } | Select-Object -First 1
    $Tools = $Responses | Where-Object { $_.id -eq 2 } | Select-Object -First 1
    if (-not $Initialize -or -not $Tools) {
      throw "MCP did not return initialize and tools/list responses. $ErrorText"
    }
    $Result.server_name = [string]$Initialize.result.serverInfo.name
    $Result.server_version = [string]$Initialize.result.serverInfo.version
    $Result.protocol_version = [string]$Initialize.result.protocolVersion
    $Result.tool_names = @($Tools.result.tools | ForEach-Object { [string]$_.name })
    $Result.tool_count = $Result.tool_names.Count
    $Required = @(
      'debug_status', 'debug_create_case', 'debug_ingest', 'debug_wait_job',
      'debug_inspect', 'debug_search', 'debug_evidence_bundle', 'debug_attach_workspace',
      'debug_code_context', 'debug_diagnose', 'debug_generate_report', 'debug_open_ui'
    )
    $Result.missing_required_tools = @($Required | Where-Object { $Result.tool_names -notcontains $_ })
    $Result.passed = $Result.server_name -eq 'gw-ap-debug' -and $Result.missing_required_tools.Count -eq 0
  } catch {
    $Result.error = Protect-CommandOutput $_.Exception.Message
  } finally {
    if ($Process -and -not $Process.HasExited) {
      Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
    if ($Process) { $Process.Dispose() }
    $env:PYTHONPATH = $PreviousPythonPath
    $env:GWAP_RUNTIME_URL = $PreviousRuntimeUrl
    $env:GWAP_AGENT_ROLE = $PreviousAgentRole
    Remove-Item -LiteralPath $InputPath, $OutputPath, $ErrorPath -Force -ErrorAction SilentlyContinue
  }
  return $Result
}

if (-not $PythonExe) {
  $PythonCandidates = @()
  if ($env:VIRTUAL_ENV) { $PythonCandidates += (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe') }
  if ($env:LOCALAPPDATA) {
    $PythonCandidates += (Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext\venv\Scripts\python.exe')
    $PythonCandidates += (Join-Path $env:LOCALAPPDATA 'GWAPDebug\venv\Scripts\python.exe')
  }
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($PythonCommand) { $PythonCandidates += $PythonCommand.Source }
  $PythonExe = $PythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
  $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
}

$CandidateNames = if ($AgentCommand) { @($AgentCommand) } else { @('codearts', 'codeagent', 'opencode') }
$DetectedClients = @()
foreach ($CandidateName in $CandidateNames) {
  $Executable = Resolve-ClientCommand $CandidateName
  if (-not $Executable) { continue }
  $Client = [ordered]@{
    requested_name = $CandidateName
    executable = $Executable
    version = $null
    help = $null
    mcp_list = $null
    supports_run = $false
    supports_mcp_command = $false
    expected_server_visible = $false
    expected_server_connected = $false
  }
  if (-not $SkipClientCommands) {
    $Client.version = Invoke-ClientCommand $Executable @('--version')
    $Client.help = Invoke-ClientCommand $Executable @('help')
    if ($Client.help.exit_code -ne 0) { $Client.help = Invoke-ClientCommand $Executable @('--help') }
    $Client.mcp_list = Invoke-ClientCommand $Executable @('mcp', 'list')
    $CombinedHelp = "$($Client.help.stdout)`n$($Client.help.stderr)"
    $CombinedMcp = "$($Client.mcp_list.stdout)`n$($Client.mcp_list.stderr)"
    $Client.supports_run = $CombinedHelp -match '(?im)(^|\s)run(\s|$)'
    $Client.supports_mcp_command = $Client.mcp_list.exit_code -eq 0
    $Client.expected_server_visible = $CombinedMcp -match [regex]::Escape($ServerName)
    # Keep this source ASCII-only for Windows PowerShell 5.1, which may decode
    # UTF-8 files without a BOM using the active ANSI code page.
    $Client.expected_server_connected = $Client.expected_server_visible -and $CombinedMcp -match '(?i)(connected|enabled|\u5df2\u8fde\u63a5|\u5df2\u542f\u7528|\u2713)'
  }
  $DetectedClients += $Client
}

$SkillChecks = @(
  (Inspect-Skill (Join-Path $ProjectRoot '.codeartsdoer\skills\gw-ap-debug\SKILL.md') 'codearts' 'project'),
  (Inspect-Skill (Join-Path $HOME '.codeartsdoer\skills\gw-ap-debug\SKILL.md') 'codearts' 'user'),
  (Inspect-Skill (Join-Path $ProjectRoot '.opencode\skills\gw-ap-debug\SKILL.md') 'opencode' 'project'),
  (Inspect-Skill (Join-Path $ProjectRoot '.claude\skills\gw-ap-debug\SKILL.md') 'claude-compatible' 'project'),
  (Inspect-Skill (Join-Path $ProjectRoot '.agents\skills\gw-ap-debug\SKILL.md') 'agent-compatible' 'project')
)

$ConfigChecks = @(
  (Inspect-ConfigFile (Join-Path $ProjectRoot '.codeartsdoer\codearts_cli.json') 'codearts-project-native'),
  (Inspect-ConfigFile (Join-Path $ProjectRoot '.codeartsdoer\codearts_cli.jsonc') 'codearts-project-native-jsonc'),
  (Inspect-ConfigFile (Join-Path $ProjectRoot '.codeartsdoer\mcp\mcp_settings.json') 'codearts-project-claude'),
  (Inspect-ConfigFile (Join-Path $HOME '.codeartsdoer\codearts_cli.json') 'codearts-user-native'),
  (Inspect-ConfigFile (Join-Path $HOME '.codeartsdoer\codearts_cli.jsonc') 'codearts-user-native-jsonc'),
  (Inspect-ConfigFile (Join-Path $ProjectRoot 'opencode.json') 'opencode-project'),
  (Inspect-ConfigFile (Join-Path $ProjectRoot 'opencode.jsonc') 'opencode-project-jsonc'),
  (Inspect-ConfigFile (Join-Path $ProjectRoot '.mcp.json') 'claude-project')
)

$RuntimeCandidates = @($RuntimeUrl)
$RuntimeCandidates += @($ConfigChecks | ForEach-Object { $_.loopback_runtime_urls })
foreach ($Port in $ScanPorts) {
  if ($Port -ge 1 -and $Port -le 65535) { $RuntimeCandidates += "http://127.0.0.1:$Port" }
}
$RuntimeChecks = @($RuntimeCandidates | Where-Object { $_ } | Sort-Object -Unique | ForEach-Object { Invoke-RuntimeProbe $_ })
$McpHandshake = Invoke-McpHandshake $PythonExe

$CodeArtsSkillReady = @($SkillChecks | Where-Object { $_.client -eq 'codearts' -and $_.exists -and $_.valid_frontmatter -and $_.cli_fallback_present }).Count -gt 0
$AnySkillReady = @($SkillChecks | Where-Object { $_.exists -and $_.valid_frontmatter -and $_.cli_fallback_present }).Count -gt 0
$SelectedClientName = if ($DetectedClients.Count) { [string]$DetectedClients[0].requested_name } else { '' }
$EffectiveClientFamily = if ($ClientFamily -ne 'Auto') {
  $ClientFamily
} elseif ($SelectedClientName -match '(?i)(codearts|codeagent)') {
  'CodeArts'
} elseif ($SelectedClientName -match '(?i)opencode') {
  'OpenCode'
} else {
  'Auto'
}
$ClientSkillReady = if ($EffectiveClientFamily -eq 'OpenCode') {
  @($SkillChecks | Where-Object {
    $_.client -in @('opencode', 'claude-compatible', 'agent-compatible') -and
    $_.exists -and $_.valid_frontmatter -and $_.cli_fallback_present
  }).Count -gt 0
} elseif ($EffectiveClientFamily -eq 'CodeArts') {
  $CodeArtsSkillReady
} elseif ($EffectiveClientFamily -eq 'ClaudeCompatible') {
  @($SkillChecks | Where-Object { $_.client -eq 'claude-compatible' -and $_.exists -and $_.valid_frontmatter -and $_.cli_fallback_present }).Count -gt 0
} elseif ($EffectiveClientFamily -eq 'AgentCompatible') {
  @($SkillChecks | Where-Object { $_.client -eq 'agent-compatible' -and $_.exists -and $_.valid_frontmatter -and $_.cli_fallback_present }).Count -gt 0
} else {
  $AnySkillReady
}
$RuntimeReady = @($RuntimeChecks | Where-Object { $_.healthy -and $_.agent_runtime_endpoint }).Count -gt 0
$ClientMcpConnected = @($DetectedClients | Where-Object { $_.expected_server_connected }).Count -gt 0
$ClientFound = $DetectedClients.Count -gt 0
$Compatibility = if ($ClientSkillReady -and $RuntimeReady -and $McpHandshake.passed -and $ClientMcpConnected) {
  'FULL_SKILL_MCP'
} elseif ($ClientSkillReady -and $RuntimeReady -and $McpHandshake.passed) {
  'SKILL_CLI_READY_MCP_CLIENT_UNCONFIRMED'
} elseif ($AnySkillReady -and $McpHandshake.passed) {
  'PORTABLE_ASSETS_READY_RUNTIME_NOT_CONFIRMED'
} else {
  'PARTIAL'
}

$Recommendations = New-Object System.Collections.Generic.List[string]
if (-not $ClientFound) { $Recommendations.Add('Pass -AgentCommand with the actual CodeAgent executable name or absolute path.') }
if (-not $ClientSkillReady) { $Recommendations.Add('Run scripts\setup_codeagent_vnext.bat with the correct -TargetClient to install a discoverable project Skill.') }
if (-not $RuntimeReady) { $Recommendations.Add('Start the isolated vNext Runtime and verify its /api/v1/health endpoint.') }
if (-not $McpHandshake.passed) { $Recommendations.Add('Fix the direct stdio MCP handshake before changing any CodeAgent MCP configuration.') }
if ($McpHandshake.passed -and -not $ClientMcpConnected) { $Recommendations.Add('Use Skill + CLI fallback now, or configure one supported MCP schema and rerun this probe.') }
if ($ClientMcpConnected) { $Recommendations.Add('Run a synthetic end-to-end diagnosis and confirm real debug_* tool calls and EVT evidence IDs.') }

$Report = [ordered]@{
  generated_at = (Get-Date).ToString('o')
  status = $Compatibility
  project_root = $ProjectRoot
  expected_server_name = $ServerName
  client_family = $EffectiveClientFamily
  transport_map = [ordered]@{
    mcp_transport = 'stdio'
    mcp_tcp_port = $null
    runtime_http_url = $RuntimeUrl
    agent_serve_port_is_separate = $true
  }
  clients = @($DetectedClients)
  skills = @($SkillChecks)
  configs = @($ConfigChecks)
  runtime = @($RuntimeChecks)
  mcp_handshake = $McpHandshake
  tool_matching_rule = 'Match exact raw debug_* names or their suffix after a client-added/normalized server prefix.'
  recommendations = @($Recommendations)
}
$Json = $Report | ConvertTo-Json -Depth 12
if ($ReportPath) {
  $Parent = Split-Path -Parent ([System.IO.Path]::GetFullPath($ReportPath))
  if ($Parent) { New-Item -ItemType Directory -Force -Path $Parent | Out-Null }
  [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($ReportPath), $Json, $Utf8NoBom)
}
if (-not $Quiet) { $Json }
if ($Strict -and $Compatibility -ne 'FULL_SKILL_MCP') { exit 2 }
exit 0
