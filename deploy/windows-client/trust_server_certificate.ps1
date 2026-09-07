[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CertificatePath,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedSha256,
    [string]$StateDirectory = '',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\file_hash.ps1')
try {
    if ((Get-Sha256Hex $CertificatePath) -ine $ExpectedSha256) { throw 'Certificate fingerprint mismatch; nothing was trusted.' }
    $pem = [IO.File]::ReadAllText([IO.Path]::GetFullPath($CertificatePath))
    if ($pem -match 'PRIVATE KEY' -or $pem -notmatch '(?s)^\s*-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+)\s*-----END CERTIFICATE-----\s*$') {
        throw 'Supply exactly one public PEM root certificate, never a private key.'
    }
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String(($Matches[1] -replace '\s','')))
    $ca = @($cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' } | Select-Object -First 1)
    if (-not $ca -or -not $ca[0].CertificateAuthority -or $cert.Subject -ne $cert.Issuer -or
        $cert.NotAfter -lt (Get-Date) -or $cert.NotBefore -gt (Get-Date)) { throw 'A currently valid self-issued CA certificate is required.' }
    if (-not $StateDirectory) { $StateDirectory = Join-Path $env:LOCALAPPDATA 'GWAPDebugClient\state' }
    if ($DryRun) {
        [ordered]@{ ok=$true; certificate_sha256=$ExpectedSha256; store='CurrentUser/Root'; changes_machine_store=$false; writes_files=$false } | ConvertTo-Json
        exit 0
    }
    [IO.Directory]::CreateDirectory($StateDirectory) | Out-Null
    $destination = Join-Path $StateDirectory 'server-root.crt'
    if ([IO.File]::Exists($destination) -and (Get-Sha256Hex $destination) -ine $ExpectedSha256) {
        throw 'A different root is already configured. Review the certificate rotation before replacing it.'
    }
    $store = [Security.Cryptography.X509Certificates.X509Store]::new('Root','CurrentUser')
    try { $store.Open('ReadWrite'); $store.Add($cert) } finally { $store.Close() }
    [IO.File]::WriteAllText($destination, $pem, [Text.UTF8Encoding]::new($false))
    Write-Host '[OK] The verified public CA is trusted for this Windows user. CodeAgent inherits it only in connector-launched sessions.'
    Write-Host ('[INFO] Certificate thumbprint (for review/removal in certmgr.msc): ' + $cert.Thumbprint)
} catch { Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
