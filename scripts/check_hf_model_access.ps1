[CmdletBinding()]
param(
    [string]$Mirror = "https://hf-mirror.com",
    [string]$Repository = "BAAI/bge-base-zh-v1.5",
    [string]$Revision = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$hfCli = Join-Path $projectRoot ".venv\Scripts\hf.exe"
$reportPath = Join-Path $projectRoot (
    "hf_model_access_report_{0}.txt" -f (Get-Date -Format "yyyyMMdd_HHmmss")
)
$report = [System.Collections.Generic.List[string]]::new()
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "gw-ap-hf-access-{0}" -f [System.Guid]::NewGuid().ToString("N")
)

. (Join-Path $PSScriptRoot "hf_model_tools.ps1")

function Add-ReportLine {
    param([string]$Text = "")
    $report.Add($Text)
    Write-Host $Text
}

function Add-CommandOutput {
    param([object[]]$Output)
    foreach ($item in $Output) {
        $line = [string]$item
        $report.Add($line)
        Write-Host $line
    }
}

$exitCode = 1
$cliSucceeded = $false
$curlSucceeded = $false
$originalOffline = [Environment]::GetEnvironmentVariable("HF_HUB_OFFLINE")
$originalTransformersOffline = [Environment]::GetEnvironmentVariable("TRANSFORMERS_OFFLINE")
try {
    $endpoint = Normalize-HfEndpoint $Mirror
    $resolvedRevision = if (-not [string]::IsNullOrWhiteSpace($Revision)) {
        $Revision
    } elseif ($Repository -eq "BAAI/bge-base-zh-v1.5") {
        "f03589ceff5aac7111bd60cfc7d497ca17ecac65"
    } elseif ($Repository -eq "Qwen/Qwen3-Reranker-0.6B") {
        "e61197ed45024b0ed8a2d74b80b4d909f1255473"
    } else {
        ""
    }
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Set-HfOnlineEnvironment `
        -Endpoint $endpoint `
        -CacheRoot (Join-Path $tempRoot "cache")

    Add-ReportLine "GW/AP Hugging Face model access check"
    Add-ReportLine "CheckedAt: $([DateTimeOffset]::Now.ToString("o"))"
    Add-ReportLine "ProjectRoot: $projectRoot"
    Add-ReportLine "Mirror: $endpoint"
    Add-ReportLine "Repository: $Repository"
    Add-ReportLine "Revision: $(
        if ([string]::IsNullOrWhiteSpace($resolvedRevision)) { "main" } else { $resolvedRevision }
    )"
    Add-ReportLine "PowerShell: $($PSVersionTable.PSVersion)"
    Add-ReportLine "HF_HUB_OFFLINE inherited: $(
        if ([string]::IsNullOrWhiteSpace($originalOffline)) { "<unset>" } else { $originalOffline }
    )"
    Add-ReportLine "TRANSFORMERS_OFFLINE inherited: $(
        if ([string]::IsNullOrWhiteSpace($originalTransformersOffline)) {
            "<unset>"
        } else {
            $originalTransformersOffline
        }
    )"
    Add-ReportLine "HTTP_PROXY configured: $(
        -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("HTTP_PROXY"))
    )"
    Add-ReportLine "HTTPS_PROXY configured: $(
        -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("HTTPS_PROXY"))
    )"
    Add-ReportLine ""

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Add-ReportLine "[FAIL] .venv Python is missing. Run scripts\start_local.bat first."
    } else {
        $pythonVersion = @(& $python --version 2>&1)
        Add-ReportLine "[INFO] Python"
        Add-CommandOutput $pythonVersion
        $hubInfo = @(
            & $python -c (
                "import importlib.util; " +
                "s=importlib.util.find_spec('huggingface_hub'); " +
                "print('huggingface_hub: not installed' if s is None else " +
                "'huggingface_hub: '+__import__('huggingface_hub').__version__)"
            ) 2>&1
        )
        Add-CommandOutput $hubInfo
        if (Test-Path -LiteralPath $hfCli -PathType Leaf) {
            $endpointOutput = @(
                & $python -c (
                    "from huggingface_hub.constants import ENDPOINT; " +
                    "print('Resolved endpoint: '+ENDPOINT)"
                ) 2>&1
            )
            Add-CommandOutput $endpointOutput
        } else {
            Add-ReportLine "[WARN] hf.exe is missing; CLI probe cannot run."
        }
    }

    Add-ReportLine ""
    Add-ReportLine "[TEST] Mirror API through curl.exe"
    try {
        $metadata = Get-HfRepositoryMetadata `
            -Endpoint $endpoint `
            -Repository $Repository `
            -Revision $resolvedRevision
        $runtimeFiles = @(Get-HfRuntimeFiles -Metadata $metadata)
        $runtimeBytes = [long](($runtimeFiles | Measure-Object -Property Size -Sum).Sum)
        Add-ReportLine (
            "[PASS] Mirror API returned revision $($metadata.sha), " +
            "$($runtimeFiles.Count) runtime files, " +
            "$([Math]::Round($runtimeBytes / 1GB, 2)) GiB."
        )
        $configFile = $runtimeFiles | Where-Object { $_.RelativePath -eq "config.json" } |
            Select-Object -First 1
        if (-not $configFile) {
            throw "Repository metadata does not contain config.json."
        }
        Invoke-HfCurlFileDownload `
            -Endpoint $endpoint `
            -Repository $Repository `
            -Revision ([string]$metadata.sha) `
            -RelativePath "config.json" `
            -DestinationRoot (Join-Path $tempRoot "curl-probe") `
            -ExpectedSize $configFile.Size `
            -ExpectedSha256 $configFile.Sha256
        $curlSucceeded = $true
        Add-ReportLine "[PASS] curl.exe downloaded and validated config.json."
    } catch {
        Add-ReportLine "[FAIL] curl mirror test: $($_.Exception.Message)"
    }

    Add-ReportLine ""
    Add-ReportLine "[TEST] Hugging Face CLI config.json download"
    if (Test-Path -LiteralPath $hfCli -PathType Leaf) {
        $env:HF_DEBUG = "1"
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $cliArguments = @(
                "download",
                $Repository,
                "config.json"
            )
            if (-not [string]::IsNullOrWhiteSpace($resolvedRevision)) {
                $cliArguments += @("--revision", $resolvedRevision)
            }
            $cliArguments += @(
                "--local-dir",
                (Join-Path $tempRoot "hf-cli-probe"),
                "--max-workers",
                "1"
            )
            $cliOutput = @(
                & $hfCli @cliArguments 2>&1
            )
            $cliExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorAction
        }
        Add-CommandOutput $cliOutput
        if ($cliExitCode -eq 0) {
            $cliSucceeded = $true
            Add-ReportLine "[PASS] hf CLI downloaded config.json."
        } else {
            Add-ReportLine "[FAIL] hf CLI exit code: $cliExitCode"
        }
    } else {
        Add-ReportLine "[SKIP] hf CLI is not installed."
    }

    Add-ReportLine ""
    if ($cliSucceeded) {
        Add-ReportLine "RESULT: PASS_HF_CLI"
        Add-ReportLine "The installer can use its preferred hf download path."
        $exitCode = 0
    } elseif ($curlSucceeded) {
        Add-ReportLine "RESULT: PASS_CURL_FALLBACK"
        Add-ReportLine (
            "The hf CLI path failed, but the optimized installer can use its " +
            "verified curl.exe fallback."
        )
        $exitCode = 0
    } else {
        Add-ReportLine "RESULT: FAIL"
        Add-ReportLine (
            "Neither the hf CLI nor curl.exe can fetch the model. Check company proxy, " +
            "TLS inspection, DNS, firewall, or mirror availability."
        )
    }
} catch {
    Add-ReportLine "[FATAL] $($_.Exception.Message)"
    Add-ReportLine "RESULT: FAIL"
} finally {
    $utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllLines($reportPath, $report, $utf8WithoutBom)
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    $tempPrefix = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::GetTempPath()
    ).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (
        $resolvedTemp.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase) -and
        [System.IO.Directory]::Exists($resolvedTemp)
    ) {
        [System.IO.Directory]::Delete($resolvedTemp, $true)
    }
    Write-Host ""
    Write-Host "[INFO] Report: $reportPath"
}

exit $exitCode
