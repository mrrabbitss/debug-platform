. (Join-Path $PSScriptRoot 'scripts\file_hash.ps1')
. (Join-Path $PSScriptRoot 'scripts\codeagent_launcher_support.ps1')
. (Join-Path $PSScriptRoot 'scripts\codeagent_launcher_http.ps1')

function Get-ClientDeployment {
    $profile = Join-Path $PSScriptRoot 'deployment.json'
    if (-not [IO.File]::Exists($profile)) { return $null }
    $value = [IO.File]::ReadAllText($profile) | ConvertFrom-Json
    if ($value.schema_version -ne 1) { throw 'Unsupported client deployment profile.' }
    return $value
}

function Initialize-ClientCertificate {
    param([string]$ServerUrl, [string]$StateDirectory)
    [IO.Directory]::CreateDirectory($StateDirectory) | Out-Null
    $saved = Join-Path $StateDirectory 'server-root.crt'
    if (-not [IO.File]::Exists($saved)) {
        if (-not ('GwapCertificateBootstrap' -as [type])) {
            Add-Type -Path (Join-Path $PSScriptRoot 'client_bootstrap.cs')
        }
        Write-Host '[INFO] Configuring the server certificate...'
        $result = [GwapCertificateBootstrap]::GetPublicCertificate($ServerUrl + '/api/v1/auth/server-certificate') | ConvertFrom-Json
        if ($result.app -ne 'GW/AP Intelligent Debug Platform' -or $result.server_id -notmatch '^GWAP-[a-f0-9]{32}$' -or
            $result.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid server certificate response.' }
        $temporary = Join-Path $StateDirectory ('certificate-' + [guid]::NewGuid().ToString('N') + '.crt')
        try {
            [IO.File]::WriteAllText($temporary, $result.certificate, [Text.UTF8Encoding]::new($false))
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'trust_server_certificate.ps1') `
                -CertificatePath $temporary -ExpectedSha256 $result.sha256 -StateDirectory $StateDirectory
            if ($LASTEXITCODE -ne 0) { throw 'Server certificate configuration failed.' }
        } finally { if ([IO.File]::Exists($temporary)) { Remove-Item -LiteralPath $temporary } }
    }
}

function Initialize-ClientIdentity {
    param([string]$ServerUrl, [string]$StateDirectory, [string]$PersonalCode, [switch]$Configure)
    $endpoint = $ServerUrl + '/mcp'
    $tokenPath = Join-Path $StateDirectory 'token.dpapi'
    $identityPath = Join-Path $StateDirectory 'engineer.json'
    $token = ''
    if (-not $Configure -and -not $PersonalCode) {
        try { $token = Read-LauncherToken $tokenPath $endpoint } catch { }
        if ($token) {
            $response = Invoke-LauncherHttp GET ($ServerUrl + '/api/v1/system/me') @{'X-API-Key'=$token}
            if ($response.status -eq 200 -and ($response.body | ConvertFrom-Json).role -in @('ENGINEER', 'EXPERT')) { return $token }
            if ($response.status -notin @(200,401,403)) { throw 'Server is temporarily unavailable. Saved identity was retained.' }
        }
    }
    if (-not $PersonalCode) { $PersonalCode = (Read-Host '个人识别码（一位小写字母 + 八位数字，例如 a12345678）').Trim() }
    if ($PersonalCode -cnotmatch '^[a-z][0-9]{8}$') { throw '识别码格式不正确：一位小写字母加八位数字。' }
    $body = @{personal_code=$PersonalCode} | ConvertTo-Json -Compress
    $response = Invoke-LauncherHttp POST ($ServerUrl + '/api/v1/auth/engineer-login') @{} $body
    if ($response.status -ne 200) {
        $detail = try { ($response.body | ConvertFrom-Json).detail } catch { '' }
        throw "无法开通工程师身份（HTTP $($response.status)）：$detail"
    }
    $identity = $response.body | ConvertFrom-Json
    if ($identity.role -notin @('ENGINEER', 'EXPERT') -or $identity.personal_code -cne $PersonalCode -or -not $identity.token) {
        throw 'The server returned an unexpected identity.'
    }
    Save-LauncherToken $tokenPath $identity.token $endpoint
    Write-LauncherJson $identityPath @{personal_code=$PersonalCode; server_url=$ServerUrl; user_id=$identity.user_id}
    $configPath = Join-Path $StateDirectory 'config.json'
    $previous = Read-LauncherJson $configPath
    Write-LauncherJson $configPath @{schema_version=1; mcp_url=$endpoint; cli_command=$previous.cli_command}
    Write-Host ('[OK] 个人身份已保存：' + $PersonalCode)
    return [string]$identity.token
}
