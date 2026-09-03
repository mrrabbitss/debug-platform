[CmdletBinding()]
param(
    [string]$CacheRoot = "",
    [string]$OutputRoot = "",
    [string]$PortableRoot = "",
    [string]$PythonExe = "python",
    [string]$Version = "0.1.0",
    [string]$IsccPath = "",
    [switch]$SkipPortableBuild,
    [switch]$SkipFrontendBuild,
    [switch]$SkipSmokeTest,
    [switch]$SkipModelSmoke,
    [switch]$SkipSetupExe,
    [switch]$RequireSetupExe,
    [switch]$ValidateCacheOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "file_hash.ps1")

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "The offline GGUF installer must be assembled on Windows."
}
if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
    throw "Version must contain three or four numeric components."
}
if ($SkipSetupExe -and $RequireSetupExe) {
    throw "SkipSetupExe and RequireSetupExe cannot be used together."
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$defaultCacheRoot = Join-Path $projectRoot "artifacts\build-cache\windows-x64"
$cachePath = if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
    $defaultCacheRoot
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $CacheRoot))
}
$allowedOutputRoot = [System.IO.Path]::GetFullPath((
    Join-Path $projectRoot "artifacts\installer"
))
$resolvedOutputRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $allowedOutputRoot
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputRoot))
}
$allowedOutputPrefix = $allowedOutputRoot.TrimEnd("\", "/") + "\"
if (
    $resolvedOutputRoot -ne $allowedOutputRoot -and
    -not $resolvedOutputRoot.StartsWith(
        $allowedOutputPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "OutputRoot must be artifacts\installer or one of its children."
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

function Get-RequiredProperty {
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Context
    )
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) {
        throw "$Context is missing required property '$Name'."
    }
    return $property.Value
}

function Get-RequiredString {
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Context
    )
    $value = [string](Get-RequiredProperty -Object $Object -Name $Name -Context $Context)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Context property '$Name' must not be empty."
    }
    return $value.Trim()
}

