[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [string]$OutputRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "file_hash.ps1")

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Component installation verification requires Windows."
}
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$package = [IO.Path]::GetFullPath($PackageRoot)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot "artifacts\installer"))
$allowedPrefix = $allowedRoot.TrimEnd("\", "/") + "\"
$nonce = [Guid]::NewGuid().ToString("N")
$runRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    Join-Path $allowedRoot ("component-verification-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $nonce.Substring(0, 8))
} else {
    [IO.Path]::GetFullPath($OutputRoot)
}
if (-not $runRoot.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputRoot must be a new child directory below artifacts\installer."
}
if ($runRoot.StartsWith($package.TrimEnd("\", "/") + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Verification output must not be inside the source package."
}
if (Test-Path -LiteralPath $runRoot) {
    throw "The verification output directory already exists; use a new directory."
}
foreach ($required in @("install_local.ps1", "component_selection.py", "package-manifest.json", "model-components.json")) {
    if (-not (Test-Path -LiteralPath (Join-Path $package $required) -PathType Leaf)) {
        throw "Final full package is not ready: missing $required."
    }
}
$sourceComponents = @(Get-Content -LiteralPath (Join-Path $package "model-components.json") -Raw | ConvertFrom-Json | Select-Object -ExpandProperty components)
if ($sourceComponents.Count -ne 2) {
    throw "The verification source must contain both GGUF model components."
}
$sourceManifestHash = Get-Sha256Hex -Path (Join-Path $package "package-manifest.json")
New-Item -ItemType Directory -Path $runRoot | Out-Null
$testParentName = [string]::Concat([char]0x5B89, [char]0x88C5, " app [space]")
$installParent = Join-Path $runRoot $testParentName
$installedRoot = Join-Path $installParent "GWAPDebugPlatform"
$externalData = Join-Path $runRoot "external-user-data"
$probeData = Join-Path $runRoot "isolated-probe-data"
$probeEnv = Join-Path $runRoot "isolated-probe.env"
New-Item -ItemType Directory -Path $externalData | Out-Null
$marker = Join-Path $externalData "preserve-marker.txt"
[IO.File]::WriteAllText($marker, "Synthetic component-install verification " + $nonce, [Text.UTF8Encoding]::new($false))
$markerHash = Get-Sha256Hex -Path $marker
$records = [Collections.Generic.List[object]]::new()
$summaryPath = Join-Path $runRoot "summary.json"
$started = [DateTime]::UtcNow
$failure = $null

function Invoke-LoggedNative {
    param([string]$Command, [string[]]$Arguments, [string]$LogPath)
    # PowerShell 5.1 can wrap native stderr warnings in ErrorRecord objects.
    # Respect the native exit code while retaining the output for diagnosis.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Command @Arguments 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $LogPath
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "Verification command failed with exit code $nativeExitCode; see $LogPath."
    }
}

try {
    foreach ($step in @(
        @{ Requested = "Core"; Expected = "Core"; Tasks = @() },
        @{ Requested = "Auto"; Expected = "Core"; Tasks = @() },
        @{ Requested = "Embedding"; Expected = "Embedding"; Tasks = @("embedding") },
        @{ Requested = "Reranker"; Expected = "Reranker"; Tasks = @("reranker") },
        @{ Requested = "Full"; Expected = "Full"; Tasks = @("embedding", "reranker") }
    )) {
        $stepStarted = [DateTime]::UtcNow
        $index = $records.Count + 1
        $label = "{0:d2}-{1}" -f $index, $step.Requested
        Write-Host "[INFO] Installing $label into the isolated verification tree..."
        $installLog = Join-Path $runRoot ($label + "-install.log")
        Invoke-LoggedNative -Command "powershell.exe" -Arguments @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $package "install_local.ps1"),
            "-InstallRoot", $installedRoot, "-NoLaunch", "-NoShortcuts",
            "-Components", $step.Requested
        ) -LogPath $installLog

        $selectionPath = Join-Path $installedRoot "installation-selection.json"
        $selection = Get-Content -LiteralPath $selectionPath -Raw | ConvertFrom-Json
        if ($selection.selection -ne $step.Expected) {
            throw "Requested $($step.Requested), expected $($step.Expected), got $($selection.selection)."
        }
        if (
            $selection.source_manifest_sha256 -ne $sourceManifestHash -or
            (Get-Sha256Hex -Path (Join-Path $installedRoot "source-package-manifest.json")) -ne $sourceManifestHash
        ) {
            throw "Installed component provenance no longer binds to the full source manifest."
        }
        $installedTasks = @($selection.installed_tasks | Sort-Object)
        $expectedTasks = @($step.Tasks | Sort-Object)
        if (($installedTasks -join ",") -ne ($expectedTasks -join ",")) {
            throw "Installed task selection does not match $($step.Expected)."
        }
        foreach ($component in $sourceComponents) {
            $modelPath = Join-Path $installedRoot ([string]$component.model)
            $expectedPresent = $expectedTasks -contains [string]$component.task_type
            if ((Test-Path -LiteralPath $modelPath -PathType Leaf) -ne $expectedPresent) {
                throw "Unexpected installed weight presence for $($component.task_type)."
            }
        }
        $runtimeExists = Test-Path -LiteralPath (Join-Path $installedRoot "runtime\llama\llama-server.exe") -PathType Leaf
        if ($runtimeExists -ne ($expectedTasks.Count -gt 0)) {
            throw "Shared CPU runtime does not match the component selection."
        }
        $checkLog = Join-Path $runRoot ($label + "-selfcheck.log")
        Invoke-LoggedNative -Command (Join-Path $installedRoot "runtime\python\python.exe") -Arguments @(
            "-B", "-s", (Join-Path $installedRoot "portable_launcher.py"),
            "--check", "--no-browser", "--data-root", $probeData, "--env-file", $probeEnv
        ) -LogPath $checkLog
        if ((Get-Sha256Hex -Path $marker) -ne $markerHash) {
            throw "External synthetic data marker changed during installation."
        }
        $leftovers = @(
            Get-ChildItem -LiteralPath $installParent -Directory -Force | Where-Object {
                $_.Name.StartsWith("GWAPDebugPlatform.installing-", [StringComparison]::OrdinalIgnoreCase) -or
                $_.Name.StartsWith("GWAPDebugPlatform.backup-", [StringComparison]::OrdinalIgnoreCase)
            }
        )
        if ($leftovers.Count -gt 0) {
            throw "The publisher left a staging/backup tree after a successful installation."
        }
        $records.Add([ordered]@{
            requested = $step.Requested
            installed = $selection.selection
            tasks = $installedTasks
            source_manifest_sha256 = $selection.source_manifest_sha256
            manifest_sha256 = Get-Sha256Hex -Path (Join-Path $installedRoot "package-manifest.json")
            external_marker_unchanged = $true
            no_staging_or_backup_left = $true
            selfcheck_passed = $true
            models_started = $false
            duration_seconds = [Math]::Round(([DateTime]::UtcNow - $stepStarted).TotalSeconds, 3)
            install_log = $installLog
            selfcheck_log = $checkLog
        })
        Write-Host "[OK] ${label}: selected bytes, manifest, external data and cleanup verified."
    }
    if ((Get-Sha256Hex -Path (Join-Path $package "package-manifest.json")) -ne $sourceManifestHash) {
        throw "The full source package manifest changed during verification."
    }
} catch {
    $failure = $_.Exception.Message
    throw
} finally {
    [ordered]@{
        status = $(if ($null -eq $failure -and $records.Count -eq 5) { "PASS" } else { "FAIL" })
        source_package = $package
        source_manifest_sha256 = $sourceManifestHash
        installed_root = $installedRoot
        final_install_preserved = (Test-Path -LiteralPath $installedRoot -PathType Container)
        probe_data = $probeData
        probe_env = $probeEnv
        external_data_marker = $marker
        model_inference_exercised = $false
        global_install_shortcuts_or_registry_changed = $false
        completed_steps = $records.Count
        started_at_utc = $started.ToString("o")
        duration_seconds = [Math]::Round(([DateTime]::UtcNow - $started).TotalSeconds, 3)
        failure = $failure
        steps = @($records.ToArray())
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host "[INFO] Component verification evidence: $summaryPath"
}
