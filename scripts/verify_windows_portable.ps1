[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,
    [ValidateRange(0, 65535)]
    [int]$Port = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$packagePath = [System.IO.Path]::GetFullPath($PackageRoot)
$python = Join-Path $packagePath "runtime\python\python.exe"
$launcher = Join-Path $packagePath "portable_launcher.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Portable Python runtime is missing: $python"
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Portable launcher is missing: $launcher"
}

if ($Port -eq 0) {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $listener.Start()
    try {
        $Port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRoot = [System.IO.Path]::GetFullPath((Join-Path $tempBase (
    "gw-ap-portable-smoke-" + [System.Guid]::NewGuid().ToString("N")
)))
if (
    -not $tempRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not ([System.IO.Path]::GetFileName($tempRoot)).StartsWith(
        "gw-ap-portable-smoke-",
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "Refusing to create an unsafe portable smoke directory: $tempRoot"
}

$testData = Join-Path $tempRoot "data"
$testEnv = Join-Path $tempRoot ".env"
$stdoutLog = Join-Path $tempRoot "server.stdout.log"
$stderrLog = Join-Path $tempRoot "server.stderr.log"
$server = $null
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

try {
    Write-Host "[INFO] Running portable package self-check..."
    & $python -B -s $launcher `
        --check `
        --no-browser `
        --data-root $testData `
        --env-file $testEnv
    if ($LASTEXITCODE -ne 0) {
        throw "Portable package self-check failed with exit code $LASTEXITCODE."
    }

    $argumentLine = (
        '-B -s "portable_launcher.py" --no-browser --port {0} ' +
        '--data-root "{1}" --env-file "{2}"'
    ) -f $Port, $testData, $testEnv
    Write-Host "[INFO] Starting isolated portable smoke server on port $Port..."
    $server = Start-Process `
        -FilePath $python `
        -ArgumentList $argumentLine `
        -WorkingDirectory $packagePath `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    $baseUrl = "http://127.0.0.1:$Port"
    $deadline = (Get-Date).AddSeconds(120)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($server.HasExited) {
            throw "Portable server exited before becoming ready (exit $($server.ExitCode))."
        }
        try {
            $health = Invoke-RestMethod `
                -Uri "$baseUrl/api/v1/health/ready" `
                -TimeoutSec 3
            if ($health.ready -eq $true -and $health.status -eq "ready") {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "Portable server did not become ready within 120 seconds."
    }

    $rootResponse = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/" -TimeoutSec 10
    if ($rootResponse.StatusCode -ne 200 -or $rootResponse.Content -notmatch 'id="app"') {
        throw "Portable root did not return the built Vue frontend."
    }
    $routeResponse = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "$baseUrl/cases/portable-smoke-route" `
        -TimeoutSec 10
    if ($routeResponse.StatusCode -ne 200 -or $routeResponse.Content -notmatch 'id="app"') {
        throw "Portable SPA fallback did not return index.html."
    }
    $live = Invoke-RestMethod -Uri "$baseUrl/api/v1/health/live" -TimeoutSec 10
    if ($live.status -ne "alive") {
        throw "Portable API liveness check returned an unexpected response."
    }

    Write-Host "[OK] Portable backend readiness passed."
    Write-Host "[OK] Built frontend and Vue route fallback passed."
    Write-Host "[OK] Portable API liveness passed."
} catch {
    if (Test-Path -LiteralPath $stdoutLog -PathType Leaf) {
        Write-Host "[INFO] Portable server stdout (tail):"
        Get-Content -LiteralPath $stdoutLog -Tail 80
    }
    if (Test-Path -LiteralPath $stderrLog -PathType Leaf) {
        Write-Host "[INFO] Portable server stderr (tail):"
        Get-Content -LiteralPath $stderrLog -Tail 80
    }
    throw
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        $server.WaitForExit(10000) | Out-Null
    }
    if (
        (Test-Path -LiteralPath $tempRoot) -and
        $tempRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        ([System.IO.Path]::GetFileName($tempRoot)).StartsWith(
            "gw-ap-portable-smoke-",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
