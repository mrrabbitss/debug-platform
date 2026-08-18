param(
    [string]$RepositoryRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "file_hash.ps1")

if (-not $RepositoryRoot) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
}

$relativePaths = @(
    "backend\pyproject.toml",
    "backend\uv.lock",
    "backend\constraints.lock",
    "frontend\package-lock.json"
)
$hashes = foreach ($relativePath in $relativePaths) {
    $path = Join-Path $RepositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Dependency file not found: $relativePath"
    }
    Get-Sha256Hex -Path $path
}

Write-Output ($hashes -join "-")