function Resolve-SafeChild {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Relative,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $normalized = $Relative.Replace("/", "\")
    $segments = @($normalized -split '[\\/]')
    if (
        [string]::IsNullOrWhiteSpace($normalized) -or
        [System.IO.Path]::IsPathRooted($normalized) -or
        $normalized.Contains(":") -or
        $segments -contains ".." -or
        $segments -contains "."
    ) {
        throw "$Label must be a safe relative path: $Relative"
    }
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd("\", "/")
    $resolved = [System.IO.Path]::GetFullPath((Join-Path $rootPath $normalized))
    $prefix = $rootPath + "\"
    if (-not $resolved.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label escapes its root: $Relative"
    }
    return $resolved
}

function Remove-SafeInstallerChild {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $resolvedOutputRoot.TrimEnd("\", "/") + "\"
    if (-not $resolved.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove a path outside artifacts\installer: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

$lockPath = Join-Path $cachePath "components.lock.json"
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
    throw (
        "Component lock is missing: $lockPath`n" +
        "Run Python 3.12 scripts\model-runtime\prepare_assets.py --all to download, " +
        "convert, verify and generate this lock, then retry."
    )
}
$assetValidator = Join-Path $projectRoot "scripts\model-runtime\validate_assets.py"
Write-Host "[INFO] Binding the component lock to the repository's pinned supply-chain manifest..."
Invoke-NativeChecked -FilePath $PythonExe -Arguments @(
    $assetValidator,
    "--asset-root", $cachePath,
    "--component-lock", $lockPath
)
try {
    $componentLock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
} catch {
    throw "Component lock is not valid JSON: $lockPath"
}
if ([int](Get-RequiredProperty $componentLock "schema_version" "component lock") -ne 1) {
    throw "Unsupported component lock schema."
}
if ((Get-RequiredString $componentLock "target" "component lock") -ne "windows-x64") {
    throw "Component lock target must be windows-x64."
}
$bundleId = Get-RequiredString $componentLock "bundle_id" "component lock"
$runtime = Get-RequiredProperty $componentLock "runtime" "component lock"
$nativeDependencies = @(
    Get-RequiredProperty $componentLock "native_dependencies" "component lock"
)
$models = @(Get-RequiredProperty $componentLock "models" "component lock")
$files = @(Get-RequiredProperty $componentLock "files" "component lock")
if ($nativeDependencies.Count -ne 1) {
    throw "The full GGUF bundle must declare exactly one native runtime dependency."
}
if ($models.Count -ne 2) {
    throw "The full GGUF bundle must declare exactly one Embedding and one Reranker model."
}
if ($files.Count -lt 5) {
    throw "The component lock does not contain a complete runtime/model/license file set."
}

foreach ($name in @("name", "version", "revision", "source_url", "license")) {
    Get-RequiredString $runtime $name "runtime" | Out-Null
}
if ((Get-RequiredString $runtime "backend" "runtime").ToLowerInvariant() -ne "cpu") {
    throw "The first offline installer accepts only the CPU llama.cpp runtime."
}
$runtimeExecutable = Get-RequiredString $runtime "executable_install_path" "runtime"
$runtimeLicense = Get-RequiredString $runtime "license_install_path" "runtime"
$runtimeCacheDirectory = Get-RequiredString $runtime "cache_directory" "runtime"
$nativeDependency = $nativeDependencies[0]
foreach ($name in @(
    "id",
    "version",
    "source_url",
    "source_sha256",
    "license",
    "license_url",
    "redistribution",
    "signer_subject_contains"
)) {
    Get-RequiredString $nativeDependency $name "native runtime dependency" | Out-Null
}
if ((Get-RequiredString $nativeDependency "id" "native runtime dependency") -ne
    "microsoft-vc143-crt-x64-app-local") {
    throw "The native runtime dependency must be the pinned Microsoft VC143 x64 CRT."
}
if ((Get-RequiredString $nativeDependency "redistribution" "native runtime dependency") -ne
    "app_local_unmodified") {
    throw "The Microsoft VC runtime must be distributed app-local and unmodified."
}
$nativeInstallPaths = @(
    Get-RequiredProperty $nativeDependency "file_install_paths" "native runtime dependency"
)
if ($nativeInstallPaths.Count -ne 10) {
    throw "The Microsoft VC143 app-local release CRT must contain exactly ten DLLs."
}

$installedPaths = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$cachePaths = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$validatedFiles = @()
foreach ($entry in $files) {
    $cacheRelative = (
        Get-RequiredString $entry "cache_path" "component file"
    ).Replace("\", "/")
    $installRelative = (
        Get-RequiredString $entry "install_path" "component file"
    ).Replace("\", "/")
    $sha256 = (Get-RequiredString $entry "sha256" "component file").ToLowerInvariant()
    try {
        $expectedSize = [long](Get-RequiredProperty $entry "size" "component file")
    } catch {
        throw "Component file size must be an integer: $installRelative"
    }
    if ($expectedSize -le 0) {
        throw "Component file size must be positive: $installRelative"
    }
    if ($sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Component file SHA-256 must be 64 lowercase hex characters: $installRelative"
    }
    if (
        -not $installRelative.StartsWith("runtime/llama/", [StringComparison]::OrdinalIgnoreCase) -and
        -not $installRelative.StartsWith("models/embedding/", [StringComparison]::OrdinalIgnoreCase) -and
        -not $installRelative.StartsWith("models/reranker/", [StringComparison]::OrdinalIgnoreCase) -and
        -not $installRelative.StartsWith("licenses/", [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Component file has an unsupported install location: $installRelative"
    }
    if (-not $installedPaths.Add($installRelative)) {
        throw "Duplicate component install path: $installRelative"
    }
    if (-not $cachePaths.Add($cacheRelative)) {
        throw "Duplicate component cache path: $cacheRelative"
    }
    $source = Resolve-SafeChild $cachePath $cacheRelative "cache_path"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Pinned component file is missing from build cache: $cacheRelative"
    }
    $sourceInfo = Get-Item -LiteralPath $source
    if ($sourceInfo.Length -ne $expectedSize) {
        throw (
            "Pinned component size mismatch for {0}: expected {1}, got {2}." -f
            $cacheRelative, $expectedSize, $sourceInfo.Length
        )
    }
    $actualHash = (Get-Sha256Hex -Path $source).ToLowerInvariant()
    if ($actualHash -ne $sha256) {
        throw "Pinned component SHA-256 mismatch for $cacheRelative."
    }
    $validatedFiles += [pscustomobject]@{
        Source = $source
        InstallPath = $installRelative
        Size = $expectedSize
        Sha256 = $sha256
    }
}

$nativeSigner = Get-RequiredString `
    $nativeDependency `
    "signer_subject_contains" `
    "native runtime dependency"
$nativeSignaturesVerified = 0
foreach ($nativePathValue in $nativeInstallPaths) {
    $nativePath = ([string]$nativePathValue).Replace("\", "/")
    if (
        -not $nativePath.StartsWith(
            "runtime/llama/",
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not $nativePath.EndsWith(".dll", [StringComparison]::OrdinalIgnoreCase) -or
        $nativePath -match 'debug_nonredist|d\.dll$'
    ) {
        throw "Unsafe Microsoft VC runtime install path: $nativePath"
    }
    $matched = @(
        $validatedFiles |
            Where-Object {
                $_.InstallPath.Equals(
                    $nativePath,
                    [StringComparison]::OrdinalIgnoreCase
                )
            }
    )
    if ($matched.Count -ne 1) {
        throw "Microsoft VC runtime file is not uniquely pinned: $nativePath"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $matched[0].Source
    if (
        $signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject.IndexOf(
            $nativeSigner,
            [StringComparison]::OrdinalIgnoreCase
        ) -lt 0
    ) {
        throw "Microsoft VC runtime Authenticode verification failed: $nativePath"
    }
    $nativeSignaturesVerified += 1
}
Write-Host (
    "[OK] Verified {0} app-local Microsoft VC143 CRT signatures." -f
    $nativeSignaturesVerified
)

$runtimeDllInstallPaths = @(
    $installedPaths |
        Where-Object {
            $_.StartsWith("runtime/llama/", [StringComparison]::OrdinalIgnoreCase) -and
            $_.EndsWith(".dll", [StringComparison]::OrdinalIgnoreCase)
        }
)
if ($runtimeDllInstallPaths.Count -eq 0) {
    throw "The llama.cpp runtime lock must include its native DLL dependencies."
}
foreach ($runtimePath in $runtimeDllInstallPaths) {
    if ($runtimePath -match '(cuda|cublas|hipblas|vulkan|sycl)') {
        throw "GPU runtime DLLs are not allowed in the CPU compatibility bundle: $runtimePath"
    }
}
$runtimeCachePath = Resolve-SafeChild $cachePath $runtimeCacheDirectory "runtime.cache_directory"
if (-not (Test-Path -LiteralPath $runtimeCachePath -PathType Container)) {
    throw "The pinned llama.cpp cache directory is missing: $runtimeCacheDirectory"
}
$unlockedRuntimeDlls = @(
    Get-ChildItem -LiteralPath $runtimeCachePath -Recurse -File -Filter "*.dll" |
        Where-Object {
            $relative = $_.FullName.Substring(
                [System.IO.Path]::GetFullPath($cachePath).TrimEnd("\", "/").Length + 1
            )
            -not $cachePaths.Contains($relative.Replace("\", "/"))
        }
)
if ($unlockedRuntimeDlls.Count -gt 0) {
    throw (
        "Every DLL from the pinned llama.cpp cache must be hash-locked; missing: " +
        $unlockedRuntimeDlls[0].FullName
    )
}

$modelComponents = @()
$seenTasks = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($model in $models) {
    $context = "model"
    $modelId = Get-RequiredString $model "id" $context
    $taskType = (Get-RequiredString $model "task_type" $context).ToLowerInvariant()
    if ($taskType -notin @("embedding", "reranker") -or -not $seenTasks.Add($taskType)) {
        throw "Models must contain one unique embedding and one unique reranker entry."
    }
    foreach ($name in @("model_name", "revision", "source_url", "license")) {
        Get-RequiredString $model $name $modelId | Out-Null
    }
    $modelFile = (
        Get-RequiredString $model "file_install_path" $modelId
    ).Replace("\", "/")
    $licenseFile = (
        Get-RequiredString $model "license_install_path" $modelId
    ).Replace("\", "/")
    if (-not $modelFile.EndsWith(".gguf", [StringComparison]::OrdinalIgnoreCase)) {
        throw "$modelId must reference a GGUF model file."
    }
    if (-not $installedPaths.Contains($modelFile)) {
        throw "$modelId references a model file that is not pinned in files[]."
    }
    if (-not $installedPaths.Contains($licenseFile)) {
        throw "$modelId references a license that is not pinned in files[]."
    }
    $serverArguments = @(Get-RequiredProperty $model "server_arguments" $modelId)
    if ($serverArguments.Count -eq 0) {
        throw "$modelId must declare llama-server arguments."
    }
    $modelComponents += [ordered]@{
        id = $modelId
        task_type = $taskType
        executable = $runtimeExecutable.Replace("\", "/")
        model = $modelFile
        model_name = Get-RequiredString $model "model_name" $modelId
        base_path = Get-RequiredString $model "base_path" $modelId
        health_path = Get-RequiredString $model "health_path" $modelId
        server_arguments = @($serverArguments | ForEach-Object { [string]$_ })
    }
}
if (-not $installedPaths.Contains($runtimeExecutable.Replace("\", "/"))) {
    throw "runtime.executable_install_path is not pinned in files[]."
}
if (-not $installedPaths.Contains($runtimeLicense.Replace("\", "/"))) {
    throw "runtime.license_install_path is not pinned in files[]."
}

Write-Host "[OK] Component cache lock, sizes and SHA-256 hashes are valid."
if ($ValidateCacheOnly) {
    Write-Host "[OK] Cache-only validation completed; no package files were written."
    exit 0
}

$portableSource = ""
if ($SkipPortableBuild) {
    if ([string]::IsNullOrWhiteSpace($PortableRoot)) {
        throw "PortableRoot is required with SkipPortableBuild."
    }
    $portableSource = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $PortableRoot))
} else {
    $portableOutputRelative = "artifacts\portable\offline-gguf-core"
    $portableArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "build_windows_portable.ps1"),
        "-OutputRoot", $portableOutputRelative,
        "-PythonExe", $PythonExe,
        "-SkipArchive"
    )
    if ($SkipFrontendBuild) {
        $portableArguments += "-SkipFrontendBuild"
    }
    if ($SkipSmokeTest) {
        $portableArguments += "-SkipSmokeTest"
    }
    Invoke-NativeChecked -FilePath "powershell.exe" -Arguments $portableArguments
    $portableSource = Join-Path $projectRoot (
        "$portableOutputRelative\debug-platform-windows-x64"
    )
}
if (-not (Test-Path -LiteralPath (Join-Path $portableSource "portable_launcher.py") -PathType Leaf)) {
    throw "Portable Core package is missing: $portableSource"
}

New-Item -ItemType Directory -Force -Path $resolvedOutputRoot | Out-Null
$stagingParent = Join-Path $resolvedOutputRoot "staging"
$packageRoot = Join-Path $stagingParent "debug-platform-windows-x64"
if (Test-Path -LiteralPath $packageRoot) {
    Remove-SafeInstallerChild -Path $packageRoot
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null
Write-Host "[INFO] Copying the verified portable Core..."
Get-ChildItem -LiteralPath $portableSource -Force | Copy-Item `
    -Destination $packageRoot `
    -Recurse `
    -Force
$oldManifest = Join-Path $packageRoot "package-manifest.json"
if (Test-Path -LiteralPath $oldManifest) {
    Remove-Item -LiteralPath $oldManifest -Force
}

Write-Host "[INFO] Assembling pinned llama.cpp and GGUF components..."
foreach ($entry in $validatedFiles) {
    $destination = Resolve-SafeChild $packageRoot $entry.InstallPath "install_path"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $entry.Source -Destination $destination -Force
}
$assembledLlamaServer = Resolve-SafeChild $packageRoot $runtimeExecutable "runtime executable"
Write-Host "[INFO] Checking that the assembled llama.cpp runtime can load all native DLLs..."
Invoke-NativeChecked -FilePath $assembledLlamaServer -Arguments @("--version")
$llamaHelpLines = @(& $assembledLlamaServer --help 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw "The assembled llama-server --help check failed with exit code $LASTEXITCODE."
}
$llamaHelp = $llamaHelpLines -join "`n"
foreach ($requiredFlag in @(
    "--api-key-file",
    "--embedding",
    "--reranking",
    "--pooling",
    "--parallel"
)) {
    if ($llamaHelp -notmatch [regex]::Escape($requiredFlag)) {
        throw "The pinned llama-server does not support required flag $requiredFlag."
    }
}

if (-not $SkipSmokeTest -and -not $SkipModelSmoke) {
    $embeddingComponent = @(
        $modelComponents | Where-Object { $_["task_type"] -eq "embedding" }
    )[0]
    $rerankerComponent = @(
        $modelComponents | Where-Object { $_["task_type"] -eq "reranker" }
    )[0]
    $assembledEmbedding = Resolve-SafeChild `
        $packageRoot `
        ([string]$embeddingComponent["model"]) `
        "assembled embedding model"
    $assembledReranker = Resolve-SafeChild `
        $packageRoot `
        ([string]$rerankerComponent["model"]) `
        "assembled reranker model"
    Write-Host "[INFO] Exercising real GGUF Embedding and Reranker inference contracts..."
    Invoke-NativeChecked -FilePath $PythonExe -Arguments @(
        (Join-Path $projectRoot "scripts\model-runtime\smoke_runtime.py"),
        "--llama-server", $assembledLlamaServer,
        "--embedding-model", $assembledEmbedding,
        "--reranker-model", $assembledReranker
    )
}

foreach ($name in @("Install.bat", "install_local.ps1", "OFFLINE_INSTALL.txt")) {
    Copy-Item `
        -LiteralPath (Join-Path $projectRoot "deploy\windows-installer\$name") `
        -Destination (Join-Path $packageRoot $name) `
        -Force
}
[ordered]@{
    schema_version = 1
    bundle_id = $bundleId
    components = $modelComponents
} | ConvertTo-Json -Depth 8 | Set-Content `
    -LiteralPath (Join-Path $packageRoot "model-components.json") `
    -Encoding UTF8
[ordered]@{
    schema_version = 1
    assembled_at_utc = [DateTime]::UtcNow.ToString("o")
    verification = [ordered]@{
        msvc_authenticode = "valid"
        msvc_signer_subject_contains = $nativeSigner
        msvc_file_count = $nativeSignaturesVerified
    }
    lock = $componentLock
} | ConvertTo-Json -Depth 20 | Set-Content `
    -LiteralPath (Join-Path $packageRoot "component-provenance.json") `
    -Encoding UTF8
$provenanceOutput = Join-Path $resolvedOutputRoot (
    "debug-platform-offline-gguf-$Version-windows-x64.provenance.json"
)
Copy-Item `
    -LiteralPath (Join-Path $packageRoot "component-provenance.json") `
    -Destination $provenanceOutput `
    -Force
$provenanceHash = (Get-Sha256Hex -Path $provenanceOutput).ToLowerInvariant()
"$provenanceHash  $([System.IO.Path]::GetFileName($provenanceOutput))" | Set-Content `
    -LiteralPath "$provenanceOutput.sha256" `
    -Encoding ASCII

$buildInfoPath = Join-Path $packageRoot "build-info.json"
$buildInfo = Get-Content -LiteralPath $buildInfoPath -Raw | ConvertFrom-Json
$buildInfo.local_model_runtime_bundled = $true
$buildInfo.default_embedding = "bundled_gguf"
$buildInfo.default_reranker = "bundled_gguf"
$buildInfo | Add-Member -NotePropertyName retrieval_bundle_id -NotePropertyValue $bundleId -Force
$buildInfo | Add-Member -NotePropertyName package_version -NotePropertyValue $Version -Force
$buildInfo | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $buildInfoPath -Encoding UTF8

foreach ($forbidden in @(".env", "data", "A.py")) {
    if (Test-Path -LiteralPath (Join-Path $packageRoot $forbidden)) {
        throw "Runtime data or a local-only file leaked into the installer: $forbidden"
    }
}

Write-Host "[INFO] Writing the complete package integrity manifest..."
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

if (-not $SkipSmokeTest) {
    $verifyArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "verify_windows_portable.ps1"),
        "-PackageRoot", $packageRoot
    )
    if ($SkipModelSmoke) {
        $verifyArguments += "-NoLocalRetrieval"
    } else {
        $verifyArguments += "-RequireLocalModels"
    }
    Invoke-NativeChecked -FilePath "powershell.exe" -Arguments $verifyArguments
}

$archivePath = Join-Path $resolvedOutputRoot (
    "debug-platform-offline-gguf-$Version-windows-x64.zip"
)
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
Write-Host "[INFO] Creating the extract-and-install offline ZIP..."
Push-Location $stagingParent
try {
    $tar = Get-Command "tar.exe" -ErrorAction SilentlyContinue
    if ($null -ne $tar) {
        Invoke-NativeChecked -FilePath $tar.Source -Arguments @(
            "-a", "-c", "-f", $archivePath, ([System.IO.Path]::GetFileName($packageRoot))
        )
    } else {
        Compress-Archive `
            -LiteralPath $packageRoot `
            -DestinationPath $archivePath `
            -CompressionLevel Optimal
    }
} finally {
    Pop-Location
}
$archiveHash = (Get-Sha256Hex -Path $archivePath).ToLowerInvariant()
"$archiveHash  $([System.IO.Path]::GetFileName($archivePath))" | Set-Content `
    -LiteralPath "$archivePath.sha256" `
    -Encoding ASCII
Write-Host "[OK] Offline ZIP: $archivePath"
Write-Host "[OK] Component provenance: $provenanceOutput"

$resolvedIscc = ""
if (-not $SkipSetupExe) {
    if (-not [string]::IsNullOrWhiteSpace($IsccPath)) {
        $resolvedIscc = [System.IO.Path]::GetFullPath($IsccPath)
    } else {
        $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            $resolvedIscc = $command.Source
        } else {
            foreach ($candidate in @(
                (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
                (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
            )) {
                if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                    $resolvedIscc = $candidate
                    break
                }
            }
        }
    }
}
if (-not [string]::IsNullOrWhiteSpace($resolvedIscc)) {
    if (-not (Test-Path -LiteralPath $resolvedIscc -PathType Leaf)) {
        throw "Inno Setup compiler is missing: $resolvedIscc"
    }
    Write-Host "[INFO] Compiling the one-click per-user Setup.exe..."
    Invoke-NativeChecked -FilePath $resolvedIscc -Arguments @(
        "/DSourceRoot=$packageRoot",
        "/DOutputRoot=$resolvedOutputRoot",
        "/DAppVersion=$Version",
        (Join-Path $projectRoot "deploy\windows-installer\DebugPlatform.iss")
    )
    $setupPath = Join-Path $resolvedOutputRoot (
        "GWAP-Debug-Platform-Setup-$Version-x64.exe"
    )
    if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
        throw "Inno Setup completed but the expected Setup.exe was not produced."
    }
    $setupHash = (Get-Sha256Hex -Path $setupPath).ToLowerInvariant()
    "$setupHash  $([System.IO.Path]::GetFileName($setupPath))" | Set-Content `
        -LiteralPath "$setupPath.sha256" `
        -Encoding ASCII
    Write-Host "[OK] One-click installer: $setupPath"
} elseif ($RequireSetupExe) {
    throw "Inno Setup 6 is required but ISCC.exe could not be found."
} else {
    Write-Host (
        "[WARN] ISCC.exe was not found. The verified offline ZIP is usable: " +
        "extract it and double-click Install.bat. Install Inno Setup 6 and rerun " +
        "with -RequireSetupExe to produce a single Setup.exe."
    )
}

Write-Host "[OK] Package staging directory: $packageRoot"
Write-Host "[OK] Offline ZIP SHA-256: $archiveHash"
