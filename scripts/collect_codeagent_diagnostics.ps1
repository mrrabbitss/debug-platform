[CmdletBinding()]
param(
  [string]$ProjectRoot = '',
  [string]$NgaCommand = 'nga',
  [string]$CodeAgentExe = '',
  [string]$RuntimeRoot = '',
  [string]$PythonExe = '',
  [string]$RuntimeUrl = 'http://127.0.0.1:8766',
  [string]$OutputDirectory = '',
  [int]$CommandTimeoutSeconds = 15,
  [switch]$SkipClientCommands
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ProjectRoot) { $ProjectRoot = $RepositoryRoot }
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
  throw "ProjectRoot does not exist: $ProjectRoot"
}
if ($CommandTimeoutSeconds -lt 1 -or $CommandTimeoutSeconds -gt 60) {
  throw 'CommandTimeoutSeconds must be between 1 and 60.'
}
$ParsedRuntimeUrl = $null
if (-not [System.Uri]::TryCreate(
  $RuntimeUrl,
  [System.UriKind]::Absolute,
  [ref]$ParsedRuntimeUrl
)) {
  throw "RuntimeUrl is invalid: $RuntimeUrl"
}
if ($ParsedRuntimeUrl.Scheme -notin @('http', 'https') -or
    $ParsedRuntimeUrl.Host -notin @('127.0.0.1', 'localhost', '::1')) {
  throw 'RuntimeUrl must use HTTP(S) on loopback; the collector will not probe a remote endpoint.'
}
if (-not $RuntimeRoot) {
  if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; pass -RuntimeRoot.' }
  $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
if (-not $PythonExe) { $PythonExe = Join-Path $RuntimeRoot 'venv\Scripts\python.exe' }
if (Test-Path -LiteralPath $PythonExe) {
  $PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
}
if (-not $OutputDirectory) {
  $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $OutputDirectory = Join-Path $RuntimeRoot "logs\codeagent-diagnostics\$Stamp"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$ProbeScript = Join-Path $PSScriptRoot 'probe_codeagent_compatibility.ps1'
if (-not (Test-Path -LiteralPath $ProbeScript)) {
  throw "Compatibility probe is missing: $ProbeScript"
}

function Resolve-ExecutableInfo([string]$Requested) {
  $Result = [ordered]@{
    requested = $Requested
    found = $false
    command_type = $null
    alias_target = $null
    resolved_path = $null
    file_name = $null
    extension = $null
    file_version = $null
    product_version = $null
  }
  if (-not $Requested) { return [pscustomobject]$Result }

  $ResolvedPath = $null
  if ([System.IO.Path]::IsPathRooted($Requested)) {
    if (Test-Path -LiteralPath $Requested) {
      $ResolvedPath = (Resolve-Path -LiteralPath $Requested).Path
      $Result.command_type = 'Application'
    }
  } else {
    $Command = Get-Command $Requested -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Command) {
      $Result.command_type = [string]$Command.CommandType
      if ($Command.CommandType -eq 'Alias') {
        $Result.alias_target = [string]$Command.Definition
        $Command = Get-Command $Command.Definition -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Command) { $Result.command_type = "Alias->$($Command.CommandType)" }
      }
      if ($Command) {
        foreach ($Candidate in @($Command.Path, $Command.Source, $Command.Definition)) {
          if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            $ResolvedPath = (Resolve-Path -LiteralPath $Candidate).Path
            break
          }
        }
      }
    }
  }

  if (-not $ResolvedPath) { return [pscustomobject]$Result }
  $Result.found = $true
  $Result.resolved_path = $ResolvedPath
  $Result.file_name = [System.IO.Path]::GetFileName($ResolvedPath)
  $Result.extension = [System.IO.Path]::GetExtension($ResolvedPath)
  try {
    $VersionInfo = (Get-Item -LiteralPath $ResolvedPath).VersionInfo
    $Result.file_version = [string]$VersionInfo.FileVersion
    $Result.product_version = [string]$VersionInfo.ProductVersion
  } catch {}
  return [pscustomobject]$Result
}

