param(
    [ValidateSet("Fast", "Full", "External")]
    [string]$Mode = "Fast",
    [string]$OutputRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "artifacts\validation"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $RepoRoot $OutputRoot
}

$StartedAt = Get-Date
$RunId = "{0}-{1}" -f $StartedAt.ToString("yyyyMMdd-HHmmss"), $Mode.ToLowerInvariant()
$RunDirectory = Join-Path $OutputRoot $RunId
$LogsDirectory = Join-Path $RunDirectory "logs"
$SummaryPath = Join-Path $RunDirectory "summary.json"
$JunitPath = Join-Path $RunDirectory "pytest.xml"
$GoldenJsonPath = Join-Path $RunDirectory "golden-evaluation.json"
$GoldenJunitPath = Join-Path $RunDirectory "golden-evaluation.xml"
$CoverageXmlPath = Join-Path $RunDirectory "coverage.xml"
$Steps = [System.Collections.Generic.List[object]]::new()
$OverallStatus = "FAILED"
$FailureMessage = $null

New-Item -ItemType Directory -Path $LogsDirectory -Force | Out-Null

function Convert-ToSafeFileName {
    param([string]$Name)
    return (($Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-").Trim("-"))
}

function Add-FailedSetupStep {
    param([string]$Message)
    $script:Steps.Add([pscustomobject]@{
        name = "environment-setup"
        status = "FAILED"
        exit_code = 1
        duration_seconds = 0
        log = $null
        detail = $Message
    })
}

function Invoke-ValidationStep {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $script:RepoRoot,
        [ValidateRange(1, 3)][int]$MaxAttempts = 1
    )

    $stepStarted = Get-Date
    $logName = "{0:D2}-{1}.log" -f ($script:Steps.Count + 1), (Convert-ToSafeFileName $Name)
    $logPath = Join-Path $script:LogsDirectory $logName
    Write-Host ""
    Write-Host ("[STEP {0}] {1}" -f ($script:Steps.Count + 1), $Name)
    Write-Host ("[LOG] {0}" -f $logPath)

    $exitCode = 1
    $attemptsUsed = 0
    Push-Location $WorkingDirectory
    try {
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $writer = New-Object System.IO.StreamWriter($logPath, $false, $utf8)
        try {
            for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
                $attemptsUsed = $attempt
                if ($attempt -gt 1) {
                    $attemptHeader = "[RETRY] Attempt $attempt of $MaxAttempts"
                    $writer.WriteLine($attemptHeader)
                    Write-Host $attemptHeader
                }
                & $FilePath @Arguments 2>&1 | ForEach-Object {
                    if ($_ -is [System.Management.Automation.ErrorRecord]) {
                        $line = $_.Exception.Message
                    } else {
                        $line = $_.ToString()
                    }
                    if (-not [string]::IsNullOrWhiteSpace($line)) {
                        $writer.WriteLine($line)
                        $writer.Flush()
                        Write-Host $line
                    }
                }
                $exitCode = $LASTEXITCODE
                if ($null -eq $exitCode) {
                    $exitCode = 0
                }
                if ($exitCode -eq 0 -or $attempt -eq $MaxAttempts) {
                    break
                }
                $retryMessage = "[WARN] Exit code $exitCode; retrying after a short delay."
                $writer.WriteLine($retryMessage)
                $writer.Flush()
                Write-Host $retryMessage
                Start-Sleep -Milliseconds 750
            }
        } finally {
            $writer.Dispose()
            $ErrorActionPreference = $oldPreference
        }
    } catch {
        $_ | Out-String | Tee-Object -FilePath $logPath -Append | Write-Host
        $exitCode = 1
    } finally {
        Pop-Location
    }

    $duration = [math]::Round(((Get-Date) - $stepStarted).TotalSeconds, 2)
    $status = if ($exitCode -eq 0) { "PASSED" } else { "FAILED" }
    $script:Steps.Add([pscustomobject]@{
        name = $Name
        status = $status
        exit_code = $exitCode
        duration_seconds = $duration
        attempts = $attemptsUsed
        log = "logs/$logName"
        detail = if ($attemptsUsed -gt 1 -and $exitCode -eq 0) {
            "Passed after a transient failure"
        } else {
            $null
        }
    })
    if ($exitCode -ne 0) {
        throw "Validation step failed: $Name (exit code $exitCode)"
    }
}

