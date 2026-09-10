[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$dataRoot = if ($env:GWAP_SERVER_DATA_ROOT) { $env:GWAP_SERVER_DATA_ROOT } else {
    Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'GWAPDebugServer'
}
$mutex = [Threading.Mutex]::new($false, 'Local\GWAPDebugServer.Install')
$owned = $false
try {
    try { $owned = $mutex.WaitOne([TimeSpan]::FromSeconds(120)) }
    catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { throw 'Another server installer is still running.' }
    & (Join-Path $PSScriptRoot 'runtime\python\python.exe') -B -s `
        (Join-Path $PSScriptRoot 'install_server_release.py') --payload $PSScriptRoot `
        --target $InstallRoot --data-root $dataRoot
    if ($LASTEXITCODE) { throw "Server release installation failed. Exit code: $LASTEXITCODE" }
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