function Find-CodeAgentExecutable($NgaInfo, [string]$ExplicitPath) {
  if ($ExplicitPath) {
    return [pscustomobject]@{
      discovery_method = 'explicit'
      info = Resolve-ExecutableInfo $ExplicitPath
    }
  }

  $Candidates = New-Object System.Collections.Generic.List[object]
  if ($NgaInfo.found -and $NgaInfo.resolved_path) {
    $NgaParent = Split-Path -Parent $NgaInfo.resolved_path
    $Candidates.Add([pscustomobject]@{
      method = 'nga-root-bin'
      path = Join-Path $NgaParent 'bin\codeagent.exe'
    })
    $Candidates.Add([pscustomobject]@{
      method = 'nga-sibling'
      path = Join-Path $NgaParent 'codeagent.exe'
    })
    if ((Split-Path -Leaf $NgaParent) -ieq 'bin') {
      $Candidates.Add([pscustomobject]@{
        method = 'nga-bin-sibling'
        path = Join-Path $NgaParent 'codeagent.exe'
      })
    }
  }
  foreach ($Candidate in $Candidates) {
    if (Test-Path -LiteralPath $Candidate.path) {
      return [pscustomobject]@{
        discovery_method = $Candidate.method
        info = Resolve-ExecutableInfo $Candidate.path
      }
    }
  }

  foreach ($Name in @('codeagent', 'codeagent.exe')) {
    $Info = Resolve-ExecutableInfo $Name
    if ($Info.found) {
      return [pscustomobject]@{
        discovery_method = 'path'
        info = $Info
      }
    }
  }
  return [pscustomobject]@{
    discovery_method = 'not-found'
    info = Resolve-ExecutableInfo 'codeagent.exe'
  }
}

function Protect-ShareableText([string]$Value, [int]$Limit = 3000) {
  if (-not $Value) { return '' }
  $Protected = $Value
  $Protected = $Protected -replace '(?im)(api[_-]?key|token|password|authorization|secret|access[_-]?key)\s*[:=]\s*[^\s,;]+', '$1=<redacted>'
  $Protected = $Protected -replace '(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer <redacted>'
  $Protected = $Protected -replace '(?i)https?://(?!(?:127\.0\.0\.1|localhost)(?::|/))[^\s"''<>]+', '<redacted-url>'
  foreach ($Replacement in @(
    [pscustomobject]@{value = $ProjectRoot; label = '%PROJECT_ROOT%'},
    [pscustomobject]@{value = $RuntimeRoot; label = '%RUNTIME_ROOT%'},
    [pscustomobject]@{value = $env:USERPROFILE; label = '%USERPROFILE%'}
  )) {
    if ($Replacement.value) {
      $Protected = [regex]::Replace(
        $Protected,
        [regex]::Escape([string]$Replacement.value),
        [string]$Replacement.label,
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
      )
    }
  }
  if ($Protected.Length -gt $Limit) {
    return $Protected.Substring(0, $Limit) + [Environment]::NewLine + '<truncated>'
  }
  return $Protected
}

function Invoke-BoundedCommand([string]$Executable, [string[]]$Arguments) {
  $StdoutPath = [System.IO.Path]::GetTempFileName()
  $StderrPath = [System.IO.Path]::GetTempFileName()
  try {
    $LaunchExecutable = $Executable
    $LaunchArguments = @($Arguments)
    $Extension = [System.IO.Path]::GetExtension($Executable)
    if ($Extension -ieq '.ps1') {
      $CmdShim = [System.IO.Path]::ChangeExtension($Executable, '.cmd')
      if (Test-Path -LiteralPath $CmdShim) {
        $LaunchExecutable = $CmdShim
      } else {
        $LaunchExecutable = (Get-Command powershell.exe -ErrorAction Stop).Source
        $LaunchArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Executable) + $Arguments
      }
    } elseif ($Extension -in @('.cmd', '.bat')) {
      $LaunchExecutable = $env:ComSpec
      if (-not $LaunchExecutable) { $LaunchExecutable = 'cmd.exe' }
      $ArgumentText = @($Arguments | ForEach-Object {
        '"' + ([string]$_).Replace('"', '""') + '"'
      }) -join ' '
      $CommandText = '"' + $Executable.Replace('"', '""') + '" ' + $ArgumentText
      $LaunchArguments = @('/d', '/s', '/c', '"' + $CommandText + '"')
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
    return [pscustomobject]@{
      arguments = @($Arguments)
      exit_code = $ExitCode
      timed_out = -not $Completed
      stdout = Get-Content -Raw -Encoding UTF8 -LiteralPath $StdoutPath -ErrorAction SilentlyContinue
      stderr = Get-Content -Raw -Encoding UTF8 -LiteralPath $StderrPath -ErrorAction SilentlyContinue
    }
  } catch {
    return [pscustomobject]@{
      arguments = @($Arguments)
      exit_code = $null
      timed_out = $false
      stdout = ''
      stderr = [string]$_.Exception.Message
    }
  } finally {
    Remove-Item -LiteralPath $StdoutPath, $StderrPath -Force -ErrorAction SilentlyContinue
  }
}

