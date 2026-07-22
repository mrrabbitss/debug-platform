param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $RepoRoot "local_doctor_result.txt"
}

$Results = [System.Collections.Generic.List[string]]::new()
$BlockingFailures = 0

function Add-Check {
    param(
        [ValidateSet("OK", "WARN", "FAIL")][string]$Status,
        [string]$Name,
        [string]$Detail
    )
    $script:Results.Add(("[{0}] {1}: {2}" -f $Status, $Name, $Detail))
    if ($Status -eq "FAIL") {
        $script:BlockingFailures++
    }
}

function Invoke-Python {
    param([string[]]$Arguments)
    $pythonArguments = @($script:PythonPrefix) + @($Arguments)
    $output = & $script:PythonExe $pythonArguments 2>&1 | Out-String
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output.Trim() }
}

function Test-PortAvailable {
    param([int]$Port)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    } catch [System.Net.Sockets.SocketException] {
        return $false
    } finally {
        $listener.Stop()
    }
}

$Results.Add("GW/AP Debug Platform - Windows local doctor")
$Results.Add(("CheckedAt: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz")))
$Results.Add(("Repository: {0}" -f $RepoRoot))
$Results.Add("")

$windowsVersion = [Environment]::OSVersion.Version
$windowsName = if ($windowsVersion.Build -ge 22000) { "Windows 11" } else { "Windows" }
$windowsDetail = "{0}, kernel {1}.{2}, build {3}, 64-bit={4}" -f @(
    $windowsName,
    $windowsVersion.Major,
    $windowsVersion.Minor,
    $windowsVersion.Build,
    [Environment]::Is64BitOperatingSystem
)
Add-Check "OK" "Windows" $windowsDetail

$requiredFiles = @(
    "backend\pyproject.toml",
    "backend\alembic.ini",
    "frontend\package.json",
    "frontend\package-lock.json",
    "scripts\start_local.bat",
    ".env.example"
)
$missingFiles = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RepoRoot $_)) })
if ($missingFiles.Count -eq 0) {
    Add-Check "OK" "Repository files" "Required project files are present"
} else {
    Add-Check "FAIL" "Repository files" ("Missing: {0}" -f ($missingFiles -join ", "))
}

if (Test-Path -LiteralPath (Join-Path $RepoRoot ".env")) {
    Add-Check "OK" ".env" "Present (values intentionally not inspected)"
} else {
    Add-Check "WARN" ".env" "Missing; start_local.bat will create it from .env.example"
}

$script:PythonExe = $null
$script:PythonPrefix = @()
$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $script:PythonExe = $venvPython
} else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $script:PythonExe = $pythonCommand.Source
    } else {
        $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyCommand) {
            $script:PythonExe = $pyCommand.Source
            $script:PythonPrefix = @("-3")
        }
    }
}

if ($script:PythonExe) {
    $pythonVersion = Invoke-Python -Arguments @("-c", "import platform; print(platform.python_version())")
    if ($pythonVersion.ExitCode -eq 0) {
        try {
            if ([version]$pythonVersion.Output -ge [version]"3.11") {
                Add-Check "OK" "Python" ("{0} ({1})" -f $pythonVersion.Output, $script:PythonExe)
            } else {
                Add-Check "FAIL" "Python" ("{0}; Python 3.11+ is required" -f $pythonVersion.Output)
            }
        } catch {
            Add-Check "FAIL" "Python" ("Unrecognized version: {0}" -f $pythonVersion.Output)
        }
    } else {
        Add-Check "FAIL" "Python" "Python was found but could not run"
    }
} else {
    Add-Check "FAIL" "Python" "Python 3 was not found in PATH and no repository .venv exists"
}

$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($nodeCommand) {
    $nodeText = (& $nodeCommand.Source --version).Trim().TrimStart("v")
    try {
        $nodeVersion = [version]$nodeText
        $supported = (($nodeVersion.Major -eq 20 -and $nodeVersion.Minor -ge 19) -or
            ($nodeVersion.Major -eq 22 -and $nodeVersion.Minor -ge 12) -or
            $nodeVersion.Major -gt 22)
        if ($supported) {
            Add-Check "OK" "Node.js" ("{0} ({1})" -f $nodeText, $nodeCommand.Source)
        } else {
            Add-Check "FAIL" "Node.js" ("{0}; Node.js 20.19+ or 22.12+ is required" -f $nodeText)
        }
    } catch {
        Add-Check "FAIL" "Node.js" ("Unrecognized version: {0}" -f $nodeText)
    }
} else {
    Add-Check "FAIL" "Node.js" "node.exe was not found in PATH"
}

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npmCommand) {
    Add-Check "OK" "npm" ((& $npmCommand.Source --version).Trim())
} else {
    Add-Check "FAIL" "npm" "npm.cmd was not found in PATH"
}

