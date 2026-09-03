[CmdletBinding()]
param(
    [ValidateSet("All", "Claude", "Codex")]
    [string]$Client = "All",

    [string]$McpUrl = $env:DEBUGPLATFORM_MCP_URL,

    [switch]$Replace,
    [switch]$SkipSkillInstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$serverName = "gw-ap-debug"
$urlVariable = "DEBUGPLATFORM_MCP_URL"
$tokenVariable = "DEBUGPLATFORM_MCP_TOKEN"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$skillSource = [IO.Path]::GetFullPath((Join-Path $repoRoot "agent-skills\gw-ap-debug"))

if ([string]::IsNullOrWhiteSpace($McpUrl)) {
    throw "Set $urlVariable or pass -McpUrl."
}
$parsedUrl = $null
if (-not [Uri]::TryCreate($McpUrl, [UriKind]::Absolute, [ref]$parsedUrl) -or
        $parsedUrl.Scheme -notin @("http", "https")) {
    throw "MCP URL must be an absolute HTTP(S) URL."
}
if ($parsedUrl.Scheme -ne "https" -and $parsedUrl.Host -notin @("127.0.0.1", "localhost", "::1")) {
    throw "Use HTTPS for a non-local MCP endpoint."
}
if (-not $DryRun -and [string]::IsNullOrWhiteSpace($env:DEBUGPLATFORM_MCP_TOKEN)) {
    throw "Set $tokenVariable in this process before installing the MCP configuration."
}
if (-not [IO.File]::Exists((Join-Path $skillSource "SKILL.md"))) {
    throw "Canonical Skill is missing: $skillSource"
}

$env:DEBUGPLATFORM_MCP_URL = $parsedUrl.AbsoluteUri

function Invoke-ClientCommand {
    param(
        [string]$Executable,
        [string[]]$Arguments
    )

    $rendered = (@($Executable) + $Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join " "
    if ($DryRun) {
        Write-Output "DRY RUN: $rendered"
        return
    }
    if ($null -eq (Get-Command $Executable -ErrorAction SilentlyContinue)) {
        throw "$Executable was not found on PATH. Install it, then rerun this script."
    }
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable exited with code $LASTEXITCODE."
    }
}

function Test-ClientEntry {
    param([string]$Executable)

    if ($DryRun -or $null -eq (Get-Command $Executable -ErrorAction SilentlyContinue)) {
        return $false
    }
    & $Executable mcp get $serverName *> $null
    return $LASTEXITCODE -eq 0
}

function Install-SkillCopy {
    param([string]$RelativeTarget)

    $profileRoot = $env:USERPROFILE
    if ([string]::IsNullOrWhiteSpace($profileRoot)) {
        throw "USERPROFILE is not available."
    }
    $target = [IO.Path]::GetFullPath((Join-Path $profileRoot $RelativeTarget))
    if ($DryRun) {
        Write-Output "DRY RUN: copy $skillSource to $target"
        return
    }
    $skillParent = [IO.Path]::GetFullPath((Split-Path $target -Parent))
    if (-not $target.StartsWith($skillParent + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to install outside the expected Skill directory: $target"
    }
    if ([IO.Directory]::Exists($target)) {
        if (-not $Replace) {
            throw "Skill target already exists: $target. Rerun with -Replace to update it."
        }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    [IO.Directory]::CreateDirectory($target) | Out-Null
    Copy-Item -Path (Join-Path $skillSource "*") -Destination $target -Recurse -Force
    Write-Output "Installed Skill: $target"
}

if (-not $SkipSkillInstall) {
    if ($Client -in @("All", "Claude")) {
        Install-SkillCopy -RelativeTarget ".claude\skills\gw-ap-debug"
    }
    if ($Client -in @("All", "Codex")) {
        Install-SkillCopy -RelativeTarget ".agents\skills\gw-ap-debug"
    }
}

if ($Client -in @("All", "Claude")) {
    $claudeConfig = [ordered]@{
        type = "http"
        url = '${DEBUGPLATFORM_MCP_URL}'
        headers = [ordered]@{
            Authorization = 'Bearer ${DEBUGPLATFORM_MCP_TOKEN}'
        }
    } | ConvertTo-Json -Compress
    if (Test-ClientEntry -Executable "claude") {
        if (-not $Replace) {
            throw "Claude MCP entry '$serverName' already exists. Rerun with -Replace to update it."
        }
        Invoke-ClientCommand -Executable "claude" -Arguments @(
            "mcp", "remove", $serverName, "--scope", "user"
        )
    }
    Invoke-ClientCommand -Executable "claude" -Arguments @(
        "mcp", "add-json", "--scope", "user", $serverName, $claudeConfig
    )
}

if ($Client -in @("All", "Codex")) {
    if (Test-ClientEntry -Executable "codex") {
        if (-not $Replace) {
            throw "Codex MCP entry '$serverName' already exists. Rerun with -Replace to update it."
        }
        Invoke-ClientCommand -Executable "codex" -Arguments @("mcp", "remove", $serverName)
    }
    Invoke-ClientCommand -Executable "codex" -Arguments @(
        "mcp", "add", $serverName,
        "--url", $parsedUrl.AbsoluteUri,
        "--bearer-token-env-var", $tokenVariable
    )
}

Write-Output "Configuration complete. Start a new CLI process, then inspect /mcp or run the client's MCP list command."