function Invoke-ProbeForCommand([string]$Label, $CommandInfo) {
  if (-not $CommandInfo.found) {
    return [pscustomobject]@{
      label = $Label
      attempted = $false
      report_path = $null
      report = $null
      error = 'Command was not found.'
    }
  }
  $ReportPath = Join-Path $OutputDirectory "$Label-probe.json"
  $Arguments = @{
    AgentCommand = $CommandInfo.resolved_path
    ClientFamily = 'CodeArts'
    ProjectRoot = $ProjectRoot
    RuntimeUrl = $RuntimeUrl
    PythonExe = $PythonExe
    ServerName = 'gw-ap-debug-vnext'
    CommandTimeoutSeconds = $CommandTimeoutSeconds
    ReportPath = $ReportPath
    Quiet = $true
  }
  if ($SkipClientCommands) { $Arguments['SkipClientCommands'] = $true }
  try {
    & $ProbeScript @Arguments
    if (-not (Test-Path -LiteralPath $ReportPath)) {
      throw "Probe report was not written: $ReportPath"
    }
    return [pscustomobject]@{
      label = $Label
      attempted = $true
      report_path = $ReportPath
      report = Get-Content -Raw -Encoding UTF8 -LiteralPath $ReportPath | ConvertFrom-Json
      error = $null
    }
  } catch {
    return [pscustomobject]@{
      label = $Label
      attempted = $true
      report_path = $(if (Test-Path -LiteralPath $ReportPath) { $ReportPath } else { $null })
      report = $null
      error = Protect-ShareableText ([string]$_.Exception.Message) 500
    }
  }
}

function ConvertTo-ShareableCommand($CommandInfo, [string]$DiscoveryMethod) {
  return [ordered]@{
    found = [bool]$CommandInfo.found
    command_type = $CommandInfo.command_type
    alias_target = $CommandInfo.alias_target
    file_name = $CommandInfo.file_name
    extension = $CommandInfo.extension
    file_version = $CommandInfo.file_version
    product_version = $CommandInfo.product_version
    discovery_method = $DiscoveryMethod
  }
}

