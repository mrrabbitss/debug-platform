[CmdletBinding()]
param(
    [ValidateSet('Status','Start','Stop','Backup','Restore','ExportCertificate','ConfigureBackup')][string]$Action = 'Status',
    [string]$DataRoot = '',
    [string]$Archive = '',
    [string]$Output = '',
    [string]$Confirm = '',
    [string]$BackupDirectory = '',
    [string]$OffsiteDirectory = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'file_hash.ps1')
try {
    if (-not $DataRoot) { $DataRoot = Join-Path $env:ProgramData 'GWAPDebugServer' }
    $DataRoot = [IO.Path]::GetFullPath($DataRoot)
    $config = Join-Path $DataRoot 'config\server.json'
    if (-not [IO.File]::Exists($config)) { throw 'Server configuration not found.' }
    $profile = [IO.File]::ReadAllText($config) | ConvertFrom-Json
    $policyPath = Join-Path $DataRoot 'config\backup-policy.json'
    if ($Action -eq 'ConfigureBackup') {
        if ([IO.File]::Exists($policyPath)) {
            $existingPolicy = [IO.File]::ReadAllText($policyPath) | ConvertFrom-Json
            if (-not $PSBoundParameters.ContainsKey('BackupDirectory')) { $BackupDirectory = $existingPolicy.directory }
            if (-not $PSBoundParameters.ContainsKey('OffsiteDirectory')) { $OffsiteDirectory = $existingPolicy.offsite_directory }
        }
        if (-not $BackupDirectory) { $BackupDirectory = Join-Path $DataRoot 'backups' }
        $BackupDirectory = [IO.Path]::GetFullPath($BackupDirectory)
        [IO.Directory]::CreateDirectory($BackupDirectory) | Out-Null
        $policy = [ordered]@{ directory=$BackupDirectory; offsite_directory=$OffsiteDirectory; schedule='02:00'; automatic_deletion=$false }
        [IO.File]::WriteAllText($policyPath, ($policy | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        $scriptPath = Join-Path $PSScriptRoot 'server_maintenance.ps1'
        $taskArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $scriptPath + '" -Action Backup -DataRoot "' + $DataRoot + '"'
        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArgs
        $trigger = New-ScheduledTaskTrigger -Daily -At '02:00'
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
        Register-ScheduledTask -TaskName 'GWAPServerBackup' -Action $taskAction -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
        Write-Host '[OK] Daily 02:00 verified full backup configured. Backup files are never automatically deleted. Offsite copies use the service account permissions.'
        exit 0
    }
    if ($Action -eq 'Status') {
        Get-Service GWAPBackend,GWAPGateway | Select-Object Name,Status,StartType
        Write-Host ('[INFO] Public address: ' + $profile.public_url)
        exit 0
    }
    if ($Action -eq 'ExportCertificate') {
        if (-not $Output) { throw 'Set -Output to a new public .crt file.' }
        if ([IO.File]::Exists($Output)) { throw 'Certificate output already exists.' }
        $rootCert = Join-Path $DataRoot 'gateway\pki\authorities\local\root.crt'
        if (-not [IO.File]::Exists($rootCert)) { throw 'The internal public root certificate is not ready; start HTTPS first or use the corporate certificate process.' }
        Copy-Item -LiteralPath $rootCert -Destination $Output
        Write-Host ('SHA256: ' + (Get-Sha256Hex $Output))
        Write-Host '[INFO] Transfer only this public certificate; verify the fingerprint through an independent channel.'
        exit 0
    }
    if ($Action -eq 'Start') { Start-Service GWAPBackend; Start-Service GWAPGateway; exit 0 }
    if ($Action -eq 'Stop') { Stop-Service GWAPGateway; Stop-Service GWAPBackend; exit 0 }
    if (-not $Archive -and $Action -eq 'Backup') {
        $backupRoot = Join-Path $DataRoot 'backups'
        if ([IO.File]::Exists($policyPath)) {
            $backupPolicy = [IO.File]::ReadAllText($policyPath) | ConvertFrom-Json
            $backupRoot = $backupPolicy.directory
            $OffsiteDirectory = $backupPolicy.offsite_directory
        }
        $Archive = Join-Path $backupRoot ('server-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8) + '.zip')
    }
    if (-not $Archive) { throw 'Set -Archive to a backup ZIP path.' }
    if ($Action -eq 'Restore' -and $Confirm -cne 'RESTORE') { throw 'Restore requires -Confirm RESTORE and preserves previous data in the rollback directory.' }
    $wasRunning = (Get-Service GWAPBackend).Status -eq 'Running'
    $maintenanceLock = [IO.File]::Open((Join-Path $DataRoot 'maintenance.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    Stop-Service GWAPGateway
    Stop-Service GWAPBackend
    $arguments = @('-B','-s',(Join-Path $PSScriptRoot 'server_admin.py'),$Action.ToLowerInvariant(),'--config',$config,'--archive',[IO.Path]::GetFullPath($Archive))
    if ($Action -eq 'Restore') { $arguments += @('--confirm','RESTORE') }
    & (Join-Path $PSScriptRoot 'runtime\python\python.exe') @arguments
    if ($LASTEXITCODE) {
        if ($Action -eq 'Backup' -and $wasRunning) { Start-Service GWAPBackend; Start-Service GWAPGateway }
        throw 'Maintenance failed; backup failure restores original service state, restore failure keeps services stopped. Previous data was preserved.'
    }
    if ($wasRunning) { Start-Service GWAPBackend; Start-Service GWAPGateway }
    if ($Action -eq 'Backup' -and $OffsiteDirectory) {
        [IO.Directory]::CreateDirectory($OffsiteDirectory) | Out-Null
        $destination = Join-Path $OffsiteDirectory ([IO.Path]::GetFileName($Archive))
        if ([IO.File]::Exists($destination)) { throw 'Offsite archive already exists; local verified backup retained.' }
        $partial = $destination + '.partial'
        Copy-Item -LiteralPath $Archive -Destination $partial -ErrorAction Stop
        if ((Get-Sha256Hex $partial) -ne (Get-Sha256Hex $Archive)) { throw 'Offsite backup hash mismatch; local verified backup retained.' }
        Move-Item -LiteralPath $partial -Destination $destination
    }
    Write-Host '[OK] Verified complete server backup operation finished; includes knowledge, cases, files, configuration and TLS identity. Retain the matching program package.'
} catch { Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
finally { if ($maintenanceLock) { $maintenanceLock.Dispose() } }
