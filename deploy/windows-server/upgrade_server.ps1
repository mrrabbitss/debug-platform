[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$NewPackageRoot, [string]$DataRoot = '', [switch]$DryRun)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'file_hash.ps1')
try {
    if (-not $DataRoot) { $DataRoot = Join-Path $env:ProgramData 'GWAPDebugServer' }
    $DataRoot = [IO.Path]::GetFullPath($DataRoot)
    $NewPackageRoot = [IO.Path]::GetFullPath($NewPackageRoot)
    if ($NewPackageRoot -eq $PSScriptRoot -or $NewPackageRoot -eq $DataRoot -or
        $NewPackageRoot.StartsWith($PSScriptRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $NewPackageRoot.StartsWith($DataRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Keep the new package in a separate version directory outside server data.'
    }
    $config = Join-Path $DataRoot 'config\server.json'
    $profile = [IO.File]::ReadAllText($config) | ConvertFrom-Json
    $newPython = Join-Path $NewPackageRoot 'runtime\python\python.exe'
    if (-not [IO.File]::Exists($newPython)) { throw 'A complete new server package is required.' }
    Push-Location -LiteralPath $NewPackageRoot
    try {
        & $newPython -B -s -c "import portable_launcher as p; p.validate_layout(); p.verify_package_manifest()"
        if ($LASTEXITCODE) { throw 'New package verification failed; current services unchanged.' }
    } finally { Pop-Location }
    if ($DryRun) { Write-Host '[OK] New package verified. Upgrade would stop services, take a full backup, switch paths and test startup; failure restores old data and program.'; exit 0 }
    $operationLock = [IO.File]::Open((Join-Path $DataRoot 'maintenance.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $wasRunning = (Get-Service GWAPBackend).Status -eq 'Running'
    Stop-Service GWAPGateway
    Stop-Service GWAPBackend
    $backup = Join-Path $DataRoot ('backups\before-upgrade-' + [Guid]::NewGuid().ToString('N') + '.zip')
    $oldPython = Join-Path $PSScriptRoot 'runtime\python\python.exe'
    & $oldPython -B -s (Join-Path $PSScriptRoot 'server_admin.py') backup --config $config --archive $backup
    if ($LASTEXITCODE) {
        if ($wasRunning) { Start-Service GWAPBackend; Start-Service GWAPGateway }
        throw 'Pre-upgrade backup failed; old program, data and service state retained.'
    }
    $firewall = Get-NetFirewallRule -Name 'GWAPDebugHTTPS' -ErrorAction SilentlyContinue
    try {
        & $newPython -B -s (Join-Path $NewPackageRoot 'server_admin.py') rewrite-services --config $config
        if ($LASTEXITCODE) { throw 'Could not switch service configuration.' }
        if ($firewall) { $firewall | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program (Join-Path $NewPackageRoot 'server-runtime\caddy.exe') | Out-Null }
        Start-Service GWAPBackend
        Start-Service GWAPGateway
        $healthy = $false
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            try {
                $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:' + $profile.backend_port + '/api/v1/health/ready')
                $request.Proxy = $null
                $request.Timeout = 2000
                $response = $request.GetResponse()
                try { $healthy = ([int]$response.StatusCode -eq 200) } finally { $response.Dispose() }
                if ($healthy) { break }
            } catch { }
            Start-Sleep -Seconds 2
        }
        if (-not $healthy -or (Get-Service GWAPGateway).Status -ne 'Running') { throw 'New server did not become healthy.' }
        if (-not $wasRunning) { Stop-Service GWAPGateway; Stop-Service GWAPBackend }
    } catch {
        Stop-Service GWAPGateway -ErrorAction SilentlyContinue
        Stop-Service GWAPBackend -ErrorAction SilentlyContinue
        & $oldPython -B -s (Join-Path $PSScriptRoot 'server_admin.py') restore --config $config --archive $backup --confirm RESTORE
        if ($LASTEXITCODE) { throw 'Automatic rollback failed; services stopped. Preserve both packages and the verified pre-upgrade backup.' }
        if ($firewall) { $firewall | Get-NetFirewallApplicationFilter | Set-NetFirewallApplicationFilter -Program (Join-Path $PSScriptRoot 'server-runtime\caddy.exe') | Out-Null }
        if ($wasRunning) { Start-Service GWAPBackend; Start-Service GWAPGateway }
        throw 'Upgrade failed; previous program, data and server identity restored.'
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $NewPackageRoot 'server_maintenance.ps1') -Action ConfigureBackup -DataRoot $DataRoot
    if ($LASTEXITCODE) { throw 'Upgrade healthy, but backup schedule must be updated to the new package.' }
    Write-Host ('[OK] Upgraded. Retain old package and backup: ' + $backup)
} catch { Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
finally { if ($operationLock) { $operationLock.Dispose() } }