function ConvertTo-ShareableProbe($ProbeResult) {
  if (-not $ProbeResult.report) {
    return [ordered]@{
      attempted = [bool]$ProbeResult.attempted
      status = 'UNAVAILABLE'
      error = $ProbeResult.error
    }
  }
  $Report = $ProbeResult.report
  $Client = @($Report.clients | Select-Object -First 1)
  $ClientSummary = $null
  if ($Client.Count -gt 0) {
    $VersionText = Protect-ShareableText ([string]$Client[0].version.stdout) 200
    $ClientSummary = [ordered]@{
      version_exit_code = $Client[0].version.exit_code
      version_timed_out = [bool]$Client[0].version.timed_out
      version_text = $VersionText.Trim()
      help_exit_code = $Client[0].help.exit_code
      help_timed_out = [bool]$Client[0].help.timed_out
      mcp_list_exit_code = $Client[0].mcp_list.exit_code
      mcp_list_timed_out = [bool]$Client[0].mcp_list.timed_out
      supports_run = [bool]$Client[0].supports_run
      supports_mcp_command = [bool]$Client[0].supports_mcp_command
      expected_server_visible = [bool]$Client[0].expected_server_visible
      expected_server_connected = [bool]$Client[0].expected_server_connected
    }
  }
  $ConfigChecks = @($Report.configs | Where-Object {
    $_.exists -or $_.contains_expected_server
  } | ForEach-Object {
    [ordered]@{
      kind = $_.kind
      exists = [bool]$_.exists
      schema = $_.schema
      contains_expected_server = [bool]$_.contains_expected_server
      parse_error = Protect-ShareableText ([string]$_.parse_error) 300
    }
  })
  $RuntimeChecks = @($Report.runtime | ForEach-Object {
    [ordered]@{
      url = $_.url
      healthy = [bool]$_.healthy
      agent_runtime_endpoint = [bool]$_.agent_runtime_endpoint
      agent_mode = $_.agent_mode
      error = Protect-ShareableText ([string]$_.error) 300
    }
  })
  return [ordered]@{
    attempted = $true
    status = $Report.status
    client_family = $Report.client_family
    client = $ClientSummary
    config_checks = $ConfigChecks
    runtime = $RuntimeChecks
    mcp_handshake = [ordered]@{
      attempted = [bool]$Report.mcp_handshake.attempted
      passed = [bool]$Report.mcp_handshake.passed
      protocol_passed = [bool]$Report.mcp_handshake.protocol_passed
      data_plane_attempted = [bool]$Report.mcp_handshake.data_plane_attempted
      data_plane_passed = [bool]$Report.mcp_handshake.data_plane_passed
      data_plane_status = $Report.mcp_handshake.data_plane_status
      data_plane_agent_mode = $Report.mcp_handshake.data_plane_agent_mode
      data_plane_error = Protect-ShareableText ([string]$Report.mcp_handshake.data_plane_error) 500
      server_name = $Report.mcp_handshake.server_name
      protocol_version = $Report.mcp_handshake.protocol_version
      tool_count = $Report.mcp_handshake.tool_count
      tool_names = @($Report.mcp_handshake.tool_names)
      missing_required_tools = @($Report.mcp_handshake.missing_required_tools)
      error = Protect-ShareableText ([string]$Report.mcp_handshake.error) 500
    }
    recommendations = @($Report.recommendations | ForEach-Object {
      Protect-ShareableText ([string]$_) 500
    })
  }
}

function Invoke-NgaDebugChecks($NgaInfo) {
  if (-not $NgaInfo.found -or $SkipClientCommands) {
    return [ordered]@{
      attempted = $false
      debug_config = $null
      debug_paths = $null
    }
  }
  $ConfigResult = Invoke-BoundedCommand $NgaInfo.resolved_path @('debug', 'config')
  $ConfigText = "$($ConfigResult.stdout)$([Environment]::NewLine)$($ConfigResult.stderr)"
  $ConfigSummary = [ordered]@{
    exit_code = $ConfigResult.exit_code
    timed_out = [bool]$ConfigResult.timed_out
    contains_expected_server = $ConfigText.Contains('gw-ap-debug-vnext')
    contains_mcp_servers = $ConfigText -match '(?i)["'']mcpServers["'']'
    contains_mcp_root = $ConfigText -match '(?i)["'']mcp["'']\s*[:=]'
    output_length = $ConfigText.Length
  }
  $ConfigText = $null

  $PathsResult = Invoke-BoundedCommand $NgaInfo.resolved_path @('debug', 'paths')
  $PathsText = Protect-ShareableText (
    "$($PathsResult.stdout)$([Environment]::NewLine)$($PathsResult.stderr)"
  ) 3000
  $PathsFile = Join-Path $OutputDirectory 'nga-debug-paths-sanitized.txt'
  [System.IO.File]::WriteAllText($PathsFile, $PathsText, $Utf8NoBom)
  return [ordered]@{
    attempted = $true
    debug_config = $ConfigSummary
    debug_paths = [ordered]@{
      exit_code = $PathsResult.exit_code
      timed_out = [bool]$PathsResult.timed_out
      sanitized_output_file = 'nga-debug-paths-sanitized.txt'
      contains_codeartsdoer = $PathsText -match '(?i)\.codeartsdoer'
      contains_opencode = $PathsText -match '(?i)opencode'
    }
  }
}

$NgaInfo = Resolve-ExecutableInfo $NgaCommand
$CodeAgentDiscovery = Find-CodeAgentExecutable $NgaInfo $CodeAgentExe
$CodeAgentInfo = $CodeAgentDiscovery.info

