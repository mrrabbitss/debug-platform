param(
    [string]$BaseCommit = "da29b2b8b44ca7abcaa2796f77fc90354777ab44",
    [string]$Branch = "codex/agent-runtime-vnext-opencode",
    [string]$DeliveryDate = "2026-08-10"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ChangedPath = Join-Path $RepoRoot "AGENT_RUNTIME_VNEXT_CHANGED_FILES.md"
$ManifestPath = Join-Path $RepoRoot "DELIVERY_MANIFEST.md"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

Push-Location $RepoRoot
try {
    & git cat-file -e "$BaseCommit^{commit}"
    if ($LASTEXITCODE -ne 0) {
        throw "Base commit does not exist: $BaseCommit"
    }

    $changedByPath = [ordered]@{}
    foreach ($line in @(& git -c core.quotepath=false diff --name-status --no-renames $BaseCommit --)) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $parts = $line -split "`t", 2
        if ($parts.Count -ne 2) {
            throw "Could not parse git diff row: $line"
        }
        $changedByPath[$parts[1].Replace("\", "/")] = $parts[0]
    }
    foreach ($path in @(& git -c core.quotepath=false ls-files --others --exclude-standard)) {
        if (-not [string]::IsNullOrWhiteSpace($path)) {
            $changedByPath[$path.Replace("\", "/")] = "A"
        }
    }

    $changedRows = @(
        $changedByPath.GetEnumerator() |
            Sort-Object -Property Name |
            ForEach-Object { "{0} {1}" -f $_.Value, $_.Name }
    )
    $changedBody = @(
        "# vNext Changed Files"
        ""
        "Base: ``main@$BaseCommit``"
        ""
        "Archived branch: ``$Branch``"
        ""
        "The following paths differ from or are newly added to the supplied latest main:"
        ""
        "``````text"
        $changedRows
        "``````"
        ""
    ) -join "`n"
    [System.IO.File]::WriteAllText($ChangedPath, $changedBody, $Utf8NoBom)

    $payloadPaths = @(
        & git -c core.quotepath=false ls-files --cached --others --exclude-standard |
            ForEach-Object { $_.Replace("\", "/") } |
            Where-Object { $_ -and $_ -ne "DELIVERY_MANIFEST.md" } |
            Sort-Object -Unique
    )
    $manifestRows = foreach ($path in $payloadPaths) {
        $absolute = Join-Path $RepoRoot $path
        if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) {
            continue
        }
        $item = Get-Item -LiteralPath $absolute
        $hash = (Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash.ToLowerInvariant()
        "| ``$path`` | $($item.Length) | ``$hash`` |"
    }
    $manifestBody = @(
        "# Delivery Manifest"
        ""
        "- Source baseline: ``mrrabbitss/debug-platform main@$BaseCommit``"
        "- Archived branch: ``$Branch``"
        "- Delivery date: ``$DeliveryDate``"
        "- Payload file count (excluding this manifest): ``$($manifestRows.Count)``"
        ""
        "## SHA-256"
        ""
        "| Path | Bytes | SHA-256 |"
        "| --- | ---: | --- |"
        $manifestRows
        ""
    ) -join "`n"
    [System.IO.File]::WriteAllText($ManifestPath, $manifestBody, $Utf8NoBom)

    Write-Host "[OK] Updated AGENT_RUNTIME_VNEXT_CHANGED_FILES.md ($($changedRows.Count) paths)."
    Write-Host "[OK] Updated DELIVERY_MANIFEST.md ($($manifestRows.Count) payload files)."
} finally {
    Pop-Location
}
