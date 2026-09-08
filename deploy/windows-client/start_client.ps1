[CmdletBinding()]
param(
    [string]$ServerUrl = '',
    [string]$CliCommand = '',
    [string]$StateDirectory = '',
    [string]$WorkingDirectory = '',
    [string]$PersonalCode = '',
    [switch]$EnrollOnly,
    [switch]$OpenBrowser,
    [switch]$Configure,
    [switch]$Check,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$previousCertificate = $env:NODE_EXTRA_CA_CERTS
$combinedCertificate = $null
$previousToken = $env:DEBUGPLATFORM_MCP_TOKEN
$previousDirectOrigin = $env:DEBUGPLATFORM_DIRECT_ORIGIN
$previousNoProxy = $env:NO_PROXY
. (Join-Path $PSScriptRoot 'client_onboarding.ps1')
try {
    if (-not $StateDirectory) { $StateDirectory = Join-Path $env:LOCALAPPDATA 'GWAPDebugClient\state' }
    if (-not $WorkingDirectory) { $WorkingDirectory = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'GWAPWorkspace' }
    $configFile = Join-Path $StateDirectory 'config.json'
    $deployment = Get-ClientDeployment
    if (-not $ServerUrl -and $deployment.server_url) { $ServerUrl = $deployment.server_url }
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
    $origin = $uri.GetLeftPart([UriPartial]::Authority)
    $simpleLogin = $deployment -and $deployment.simple_engineer_login
    if ($simpleLogin -and -not $DryRun) {
        $env:DEBUGPLATFORM_DIRECT_ORIGIN = $origin
        $env:NO_PROXY = (@($previousNoProxy, $uri.Host) | Where-Object { $_ }) -join ','
        Initialize-ClientCertificate $origin $StateDirectory
        $env:DEBUGPLATFORM_MCP_TOKEN = Initialize-ClientIdentity $origin $StateDirectory $PersonalCode -Configure:$Configure
        if ($EnrollOnly -or $Configure) { Write-Host '[OK] Installation and engineer registration complete.'; exit 0 }
        if ($OpenBrowser) {
            $response = Invoke-LauncherHttp POST ($origin + '/api/v1/auth/browser-ticket') @{'X-API-Key'=$env:DEBUGPLATFORM_MCP_TOKEN}
            if ($response.status -ne 200) { throw 'Unable to open the browser session. Please try again.' }
            $ticket = ($response.body | ConvertFrom-Json).ticket
            Start-Process ($origin + '/#gwap-login=' + [Uri]::EscapeDataString($ticket))
            exit 0
        }
    }
    if ($OpenBrowser -and -not $simpleLogin -and -not $DryRun) {
        Start-Process $origin
        exit 0
    }
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
        -WorkingDirectory $WorkingDirectory -Configure:($Configure -and -not $simpleLogin) -Check:$Check -DryRun:$DryRun
    exit $LASTEXITCODE
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    $env:DEBUGPLATFORM_MCP_TOKEN = $previousToken
    $env:DEBUGPLATFORM_DIRECT_ORIGIN = $previousDirectOrigin
    $env:NO_PROXY = $previousNoProxy
    $env:NODE_EXTRA_CA_CERTS = $previousCertificate
    if ($combinedCertificate -and [IO.File]::Exists($combinedCertificate)) { Remove-Item -LiteralPath $combinedCertificate -Force }
}