$NgaProbeResult = Invoke-ProbeForCommand 'nga' $NgaInfo
$CodeAgentProbeResult = Invoke-ProbeForCommand 'codeagent-exe' $CodeAgentInfo
$NgaProbe = ConvertTo-ShareableProbe $NgaProbeResult
$CodeAgentProbe = ConvertTo-ShareableProbe $CodeAgentProbeResult
$DebugChecks = Invoke-NgaDebugChecks $NgaInfo

$NgaConnected = $false
if ($NgaProbe.client) { $NgaConnected = [bool]$NgaProbe.client.expected_server_connected }
$CodeAgentConnected = $false
if ($CodeAgentProbe.client) {
  $CodeAgentConnected = [bool]$CodeAgentProbe.client.expected_server_connected
}
$DirectProtocolPassed = [bool]$NgaProbe.mcp_handshake.protocol_passed -or [bool]$CodeAgentProbe.mcp_handshake.protocol_passed
$DirectDataPlanePassed = [bool]$NgaProbe.mcp_handshake.data_plane_passed -or [bool]$CodeAgentProbe.mcp_handshake.data_plane_passed
$DirectMcpPassed = [bool]$NgaProbe.mcp_handshake.passed -or [bool]$CodeAgentProbe.mcp_handshake.passed
$KnownConfigContainsServer = @(
  @($NgaProbe.config_checks) + @($CodeAgentProbe.config_checks) |
    Where-Object { $_.contains_expected_server }
).Count -gt 0

$DiagnosticStatus = if ($NgaConnected -and $DirectMcpPassed) {
  'FULL_NGA_MCP'
} elseif ($NgaConnected -and $DirectProtocolPassed) {
  'NGA_MCP_CONNECTED_RUNTIME_TOOL_FAILED'
} elseif ($CodeAgentConnected -and $DirectMcpPassed) {
  'CODEAGENT_EXE_MCP_ONLY'
} elseif ($CodeAgentConnected -and $DirectProtocolPassed) {
  'CODEAGENT_EXE_MCP_CONNECTED_RUNTIME_TOOL_FAILED'
} elseif ($DirectMcpPassed -and $KnownConfigContainsServer) {
  'DIRECT_MCP_OK_CLIENT_CONFIG_NOT_ACTIVE'
} elseif ($DirectMcpPassed) {
  'DIRECT_MCP_OK_CLIENT_UNCONFIRMED'
} elseif ($DirectProtocolPassed) {
  'DIRECT_MCP_PROTOCOL_OK_RUNTIME_TOOL_FAILED'
} elseif (-not $NgaInfo.found -and -not $CodeAgentInfo.found) {
  'CLIENT_COMMANDS_NOT_FOUND'
} else {
  'MCP_HANDSHAKE_FAILED'
}

$Findings = New-Object System.Collections.Generic.List[string]
if (-not $NgaInfo.found) {
  $Findings.Add('The nga launcher was not found. Pass -NgaCommand with its absolute path.')
}
if (-not $CodeAgentInfo.found) {
  $Findings.Add('bin\codeagent.exe was not found. Pass -CodeAgentExe with its absolute path.')
}
if ($DirectProtocolPassed) {
  $Findings.Add('The GW/AP MCP server passed a direct stdio initialize/tools-list handshake.')
} else {
  $Findings.Add('The direct GW/AP MCP handshake failed; fix this before changing client configuration.')
}
if ($DirectDataPlanePassed) {
  $Findings.Add('A read-only debug_status MCP tools/call reached the local Runtime data plane successfully.')
} elseif ($DirectProtocolPassed) {
  $Findings.Add('MCP protocol discovery works, but read-only debug_status cannot reach the Runtime data plane; inspect loopback proxy handling.')
}
if ($NgaConnected -and $DirectMcpPassed) {
  $Findings.Add('The actual nga launcher reports gw-ap-debug-vnext as connected.')
} elseif ($NgaConnected) {
  $Findings.Add('The actual nga launcher reports connected, but the Runtime tool path is not usable yet.')
} elseif ($CodeAgentConnected -and $DirectMcpPassed) {
  $Findings.Add('codeagent.exe connects to MCP but nga does not; the wrapper changes configuration or environment.')
} elseif ($DirectMcpPassed) {
  $Findings.Add('The MCP server works but neither tested client path confirms the configured server.')
}
if ($DebugChecks.debug_config -and -not $DebugChecks.debug_config.contains_expected_server) {
  $Findings.Add('nga debug config does not contain gw-ap-debug-vnext; inspect the effective config root or schema.')
}
$Findings.Add('Share only the shareable JSON/TXT files after reviewing them; raw probe reports are local-only.')

