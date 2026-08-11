[CmdletBinding()]
param(
    [string]$EnvPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($EnvPath)) {
    $EnvPath = Join-Path $repositoryRoot ".env"
}
$resolvedEnvPath = [System.IO.Path]::GetFullPath($EnvPath)
$templatePath = Join-Path $repositoryRoot ".env.example"

if (-not (Test-Path -LiteralPath $resolvedEnvPath -PathType Leaf)) {
    if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
        throw "Neither the target .env file nor .env.example exists."
    }
    Copy-Item -LiteralPath $templatePath -Destination $resolvedEnvPath
    Write-Host "[INFO] Created the local .env file from .env.example."
}

$content = [System.IO.File]::ReadAllText($resolvedEnvPath)
$lineEnding = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }
$lines = [System.Text.RegularExpressions.Regex]::Split($content, "\r\n|\n|\r")
$updatedLines = [System.Collections.Generic.List[string]]::new()
$settingFound = $false

foreach ($line in $lines) {
    if ($line -match "^[\t ]*MODEL_ALLOW_PRIVATE_ENDPOINTS[\t ]*=") {
        if (-not $settingFound) {
            $updatedLines.Add("MODEL_ALLOW_PRIVATE_ENDPOINTS=true")
            $settingFound = $true
        }
        continue
    }
    $updatedLines.Add($line)
}

if (-not $settingFound) {
    while ($updatedLines.Count -gt 0 -and $updatedLines[$updatedLines.Count - 1] -eq "") {
        $updatedLines.RemoveAt($updatedLines.Count - 1)
    }
    if ($updatedLines.Count -gt 0) {
        $updatedLines.Add("")
    }
    $updatedLines.Add("MODEL_ALLOW_PRIVATE_ENDPOINTS=true")
    $updatedLines.Add("")
}

$updatedContent = $updatedLines -join $lineEnding
if (-not $updatedContent.EndsWith($lineEnding)) {
    $updatedContent += $lineEnding
}

if ($updatedContent -ceq $content) {
    Write-Host "[OK] MODEL_ALLOW_PRIVATE_ENDPOINTS is already enabled."
    exit 0
}

$temporaryPath = Join-Path (
    Split-Path -Parent $resolvedEnvPath
) (".{0}.{1}.tmp" -f (Split-Path -Leaf $resolvedEnvPath), $PID)

try {
    $utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($temporaryPath, $updatedContent, $utf8WithoutBom)
    Move-Item -LiteralPath $temporaryPath -Destination $resolvedEnvPath -Force
}
finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

Write-Host "[OK] Enabled MODEL_ALLOW_PRIVATE_ENDPOINTS in the local .env file."
Write-Host "[WARNING] This permits approved model and proxy profiles to reach private-network hosts."
Write-Host "[INFO] Loopback, link-local, metadata, and production allowlist safeguards remain active."
