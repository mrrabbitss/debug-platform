# Shared Windows PowerShell 5.1 helpers. No function executes during dot-sourcing.

function Test-LauncherInteractive {
    return [Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and
        -not (@([Environment]::GetCommandLineArgs()) -match '^-(NonInteractive|NonI)$')
}

function Read-LauncherJson {
    param([string]$Path)
    if (-not [IO.File]::Exists($Path)) { return $null }
    try { return [IO.File]::ReadAllText($Path) | ConvertFrom-Json }
    catch { throw "Invalid launcher config: $Path. Use -Configure to replace it." }
}

function Write-LauncherJson {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        if ([IO.File]::Exists($temporary)) { Remove-Item -LiteralPath $temporary -Force }
    }
}

function Get-LauncherCliPath {
    param([string]$Explicit, [string]$Saved, [bool]$AllowPrompt)
    $candidates = [Collections.Generic.List[string]]::new()
    if ($Explicit) { $candidates.Add($Explicit) }
    else {
        if ($Saved) { $candidates.Add($Saved) }
        $candidates.Add('codeagent')
        foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
            if (-not $base) { continue }
            foreach ($relative in @('CodeAgentCLI', 'CodeAgentCLI\bin')) {
                foreach ($name in @('codeagent.exe', 'codeagent.cmd', 'codeagent.bat', 'codeagent.ps1', 'codeagent')) {
                    $candidates.Add((Join-Path (Join-Path $base $relative) $name))
                }
            }
        }
    }
    foreach ($candidate in $candidates) {
        if ([IO.File]::Exists($candidate)) {
            $extension = [IO.Path]::GetExtension($candidate).ToLowerInvariant()
            if ($extension -in @('.exe', '.cmd', '.bat', '.ps1', '.com', '')) {
                return [IO.Path]::GetFullPath($candidate)
            }
        }
        if ([Management.Automation.WildcardPattern]::ContainsWildcardCharacters($candidate)) { continue }
        $command = Get-Command -Name $candidate -CommandType Application,ExternalScript -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($command -and [IO.File]::Exists($command.Source)) { return [IO.Path]::GetFullPath($command.Source) }
    }
    if ($AllowPrompt -and (Test-LauncherInteractive)) {
        Write-Host '[INFO] CodeAgent was not found. Enter its full .exe/.cmd/.bat/.ps1 path.'
        $answer = (Read-Host 'CodeAgent path (blank cancels)').Trim().Trim('"')
        if ($answer) { return Get-LauncherCliPath -Explicit $answer -Saved '' -AllowPrompt $false }
    }
    return $null
}

function Invoke-LauncherNative {
    param([string]$Path, [string[]]$Arguments = @(), [string]$LogPath = '', [switch]$Append)
    $previousPreference = $ErrorActionPreference
    # Windows PowerShell 5.1 represents redirected native stderr as error records.
    # A warning must not abort bootstrap/help before the actual exit code is read.
    $ErrorActionPreference = 'Continue'
    $previousExitCode = $global:LASTEXITCODE
    $global:LASTEXITCODE = $null
    $output = @()
    try {
        if ($LogPath) {
            if ($Append) { & $Path @Arguments *>> $LogPath; $invoked = $? }
            else { & $Path @Arguments *> $LogPath; $invoked = $? }
        } else {
            $output = & $Path @Arguments 2>&1
            $invoked = $?
        }
        $code = if ($null -ne $global:LASTEXITCODE) { [int]$global:LASTEXITCODE } elseif ($invoked) { 0 } else { 1 }
        return [pscustomobject]@{ exit_code = $code; output = ($output | Out-String) }
    } finally {
        $global:LASTEXITCODE = $previousExitCode
        $ErrorActionPreference = $previousPreference
    }
}

function Assert-LauncherCliFlags {
    param([string]$Path)
    try { $help = Invoke-LauncherNative -Path $Path -Arguments @('--help') }
    catch { throw 'CodeAgent --help failed. Check -CliCommand and its runtime dependencies.' }
    if ($help.exit_code -ne 0) { throw 'CodeAgent --help failed. Check -CliCommand and its runtime dependencies.' }
    foreach ($flag in @('--mcp-config', '--strict-mcp-config', '--append-system-prompt')) {
        if (-not $help.output.Contains($flag)) { throw "This CodeAgent build does not advertise $flag. A Claude-compatible build is required." }
    }
}

