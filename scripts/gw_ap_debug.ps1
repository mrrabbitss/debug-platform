#requires -Version 5.1
<#
.SYNOPSIS
Runs the GW/AP Debug Skill CLI with an automatically selected compatible Python.

.EXAMPLE
.\scripts\gw_ap_debug.ps1 doctor --check host-agent

.EXAMPLE
.\scripts\gw_ap_debug.ps1 run --mode deterministic --title "smoke" --log "D:\logs\collectDebuginfo"
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Python = "",
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$SkillArguments = @()
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "python_runtime.ps1")

$runtime = Resolve-GwApPython -Requested $Python
$entrypoint = Join-Path $PSScriptRoot "debug_platform_skill.py"
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw "Skill entrypoint is missing: $entrypoint"
}
if ($SkillArguments.Count -eq 0) {
    $SkillArguments = @("--help")
}

& $runtime.Command @($runtime.PrefixArguments) -B $entrypoint @SkillArguments
exit $LASTEXITCODE
