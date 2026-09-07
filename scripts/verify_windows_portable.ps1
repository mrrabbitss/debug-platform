[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [switch]$RequireLocalModels,
    [switch]$NoLocalRetrieval
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$packagePath = [System.IO.Path]::GetFullPath($PackageRoot)
$python = Join-Path $packagePath "runtime\python\python.exe"
$launcher = Join-Path $packagePath "portable_launcher.py"
$demoRoot = Join-Path $packagePath "app\backend\demo_data\ap_frequent_offline"
$skillEntry = Join-Path $packagePath "agent-skills\gw-ap-debug\SKILL.md"
$knowledgeUploadHelper = Join-Path $packagePath "agent-skills\gw-ap-debug\scripts\upload-knowledge-markdown.ps1"
$installerPowerShell = Join-Path $packagePath "scripts\install_agent_skill_mcp.ps1"
$installerBatch = Join-Path $packagePath "scripts\install_agent_skill_mcp.bat"
$expectedDemoHashes = @{
    "GW_collectDebuginfo_demo.txt" = "a8bdb3121c3093b038fa67e0ce103f0d5483fe6062ddd41518eb7befdc85f318"
    "AP_collectDebuginfo_demo.txt" = "80a72a62e371b328c649569118cd7a688c472660ab29ec55cabae1b75ff539ed"
    "glm52_success_snapshot.json" = "844e0ced48d69586b5393871e0f369be77c88ebf46786f4050dff09d78c71a29"
    "methods\manifest.json" = "f409404f61ec22596fd9f864fe4b0150c63213b86d7d4f8b1d9b0b2e0f76d24e"
    "methods\ap-offline-log-analysis.md" = "d999b853c7b142d796d2361f519bdefeb168293881a4ce3390abee2147159a40"
    "methods\ap-offline-fault-tree.md" = "e3793d888d0059ffbbfeb297612a9f31ee3dcf99d7299677bf3b57b9b0f04099"
}

if ($RequireLocalModels -and $NoLocalRetrieval) {
    throw "RequireLocalModels and NoLocalRetrieval cannot be used together."
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Portable Python runtime is missing: $python"
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Portable launcher is missing: $launcher"
}
foreach ($requiredPath in @(
    $skillEntry,
    $knowledgeUploadHelper,
    $installerPowerShell,
    $installerBatch
)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Portable Skill/MCP deployment file is missing: $requiredPath"
    }
}
foreach ($entry in $expectedDemoHashes.GetEnumerator()) {
    $fixture = Join-Path $demoRoot $entry.Key
    if (-not (Test-Path -LiteralPath $fixture -PathType Leaf)) {
        throw "Bundled recorded GLM demo fixture is missing: $fixture"
    }
    $actualHash = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $entry.Value) {
        throw "Bundled recorded GLM demo fixture failed SHA-256 verification: $($entry.Key)"
    }
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

Write-Host "[INFO] Verifying the packaged Skill/MCP installer in dry-run mode..."
& $python -B -s (Join-Path $packagePath "portable_codeagent.py") --dry-run --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Packaged CodeAgent launcher dry-run failed with exit code $LASTEXITCODE."
}
& powershell.exe `
    -NoLogo `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $installerPowerShell `
    -Client All `
    -McpUrl "http://127.0.0.1:$Port/mcp" `
    -DryRun
if ($LASTEXITCODE -ne 0) {
    throw "Packaged Skill/MCP installer dry-run failed with exit code $LASTEXITCODE."
}

Write-Host "[INFO] Verifying the packaged Markdown knowledge upload helper..."
& powershell.exe `
    -NoLogo `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $knowledgeUploadHelper `
    -Path (Join-Path $demoRoot "methods\ap-offline-log-analysis.md") `
    -ApiBaseUrl "http://127.0.0.1:$Port/api/v1" `
    -DryRun
