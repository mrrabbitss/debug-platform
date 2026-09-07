[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SetupExe,
    [Parameter(Mandatory = $true)][string]$PackageRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "file_hash.ps1")
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Setup lifecycle verification requires Windows."
}
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$setupPath = [IO.Path]::GetFullPath($SetupExe)
$sourcePackage = [IO.Path]::GetFullPath($PackageRoot)
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) { throw "Setup.exe is unavailable." }
$localAppDataPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$programsPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
$desktopPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$applicationRoot = Join-Path $localAppDataPath "Programs\GWAPDebugPlatform"
$uninstallerRoot = Join-Path $localAppDataPath "Programs\GWAPDebugPlatform-Uninstall"
$businessDataRoot = Join-Path $localAppDataPath "GWAPDebugPlatform"
$menuRoot = Join-Path $programsPath "GWAP Debug Platform"
$shortcutPaths = @(
    (Join-Path $menuRoot "GWAP Debug Platform.lnk"),
    (Join-Path $menuRoot "GWAP Debug Platform - CodeAgent.lnk"),
    (Join-Path $desktopPath "GWAP Debug Platform.lnk"),
    (Join-Path $desktopPath "GWAP Debug Platform - CodeAgent.lnk")
)
$uninstallKeys = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{61F92736-C499-46E0-BADF-561EB9A6D3C4}_is1",
    "HKCU:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{61F92736-C499-46E0-BADF-561EB9A6D3C4}_is1",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{61F92736-C499-46E0-BADF-561EB9A6D3C4}_is1",
    "HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{61F92736-C499-46E0-BADF-561EB9A6D3C4}_is1"
)

