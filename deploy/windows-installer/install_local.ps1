[CmdletBinding()]
param(
    [string]$InstallRoot = "",
    [switch]$NoLaunch,
    [switch]$NoDesktopShortcut,
    [switch]$NoShortcuts,
    [ValidateRange(1, 600)][int]$LockTimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sourceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$localAppData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::LocalApplicationData
)
if ([string]::IsNullOrWhiteSpace($localAppData)) {
    throw "LOCALAPPDATA is unavailable."
}
$defaultParent = [System.IO.Path]::GetFullPath((
    Join-Path $localAppData "Programs"
))
$destination = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    Join-Path $defaultParent "GWAPDebugPlatform"
} else {
    [System.IO.Path]::GetFullPath($InstallRoot)
}
$destination = [System.IO.Path]::GetFullPath($destination)
$destinationParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $destination))
if (
    $destination -eq [System.IO.Path]::GetPathRoot($destination) -or
    $destination -eq $localAppData -or
    $destination -eq $defaultParent
) {
    throw "InstallRoot must name a dedicated application directory."
}
$sourcePrefix = $sourceRoot.TrimEnd("\", "/") + "\"
$destinationPrefix = $destination.TrimEnd("\", "/") + "\"
if (
    $sourceRoot.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $destination.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "Extract the offline bundle outside the selected installation directory."
}

function Assert-ManagedSibling {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Prefix
    )
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $resolved))
    $name = [System.IO.Path]::GetFileName($resolved)
    if (
        $parent -ne $destinationParent -or
        -not $name.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to mutate an unmanaged installation path: $resolved"
    }
}

function New-PlatformShortcuts {
    param([Parameter(Mandatory = $true)][string]$InstalledRoot)
    $shell = New-Object -ComObject WScript.Shell
    $startMenuDirectory = Join-Path (
        [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    ) "GWAP Debug Platform"
    New-Item -ItemType Directory -Force -Path $startMenuDirectory | Out-Null
    $shortcutTargets = @(
        (Join-Path $startMenuDirectory "GWAP Debug Platform.lnk")
    )
    if (-not $NoDesktopShortcut) {
        $shortcutTargets += Join-Path (
            [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
        ) "GWAP Debug Platform.lnk"
    }
    foreach ($shortcutPath in $shortcutTargets) {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $InstalledRoot "start.bat"
        $shortcut.WorkingDirectory = $InstalledRoot
        $shortcut.Description = "Start GW/AP Intelligent Debug Platform"
        $shortcut.Save()
    }
}

function Assert-RestorableBackup {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-ManagedSibling -Path $Path -Prefix "GWAPDebugPlatform.backup-"
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $backupInfo = Get-Item -LiteralPath $resolved -Force
    if (
        -not $backupInfo.PSIsContainer -or
        ($backupInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        $backupInfo.Name -notmatch '^GWAPDebugPlatform\.backup-[0-9a-f]{32}$'
    ) {
        throw "The orphaned backup is not a regular managed backup directory: $resolved"
    }
    if ($resolved -eq $sourceRoot) {
        throw "The extracted offline bundle cannot also be used as a recovery backup."
    }

    $requiredPaths = @(
        "package-manifest.json",
        "portable_launcher.py",
        "start.bat",
        "model-components.json",
        "runtime\python\python.exe"
    )
    foreach ($relativePath in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $resolved $relativePath) -PathType Leaf)) {
            throw "The orphaned backup is incomplete; missing $relativePath."
        }
    }

    try {
        $manifest = Get-Content `
            -LiteralPath (Join-Path $resolved "package-manifest.json") `
            -Raw | ConvertFrom-Json
    } catch {
        throw "The orphaned backup package-manifest.json is not valid JSON."
    }
    if ([int]$manifest.schema_version -ne 1 -or $null -eq $manifest.files) {
        throw "The orphaned backup package manifest has an unsupported shape."
    }
    $manifestPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in @($manifest.files)) {
        $relativePath = [string]$entry.path
        if (-not [string]::IsNullOrWhiteSpace($relativePath)) {
            $manifestPaths.Add($relativePath.Replace("\", "/")) | Out-Null
        }
    }
    foreach ($relativePath in $requiredPaths | Where-Object { $_ -ne "package-manifest.json" }) {
        if (-not $manifestPaths.Contains($relativePath.Replace("\", "/"))) {
            throw "The orphaned backup manifest does not cover $relativePath."
        }
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot "package-manifest.json") -PathType Leaf)) {
    throw "The offline bundle is incomplete: package-manifest.json is missing."
}
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot "model-components.json") -PathType Leaf)) {
    throw "The offline bundle does not contain the GGUF Embedding/Reranker components."
}