function Write-ValidationSummary {
    $finishedAt = Get-Date
    $summary = [ordered]@{
        schema_version = 1
        run_id = $script:RunId
        mode = $script:Mode
        status = $script:OverallStatus
        started_at = $script:StartedAt.ToString("o")
        finished_at = $finishedAt.ToString("o")
        duration_seconds = [math]::Round(($finishedAt - $script:StartedAt).TotalSeconds, 2)
        repository = $script:RepoRoot
        powershell = $PSVersionTable.PSVersion.ToString()
        os = [Environment]::OSVersion.VersionString
        failure = $script:FailureMessage
        steps = @($script:Steps)
    }
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:SummaryPath -Encoding utf8
    Write-Host ""
    Write-Host ("[{0}] Validation {1}." -f $script:OverallStatus, $script:Mode)
    Write-Host ("[SUMMARY] {0}" -f $script:SummaryPath)
}

Write-Host "GW/AP Debug Platform - Repository Validation"
Write-Host ("Mode: {0}" -f $Mode)
Write-Host ("Run directory: {0}" -f $RunDirectory)

try {
    $PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $Npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $Docker = $null
    if ($Mode -eq "External") {
        $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
        if ($null -eq $dockerCommand) {
            throw "External validation requires Docker Desktop with docker.exe available in PATH."
        }
        $Docker = $dockerCommand.Source
    }

    if ($Mode -in @("Full", "External")) {
        Invoke-ValidationStep `
            -Name "Bootstrap locked local dependencies" `
            -FilePath "cmd.exe" `
            -Arguments @("/d", "/c", "scripts\bootstrap_local.bat", "--no-pause")
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $Vite = Join-Path $RepoRoot "frontend\node_modules\vite\bin\vite.js"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Missing .venv. Run scripts\bootstrap_local.bat before Fast validation."
    }
    if (-not (Test-Path -LiteralPath $Vite -PathType Leaf)) {
        throw "Missing frontend dependencies. Run scripts\bootstrap_local.bat before Fast validation."
    }

    Invoke-ValidationStep `
        -Name "Verify Python lock files" `
        -FilePath $PowerShell `
        -Arguments @(
            "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $PSScriptRoot "refresh_python_lock.ps1"), "-Check"
        )
    Invoke-ValidationStep -Name "Python dependency consistency" -FilePath $Python -Arguments @("-m", "pip", "check")
    Invoke-ValidationStep `
        -Name "Python static checks" `
        -FilePath $Python `
        -Arguments @("-m", "ruff", "check", "backend/app", "backend/tests", "scripts")
    Invoke-ValidationStep `
        -Name "Python bytecode compilation" `
        -FilePath $Python `
        -Arguments @("-m", "compileall", "-q", "backend/app", "backend/tests", "scripts")
    Invoke-ValidationStep `
        -Name "Repository harness contracts" `
        -FilePath $Python `
        -Arguments @("scripts/check_repo_harness.py")
    Invoke-ValidationStep `
        -Name "Architecture boundaries and size ratchets" `
        -FilePath $Python `
        -Arguments @("scripts/check_architecture.py")
    Invoke-ValidationStep `
        -Name "Golden dataset quality gates" `
        -FilePath $Python `
        -Arguments @(
            "scripts/run_golden_evals.py",
            "--output", $GoldenJsonPath,
            "--junit", $GoldenJunitPath
        )

    if ($Mode -eq "Fast") {
        Invoke-ValidationStep `
            -Name "Fast backend regression" `
            -FilePath $Python `
            -WorkingDirectory (Join-Path $RepoRoot "backend") `
            -Arguments @(
                "-m", "pytest", "-q",
                "tests/test_deployment_files.py",
                "tests/test_local_startup_scripts.py",
                "tests/test_harness_contract.py",
                "--junitxml", $JunitPath
            )
    } else {
        Invoke-ValidationStep `
            -Name "Full backend regression" `
            -FilePath $Python `
            -Arguments @(
                "scripts/run_backend_tests.py",
                "--junit", $JunitPath,
                "--coverage-xml", $CoverageXmlPath
            )
    }

    Invoke-ValidationStep `
        -Name "Frontend type check and production build" `
        -FilePath $Npm `
        -WorkingDirectory (Join-Path $RepoRoot "frontend") `
        -Arguments @("run", "build") `
        -MaxAttempts 2

    if ($Mode -in @("Full", "External")) {
        Invoke-ValidationStep -Name "Python dependency audit" -FilePath $Python -Arguments @("-m", "pip_audit")
        Invoke-ValidationStep `
            -Name "Frontend production dependency audit" `
            -FilePath $Npm `
            -WorkingDirectory (Join-Path $RepoRoot "frontend") `
            -Arguments @("audit", "--omit=dev", "--audit-level=high")
        Invoke-ValidationStep `
            -Name "Install VS Code extension dependencies" `
            -FilePath $Npm `
            -WorkingDirectory (Join-Path $RepoRoot "vscode-extension") `
            -Arguments @("ci")
        Invoke-ValidationStep `
            -Name "Compile VS Code extension" `
            -FilePath $Npm `
            -WorkingDirectory (Join-Path $RepoRoot "vscode-extension") `
            -Arguments @("run", "compile")
        Invoke-ValidationStep `
            -Name "VS Code extension production dependency audit" `
            -FilePath $Npm `
            -WorkingDirectory (Join-Path $RepoRoot "vscode-extension") `
            -Arguments @("audit", "--omit=dev", "--audit-level=high")
        Invoke-ValidationStep `
            -Name "Windows local doctor" `
            -FilePath $PowerShell `
            -Arguments @(
                "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $PSScriptRoot "doctor_local.ps1"),
                "-OutputPath", (Join-Path $RunDirectory "doctor.txt")
            )
        Invoke-ValidationStep `
            -Name "Isolated backend and frontend runtime smoke" `
            -FilePath $PowerShell `
            -Arguments @(
                "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $PSScriptRoot "runtime_smoke.ps1")
            )
        Invoke-ValidationStep `
            -Name "Isolated browser knowledge-curation E2E" `
            -FilePath $PowerShell `
            -Arguments @(
                "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $PSScriptRoot "run_browser_e2e.ps1")
            )
    }

    if ($Mode -eq "External") {
        Invoke-ValidationStep `
            -Name "Docker Compose configuration" `
            -FilePath $Docker `
            -Arguments @("compose", "config", "--quiet")
        Invoke-ValidationStep `
            -Name "Build backend container" `
            -FilePath $Docker `
            -Arguments @("build", "--tag", "gw-ap-debug-backend:validation", "backend")
        Invoke-ValidationStep `
            -Name "Build frontend container" `
            -FilePath $Docker `
            -Arguments @("build", "--tag", "gw-ap-debug-frontend:validation", "frontend")
    }

    $OverallStatus = "PASSED"
} catch {
    $FailureMessage = $_.Exception.Message
    if ($Steps.Count -eq 0) {
        Add-FailedSetupStep $FailureMessage
    }
    Write-Host ""
    Write-Host ("[ERROR] {0}" -f $FailureMessage)
} finally {
    Write-ValidationSummary
}

if ($OverallStatus -ne "PASSED") {
    exit 1
}
exit 0
