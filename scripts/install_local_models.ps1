[CmdletBinding()]
param(
    [string]$Mirror = "https://hf-mirror.com",
    [ValidateRange(1, 16)]
    [int]$MaxWorkers = 4,
    [switch]$SkipRuntimeInstall,
    [switch]$SkipCompatibilityTest,
    [switch]$VerifyOnly,
    [switch]$CreateDirectoriesOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$modelsRoot = Join-Path $projectRoot "models"

function Invoke-CheckedPython {
    param([string[]]$Arguments)
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

Write-Host "[INFO] Project root: $projectRoot"
Write-Host "[INFO] Models root:  $modelsRoot"
foreach ($folder in @("inference", "reranker", "embedding")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $modelsRoot $folder) | Out-Null
}
Write-Host "[OK] Created models\inference, models\reranker and models\embedding."

if ($CreateDirectoriesOnly) {
    Write-Host "[OK] Directory-only operation completed."
    exit 0
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Host (
        "[ERROR] " +
        "Python virtual environment was not found. Run scripts\start_local.bat once, " +
        "close the service windows, and retry."
    )
    exit 1
}

$scriptExitCode = 0
Push-Location $projectRoot
try {
    if (-not $VerifyOnly -and -not $SkipRuntimeInstall) {
        Write-Host "[INFO] Installing the optional local-model runtime..."
        Invoke-CheckedPython @("-m", "pip", "install", "-e", "backend[local-models]")
    }

    $env:PYTHONUTF8 = "1"
    $env:HF_ENDPOINT = $Mirror.TrimEnd("/")
    $env:HF_HOME = Join-Path $modelsRoot ".cache\huggingface"
    $env:HF_HUB_DOWNLOAD_TIMEOUT = "120"
    $env:HF_HUB_ETAG_TIMEOUT = "30"
    $env:HF_HUB_DISABLE_IMPLICIT_TOKEN = "1"
    $env:HF_HUB_DISABLE_TELEMETRY = "1"
    $env:DO_NOT_TRACK = "1"

    $downloadArguments = @(
        (Join-Path $PSScriptRoot "download_local_models.py"),
        "--models-root", $modelsRoot,
        "--endpoint", $env:HF_ENDPOINT,
        "--max-workers", [string]$MaxWorkers
    )
    if ($VerifyOnly) {
        $downloadArguments += "--verify-only"
    }
    Invoke-CheckedPython $downloadArguments

    if (-not $SkipCompatibilityTest) {
        Write-Host "[INFO] Loading both models through the application adapters..."
        Invoke-CheckedPython @(
            (Join-Path $PSScriptRoot "verify_local_models.py"),
            "--models-root", $modelsRoot
        )
    }
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    $scriptExitCode = 1
} finally {
    Pop-Location
}

if ($scriptExitCode -ne 0) {
    exit $scriptExitCode
}

Write-Host ""
Write-Host "[OK] Local model installation and validation completed."
Write-Host "[INFO] Embedding: models\embedding\bge-base-zh-v1.5"
Write-Host "[INFO] Reranker:  models\reranker\Qwen3-Reranker-0.6B"
Write-Host "[INFO] The models directory is ignored by Git and will not be uploaded."
Write-Host "[NEXT] Restart scripts\start_local.bat."
Write-Host "[NEXT] In System Settings, test and activate the profiles containing 'project models directory'."
Write-Host "[NEXT] Rebuild the knowledge vector index after activating the Embedding profile."
