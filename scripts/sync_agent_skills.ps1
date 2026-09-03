[CmdletBinding()]
param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot "agent-skills\gw-ap-debug"))
$targetRoots = @(
    [IO.Path]::GetFullPath((Join-Path $repoRoot ".agents\skills\gw-ap-debug")),
    [IO.Path]::GetFullPath((Join-Path $repoRoot ".claude\skills\gw-ap-debug"))
)

if (-not [IO.File]::Exists((Join-Path $sourceRoot "SKILL.md"))) {
    throw "Canonical Skill is missing: $sourceRoot"
}

function Get-SkillFiles {
    param([string]$Root)

    $files = @{}
    if (-not [IO.Directory]::Exists($Root)) {
        return $files
    }
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File) {
        $relative = $file.FullName.Substring($Root.Length).TrimStart("\", "/")
        $files[$relative] = $file.FullName
    }
    return $files
}

$sourceFiles = Get-SkillFiles -Root $sourceRoot

foreach ($targetRoot in $targetRoots) {
    $allowedParent = [IO.Path]::GetFullPath((Split-Path $targetRoot -Parent))
    if (-not $targetRoot.StartsWith($allowedParent + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to sync outside the expected Skill directory: $targetRoot"
    }

    $targetFiles = Get-SkillFiles -Root $targetRoot
    $problems = [Collections.Generic.List[string]]::new()

    foreach ($relative in $sourceFiles.Keys) {
        if (-not $targetFiles.ContainsKey($relative)) {
            $problems.Add("missing $relative")
            continue
        }
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceFiles[$relative]).Hash
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetFiles[$relative]).Hash
        if ($sourceHash -ne $targetHash) {
            $problems.Add("changed $relative")
        }
    }
    foreach ($relative in $targetFiles.Keys) {
        if (-not $sourceFiles.ContainsKey($relative)) {
            $problems.Add("extra $relative")
        }
    }

    if ($Check) {
        if ($problems.Count -gt 0) {
            throw "Skill mirror is out of date at $targetRoot`: $($problems -join ', ')"
        }
        Write-Output "Skill mirror is current: $targetRoot"
        continue
    }

    [IO.Directory]::CreateDirectory($targetRoot) | Out-Null
    foreach ($relative in $sourceFiles.Keys) {
        $destination = [IO.Path]::GetFullPath((Join-Path $targetRoot $relative))
        if (-not $destination.StartsWith($targetRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to copy outside the Skill mirror: $destination"
        }
        [IO.Directory]::CreateDirectory((Split-Path $destination -Parent)) | Out-Null
        Copy-Item -LiteralPath $sourceFiles[$relative] -Destination $destination -Force
    }
    foreach ($relative in $targetFiles.Keys) {
        if ($sourceFiles.ContainsKey($relative)) { continue }
        $stalePath = [IO.Path]::GetFullPath($targetFiles[$relative])
        if (-not $stalePath.StartsWith($targetRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a file outside the Skill mirror: $stalePath"
        }
        Remove-Item -LiteralPath $stalePath -Force
    }
    Write-Output "Synchronized Skill mirror: $targetRoot"
}