function Assert-NoInstalledApplicationProcess {
    $prefix = $applicationRoot.TrimEnd("\", "/") + "\"
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($running.Count -gt 0) { throw "A process is running from the default application tree; refusing to continue." }
}

# This harness is never allowed to adopt or remove an existing installation.
foreach ($target in @($applicationRoot, $uninstallerRoot, $businessDataRoot, $menuRoot) + $shortcutPaths + $uninstallKeys) {
    if (Test-Path -LiteralPath $target) {
        throw "Default lifecycle test requires an empty initial state; existing target: $target"
    }
}
Assert-NoInstalledApplicationProcess
$sourceModels = @(Get-Content -LiteralPath (Join-Path $sourcePackage "model-components.json") -Raw | ConvertFrom-Json | Select-Object -ExpandProperty components)
if ($sourceModels.Count -ne 2) { throw "Expected the complete final GGUF package." }
$sourceManifestHash = Get-Sha256Hex -Path (Join-Path $sourcePackage "package-manifest.json")
$runRoot = Join-Path $repositoryRoot ("artifacts\installer\setup-verification-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $runRoot | Out-Null
New-Item -ItemType Directory -Path $businessDataRoot | Out-Null
$businessMarker = Join-Path $businessDataRoot "installer-verification-preserve-marker.txt"
[IO.File]::WriteAllText($businessMarker, "Synthetic Setup boundary marker. Evidence: " + $runRoot, [Text.UTF8Encoding]::new($false))
$businessMarkerHash = Get-Sha256Hex -Path $businessMarker
$records = [Collections.Generic.List[object]]::new()
$started = [DateTime]::UtcNow
$failure = $null
$uninstallSucceeded = $false

function Invoke-InstallerProcess {
    param([string]$Executable, [string[]]$Arguments)
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.Arguments = $Arguments -join " "
    $startInfo.WorkingDirectory = Split-Path -Parent $Executable
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $process = [Diagnostics.Process]::Start($startInfo)
    try {
        $elapsed = [Diagnostics.Stopwatch]::StartNew()
        $nextUpdate = 30
        while (-not $process.WaitForExit(1000)) {
            if ($elapsed.Elapsed.TotalSeconds -ge $nextUpdate) {
                Write-Host "[INFO] Waiting for installer PID $($process.Id), $([int]$elapsed.Elapsed.TotalSeconds)s..."
                $nextUpdate += 30
            }
            if ($elapsed.Elapsed.TotalMinutes -gt 15) {
                throw "Installer PID $($process.Id) exceeded 15 minutes; it was not forcibly terminated."
            }
        }
        $exitCode = $process.ExitCode
        if ($exitCode -ne 0) { throw "Installer process exited with actual exit code $exitCode." }
        return $exitCode
    } finally {
        $process.Dispose()
    }
}

try {
    foreach ($step in @(
        @{ Type = "core"; Selection = "Core"; Tasks = @() },
        @{ Type = "embedding"; Selection = "Embedding"; Tasks = @("embedding") },
        @{ Type = "reranker"; Selection = "Reranker"; Tasks = @("reranker") },
        @{ Type = "full"; Selection = "Full"; Tasks = @("embedding", "reranker") }
    )) {
        Assert-NoInstalledApplicationProcess
        $stepStarted = [DateTime]::UtcNow
        $label = "{0:d2}-{1}" -f ($records.Count + 1), $step.Type
        $setupLog = Join-Path $runRoot ($label + "-setup.log")
        Write-Host "[INFO] Real Setup /TYPE=$($step.Type), default per-user directory..."
        $exitCode = Invoke-InstallerProcess -Executable $setupPath -Arguments @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
            "/NOCLOSEAPPLICATIONS", "/NOFORCECLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS",
            ("/TYPE=" + $step.Type), "/TASKS=desktopicon", ('/LOG="' + $setupLog + '"')
        )
        $selection = Get-Content -LiteralPath (Join-Path $applicationRoot "installation-selection.json") -Raw | ConvertFrom-Json
        if ($selection.selection -ne $step.Selection -or $selection.source_manifest_sha256 -ne $sourceManifestHash) {
            throw "Setup type did not produce the expected selection and source provenance."
        }
        $actualTasks = @($selection.installed_tasks | Sort-Object)
        $expectedTasks = @($step.Tasks | Sort-Object)
        if (($actualTasks -join ",") -ne ($expectedTasks -join ",")) { throw "Setup installed unexpected tasks." }
        foreach ($model in $sourceModels) {
            $expected = $expectedTasks -contains [string]$model.task_type
            if ((Test-Path -LiteralPath (Join-Path $applicationRoot $model.model) -PathType Leaf) -ne $expected) {
                throw "Setup installed unexpected GGUF weight files."
            }
        }
        $checkLog = Join-Path $runRoot ($label + "-selfcheck.log")
        & (Join-Path $applicationRoot "runtime\python\python.exe") -B -s (Join-Path $applicationRoot "portable_launcher.py") `
            --check --no-browser --data-root (Join-Path $runRoot "probe-data") --env-file (Join-Path $runRoot "probe.env") 2>&1 | Tee-Object -FilePath $checkLog
        if ($LASTEXITCODE -ne 0) { throw "Installed Setup package selfcheck failed." }
        $wscript = New-Object -ComObject WScript.Shell
        foreach ($shortcutPath in $shortcutPaths) {
            if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { throw "Expected Setup shortcut is missing: $shortcutPath" }
            $shortcut = $wscript.CreateShortcut($shortcutPath)
            $entry = if ($shortcutPath.EndsWith(" - CodeAgent.lnk", [StringComparison]::OrdinalIgnoreCase)) { "start_codeagent.bat" } else { "start.bat" }
            if ($shortcut.TargetPath -ne (Join-Path $applicationRoot $entry)) { throw "Setup shortcut has an unexpected target." }
        }
        $registered = @($uninstallKeys | Where-Object { Test-Path -LiteralPath $_ })
        if ($registered.Count -ne 1) { throw "Expected one application uninstall registration." }
        if (-not (Test-Path -LiteralPath (Join-Path $uninstallerRoot "unins000.exe") -PathType Leaf)) { throw "Expected managed uninstaller is missing." }
        if ((Get-Sha256Hex -Path $businessMarker) -ne $businessMarkerHash) { throw "Business-data marker changed." }
        Assert-NoInstalledApplicationProcess
        $records.Add([ordered]@{
            type = $step.Type; selection = $selection.selection; tasks = $actualTasks
            actual_process_exit_code = $exitCode; selfcheck_passed = $true
            all_four_shortcuts_verified = $true; uninstall_registry_count = $registered.Count
            business_data_unchanged = $true; no_application_or_model_processes = $true
            setup_log = $setupLog; selfcheck_log = $checkLog
            duration_seconds = [Math]::Round(([DateTime]::UtcNow - $stepStarted).TotalSeconds, 3)
        })
        Write-Host "[OK] Setup /TYPE=$($step.Type), shortcuts, registration, selfcheck and data boundary passed."
    }

    Assert-NoInstalledApplicationProcess
    $uninstallExe = Join-Path $uninstallerRoot "unins000.exe"
    if (-not [IO.Path]::GetFullPath($uninstallExe).StartsWith($uninstallerRoot.TrimEnd("\") + "\", [StringComparison]::OrdinalIgnoreCase)) { throw "Unexpected uninstaller target." }
    Write-Host "[INFO] Uninstalling only the application created by this test..."
    $uninstallExitCode = Invoke-InstallerProcess -Executable $uninstallExe -Arguments @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", ('/LOG="' + (Join-Path $runRoot "05-uninstall.log") + '"')
    )
    $cleanupDeadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $remaining = @(@($applicationRoot, $uninstallerRoot, $menuRoot) + $shortcutPaths + $uninstallKeys | Where-Object { Test-Path -LiteralPath $_ })
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $cleanupDeadline)
    if ($remaining.Count -gt 0) { throw "Uninstall left application/shortcut/registry targets: $($remaining -join ', ')" }
    if ((Get-Sha256Hex -Path $businessMarker) -ne $businessMarkerHash) { throw "Uninstall changed the preserved business-data marker." }
    $uninstallSucceeded = $true
    Write-Host "[OK] App, uninstaller, shortcuts and registry removed; business-data marker preserved."
} catch {
    $failure = $_.Exception.Message
    throw
} finally {
    $summary = [ordered]@{
        status = $(if ($null -eq $failure -and $uninstallSucceeded) { "PASS" } else { "FAIL" })
        setup_exe = $setupPath; setup_sha256 = Get-Sha256Hex -Path $setupPath
        source_package = $sourcePackage; application_root = $applicationRoot
        clean_state_preflight_passed = $true; completed_installations = $records.Count
        uninstalled = $uninstallSucceeded; business_data_marker_preserved_at = $businessMarker
        model_inference_exercised = $false; steps = @($records.ToArray())
        started_at_utc = $started.ToString("o")
        duration_seconds = [Math]::Round(([DateTime]::UtcNow - $started).TotalSeconds, 3)
        failure = $failure
    }
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runRoot "summary.json") -Encoding UTF8
    Write-Host "[INFO] Setup lifecycle evidence: $(Join-Path $runRoot 'summary.json')"
}
