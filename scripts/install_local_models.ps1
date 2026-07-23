[CmdletBinding()]
param(
    [string]$Mirror = "https://hf-mirror.com",
    [ValidateRange(1, 16)]
    [int]$MaxWorkers = 4,
    [ValidateSet("Auto", "HfCli", "Curl")]
    [string]$DownloadMode = "Auto",
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

. (Join-Path $PSScriptRoot "hf_model_tools.ps1")

function Invoke-CheckedPython {
    param([string[]]$Arguments)
    & $python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE."
    }
}

function Invoke-HfCliCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    # PowerShell 5.1 can turn a native program's stderr into PowerShell error
    # records. Capture it with Continue so a failed CLI probe returns its real
    # exit code and Auto mode still gets a chance to use curl.exe.
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& $hfCli @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    foreach ($item in $output) {
        Write-Host ([string]$item)
    }
    return $exitCode
}

function Test-HfCliRepositoryAccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$Revision,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $hfCli -PathType Leaf)) {
        Write-Host "[WARN] hf.exe is unavailable; skipping the preferred CLI path."
        return $false
    }
    Write-Host "[INFO] Preflight: hf download $Repository config.json"
    $exitCode = Invoke-HfCliCommand -Arguments @(
        "download",
        $Repository,
        "config.json",
        "--revision",
        $Revision,
        "--local-dir",
        $Destination,
        "--max-workers",
        "1"
    )
    if ($exitCode -eq 0) {
        Write-Host "[OK] Hugging Face CLI preflight succeeded for $Repository."
        return $true
    }
    Write-Host (
        "[WARN] Hugging Face CLI preflight failed for $Repository (exit $exitCode). " +
        "This commonly means that a proxy or mirror broke the metadata/HEAD request."
    )
    return $false
}

function Invoke-HfCliRepositoryDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$Revision,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Host (
            "[INFO] hf download $Repository --local-dir `"$Destination`" " +
            "(attempt $attempt/3)"
        )
        $exitCode = Invoke-HfCliCommand -Arguments @(
            "download",
            $Repository,
            "--revision",
            $Revision,
            "--local-dir",
            $Destination,
            "--max-workers",
            [string]$MaxWorkers
        )
        if ($exitCode -eq 0) {
            return $true
        }
        if ($attempt -lt 3) {
            $retryDelay = 5 * $attempt
            Write-Host "[WARN] Download failed; retrying in $retryDelay seconds..."
            Start-Sleep -Seconds $retryDelay
        }
    }
    return $false
}

function Invoke-ModelDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$Revision,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if ($DownloadMode -in @("Auto", "HfCli")) {
        $preflightSucceeded = Test-HfCliRepositoryAccess `
            -Repository $Repository `
            -Revision $Revision `
            -Destination $Destination
        if ($preflightSucceeded) {
            $downloadSucceeded = Invoke-HfCliRepositoryDownload `
                -Repository $Repository `
                -Revision $Revision `
                -Destination $Destination
            if ($downloadSucceeded) {
                return "hf-cli"
            }
            Write-Host "[WARN] hf CLI download failed after its preflight succeeded."
        }
        if ($DownloadMode -eq "HfCli") {
            throw (
                "Hugging Face CLI could not download $Repository. Run " +
                "scripts\check_hf_model_access.bat, or retry with -DownloadMode Curl."
            )
        }
        Write-Host "[INFO] Switching $Repository to the verified curl.exe fallback."
    }

    try {
        $curlResult = Invoke-HfCurlRepositoryDownload `
            -Endpoint $env:HF_ENDPOINT `
            -Repository $Repository `
            -Revision $Revision `
            -Destination $Destination
        Write-Host (
            "[OK] curl fallback completed $Repository at revision " +
            "$($curlResult.Revision)."
        )
        return "curl-fallback"
    } catch {
        throw (
            "All download paths failed for $Repository. $($_.Exception.Message) " +
            "Run scripts\check_hf_model_access.bat and send the generated report."
        )
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

try {
    $normalizedMirror = Normalize-HfEndpoint $Mirror
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
}

Set-HfOnlineEnvironment `
    -Endpoint $normalizedMirror `
    -CacheRoot (Join-Path $modelsRoot ".cache\huggingface")
Write-Host "[INFO] HF_ENDPOINT=$env:HF_ENDPOINT"
Write-Host "[INFO] Download mode: $DownloadMode"
Write-Host "[INFO] Offline mode is disabled for installation."

$scriptExitCode = 0
$downloadMethods = [System.Collections.Generic.List[string]]::new()
Push-Location $projectRoot
try {
    if (-not $VerifyOnly -and -not $SkipRuntimeInstall) {
        Write-Host "[INFO] Installing the optional local-model runtime..."
        Invoke-CheckedPython @("-m", "pip", "install", "-e", "backend[local-models]")
    }

    if (-not $VerifyOnly) {
        if (
            $DownloadMode -eq "HfCli" -and
            -not (Test-Path -LiteralPath $hfCli -PathType Leaf)
        ) {
            throw (
                "Hugging Face CLI was not found at $hfCli. " +
                "Remove -SkipRuntimeInstall or install backend[local-models] first."
            )
        }
        $embeddingMethod = Invoke-ModelDownload `
            -Repository "BAAI/bge-base-zh-v1.5" `
            -Revision "f03589ceff5aac7111bd60cfc7d497ca17ecac65" `
            -Destination (Join-Path $modelsRoot "embedding\bge-base-zh-v1.5")
        $downloadMethods.Add($embeddingMethod)
        $rerankerMethod = Invoke-ModelDownload `
            -Repository "Qwen/Qwen3-Reranker-0.6B" `
            -Revision "e61197ed45024b0ed8a2d74b80b4d909f1255473" `
            -Destination (Join-Path $modelsRoot "reranker\Qwen3-Reranker-0.6B")
        $downloadMethods.Add($rerankerMethod)
    }

    $downloadMethod = if ($VerifyOnly) {
        "verification-only"
    } else {
        (@($downloadMethods | Sort-Object -Unique) -join "+")
    }
    Invoke-CheckedPython @(
        (Join-Path $PSScriptRoot "validate_local_models.py"),
        "--models-root", $modelsRoot,
        "--endpoint", $env:HF_ENDPOINT,
        "--download-method", $downloadMethod
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