function Get-LauncherSetting {
    param([string]$Name, [string]$Default = '')
    $processValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($null -ne $processValue) { return $processValue }
    $envFile = Join-Path $script:RepoRoot '.env'
    if ($env:DEBUG_PLATFORM_ENV_FILE) { $envFile = [IO.Path]::GetFullPath($env:DEBUG_PLATFORM_ENV_FILE) }
    if ([IO.File]::Exists($envFile)) {
        $found = $null
        foreach ($line in [IO.File]::ReadLines($envFile)) {
            if ($line -match ('^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*=\s*(.*)$')) {
                $value = $Matches[1].Trim()
                if ($value -match '^"((?:\\.|[^"])*)"\s*(?:#.*)?$') {
                    $value = $Matches[1].Replace('\"', '"').Replace('\\', '\')
                } elseif ($value -match "^'([^']*)'\s*(?:#.*)?$") {
                    $value = $Matches[1]
                } else { $value = ($value -replace '\s+#.*$', '').Trim() }
                $found = $value
            }
        }
        if ($null -ne $found) { return $found }
    }
    return $Default
}

function Read-LauncherToken {
    param([string]$Path, [string]$ExpectedUrl)
    if (-not [IO.File]::Exists($Path)) { return '' }
    try {
        Add-Type -AssemblyName System.Security
        $encrypted = [Convert]::FromBase64String([IO.File]::ReadAllText($Path).Trim())
        $entropy = [Text.Encoding]::UTF8.GetBytes('gwap-codeagent-launcher-v1')
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        $record = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
        if ($record.endpoint -cne $ExpectedUrl) { return '' }
        return [string]$record.token
    } catch { throw 'Saved token cannot be decrypted by this Windows account. Use -Configure with a new access token.' }
}

function Save-LauncherToken {
    param([string]$Path, [string]$Token, [string]$Endpoint)
    Add-Type -AssemblyName System.Security
    $entropy = [Text.Encoding]::UTF8.GetBytes('gwap-codeagent-launcher-v1')
    $record = @{ token = $Token; endpoint = $Endpoint } | ConvertTo-Json -Compress
    $encrypted = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($record),
        $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllText($Path, [Convert]::ToBase64String($encrypted), [Text.Encoding]::ASCII)
}

function New-LauncherToken {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Request-LauncherToken {
    if (-not (Test-LauncherInteractive)) {
        throw 'Set DEBUGPLATFORM_MCP_TOKEN before -Configure/-Check, or configure once in an interactive terminal.'
    }
    $credential = Get-Credential -UserName 'debug-platform' -Message 'Enter a Debug Platform access token (not a model API key).'
    if ($null -eq $credential) { throw 'Access token entry was cancelled.' }
    return $credential.GetNetworkCredential().Password
}

function Test-LauncherPortFree {
    param([int]$Port)
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try { $listener.Start(); return $true }
    catch [Net.Sockets.SocketException] { return $false }
    finally { $listener.Stop() }
}

function ConvertTo-LauncherArgument {
    param([string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Get-LauncherSha256 {
    param([string]$Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '') }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}

function Initialize-LauncherBackend {
    param([string]$StateRoot)
    $python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
    $log = Join-Path $StateRoot 'backend-bootstrap.log'
    if (-not [IO.File]::Exists($python)) {
        $candidate = $null
        $prefix = @()
        foreach ($name in @('python.exe', 'py.exe')) {
            $found = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $found) { continue }
            $arguments = if ($name -eq 'py.exe') { @('-3') } else { @() }
            $probe = Invoke-LauncherNative -Path $found.Source -Arguments ($arguments + @('-c',
                'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)')) -LogPath $log
            if ($probe.exit_code -eq 0) { $candidate = $found; $prefix = $arguments; break }
        }
        if (-not $candidate) { throw 'Python 3.11+ is required for a local backend. Install Python or use an existing remote -McpUrl.' }
        Write-Host '[INFO] Preparing the backend virtual environment (no Node/npm required)...'
        $created = Invoke-LauncherNative -Path $candidate.Source -Arguments ($prefix + @('-m', 'venv',
            (Join-Path $script:RepoRoot '.venv'))) -LogPath $log -Append
        if ($created.exit_code -ne 0) { throw "Backend virtual environment creation failed. Details: $log" }
    }
    $version = Invoke-LauncherNative -Path $python -Arguments @('-c',
        'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)') -LogPath $log
    if ($version.exit_code -ne 0) { throw "The repository .venv requires Python 3.11+. Details: $log" }
    $backendHashes = @('backend\pyproject.toml', 'backend\uv.lock', 'backend\constraints.lock') | ForEach-Object {
        Get-LauncherSha256 (Join-Path $script:RepoRoot $_)
    }
    $fingerprint = $backendHashes -join '-'
    $stampFile = Join-Path $StateRoot 'backend-dependencies.sha256'
    $stamp = if ([IO.File]::Exists($stampFile)) { [IO.File]::ReadAllText($stampFile).Trim() } else { '' }
    $fullStampFile = Join-Path $script:RepoRoot '.local_dependency_stamp'
    $fullStamp = if ([IO.File]::Exists($fullStampFile)) { [IO.File]::ReadAllText($fullStampFile).Trim() } else { '' }
    $frontendLock = Join-Path $script:RepoRoot 'frontend\package-lock.json'
    $expectedFull = $fingerprint + '-' + (Get-LauncherSha256 $frontendLock)
    if ($stamp -ne $fingerprint -and $fullStamp -ne $expectedFull) {
        Write-Host '[INFO] Installing locked backend dependencies (no frontend build)...'
        $constraints = Join-Path $script:RepoRoot 'backend\constraints.lock'
        $prepared = Invoke-LauncherNative -Path $python -Arguments @('-m', 'pip', 'install', '--upgrade',
            '--constraint', $constraints, 'pip') -LogPath $log -Append
        if ($prepared.exit_code -ne 0) { throw "Backend pip preparation failed. Details: $log" }
        $installed = Invoke-LauncherNative -Path $python -Arguments @('-m', 'pip', 'install', '--constraint',
            $constraints, '-e', (Join-Path $script:RepoRoot 'backend')) -LogPath $log -Append
        if ($installed.exit_code -ne 0) { throw "Backend dependency installation failed. Details: $log" }
    }
    $imported = Invoke-LauncherNative -Path $python -Arguments @('-c', 'import fastapi, uvicorn, mcp, app') -LogPath $log -Append
    if ($imported.exit_code -ne 0) { throw "Backend import failed. Details: $log" }
    [IO.File]::WriteAllText($stampFile, $fingerprint, [Text.Encoding]::ASCII)
    return $python
}

function Start-LauncherBackend {
    param([string]$Python, [Uri]$Uri, [string]$Token, [string]$StateRoot, [bool]$InjectToken)
    $overrides = @{ MCP_PUBLIC_BASE_URL = $Uri.GetLeftPart([UriPartial]::Authority) }
    if ($InjectToken) { $overrides.MCP_BEARER_TOKEN = $Token }
    $frontend = Join-Path $script:RepoRoot 'frontend\dist'
    if ([IO.File]::Exists((Join-Path $frontend 'index.html')) -and -not (Get-LauncherSetting 'STATIC_FRONTEND_ROOT')) {
        $overrides.STATIC_FRONTEND_ROOT = $frontend
    }
    $old = @{}
    foreach ($name in $overrides.Keys) {
        $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
    }
    $runId = [guid]::NewGuid().ToString('N')
    try {
        $arguments = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$Uri.Port) |
            ForEach-Object { ConvertTo-LauncherArgument $_ }
        return Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory (Join-Path $script:RepoRoot 'backend') `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $StateRoot "backend-$runId.out.log") `
            -RedirectStandardError (Join-Path $StateRoot "backend-$runId.err.log")
    } finally {
        foreach ($name in $old.Keys) { [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process') }
    }
}
