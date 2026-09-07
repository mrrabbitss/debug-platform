[CmdletBinding()]
param(
    [string]$PublicUrl = '',
    [string]$DataRoot = '',
    [string]$Certificate = '',
    [string]$CertificateKey = '',
    [switch]$OpenFirewall,
    [switch]$NoStart,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
try {
    if (-not $DataRoot) { $DataRoot = Join-Path $env:ProgramData 'GWAPDebugServer' }
    $DataRoot = [IO.Path]::GetFullPath($DataRoot)
    if (-not $PublicUrl) {
        if ($DryRun -or [Console]::IsInputRedirected) { throw 'Specify -PublicUrl https://your-server' }
        $PublicUrl = (Read-Host 'Server HTTPS address (DNS name or fixed IP)').Trim()
    }
    $uri = [Uri]$PublicUrl
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or $uri.UserInfo -or
        $uri.Query -or $uri.Fragment -or $uri.AbsolutePath.TrimEnd('/')) { throw 'Use an HTTPS origin without a path.' }
    if ($DataRoot -eq [IO.Path]::GetPathRoot($DataRoot) -or $DataRoot -eq $env:ProgramData -or $DataRoot -eq $PSScriptRoot) {
        throw 'Use a dedicated server data directory.'
    }
    $python = Join-Path $PSScriptRoot 'runtime\python\python.exe'
    $caddy = Join-Path $PSScriptRoot 'server-runtime\caddy.exe'
    $wrapper = Join-Path $PSScriptRoot 'server-runtime\WinSW-x64.exe'
    foreach ($path in @($python, $caddy, $wrapper)) { if (-not [IO.File]::Exists($path)) { throw 'The complete server package is required.' } }
    if ((Get-Service GWAPBackend,GWAPGateway -ErrorAction SilentlyContinue)) { throw 'Platform services already exist. Use server maintenance to upgrade; nothing was replaced.' }
    if ([IO.Directory]::Exists($DataRoot) -and (Get-ChildItem -LiteralPath $DataRoot -Force | Select-Object -First 1)) {
        throw 'Use a new empty server data directory; existing data needs the restore/migration workflow.'
    }
    if ($DryRun) {
        [ordered]@{ ok=$true; public_url=$PublicUrl; data_root=$DataRoot; account='LocalService'; backend_bind='127.0.0.1';
            firewall_profiles=@('Domain','Private'); firewall_requested=[bool]$OpenFirewall; writes_files=$false } | ConvertTo-Json
        exit 0
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run Install Server.bat as administrator.' }
    [IO.Directory]::CreateDirectory($DataRoot) | Out-Null
    # Fixed well-known SIDs work with non-English Windows installations.
    & icacls.exe $DataRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' | Out-Null
    if ($LASTEXITCODE) { throw 'Could not protect the server data directory.' }
    $arguments = @('-B','-s',(Join-Path $PSScriptRoot 'server_admin.py'),'configure','--public-url',$PublicUrl,'--data-root',$DataRoot)
    if ($Certificate -or $CertificateKey) { $arguments += @('--certificate',$Certificate,'--certificate-key',$CertificateKey) }
    & $python @arguments
    if ($LASTEXITCODE) { throw 'Server configuration failed. Review the output; no service was started.' }
    $config = Join-Path $DataRoot 'config\server.json'
    & $caddy validate --config (Join-Path $DataRoot 'config\caddy.json')
    if ($LASTEXITCODE) { throw 'HTTPS gateway configuration is invalid.' }
    & $python -B -s (Join-Path $PSScriptRoot 'server_admin.py') initialize-admin --config $config
    if ($LASTEXITCODE) { throw 'Administrator initialization failed.' }
    $tokenPath = Join-Path $DataRoot 'config\bootstrap-token.txt'
    & icacls.exe $tokenPath /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE) { throw 'Could not protect the bootstrap credential.' }
    foreach ($name in @('GWAPBackend','GWAPGateway')) {
        $servicePath = Join-Path $DataRoot "services\$name.exe"
        Copy-Item -LiteralPath $wrapper -Destination $servicePath
        & $servicePath install
        if ($LASTEXITCODE) { throw "Failed to install $name. Existing data was preserved; inspect Windows Services before retrying." }
    }
    if ($OpenFirewall) {
        New-NetFirewallRule -Name 'GWAPDebugHTTPS' -DisplayName 'GW/AP HTTPS (company LAN)' -Direction Inbound `
            -Protocol TCP -LocalPort $uri.Port -Action Allow -Program $caddy -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
    }
    if (-not $NoStart) { Start-Service GWAPBackend; Start-Service GWAPGateway }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'server_maintenance.ps1') -Action ConfigureBackup -DataRoot $DataRoot
    if ($LASTEXITCODE) { throw 'Server installed but automatic backup configuration failed; configure backup before using real data.' }
    Write-Host ('[OK] Server installed. Address: ' + $PublicUrl)
    Write-Host ('[INFO] One-day administrator token: ' + $tokenPath + ' (open locally, then create personal accounts).')
    Write-Host '[INFO] Internal-CA mode requires clients to trust the exported public root certificate. Never copy its private key.'
} catch { Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
