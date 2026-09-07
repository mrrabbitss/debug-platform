[CmdletBinding()]
param(
    [string]$ServerUrl = '',
    [string]$CliCommand = '',
    [string]$StateDirectory = '',
    [string]$WorkingDirectory = '',
    [switch]$Configure,
    [switch]$Check,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$previousCertificate = $env:NODE_EXTRA_CA_CERTS
$combinedCertificate = $null
try {
    if (-not $StateDirectory) { $StateDirectory = Join-Path $env:LOCALAPPDATA 'GWAPDebugClient\state' }
    if (-not $WorkingDirectory) { $WorkingDirectory = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'GWAPWorkspace' }
    $configFile = Join-Path $StateDirectory 'config.json'
    if (-not $ServerUrl -and [IO.File]::Exists($configFile)) {
        $ServerUrl = ([IO.File]::ReadAllText($configFile) | ConvertFrom-Json).mcp_url
    }
    if (-not $ServerUrl) {
        if ($DryRun -or $Check -or [Console]::IsInputRedirected) {
            throw 'Set -ServerUrl https://your-server on first use.'
        }
        $ServerUrl = (Read-Host 'Platform HTTPS address').Trim()
    }
    $uri = $null
    if (-not [Uri]::TryCreate($ServerUrl, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or
        $uri.AbsolutePath.TrimEnd('/') -notin @('', '/mcp')) {
        throw 'Use an HTTPS server origin or its /mcp URL without credentials.'
    }
    $mcpUrl = $uri.GetLeftPart([UriPartial]::Authority) + '/mcp'
    $certificate = Join-Path $StateDirectory 'server-root.crt'
    if (-not $DryRun -and [IO.File]::Exists($certificate)) {
        if ($previousCertificate -and [IO.Path]::GetFullPath($previousCertificate) -ne [IO.Path]::GetFullPath($certificate)) {
            if (-not [IO.File]::Exists($previousCertificate)) { throw 'The existing NODE_EXTRA_CA_CERTS file is unavailable; it was not replaced.' }
            $combinedCertificate = Join-Path $StateDirectory ('ca-session-' + [guid]::NewGuid().ToString('N') + '.pem')
            [IO.File]::WriteAllText($combinedCertificate,
                ([IO.File]::ReadAllText($previousCertificate) + "`n" + [IO.File]::ReadAllText($certificate)), [Text.UTF8Encoding]::new($false))
            $env:NODE_EXTRA_CA_CERTS = $combinedCertificate
        } else { $env:NODE_EXTRA_CA_CERTS = $certificate }
    }
    if (-not [IO.Directory]::Exists($WorkingDirectory)) {
        if ($DryRun) { $WorkingDirectory = (Get-Location).Path }
        else { [IO.Directory]::CreateDirectory($WorkingDirectory) | Out-Null }
    }
    # All client state is per user; ConnectOnly unconditionally disables backend bootstrap.
    & (Join-Path $PSScriptRoot 'scripts\start_codeagent.ps1') -ConnectOnly `
        -McpUrl $mcpUrl -CliCommand $CliCommand -StateDirectory $StateDirectory `
        -WorkingDirectory $WorkingDirectory -Configure:$Configure -Check:$Check -DryRun:$DryRun
    exit $LASTEXITCODE
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    $env:NODE_EXTRA_CA_CERTS = $previousCertificate
    if ($combinedCertificate -and [IO.File]::Exists($combinedCertificate)) { Remove-Item -LiteralPath $combinedCertificate -Force }
}
