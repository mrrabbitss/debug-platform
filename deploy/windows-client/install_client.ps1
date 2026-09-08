[CmdletBinding()]
param([string]$InstallRoot = '', [switch]$NoShortcut, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\file_hash.ps1')
try {
    if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugClient\versions' }
    $manifestPath = Join-Path $PSScriptRoot 'client-manifest.json'
    $manifest = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.package_id -notmatch '^[a-f0-9]{16}$') { throw 'Invalid client manifest.' }
    $sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    $seen = @{}
    foreach ($entry in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $sourceRoot $entry.path))
        if (-not $path.StartsWith($sourceRoot, [StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($path)) {
            throw 'Unsafe or duplicate client package path.'
        }
        $seen[$path] = $true
        if (-not [IO.File]::Exists($path) -or (Get-Item -LiteralPath $path).Length -ne $entry.size) { throw 'Client package is incomplete.' }
        if ((Get-Sha256Hex $path) -ine $entry.sha256) { throw 'Client package integrity check failed.' }
    }
    foreach ($item in Get-ChildItem -LiteralPath $sourceRoot -File -Recurse) {
        if ($item.Name -eq 'client-manifest.json' -and $item.DirectoryName -eq $PSScriptRoot) { continue }
        if (-not $seen.ContainsKey($item.FullName)) { throw 'Unexpected file in client package.' }
    }
    $target = Join-Path ([IO.Path]::GetFullPath($InstallRoot)) $manifest.package_id
    if ($DryRun) {
        [ordered]@{ ok=$true; target=$target; platform_runtime=$false; global_cli_changes=$false; writes_files=$false } | ConvertTo-Json
        exit 0
    }
    if ([IO.Directory]::Exists($target)) {
        foreach ($entry in $manifest.files) {
            $existing = Join-Path $target $entry.path
            if (-not [IO.File]::Exists($existing) -or (Get-Sha256Hex $existing) -ine $entry.sha256) {
                throw 'Installed files differ from this package; they were not overwritten.'
            }
        }
    } else {
        [IO.Directory]::CreateDirectory($target) | Out-Null
        foreach ($entry in $manifest.files) {
            $destination = Join-Path $target $entry.path
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
            Copy-Item -LiteralPath (Join-Path $sourceRoot $entry.path) -Destination $destination
        }
        Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $target 'client-manifest.json')
    }
    if (-not $NoShortcut) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'GWAP CodeAgent.lnk'))
        $shortcut.TargetPath = Join-Path $target 'Start.bat'
        $shortcut.WorkingDirectory = $target
        $shortcut.Description = 'Connect CodeAgent to the GW/AP platform; preserves existing CLI configuration'
        $shortcut.Save()
        $webShortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'GWAP Platform.lnk'))
        $webShortcut.TargetPath = Join-Path $target 'Open Platform.bat'
        $webShortcut.WorkingDirectory = $target
        $webShortcut.Save()
    }
    Write-Host ('[OK] Client installed: ' + $target)
    if ([IO.File]::Exists((Join-Path $target 'deployment.json'))) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $target 'start_client.ps1') -EnrollOnly
        if ($LASTEXITCODE -ne 0) { throw 'Files were installed. Start the server, then open GWAP Platform to finish registration.' }
    } else {
        Write-Host '[INFO] Open GWAP CodeAgent, enter the server HTTPS address and your personal token once.'
    }
} catch { Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
