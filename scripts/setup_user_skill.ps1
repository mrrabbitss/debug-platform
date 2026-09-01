#requires -Version 5.1
<#
.SYNOPSIS
Registers this checkout as a user-level Skill for Claude Code, Codex, and OpenCode.

.DESCRIPTION
The checkout remains the single canonical copy. If it already occupies a CLI
discovery path, that client uses the real directory directly; the script
creates directory junctions for the other selected clients. Updates and
provenance checks therefore apply consistently to every CLI. Existing unrelated
paths are never overwritten.
#>
[CmdletBinding()]
param(
    [ValidateSet("All", "Codex", "Claude", "OpenCode")]
    [string[]]$Clients = @("All"),
    [string]$HomeRoot = $HOME,
    [string]$Python = "",
    [switch]$Update,
    [switch]$RunBootstrap,
    [switch]$RunValidation,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$SkillRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
. (Join-Path $PSScriptRoot "python_runtime.ps1")

function Write-Step([string]$Message) {
    Write-Host "[gw-ap-debug] $Message"
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory = $SkillRoot
    )
    $display = (($FilePath) + " " + ($Arguments -join " ")).Trim()
    if ($PlanOnly) {
        Write-Step "PLAN: $display"
        return
    }
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $display"
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $SkillRoot "SKILL.md") -PathType Leaf)) {
    throw "SKILL.md is missing from canonical checkout: $SkillRoot"
}

$pythonRuntime = Resolve-GwApPython -Requested $Python
Write-Step "Python $($pythonRuntime.Version): $($pythonRuntime.Display)"

$selected = [Collections.Generic.List[string]]::new()
if ($Clients -contains "All") {
    @("Claude", "Codex", "OpenCode") | ForEach-Object { $selected.Add($_) }
}
else {
    $Clients | ForEach-Object {
        if (-not $selected.Contains($_)) { $selected.Add($_) }
    }
}

$relativeTargets = @{
    Codex = ".agents\skills\gw-ap-debug"
    Claude = ".claude\skills\gw-ap-debug"
    OpenCode = ".config\opencode\skills\gw-ap-debug"
}

foreach ($client in $selected) {
    $target = [IO.Path]::GetFullPath((Join-Path $HomeRoot $relativeTargets[$client]))
    if (Test-Path -LiteralPath $target) {
        if ([StringComparer]::OrdinalIgnoreCase.Equals($target.TrimEnd('\'), $SkillRoot.TrimEnd('\'))) {
            Write-Step "$client uses the canonical checkout directly: $target"
            continue
        }
        $item = Get-Item -LiteralPath $target -Force
        $linkTarget = $item.Target
        if ($linkTarget -is [array]) { $linkTarget = $linkTarget[0] }
        if (-not $linkTarget) {
            throw "$client target already exists and is not a link: $target"
        }
        $resolvedTarget = if ([IO.Path]::IsPathRooted([string]$linkTarget)) {
            [IO.Path]::GetFullPath([string]$linkTarget)
        }
        else {
            [IO.Path]::GetFullPath((Join-Path $item.Parent.FullName ([string]$linkTarget)))
        }
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals($resolvedTarget.TrimEnd('\'), $SkillRoot.TrimEnd('\'))) {
            throw "$client target points elsewhere: $target -> $resolvedTarget"
        }
        Write-Step "$client already registered: $target"
        continue
    }

    if ($PlanOnly) {
        Write-Step "PLAN: create $client junction $target -> $SkillRoot"
        continue
    }
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    New-Item -ItemType Junction -Path $target -Target $SkillRoot | Out-Null
    Write-Step "Registered ${client}: $target"
}

$clientCommands = @{ Claude = "claude"; Codex = "codex"; OpenCode = "opencode" }
foreach ($client in $selected) {
    $command = $clientCommands[$client]
    $resolved = Get-Command $command -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($resolved) {
        $version = (& $command --version 2>$null | Select-Object -First 1)
        Write-Step "$client CLI detected: $version"
    }
    else {
        Write-Warning "$client Skill is registered, but its CLI command '$command' is not currently available."
    }
}

if ($Update) {
    if (-not (Test-Path -LiteralPath (Join-Path $SkillRoot ".git"))) {
        throw "Update requires a Git checkout: $SkillRoot"
    }
    Invoke-Checked "git" @("-C", $SkillRoot, "pull", "--ff-only", "origin", "skillonly")
}

if ($RunBootstrap) {
    Invoke-Checked $pythonRuntime.Command (@($pythonRuntime.PrefixArguments) + @("-B", (Join-Path $SkillRoot "scripts\debug_platform_skill.py"), "bootstrap"))
}

if ($RunValidation) {
    Invoke-Checked $pythonRuntime.Command (@($pythonRuntime.PrefixArguments) + @("-B", "-m", "unittest", "discover", "-s", "tests", "-q"))
    Invoke-Checked $pythonRuntime.Command (@($pythonRuntime.PrefixArguments) + @("-B", (Join-Path $SkillRoot "scripts\check_provenance.py")))
    Invoke-Checked $pythonRuntime.Command (@($pythonRuntime.PrefixArguments) + @("-B", (Join-Path $SkillRoot "scripts\validate_release.py")))
    Invoke-Checked $pythonRuntime.Command (@($pythonRuntime.PrefixArguments) + @("-B", (Join-Path $SkillRoot "scripts\debug_platform_skill.py"), "doctor", "--check", "host-agent"))
}

Write-Step "Ready. Canonical checkout: $SkillRoot"
if ($selected.Contains("Claude")) {
    Write-Step "Claude Code: start 'claude', run '/skills' to confirm discovery, then invoke '/gw-ap-debug <log paths> <symptom>'."
}
if ($selected.Contains("Codex")) {
    Write-Step "Codex CLI: start 'codex' and ask it to use `$gw-ap-debug for the supplied GW/AP logs."
}
if ($selected.Contains("OpenCode")) {
    Write-Step "OpenCode CLI: start 'opencode' and ask it to use gw-ap-debug for the supplied GW/AP logs."
}