$venvReady = Test-Path -LiteralPath $venvPython
$frontendReady = Test-Path -LiteralPath (Join-Path $RepoRoot "frontend\node_modules\.package-lock.json")
if ($venvReady -and $frontendReady) {
    Add-Check "OK" "Installed dependencies" "Python virtual environment and frontend node_modules are present"
} else {
    $missing = @()
    if (-not $venvReady) { $missing += ".venv" }
    if (-not $frontendReady) { $missing += "frontend\node_modules" }
    Add-Check "WARN" "Installed dependencies" (("Missing {0}; bootstrap_local.bat can install them" -f ($missing -join ", ")))
}

$dependencyFiles = @(
    (Join-Path $RepoRoot "backend\pyproject.toml"),
    (Join-Path $RepoRoot "frontend\package-lock.json")
)
if (($dependencyFiles | Where-Object { -not (Test-Path -LiteralPath $_) }).Count -eq 0) {
    $expectedStamp = (($dependencyFiles | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join "-")
    $stampPath = Join-Path $RepoRoot ".local_dependency_stamp"
    if (Test-Path -LiteralPath $stampPath) {
        $installedStamp = (Get-Content -LiteralPath $stampPath -Raw).Trim()
        if ($installedStamp -eq $expectedStamp) {
            Add-Check "OK" "Dependency fingerprint" "Installed dependencies match lock/config files"
        } else {
            Add-Check "WARN" "Dependency fingerprint" "Dependency files changed; the next start will bootstrap again"
        }
    } else {
        Add-Check "WARN" "Dependency fingerprint" "No local stamp; the next start will bootstrap once"
    }
}

if ($venvReady) {
    Push-Location (Join-Path $RepoRoot "backend")
    try {
        $backendImport = Invoke-Python -Arguments @("-c", "from app.main import app; from app.services.parser_registry import registry; print(app.title)")
    } finally {
        Pop-Location
    }
    if ($backendImport.ExitCode -eq 0 -and $backendImport.Output -match "GW/AP") {
        Add-Check "OK" "Backend import" $backendImport.Output
    } else {
        Add-Check "FAIL" "Backend import" ($backendImport.Output -replace "\r?\n", " | ")
    }
}

$dataDirectory = Join-Path $RepoRoot "backend\data"
try {
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $probePath = Join-Path $dataDirectory (".doctor-write-{0}.tmp" -f [guid]::NewGuid().ToString("N"))
    Set-Content -LiteralPath $probePath -Value "ok" -Encoding Ascii
    Remove-Item -LiteralPath $probePath -Force
    Add-Check "OK" "Data directory" "backend\data is writable"
} catch {
    Add-Check "FAIL" "Data directory" "backend\data is not writable"
}

try {
    $backend = Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -TimeoutSec 2
    if ($backend.name -eq "GW/AP Intelligent Debug Platform" -and $backend.api -eq "/api/v1") {
        Add-Check "OK" "Port 8000" "A healthy project backend is already running"
    } else {
        Add-Check "FAIL" "Port 8000" "Occupied by a different HTTP service"
    }
} catch {
    if (Test-PortAvailable 8000) {
        Add-Check "OK" "Port 8000" "Available"
    } else {
        Add-Check "FAIL" "Port 8000" "Occupied by a non-project or unhealthy service"
    }
}

if (Test-PortAvailable 5173) {
    Add-Check "OK" "Port 5173" "Available"
} else {
    Add-Check "WARN" "Port 5173" "Already in use; this may be an existing frontend"
}

$Results.Add("")
$Results.Add(("BlockingFailures: {0}" -f $BlockingFailures))
if ($BlockingFailures -eq 0) {
    $Results.Add("Result: PASS - scripts\start_local.bat can be attempted.")
} else {
    $Results.Add("Result: FAIL - fix the FAIL items before starting.")
}
$Results.Add("Privacy: .env values, API keys, database contents, and log contents were not read.")

$Results | Set-Content -LiteralPath $OutputPath -Encoding UTF8
$Results | ForEach-Object { Write-Host $_ }
if ($BlockingFailures -gt 0) { exit 1 }
exit 0
