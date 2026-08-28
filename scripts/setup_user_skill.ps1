#requires -Version 5.1
<#
.SYNOPSIS
Registers this checkout as a user-level Skill for Codex, Claude Code, and OpenCode.

.DESCRIPTION
The checkout remains the single canonical copy. The script creates directory
junctions in the selected CLI discovery locations, so updates and provenance
checks apply consistently to every CLI. Existing unrelated paths are never
overwritten.
#>
[CmdletBinding()]
param(
    [ValidateSet("All", "Codex", "Claude", "OpenCode")]
    [string[]]$Clients = @("All"),
    [string]$HomeRoot = $HOME,
    [string]$Python = "python",
    [switch]$Update,
    [switch]$RunBootstrap,
    [switch]$RunValidation,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$SkillRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

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

$selected = [Collections.Generic.List[string]]::new()
if ($Clients -contains "All") {
    @("Codex", "Claude", "OpenCode") | ForEach-Object { $selected.Add($_) }
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

if ($Update) {
    if (-not (Test-Path -LiteralPath (Join-Path $SkillRoot ".git"))) {
        throw "Update requires a Git checkout: $SkillRoot"
    }
    Invoke-Checked "git" @("-C", $SkillRoot, "pull", "--ff-only", "origin", "skillonly")
}

if ($RunBootstrap) {
    Invoke-Checked $Python @("-B", (Join-Path $SkillRoot "scripts\debug_platform_skill.py"), "bootstrap")
}

if ($RunValidation) {
    Invoke-Checked $Python @("-B", "-m", "unittest", "discover", "-s", "tests", "-q")
    Invoke-Checked $Python @("-B", (Join-Path $SkillRoot "scripts\check_provenance.py"))
    Invoke-Checked $Python @("-B", (Join-Path $SkillRoot "scripts\validate_release.py"))
    Invoke-Checked $Python @("-B", (Join-Path $SkillRoot "scripts\debug_platform_skill.py"), "doctor", "--check", "host-agent")
}

Write-Step "Ready. Canonical checkout: $SkillRoot"
