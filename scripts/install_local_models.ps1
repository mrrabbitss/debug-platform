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
$hfCli = Join-Path $projectRoot ".venv\Scripts\hf.exe"
$modelsRoot = Join-Path $projectRoot "models"

function Invoke-CheckedPython {
    param([string[]]$Arguments)
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

function Invoke-HfDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Host (
            "[INFO] hf download $Repository --local-dir `"$Destination`" " +
            "(attempt $attempt/3)"
        )
        & $hfCli download $Repository `
            --local-dir $Destination `
            --max-workers $MaxWorkers
        if ($LASTEXITCODE -eq 0) {
            return
        }
        if ($attempt -lt 3) {
            $retryDelay = 5 * $attempt
            Write-Host "[WARN] Download failed; retrying in $retryDelay seconds..."
            Start-Sleep -Seconds $retryDelay
        }
    }
    throw "hf download failed for $Repository after 3 attempts."
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

$normalizedMirror = $Mirror.Trim().TrimEnd("/")
try {
    $mirrorUri = [System.Uri]$normalizedMirror
} catch {
    Write-Host "[ERROR] Mirror must be a valid HTTP(S) URL."
    exit 1
}
if (
    -not $mirrorUri.IsAbsoluteUri -or
    $mirrorUri.Scheme -notin @("http", "https") -or
    [string]::IsNullOrWhiteSpace($mirrorUri.Host) -or
    -not [string]::IsNullOrWhiteSpace($mirrorUri.UserInfo) -or
    -not [string]::IsNullOrWhiteSpace($mirrorUri.Query) -or
    -not [string]::IsNullOrWhiteSpace($mirrorUri.Fragment)
) {
    Write-Host (
        "[ERROR] Mirror must be an HTTP(S) URL with a hostname and no credentials, " +
        "query, or fragment."
    )
    exit 1
}

$env:PYTHONUTF8 = "1"
$env:HF_ENDPOINT = $normalizedMirror
$env:HF_HOME = Join-Path $modelsRoot ".cache\huggingface"
$env:HF_HUB_DOWNLOAD_TIMEOUT = "120"
$env:HF_HUB_ETAG_TIMEOUT = "30"
$env:HF_HUB_DISABLE_IMPLICIT_TOKEN = "1"
$env:HF_HUB_DISABLE_TELEMETRY = "1"
$env:DO_NOT_TRACK = "1"
Write-Host "[INFO] HF_ENDPOINT=$env:HF_ENDPOINT"

$scriptExitCode = 0
Push-Location $projectRoot
try {
    if (-not $VerifyOnly -and -not $SkipRuntimeInstall) {
        Write-Host "[INFO] Installing the optional local-model runtime..."
        Invoke-CheckedPython @("-m", "pip", "install", "-e", "backend[local-models]")
    }

    if (-not $VerifyOnly) {
        if (-not (Test-Path -LiteralPath $hfCli -PathType Leaf)) {
            throw (
                "Hugging Face CLI was not found at $hfCli. " +
                "Remove -SkipRuntimeInstall or install backend[local-models] first."
            )
        }
        Invoke-HfDownload `
            -Repository "BAAI/bge-base-zh-v1.5" `
            -Destination (Join-Path $modelsRoot "embedding\bge-base-zh-v1.5")
        Invoke-HfDownload `
            -Repository "Qwen/Qwen3-Reranker-0.6B" `
            -Destination (Join-Path $modelsRoot "reranker\Qwen3-Reranker-0.6B")
    }

    Invoke-CheckedPython @(
        (Join-Path $PSScriptRoot "validate_local_models.py"),
        "--models-root", $modelsRoot,
        "--endpoint", $env:HF_ENDPOINT
    )

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