if ($LASTEXITCODE -ne 0) {
    throw "Packaged Markdown knowledge helper dry-run failed with exit code $LASTEXITCODE."
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

    $localModelArgument = if ($NoLocalRetrieval) {
        " --no-local-retrieval"
    } else {
        ""
    }
    $argumentLine = (
        '-B -s "portable_launcher.py" --no-browser --port {0} ' +
        '--data-root "{1}" --env-file "{2}"{3}'
    ) -f $Port, $testData, $testEnv, $localModelArgument
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
    $startupTimeoutSeconds = if ($RequireLocalModels) { 720 } else { 120 }
    $deadline = (Get-Date).AddSeconds($startupTimeoutSeconds)
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
        throw "Portable server did not become ready within $startupTimeoutSeconds seconds."
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

    Write-Host "[INFO] Importing and validating the bundled AP offline demo..."
    $demo = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/v1/demo-cases/ap-frequent-offline" `
        -TimeoutSec 60
    if (
        $demo.created -ne $true -or
        $demo.demo_snapshot -ne $true -or
        $demo.model_called -ne $false -or
        $demo.recorded_model_run -ne $true -or
        [string]$demo.recorded_model -ne "glm-5.2" -or
        [int]$demo.recorded_total_tokens -ne 321453 -or
        [int]$demo.artifact_count -ne 2 -or
        [int]$demo.event_count -ne 74 -or
        [int]$demo.triage_run_count -ne 2
    ) {
        throw "Bundled demo import returned an incomplete snapshot."
    }
    $demoCaseId = [string]$demo.case.id
    $secondDemo = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/v1/demo-cases/ap-frequent-offline" `
        -TimeoutSec 30
    if ($secondDemo.created -ne $false -or [string]$secondDemo.case.id -ne $demoCaseId) {
        throw "Bundled demo import is not idempotent."
    }
    # Windows PowerShell 5.1 emits a top-level JSON array from
    # Invoke-RestMethod as one pipeline object. Assign it directly so its own
    # Count/indexing semantics are preserved instead of wrapping it in @(...).
    $demoArtifacts = Invoke-RestMethod `
        -Uri "$baseUrl/api/v1/cases/$demoCaseId/artifacts" `
        -TimeoutSec 30
    $demoArtifactStatuses = @(
        $demoArtifacts | ForEach-Object { [string]$_.status } | Sort-Object -Unique
    )
    $demoArtifactTypes = @(
        $demoArtifacts |
            ForEach-Object { [string]$_.source_device_type } |
            Sort-Object -Unique
    )
    if (
        $demoArtifacts.Count -ne 2 -or
        @($demoArtifacts | Where-Object { $_.status -ne "PARSED" }).Count -ne 0 -or
        $demoArtifactTypes -notcontains "GW" -or
        $demoArtifactTypes -notcontains "AP"
    ) {
        throw (
            "Bundled demo artifacts are not a parsed GW/AP pair: " +
            "count=$($demoArtifacts.Count), " +
            "statuses=$($demoArtifactStatuses -join ','), " +
            "types=$($demoArtifactTypes -join ',')."
        )
    }
    foreach ($artifact in $demoArtifacts) {
        $triage = Invoke-RestMethod `
            -Uri "$baseUrl/api/v1/cases/$demoCaseId/log-triage?artifact_id=$($artifact.id)" `
            -TimeoutSec 30
        if (
            $triage.status -ne "COMPLETED" -or
            $triage.plan.demo_snapshot -ne $true -or
            $triage.plan.recorded_model_run -ne $true -or
            [string]$triage.plan.planner_mode -ne "llm" -or
            $triage.summary.demo_snapshot -ne $true -or
            $triage.summary.recorded_model_run -ne $true -or
            [string]$triage.model_name -ne "glm-5.2" -or
            [int]$triage.summary.exact_hit_count -le 0
        ) {
            throw "Bundled demo triage is incomplete for $($artifact.original_name)."
        }
        $pages = @{}
        foreach ($bucket in @("LLM_RELEVANT", "METHOD_REQUIRED", "OTHER")) {
            $pages[$bucket] = Invoke-RestMethod `
                -Uri "$baseUrl/api/v1/cases/$demoCaseId/log-triage/$($triage.id)/evidence?bucket=$bucket&limit=100" `
                -TimeoutSec 30
            if ([int]$pages[$bucket].total -le 0) {
                throw "Bundled demo triage bucket $bucket is empty for $($artifact.original_name)."
            }
        }
        $repeated = @(
            $pages["LLM_RELEVANT"].items |
                Where-Object { [int]$_.occurrence_count -gt 1 } |
                Select-Object -First 1
        )
        if ($repeated.Count -ne 1) {
            throw "Bundled demo has no expandable repeated LLM-relevant match."
        }
        $occurrences = Invoke-RestMethod `
            -Uri "$baseUrl/api/v1/cases/$demoCaseId/log-triage/$($triage.id)/evidence/$($repeated[0].id)/occurrences?limit=100" `
            -TimeoutSec 30
        if ([int]$occurrences.total -ne [int]$repeated[0].occurrence_count) {
            throw "Bundled demo occurrence expansion lost source positions."
        }
        $firstHit = @($occurrences.items)[0]
        $encodedPath = [Uri]::EscapeDataString([string]$firstHit.source_file)
        $sourceResponse = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "$baseUrl/api/v1/artifacts/$($artifact.id)/content?path=$encodedPath&start_line=$($firstHit.line_start)&line_count=1" `
            -TimeoutSec 30
        # Windows PowerShell returns a string; PowerShell 7 returns string[].
        $sourceStartLine = @($sourceResponse.Headers["X-Start-Line"])[0]
        if ($sourceResponse.StatusCode -ne 200 -or [int]$sourceStartLine -ne [int]$firstHit.line_start) {
            throw "Bundled demo evidence cannot jump to its original source line."
        }
    }
    $demoAnalyses = Invoke-RestMethod `
        -Uri "$baseUrl/api/v1/cases/$demoCaseId/analyses" `
        -TimeoutSec 30
    if ($demoAnalyses.Count -lt 1) {
        throw "Bundled demo diagnosis snapshot is missing."
    }
    $demoDiagnosis = $demoAnalyses[0].result_json | ConvertFrom-Json
    if (
        $demoDiagnosis.demo_snapshot -ne $true -or
        $demoDiagnosis.recorded_model_run -ne $true -or
        $demoDiagnosis.synthesis_status.mode -ne "LLM_EVIDENCE_VALIDATED" -or
        $demoDiagnosis.synthesis_status.accepted -ne $true -or
        $demoDiagnosis.diagnostic_planning.planner_mode -ne "llm_multiround" -or
        @($demoDiagnosis.diagnostic_planning.rounds).Count -ne 2 -or
        $demoDiagnosis.diagnostic_planning.fault_tree_coverage.complete -ne $true -or
        [int]$demoDiagnosis.diagnostic_planning.fault_tree_coverage.total -ne 27 -or
        @($demoDiagnosis.hypotheses).Count -ne 5
    ) {
        throw "Bundled demo diagnosis did not preserve its validated evidence snapshot."
    }
    $demoAgentRun = Invoke-RestMethod `
        -Uri "$baseUrl/api/v1/cases/$demoCaseId/agent-runs/$($demoAnalyses[0].agent_run_id)" `
        -TimeoutSec 30
    if (
        [string]$demoAgentRun.execution_mode -ne "recorded_llm_snapshot" -or
        [string]$demoAgentRun.model_name -ne "glm-5.2" -or
        [int]$demoAgentRun.usage.total_tokens -ne 321453 -or
        [int]$demoAgentRun.duration_ms -ne 146082 -or
        @($demoAgentRun.events).Count -ne 16
    ) {
        throw "Bundled demo did not preserve the recorded GLM-5.2 usage and trace."
    }
    $reportPreview = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "$baseUrl/api/v1/cases/$demoCaseId/analyses/$($demoAnalyses[0].id)/report/preview" `
        -TimeoutSec 30
    $lineMarker = [string][char]0x7B2C
    if (
        $reportPreview.Content -notmatch ("GW_collectDebuginfo_demo.txt - " + $lineMarker) -or
        $reportPreview.Content -notmatch ("AP_collectDebuginfo_demo.txt - " + $lineMarker) -or
        $reportPreview.Content -match "\b(?:EVT|LDE)-[A-Za-z0-9_.:-]+\b"
    ) {
        throw "Bundled demo report did not render safe file-line evidence."
    }
    Write-Host "[OK] Bundled recorded GLM-5.2 demo, source jumps, usage and diagnosis passed."

    if ($RequireLocalModels) {
        $modelStatusPath = Join-Path $testData "logs\local-models\status.json"
        if (-not (Test-Path -LiteralPath $modelStatusPath -PathType Leaf)) {
            throw "Bundled model status was not written: $modelStatusPath"
        }
        $modelStatus = Get-Content -LiteralPath $modelStatusPath -Raw | ConvertFrom-Json
        $components = @($modelStatus.components)
        $readyTasks = @(
            $components |
                Where-Object { $_.status -eq "ready" } |
                ForEach-Object { [string]$_.task_type }
        )
        foreach ($task in @("embedding", "reranker")) {
            if ($task -notin $readyTasks) {
                throw "Bundled $task sidecar did not become ready."
            }
        }
        $profiles = @(
            Invoke-RestMethod `
                -Uri "$baseUrl/api/v1/system/models" `
                -TimeoutSec 30
        )
        $embeddingProfile = @(
            $profiles | Where-Object {
                $_.id -eq "MODEL-embedding-bundled-gguf" -and
                $_.provider -eq "llama_cpp_local" -and
                $_.is_active -eq $true
            }
        )
        $rerankerProfile = @(
            $profiles | Where-Object {
                $_.id -eq "MODEL-reranker-bundled-gguf" -and
                $_.provider -eq "llama_cpp_local" -and
                $_.is_active -eq $true
            }
        )
        if ($embeddingProfile.Count -ne 1 -or $rerankerProfile.Count -ne 1) {
            throw "Bundled GGUF profiles were not activated on the fresh portable database."
        }
        $embeddingProbe = Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUrl/api/v1/system/models/MODEL-embedding-bundled-gguf/test" `
            -TimeoutSec 180
        if (
            $embeddingProbe.ok -ne $true -or
            [int]$embeddingProbe.dimension -ne 768 -or
            [int]$embeddingProbe.vectors -ne 2
        ) {
            throw "Bundled BGE did not satisfy the platform Embedding contract."
        }
        $rerankerProbe = Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUrl/api/v1/system/models/MODEL-reranker-bundled-gguf/test" `
            -TimeoutSec 240
        $ranking = @($rerankerProbe.ranking)
        if (
            $rerankerProbe.ok -ne $true -or
            $rerankerProbe.disabled -eq $true -or
            $ranking.Count -ne 2 -or
            [int]$ranking[0][0] -ne 0
        ) {
            throw "Bundled Qwen did not satisfy the platform Reranker contract."
        }
        Write-Host "[OK] Bundled GGUF profiles auto-activated and passed real platform inference."
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