$SanitizedPathsFile = Join-Path $OutputDirectory 'nga-debug-paths-sanitized.txt'
$ShareableFileNames = @(
  'codeagent-diagnostics-shareable.json',
  'codeagent-diagnostics-shareable.txt'
)
if (Test-Path -LiteralPath $SanitizedPathsFile) {
  $ShareableFileNames += 'nga-debug-paths-sanitized.txt'
}
$LocalOnlyFileNames = @()
if ($NgaProbeResult.report_path) { $LocalOnlyFileNames += 'nga-probe.json' }
if ($CodeAgentProbeResult.report_path) {
  $LocalOnlyFileNames += 'codeagent-exe-probe.json'
}

$Shareable = [ordered]@{
  schema_version = 2
  generated_at = (Get-Date).ToString('o')
  status = $DiagnosticStatus
  expected_server_name = 'gw-ap-debug-vnext'
  project_root = '%PROJECT_ROOT%'
  runtime_url = $RuntimeUrl
  commands = [ordered]@{
    nga = ConvertTo-ShareableCommand $NgaInfo 'requested'
    codeagent_exe = ConvertTo-ShareableCommand $CodeAgentInfo $CodeAgentDiscovery.discovery_method
  }
  probes = [ordered]@{
    nga = $NgaProbe
    codeagent_exe = $CodeAgentProbe
  }
  nga_debug = $DebugChecks
  findings = @($Findings)
  privacy = [ordered]@{
    shareable_files = $ShareableFileNames
    local_only_files = $LocalOnlyFileNames
    warning = 'Do not share full configs, environment dumps, credentials, internal endpoints, or raw logs.'
  }
}

$ShareableJsonPath = Join-Path $OutputDirectory 'codeagent-diagnostics-shareable.json'
[System.IO.File]::WriteAllText(
  $ShareableJsonPath,
  ($Shareable | ConvertTo-Json -Depth 20),
  $Utf8NoBom
)
$ShareableTextPath = Join-Path $OutputDirectory 'codeagent-diagnostics-shareable.txt'
$TextLines = @(
  'GW/AP CodeAgent Diagnostic Summary',
  "status=$DiagnosticStatus",
  "nga_found=$($NgaInfo.found)",
  "codeagent_exe_found=$($CodeAgentInfo.found)",
  "codeagent_discovery=$($CodeAgentDiscovery.discovery_method)",
  "direct_mcp_protocol_passed=$DirectProtocolPassed",
  "direct_mcp_data_plane_passed=$DirectDataPlanePassed",
  "direct_mcp_passed=$DirectMcpPassed",
  "known_config_contains_server=$KnownConfigContainsServer",
  "nga_server_connected=$NgaConnected",
  "codeagent_exe_server_connected=$CodeAgentConnected",
  "nga_debug_config_contains_server=$($DebugChecks.debug_config.contains_expected_server)",
  '',
  'Findings:'
) + @($Findings | ForEach-Object { "- $_" })
[System.IO.File]::WriteAllText(
  $ShareableTextPath,
  ($TextLines -join [Environment]::NewLine),
  $Utf8NoBom
)

[ordered]@{
  status = 'PASS'
  diagnostic_status = $DiagnosticStatus
  output_directory = $OutputDirectory
  share_this_json = $ShareableJsonPath
  share_this_text = $ShareableTextPath
  optional_sanitized_paths = $(if (Test-Path -LiteralPath $SanitizedPathsFile) {
    $SanitizedPathsFile
  } else {
    $null
  })
  do_not_share_raw_probe_reports = @(
    $NgaProbeResult.report_path,
    $CodeAgentProbeResult.report_path
  ) | Where-Object { $_ }
} | ConvertTo-Json -Depth 5
