[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Check,
    [switch]$Configure,
    [string]$CliCommand = '',
    [string]$McpUrl = '',
    [string]$StateDirectory = '',
    [ValidateRange(10, 600)]
    [int]$BackendStartupTimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
$script:RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'codeagent_launcher_support.ps1')
. (Join-Path $PSScriptRoot 'codeagent_launcher_http.ps1')
$ownedBackend = $null
$sessionFile = $null
$exitCode = 0
$previousLocation = Get-Location
$previousClientEnvironment = @{}

try {
    if (-not $StateDirectory) { $StateDirectory = Join-Path $script:RepoRoot '.agent-runtime\codeagent-launcher' }
    $stateRoot = [IO.Path]::GetFullPath($StateDirectory)
    $configPath = Join-Path $stateRoot 'config.json'
    $tokenPath = Join-Path $stateRoot 'token.dpapi'
    $config = $null
    try { $config = Read-LauncherJson $configPath } catch { if (-not $Configure) { throw } }
    if (-not $McpUrl) { $McpUrl = if ($config.mcp_url) { $config.mcp_url } else { 'http://127.0.0.1:8000/mcp' } }
    $mcpUri = $null
    if (-not [Uri]::TryCreate($McpUrl, [UriKind]::Absolute, [ref]$mcpUri) -or
            $mcpUri.Scheme -notin @('http', 'https') -or $mcpUri.UserInfo -or $mcpUri.Query -or $mcpUri.Fragment -or
            -not $mcpUri.AbsolutePath.TrimEnd('/').EndsWith('/mcp', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Use a complete HTTP(S) /mcp URL without credentials, query or fragment.'
    }
    if (-not $mcpUri.IsLoopback -and $mcpUri.Scheme -ne 'https') { throw 'A remote MCP service must use HTTPS.' }
    $builder = [UriBuilder]::new($mcpUri)
    $builder.Path = $builder.Path.TrimEnd('/')
    if ($builder.Scheme -eq 'http' -and $mcpUri.IsLoopback -and $builder.Path -eq '/mcp') {
        if ($builder.Host -notin @('127.0.0.1', 'localhost')) {
            throw 'Local auto-start supports 127.0.0.1/localhost only. Use http://127.0.0.1:<port>/mcp.'
        }
        $builder.Host = '127.0.0.1'
    }
    $mcpUri = $builder.Uri
    $localBackend = $mcpUri.IsLoopback -and $mcpUri.Scheme -eq 'http' -and $mcpUri.AbsolutePath -eq '/mcp'
    if ($localBackend -and $mcpUri.Port -lt 1024) { throw 'A local backend requires a port between 1024 and 65535.' }
    $skillPath = Join-Path $script:RepoRoot '.claude\skills\gw-ap-debug\SKILL.md'
    if (-not [IO.File]::Exists($skillPath)) { throw "The current project Skill is missing: $skillPath" }
    $resolvedCli = Get-LauncherCliPath -Explicit $CliCommand -Saved $config.cli_command -AllowPrompt (-not $DryRun -and -not $Check)
    if ($DryRun) {
        [ordered]@{
            dry_run = $true; repository_root = $script:RepoRoot; state_directory = $stateRoot
            mcp_url = $mcpUri.AbsoluteUri; local_backend_start_allowed = [bool]$localBackend
            cli_command = $resolvedCli; cli_available = [bool]$resolvedCli; skill_path = $skillPath
            token_persistence = 'Windows DPAPI CurrentUser'; changes_global_cli_config = $false
            starts_processes = $false; writes_files = $false
        } | ConvertTo-Json -Depth 5
        exit 0
    }
    if (-not $Check) {
        if (-not $resolvedCli) { throw 'CodeAgent was not found. Run start_codeagent.bat -Configure -CliCommand "C:\path\codeagent.cmd" or install it on PATH.' }
        Assert-LauncherCliFlags $resolvedCli
    }
    $token = $env:DEBUGPLATFORM_MCP_TOKEN
    $injectToken = $false
    $localPortInitiallyFree = $localBackend -and (Test-LauncherPortFree $mcpUri.Port)
    if ($localBackend) {
        $authMode = Get-LauncherSetting 'AUTH_MODE' 'local'
        $apiKey = Get-LauncherSetting 'API_KEY'
        $configuredMcpToken = Get-LauncherSetting 'MCP_BEARER_TOKEN'
        $legacyAllowed = (Get-LauncherSetting 'AUTH_ALLOW_LEGACY_ADMIN' 'true') -match '^(true|1|yes|on)$'
        if (-not $token -and $apiKey -and ($authMode -in @('local', 'api_key') -or $legacyAllowed)) { $token = $apiKey }
        if (-not $token -and $authMode -eq 'local' -and -not $apiKey -and $configuredMcpToken) { $token = $configuredMcpToken }
    }
    if (-not $token -and $Configure -and (-not $localBackend -or -not $localPortInitiallyFree)) {
        $token = Request-LauncherToken
    }
    if (-not $token -and (-not $config.mcp_url -or $config.mcp_url -ceq $mcpUri.AbsoluteUri)) {
        try { $token = Read-LauncherToken $tokenPath $mcpUri.AbsoluteUri } catch { if (-not $Configure) { throw } }
    }
    if (-not $token) {
        if ($localBackend -and $authMode -eq 'local' -and -not $apiKey -and -not $configuredMcpToken) { $token = New-LauncherToken }
        else { $token = Request-LauncherToken }
    }
    if ([string]::IsNullOrWhiteSpace($token)) { throw 'A Debug Platform access token is required.' }
    if ($localBackend -and $authMode -eq 'local' -and -not $apiKey -and -not $configuredMcpToken) { $injectToken = $true }
    [IO.Directory]::CreateDirectory($stateRoot) | Out-Null
    $backendReused = $true
    if ($localBackend -and (Test-LauncherPortFree $mcpUri.Port)) {
        $python = Initialize-LauncherBackend $stateRoot
        # Check again after bootstrap: never race an existing listener by terminating it.
        if (Test-LauncherPortFree $mcpUri.Port) {
            $ownedBackend = Start-LauncherBackend $python $mcpUri $token $stateRoot $injectToken
            $backendReused = $false
            Write-Host "[INFO] Started a session-owned backend on port $($mcpUri.Port)."
            $deadline = (Get-Date).AddSeconds($BackendStartupTimeoutSeconds)
            $ready = $false
            while ((Get-Date) -lt $deadline) {
                if ($ownedBackend.HasExited) { throw "Backend startup failed. Review the managed logs in $stateRoot." }
                try {
                    $health = Invoke-LauncherHttp GET ($mcpUri.GetLeftPart([UriPartial]::Authority) + '/api/v1/health/ready') @{} '' 2
                    if ($health.status -eq 200 -and ($health.body | ConvertFrom-Json).ready) { $ready = $true; break }
                } catch { }
                Start-Sleep -Milliseconds 350
            }
            if (-not $ready) { throw "Backend readiness timed out. Review the managed logs in $stateRoot." }
        }
    }
    try { $status = Test-LauncherConnection $mcpUri $token }
    catch {
        if ($backendReused -and $localBackend) {
            throw ($_.Exception.Message + ' The existing backend was left running. If it has no MCP token configured, stop that backend yourself and restart through start_codeagent.bat; otherwise use -Configure with its REST/MCP access token.')
        }
        throw
    }
    Save-LauncherToken $tokenPath $token $mcpUri.AbsoluteUri
    $savedCli = if ($resolvedCli) { $resolvedCli } else { $config.cli_command }
    Write-LauncherJson $configPath ([ordered]@{ schema_version = 1; cli_command = $savedCli; mcp_url = $mcpUri.AbsoluteUri })
    $summary = [ordered]@{
        ok = $true; mcp_url = $mcpUri.AbsoluteUri; backend_reused = [bool]$backendReused
        inference_owner = $status.inference_owner; backend_chat_allowed = $status.backend_chat_allowed
        backend_chat_calls = $status.backend_chat_calls; authenticated_role = $status.authenticated_role
        cli_available = [bool]$resolvedCli; skill_path = $skillPath
    }
    if ($Check -or $Configure) { $summary | ConvertTo-Json -Depth 5; exit 0 }
    $sessionRoot = Join-Path $stateRoot 'sessions'
    [IO.Directory]::CreateDirectory($sessionRoot) | Out-Null
    $sessionFile = Join-Path $sessionRoot ('session-' + [guid]::NewGuid().ToString('N') + '.json')
    Write-LauncherJson $sessionFile @{ mcpServers = @{ 'gw-ap-debug' = @{
        type = 'http'; url = $mcpUri.AbsoluteUri; headers = @{ Authorization = 'Bearer ${DEBUGPLATFORM_MCP_TOKEN}' }
    } } }
    foreach ($name in @('DEBUGPLATFORM_MCP_URL', 'DEBUGPLATFORM_MCP_TOKEN')) {
        $previousClientEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $env:DEBUGPLATFORM_MCP_URL = $mcpUri.AbsoluteUri
    $env:DEBUGPLATFORM_MCP_TOKEN = $token
    $prompt = "For GW/AP diagnosis and Markdown knowledge routing, explicitly read and follow the current project Skill at: $skillPath . Use its real directory for helper scripts. Do not substitute another same-named user Skill. The active CodeAgent model owns reasoning; use the gw-ap-debug MCP evidence plane and verify debug_status before work."
    $cliArguments = @('--mcp-config', $sessionFile, '--strict-mcp-config', '--append-system-prompt', $prompt)
    Set-Location -LiteralPath $script:RepoRoot
    Write-Host '[OK] REST and MCP verified. Starting CodeAgent with the current project Skill.'
    & $resolvedCli @cliArguments
    if ($null -ne $LASTEXITCODE) { $exitCode = $LASTEXITCODE }
} catch {
    Write-Host ("[ERROR] " + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
} finally {
    foreach ($name in $previousClientEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousClientEnvironment[$name], 'Process')
    }
    if ($sessionFile -and [IO.File]::Exists($sessionFile)) { Remove-Item -LiteralPath $sessionFile -Force }
    if ($ownedBackend) {
        try {
            if (-not $ownedBackend.HasExited) { Stop-Process -Id $ownedBackend.Id -Force -ErrorAction Stop }
        } catch { Write-Host '[WARN] Could not stop the session-owned backend; inspect its managed log/process.' }
        $ownedBackend.Dispose()
    }
    Set-Location -LiteralPath $previousLocation.Path
}
exit $exitCode