$mutexName = "Local\GWAPDebugPlatform.Install"
$installMutex = $null
$lockAcquired = $false
try {
    $installMutex = [System.Threading.Mutex]::new($false, $mutexName)
    try {
        $lockAcquired = $installMutex.WaitOne(
            [TimeSpan]::FromSeconds($LockTimeoutSeconds)
        )
    } catch [System.Threading.AbandonedMutexException] {
        # WaitOne grants ownership when it reports an abandoned mutex. The
        # recovery scan below handles any backup left by the terminated process.
        $lockAcquired = $true
        Write-Warning "A previous installer terminated unexpectedly; checking recovery state."
    }
    if (-not $lockAcquired) {
        throw (
            "Timed out after $LockTimeoutSeconds seconds waiting for another " +
            "GW/AP Debug Platform installation to finish."
        )
    }

    if (
        -not (Test-Path -LiteralPath $destination) -and
        (Test-Path -LiteralPath $destinationParent -PathType Container)
    ) {
        $backupCandidates = @(
            Get-ChildItem -LiteralPath $destinationParent -Directory -Force |
                Where-Object {
                    $_.Name.StartsWith(
                        "GWAPDebugPlatform.backup-",
                        [System.StringComparison]::OrdinalIgnoreCase
                    )
                }
        )
        if ($backupCandidates.Count -gt 1) {
            throw (
                "The application directory is missing and multiple managed backups exist; " +
                "refusing to guess which backup to restore."
            )
        }
        if ($backupCandidates.Count -eq 1) {
            $recoveryBackup = [System.IO.Path]::GetFullPath(
                $backupCandidates[0].FullName
            )
            Assert-RestorableBackup -Path $recoveryBackup
            Write-Warning "Restoring the only verified orphaned application backup before installation."
            Move-Item -LiteralPath $recoveryBackup -Destination $destination
            Write-Host "[OK] Recovered the previous application tree."
        }
    }

    if ($sourceRoot -eq $destination) {
        Write-Host "[INFO] The platform is already running from its installation directory."
        if (-not $NoShortcuts) {
            New-PlatformShortcuts -InstalledRoot $destination
        }
        if (-not $NoLaunch) {
            Start-Process -FilePath (Join-Path $destination "start.bat") -WorkingDirectory $destination
        }
        return
    }

    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    $nonce = [System.Guid]::NewGuid().ToString("N")
    $staging = Join-Path $destinationParent ("GWAPDebugPlatform.installing-" + $nonce)
    $backup = Join-Path $destinationParent ("GWAPDebugPlatform.backup-" + $nonce)
    Assert-ManagedSibling -Path $staging -Prefix "GWAPDebugPlatform.installing-"
    Assert-ManagedSibling -Path $backup -Prefix "GWAPDebugPlatform.backup-"

    $previousMoved = $false
    $newInstalled = $false
    try {
        Write-Host "[INFO] Copying the offline components..."
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        Get-ChildItem -LiteralPath $sourceRoot -Force | Copy-Item `
            -Destination $staging `
            -Recurse `
            -Force

        $python = Join-Path $staging "runtime\python\python.exe"
        $launcher = Join-Path $staging "portable_launcher.py"
        if (
            -not (Test-Path -LiteralPath $python -PathType Leaf) -or
            -not (Test-Path -LiteralPath $launcher -PathType Leaf)
        ) {
            throw "The bundled application runtime is incomplete."
        }
        $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $verificationRoot = [System.IO.Path]::GetFullPath((Join-Path $tempBase (
            "gw-ap-installer-check-" + $nonce
        )))
        if (
            -not $verificationRoot.StartsWith(
                $tempBase,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not ([System.IO.Path]::GetFileName($verificationRoot)).StartsWith(
                "gw-ap-installer-check-",
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Refusing to use an unsafe installer verification directory."
        }
        try {
            & $python -B -s $launcher `
                --check `
                --no-browser `
                --data-root (Join-Path $verificationRoot "data") `
                --env-file (Join-Path $verificationRoot ".env")
            if ($LASTEXITCODE -ne 0) {
                throw "Offline component integrity verification failed with exit code $LASTEXITCODE."
            }
        } finally {
            if (Test-Path -LiteralPath $verificationRoot) {
                Remove-Item -LiteralPath $verificationRoot -Recurse -Force
            }
        }

        if (Test-Path -LiteralPath $destination) {
            Write-Host "[INFO] Preserving the current application until the upgrade is verified..."
            Move-Item -LiteralPath $destination -Destination $backup
            $previousMoved = $true
        }
        Move-Item -LiteralPath $staging -Destination $destination
        $newInstalled = $true
        if (-not $NoShortcuts) {
            New-PlatformShortcuts -InstalledRoot $destination
        }

        if ($previousMoved -and (Test-Path -LiteralPath $backup)) {
            Assert-ManagedSibling -Path $backup -Prefix "GWAPDebugPlatform.backup-"
            Remove-Item -LiteralPath $backup -Recurse -Force
        }
        Write-Host "[OK] Installed to $destination"
        Write-Host "[OK] Runtime data remains under %LOCALAPPDATA%\GWAPDebugPlatform."
    } catch {
        if (Test-Path -LiteralPath $staging) {
            Assert-ManagedSibling -Path $staging -Prefix "GWAPDebugPlatform.installing-"
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($newInstalled -and (Test-Path -LiteralPath $destination)) {
            Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (
            $previousMoved -and
            (Test-Path -LiteralPath $backup) -and
            -not (Test-Path -LiteralPath $destination)
        ) {
            Move-Item -LiteralPath $backup -Destination $destination -ErrorAction SilentlyContinue
        }
        throw
    }

    if (-not $NoLaunch) {
        Start-Process -FilePath (Join-Path $destination "start.bat") -WorkingDirectory $destination
    }
} finally {
    if ($lockAcquired -and $null -ne $installMutex) {
        try {
            $installMutex.ReleaseMutex()
        } catch [System.ApplicationException] {
            Write-Warning "The installer mutex was no longer owned during cleanup."
        }
    }
    if ($null -ne $installMutex) {
        $installMutex.Dispose()
    }
}
