[CmdletBinding()]
param([string]$PythonExe = '', [switch]$DryRun)

$ErrorActionPreference = 'Stop'

function Invoke-SetupCommand {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $FilePath" }
}

function Find-SetupPython {
    param([string]$Explicit)
    $candidates = @()
    if ($Explicit) { $candidates += $Explicit }
    else {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            try {
                $found = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $candidates += [string]($found | Select-Object -Last 1) }
            } catch { }
        }
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($python) { $candidates += $python.Source }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = & $candidate -c "import sys; print('%s.%s' % sys.version_info[:2])"
        if ($LASTEXITCODE -eq 0 -and ([string]($version | Select-Object -Last 1)).Trim() -eq '3.12') {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw 'Python 3.12 is required for model preparation. Install it, then run setup_server.bat again.'
}

function Test-SetupPackage {
    param([string]$Package)
    foreach ($relative in @('runtime/python/python.exe', 'portable_launcher.py', 'server_admin.py',
                            'server-runtime/caddy.exe', 'model-components.json', 'package-manifest.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Package $relative) -PathType Leaf)) {
            throw "Server directory is incomplete: $Package. It was not overwritten."
        }
    }
    $components = ([IO.File]::ReadAllText((Join-Path $Package 'model-components.json')) | ConvertFrom-Json).components
    if ('embedding' -notin @($components.task_type) -or 'reranker' -notin @($components.task_type)) {
        throw 'The server requires both Embedding and Reranker components.'
    }
    Push-Location -LiteralPath $Package
    try {
        Invoke-SetupCommand (Join-Path $Package 'runtime/python/python.exe') @('-B', '-s', '-c',
            "import sys; sys.path.insert(0,'.'); import portable_launcher as p; p.validate_layout(); p.verify_package_manifest()")
    } finally { Pop-Location }
}

function Invoke-ServerSetup {
    param([string]$RepositoryRoot, [string]$PythonExe, [switch]$DryRun)
    $root = [IO.Path]::GetFullPath($RepositoryRoot)
    $lan = Join-Path $root 'artifacts/lan'
    $target = Join-Path $lan 'server-pilot-20260907'
    $titles = @('Install locked dependencies', 'Prepare and verify local models',
                'Build the complete server runtime', 'Assemble HTTPS server')
    if ($DryRun) {
        [ordered]@{ steps=$titles; server_directory=$target; writes_files=$false;
                    installs_services=$false; existing_server_overwritten=$false } | ConvertTo-Json
        return
    }
    [IO.Directory]::CreateDirectory($lan) | Out-Null
    $lock = $null
    $savedPath = $env:PATH
    Push-Location -LiteralPath $root
    try {
        try { $lock = [IO.File]::Open((Join-Path $lan 'server-setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw 'Another server setup is running. Wait for it to finish.' }
        if (Test-Path -LiteralPath $target) {
            Test-SetupPackage $target
            Write-Host '[OK] Server is already prepared. Run scripts\start_lan_server.bat.'
            return
        }
        $python = Find-SetupPython $PythonExe
        foreach ($name in @('node.exe', 'npm.cmd', 'git.exe')) {
            if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Missing $name. Install Node.js 22.12+ and Git first." }
        }
        Invoke-SetupCommand 'node.exe' @('-e', "const [a,b]=process.versions.node.split('.').map(Number);process.exit((a===22&&b>=12)||a>22?0:1)")
        # Make the existing bootstrap use the selected Python, without changing global PATH.
        $env:PATH = (Split-Path -Parent $python) + [IO.Path]::PathSeparator + $savedPath
        $staging = Join-Path $lan ('server-building-' + [guid]::NewGuid().ToString('N'))
        $venvPython = Join-Path $root '.venv/Scripts/python.exe'
        $commands = @(
            @{ file=(Join-Path $root 'scripts/bootstrap_local.bat'); args=@('--no-pause') },
            @{ file=$python; args=@('-B', (Join-Path $root 'scripts/model-runtime/prepare_assets.py'), '--all') },
            @{ file='powershell.exe'; args=@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                (Join-Path $root 'scripts/build_windows_gguf_installer.ps1'), '-Version', '0.3.1', '-SkipSetupExe',
                '-PythonExe', $python, '-OutputRoot', 'artifacts/installer/release-build') },
            @{ file=$venvPython; args=@('-B', (Join-Path $root 'scripts/build_windows_server.py'), '--portable',
                'artifacts/installer/release-build/staging/debug-platform-windows-x64', '--output', $staging) }
        )
        for ($index=0; $index -lt $commands.Count; $index++) {
            Write-Host ("[{0}/4] {1}" -f ($index+1), $titles[$index])
            try { Invoke-SetupCommand $commands[$index].file $commands[$index].args }
            catch { throw ("Step {0} failed: {1}. Fix the error and run setup_server.bat again; downloaded cache is retained." -f ($index+1), $_.Exception.Message) }
        }
        Test-SetupPackage $staging
        # Only publish the complete, verified directory; never replace a prior server.
        [IO.Directory]::Move($staging, $target)
        Write-Host '[OK] Setup complete. Run scripts\start_lan_server.bat to start the server.'
    } finally {
        $env:PATH = $savedPath
        if ($lock) { $lock.Dispose() }
        Pop-Location
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-ServerSetup -RepositoryRoot (Split-Path -Parent $PSScriptRoot) -PythonExe $PythonExe -DryRun:$DryRun }
    catch { Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
}
