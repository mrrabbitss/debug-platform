[CmdletBinding()]
param(
    [string]$OutputRoot = "",
    [string]$PythonExe = "python",
    [switch]$SkipFrontendBuild,
    [switch]$SkipSmokeTest,
    [switch]$SkipArchive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "file_hash.ps1")

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "The portable Windows package must be built on Windows."
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$allowedOutputRoot = [System.IO.Path]::GetFullPath((
    Join-Path $projectRoot "artifacts\portable"
))
$resolvedOutputRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $allowedOutputRoot
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputRoot))
}
$allowedPrefix = $allowedOutputRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
if (
    $resolvedOutputRoot -ne $allowedOutputRoot -and
    -not $resolvedOutputRoot.StartsWith(
        $allowedPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "OutputRoot must be artifacts\portable or one of its child directories."
}

$packageRoot = Join-Path $resolvedOutputRoot "debug-platform-windows-x64"
$archivePath = Join-Path $resolvedOutputRoot "debug-platform-windows-x64.zip"
$runtimeRoot = Join-Path $packageRoot "runtime\python"
$sitePackages = Join-Path $runtimeRoot "Lib\site-packages"
$backendTarget = Join-Path $packageRoot "app\backend"
$frontendTarget = Join-Path $packageRoot "web"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

function Copy-FilteredTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination,
        [string[]]$ExcludedDirectories = @(),
        [string[]]$ExcludedExtensions = @()
    )

    $sourcePath = [System.IO.Path]::GetFullPath($Source).TrimEnd("\", "/")
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw "Source directory is missing: $sourcePath"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $sourcePrefix = $sourcePath + [System.IO.Path]::DirectorySeparatorChar
    foreach ($file in Get-ChildItem -LiteralPath $sourcePath -Recurse -File) {
        $relative = $file.FullName.Substring($sourcePrefix.Length)
        $segments = $relative -split '[\\/]'
        if ($segments | Where-Object { $_ -in $ExcludedDirectories }) {
            continue
        }
        if ($file.Extension -in $ExcludedExtensions) {
            continue
        }
        $target = Join-Path $Destination $relative
        $targetDirectory = Split-Path -Parent $target
        New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
}

function Remove-SafePackageChild {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $packagePrefix = [System.IO.Path]::GetFullPath($packageRoot).TrimEnd("\", "/") + "\"
    if (-not $resolved.StartsWith(
        $packagePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove a path outside the package staging directory: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $resolvedOutputRoot | Out-Null
if (Test-Path -LiteralPath $packageRoot) {
    $packageParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $packageRoot))
    if ($packageParent -ne $resolvedOutputRoot) {
        throw "Refusing to replace an unexpected package directory: $packageRoot"
    }
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

Write-Host "[INFO] Inspecting the portable build Python runtime..."
$pythonInfoRaw = & $PythonExe -c (
    "import json, platform, sys; " +
    "print(json.dumps({'executable':sys.executable,'base_prefix':sys.base_prefix," +
    "'version':[sys.version_info.major,sys.version_info.minor,sys.version_info.micro]," +
    "'bits':platform.architecture()[0],'platform':sys.platform}))"
)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect Python through $PythonExe."
}
$pythonInfo = $pythonInfoRaw | ConvertFrom-Json
if (
    $pythonInfo.platform -ne "win32" -or
    $pythonInfo.bits -ne "64bit" -or
    [int]$pythonInfo.version[0] -ne 3 -or
    [int]$pythonInfo.version[1] -lt 11 -or
    [int]$pythonInfo.version[1] -ge 15
) {
    throw "Portable builds require 64-bit CPython 3.11 through 3.14 on Windows."
}

$pythonBase = [System.IO.Path]::GetFullPath([string]$pythonInfo.base_prefix)
$pythonExecutable = [System.IO.Path]::GetFullPath([string]$pythonInfo.executable)
$pythonDirectory = Split-Path -Parent $pythonExecutable
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
Copy-Item -LiteralPath $pythonExecutable -Destination (Join-Path $runtimeRoot "python.exe")

foreach ($name in @("pythonw.exe", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll")) {
    $candidate = Join-Path $pythonDirectory $name
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $candidate = Join-Path $pythonBase $name
    }
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        Copy-Item -LiteralPath $candidate -Destination (Join-Path $runtimeRoot $name)
    }
}
$versionDll = "python{0}{1}.dll" -f $pythonInfo.version[0], $pythonInfo.version[1]
$versionDllPath = Join-Path $pythonBase $versionDll
if (-not (Test-Path -LiteralPath $versionDllPath -PathType Leaf)) {
    $versionDllPath = Join-Path $pythonDirectory $versionDll
}
if (-not (Test-Path -LiteralPath $versionDllPath -PathType Leaf)) {
    throw "Python runtime DLL is missing: $versionDll"
}
Copy-Item -LiteralPath $versionDllPath -Destination (Join-Path $runtimeRoot $versionDll)

$isolatedPathFile = Join-Path $runtimeRoot (
    $versionDll.Replace(".dll", "._pth")
)
@(
    ".",
    "Lib",
    "DLLs",
    "Lib\site-packages",
    "import site"
) | Set-Content -LiteralPath $isolatedPathFile -Encoding ASCII

Copy-FilteredTree `
    -Source (Join-Path $pythonBase "DLLs") `
    -Destination (Join-Path $runtimeRoot "DLLs") `
    -ExcludedDirectories @("__pycache__") `
    -ExcludedExtensions @(".pyc")
Copy-FilteredTree `
    -Source (Join-Path $pythonBase "Lib") `
    -Destination (Join-Path $runtimeRoot "Lib") `
    -ExcludedDirectories @(
        "site-packages",
        "__pycache__",
        "ensurepip",
        "idlelib",
        "test",
        "tkinter",
        "turtledemo",
        "venv"
    ) `
    -ExcludedExtensions @(".pyc")
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

Write-Host "[INFO] Installing only the platform runtime dependencies..."
Invoke-NativeChecked -FilePath $PythonExe -Arguments @(
    "-m", "pip", "install",
    "--disable-pip-version-check",
    "--no-input",
    "--no-compile",
    "--no-warn-conflicts",
    "--no-warn-script-location",
    "--ignore-installed",
    "--target", $sitePackages,
    "--constraint", (Join-Path $projectRoot "backend\constraints.lock"),
    (Join-Path $projectRoot "backend")
)

Remove-SafePackageChild -Path (Join-Path $sitePackages "app")
foreach ($metadata in Get-ChildItem -LiteralPath $sitePackages -Directory -Filter (
    "gw_ap_debug_backend-*.dist-info"
)) {
    Remove-SafePackageChild -Path $metadata.FullName
}

Write-Host "[INFO] Copying backend application and migration files..."
Copy-FilteredTree `
    -Source (Join-Path $projectRoot "backend\app") `
    -Destination (Join-Path $backendTarget "app") `
    -ExcludedDirectories @("__pycache__", "data") `
    -ExcludedExtensions @(".pyc")
Copy-Item `
    -LiteralPath (Join-Path $projectRoot "backend\alembic.ini") `
    -Destination (Join-Path $backendTarget "alembic.ini")

if (-not $SkipFrontendBuild) {
    Write-Host "[INFO] Building the Vue frontend..."
    Push-Location (Join-Path $projectRoot "frontend")
    try {
        Invoke-NativeChecked -FilePath "npm.cmd" -Arguments @("ci")
        Invoke-NativeChecked -FilePath "npm.cmd" -Arguments @("run", "build")
    } finally {
        Pop-Location
    }
}
$frontendDist = Join-Path $projectRoot "frontend\dist"
if (-not (Test-Path -LiteralPath (Join-Path $frontendDist "index.html") -PathType Leaf)) {
    throw "Built frontend is missing. Run without -SkipFrontendBuild."
}
Write-Host "[INFO] Copying the built frontend..."
Copy-FilteredTree -Source $frontendDist -Destination $frontendTarget

foreach ($name in @("portable_launcher.py", "start.bat", "README.txt")) {
    Copy-Item `
        -LiteralPath (Join-Path $projectRoot "deploy\windows-portable\$name") `
        -Destination (Join-Path $packageRoot $name)
}
Copy-Item `
    -LiteralPath (Join-Path $projectRoot ".env.example") `
    -Destination (Join-Path $packageRoot ".env.example")

$commit = (& git -C $projectRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0) {
    $commit = "unknown"
}
$gitStatus = @(& git -C $projectRoot status --porcelain 2>$null)
$sourceDirty = $LASTEXITCODE -eq 0 -and $gitStatus.Count -gt 0
$buildInfo = [ordered]@{
    schema_version = 1
    commit = ([string]$commit).Trim()
    source_dirty = $sourceDirty
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    python = ($pythonInfo.version -join ".")
    architecture = $pythonInfo.bits
    frontend_bundled = $true
    local_model_runtime_bundled = $false
    default_embedding = "hashing"
    default_reranker = "disabled"
}
$buildInfo | ConvertTo-Json | Set-Content `
    -LiteralPath (Join-Path $packageRoot "build-info.json") `
    -Encoding UTF8

foreach ($forbiddenPath in @(".env", "data", "models", "A.py")) {
    $candidate = Join-Path $packageRoot $forbiddenPath
    if (Test-Path -LiteralPath $candidate) {
        throw "Runtime data or a local-only file leaked into the package: $forbiddenPath"
    }
}
$leakedArchives = @(
    Get-ChildItem -LiteralPath $packageRoot -Recurse -File |
        Where-Object { $_.Extension -in @(".rar", ".7z") }
)
if ($leakedArchives.Count -gt 0) {
    throw "An archive leaked into the portable package: $($leakedArchives[0].FullName)"
}

Write-Host "[INFO] Writing the package integrity manifest..."
$packagePrefix = [System.IO.Path]::GetFullPath($packageRoot).TrimEnd("\", "/") + "\"
$manifestFiles = @(
    foreach ($file in Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Sort-Object FullName) {
        if ($file.Name -eq "package-manifest.json") {
            continue
        }
        [ordered]@{
            path = $file.FullName.Substring($packagePrefix.Length).Replace("\", "/")
            size = $file.Length
            sha256 = (Get-Sha256Hex -Path $file.FullName).ToLowerInvariant()
        }
    }
)
[ordered]@{
    schema_version = 1
    files = $manifestFiles
} | ConvertTo-Json -Depth 4 | Set-Content `
    -LiteralPath (Join-Path $packageRoot "package-manifest.json") `
    -Encoding UTF8

$bundledPython = Join-Path $runtimeRoot "python.exe"
Write-Host "[INFO] Verifying that native local-model packages are absent..."
Invoke-NativeChecked -FilePath $bundledPython -Arguments @(
    "-B",
    "-s",
    "-c",
    (
        "import importlib.util; " +
        "assert importlib.util.find_spec('fastapi'); " +
        "assert importlib.util.find_spec('uvicorn'); " +
        "assert importlib.util.find_spec('torch') is None; " +
        "assert importlib.util.find_spec('sentence_transformers') is None"
    )
)

if (-not $SkipSmokeTest) {
    & powershell.exe `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot "verify_windows_portable.ps1") `
        -PackageRoot $packageRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Portable package smoke verification failed with exit code $LASTEXITCODE."
    }
}

$manifestData = Get-Content `
    -LiteralPath (Join-Path $packageRoot "package-manifest.json") `
    -Raw | ConvertFrom-Json
$packagedFilesAfterSmoke = @(
    Get-ChildItem -LiteralPath $packageRoot -Recurse -File |
        Where-Object { $_.Name -ne "package-manifest.json" }
)
if ($packagedFilesAfterSmoke.Count -ne $manifestData.files.Count) {
    throw (
        "Portable smoke mutated the package: manifest has {0} files, package has {1}." `
            -f $manifestData.files.Count, $packagedFilesAfterSmoke.Count
    )
}

if (-not $SkipArchive) {
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Write-Host "[INFO] Creating portable ZIP..."
    Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal
    $archiveHash = (Get-Sha256Hex -Path $archivePath).ToLowerInvariant()
    "$archiveHash  $([System.IO.Path]::GetFileName($archivePath))" | Set-Content `
        -LiteralPath "$archivePath.sha256" `
        -Encoding ASCII
    Write-Host "[OK] Portable archive: $archivePath"
    Write-Host "[OK] SHA-256: $archiveHash"
} else {
    Write-Host "[OK] Portable package directory: $packageRoot"
}
