param(
    [int]$BackendPort = 18000,
    [int]$FrontendPort = 15173,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Node = (Get-Command node.exe -ErrorAction Stop).Source
$Vite = Join-Path $RepoRoot "frontend\node_modules\vite\bin\vite.js"
$BackendProcess = $null
$FrontendProcess = $null
$Succeeded = $false

function Wait-ProjectEndpoint {
    param([string]$Uri, [int]$Seconds, [scriptblock]$Validator)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
            if (& $Validator $response) { return $true }
        } catch {
            Start-Sleep -Milliseconds 500
            continue
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Restore-EnvironmentValue {
    param([string]$Name, [AllowNull()][string]$Value)
    if ($null -eq $Value) {
        Remove-Item ("Env:{0}" -f $Name) -ErrorAction SilentlyContinue
    } else {
        Set-Item ("Env:{0}" -f $Name) $Value
    }
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

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing .venv. Run scripts\bootstrap_local.bat first."
}
if (-not (Test-Path -LiteralPath $Vite)) {
    throw "Missing frontend dependencies. Run scripts\bootstrap_local.bat first."
}
foreach ($port in @($BackendPort, $FrontendPort)) {
    if (-not (Test-PortAvailable $port)) {
        throw "Validation port $port is already occupied. Choose another port."
    }
}

$ValidationRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "gw-ap-runtime-smoke-{0}" -f [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $ValidationRoot | Out-Null
$DatabasePath = Join-Path $ValidationRoot "validation.db"
$StoragePath = Join-Path $ValidationRoot "storage"
$BackendOut = Join-Path $ValidationRoot "backend.out.log"
$BackendErr = Join-Path $ValidationRoot "backend.err.log"
$FrontendOut = Join-Path $ValidationRoot "frontend.out.log"
$FrontendErr = Join-Path $ValidationRoot "frontend.err.log"
$oldDatabase = $env:DATABASE_URL
$oldStorage = $env:STORAGE_ROOT
$oldProxy = $env:VITE_BACKEND_PROXY
$oldAppEnv = $env:APP_ENV
$oldApiKey = $env:API_KEY
$oldAuthMode = $env:AUTH_MODE
$oldLlmProvider = $env:LLM_PROVIDER
$oldLlmApiKey = $env:LLM_API_KEY
$oldLlmBaseUrl = $env:LLM_BASE_URL
$oldLlmModel = $env:LLM_MODEL

try {
    $env:DATABASE_URL = "sqlite:///" + ($DatabasePath -replace "\\", "/")
    $env:STORAGE_ROOT = $StoragePath
    $env:APP_ENV = "test"
    $env:API_KEY = ""
    $env:AUTH_MODE = "local"
    $env:LLM_PROVIDER = "mock"
    $env:LLM_API_KEY = ""
    $env:LLM_BASE_URL = ""
    $env:LLM_MODEL = ""
    $backendStart = @{
        FilePath = $Python
        ArgumentList = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $BackendPort)
        WorkingDirectory = (Join-Path $RepoRoot "backend")
        WindowStyle = "Hidden"
        RedirectStandardOutput = $BackendOut
        RedirectStandardError = $BackendErr
        PassThru = $true
    }
    $BackendProcess = Start-Process @backendStart
    $backendUrl = "http://127.0.0.1:$BackendPort"
    $backendReady = Wait-ProjectEndpoint "$backendUrl/" 90 {
        param($response)
        $response.name -eq "GW/AP Intelligent Debug Platform" -and $response.api -eq "/api/v1"
    }
    if (-not $backendReady) {
        $detail = (Get-Content -LiteralPath $BackendErr -Raw -ErrorAction SilentlyContinue)
        throw "Backend did not become healthy. $detail"
    }

    $env:VITE_BACKEND_PROXY = $backendUrl
    $frontendStart = @{
        FilePath = $Node
        ArgumentList = @($Vite, "--host", "127.0.0.1", "--port", $FrontendPort, "--strictPort")
        WorkingDirectory = (Join-Path $RepoRoot "frontend")
        WindowStyle = "Hidden"
        RedirectStandardOutput = $FrontendOut
        RedirectStandardError = $FrontendErr
        PassThru = $true
    }
    $FrontendProcess = Start-Process @frontendStart
    $frontendUrl = "http://127.0.0.1:$FrontendPort"
    $frontendReady = Wait-ProjectEndpoint "$frontendUrl/api/v1/system/models" 60 {
        param($response)
        $null -ne $response
    }
    if (-not $frontendReady) {
        $detail = (Get-Content -LiteralPath $FrontendErr -Raw -ErrorAction SilentlyContinue)
        throw "Frontend or API proxy did not become healthy. $detail"
    }

    $caseRequest = @{
        Method = "Post"
        Uri = "$frontendUrl/api/v1/cases"
        ContentType = "application/json"
        Body = '{"title":"Runtime smoke test","device_type":"GW","description":"isolated validation"}'
    }
    $case = Invoke-RestMethod @caseRequest
    $caseRoundTrip = Invoke-RestMethod -Uri "$frontendUrl/api/v1/cases/$($case.id)" -TimeoutSec 5
    if ($caseRoundTrip.title -ne "Runtime smoke test") {
        throw "Case API round-trip returned unexpected data"
    }

    $Succeeded = $true
    $state = [ordered]@{
        ok = $true
        validation_root = $ValidationRoot
        database = $DatabasePath
        backend_pid = $BackendProcess.Id
        frontend_pid = $FrontendProcess.Id
        backend_url = $backendUrl
        frontend_url = $frontendUrl
        case_id = $case.id
        kept_running = [bool]$KeepRunning
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ValidationRoot "state.json") -Encoding UTF8
    $state | ConvertTo-Json -Compress
} finally {
    Restore-EnvironmentValue "DATABASE_URL" $oldDatabase
    Restore-EnvironmentValue "STORAGE_ROOT" $oldStorage
    Restore-EnvironmentValue "VITE_BACKEND_PROXY" $oldProxy
    Restore-EnvironmentValue "APP_ENV" $oldAppEnv
    Restore-EnvironmentValue "API_KEY" $oldApiKey
    Restore-EnvironmentValue "AUTH_MODE" $oldAuthMode
    Restore-EnvironmentValue "LLM_PROVIDER" $oldLlmProvider
    Restore-EnvironmentValue "LLM_API_KEY" $oldLlmApiKey
    Restore-EnvironmentValue "LLM_BASE_URL" $oldLlmBaseUrl
    Restore-EnvironmentValue "LLM_MODEL" $oldLlmModel
    if (-not ($Succeeded -and $KeepRunning)) {
        foreach ($process in @($FrontendProcess, $BackendProcess)) {
            if ($process -and -not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
