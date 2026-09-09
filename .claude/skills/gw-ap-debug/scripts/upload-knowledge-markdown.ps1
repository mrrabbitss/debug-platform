[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [ValidateCount(1, 20)]
    [string[]]$Path,

    [ValidateCount(1, 20)]
    [string[]]$RelativePath,

    [string]$ApiBaseUrl = $env:DEBUGPLATFORM_API_BASE_URL,

    [string]$Token = $env:DEBUGPLATFORM_MCP_TOKEN,

    [ValidateSet("LOW", "MEDIUM", "HIGH")]
    [string]$TrustLevel = "MEDIUM",

    [ValidateSet("PUBLIC", "INTERNAL", "RESTRICTED")]
    [string]$Confidentiality = "INTERNAL",

    [ValidateRange(1, 30)]
    [int]$PollIntervalSeconds = 2,

    [ValidateRange(1, 7200)]
    [int]$MaxWaitSeconds = 7200,

    [switch]$NoWait,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-ApiBaseUrl {
    param([string]$ExplicitUrl)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitUrl)) {
        $apiUri = $null
        if (-not [Uri]::TryCreate($ExplicitUrl, [UriKind]::Absolute, [ref]$apiUri)) {
            throw "ApiBaseUrl must be an absolute HTTP(S) URL."
        }
        if ($apiUri.Scheme -notin @("http", "https")) {
            throw "ApiBaseUrl must use HTTP or HTTPS."
        }
        return $ExplicitUrl.TrimEnd("/")
    }
    $mcpUrl = $env:DEBUGPLATFORM_MCP_URL
    if ([string]::IsNullOrWhiteSpace($mcpUrl)) {
        throw "Set DEBUGPLATFORM_MCP_URL or DEBUGPLATFORM_API_BASE_URL, or pass -ApiBaseUrl."
    }
    $mcpUri = $null
    if (-not [Uri]::TryCreate($mcpUrl, [UriKind]::Absolute, [ref]$mcpUri)) {
        throw "DEBUGPLATFORM_MCP_URL must be an absolute HTTP(S) URL."
    }
    if ($mcpUri.Scheme -notin @("http", "https")) {
        throw "DEBUGPLATFORM_MCP_URL must use HTTP or HTTPS."
    }
    $builder = [UriBuilder]::new($mcpUri)
    $basePath = $builder.Path.TrimEnd("/")
    if ($basePath.EndsWith("/mcp", [StringComparison]::OrdinalIgnoreCase)) {
        $basePath = $basePath.Substring(0, $basePath.Length - 4)
    }
    $builder.Path = ($basePath.TrimEnd("/") + "/api/v1")
    $builder.Query = ""
    $builder.Fragment = ""
    return $builder.Uri.AbsoluteUri.TrimEnd("/")
}

function Assert-SuccessResponse {
    param(
        [System.Net.Http.HttpResponseMessage]$Response,
        [string]$Operation
    )
    $body = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $Response.IsSuccessStatusCode) {
        throw "$Operation failed with HTTP $([int]$Response.StatusCode): $body"
    }
    return $body
}

function New-StringPart {
    param(
        [string]$Value,
        [string]$Name,
        [System.Net.Http.MultipartFormDataContent]$Multipart
    )
    $Multipart.Add([System.Net.Http.StringContent]::new($Value), $Name)
}

$resolvedPaths = @()
foreach ($inputPath in $Path) {
    $resolvedPath = [IO.Path]::GetFullPath($inputPath)
    if (-not [IO.File]::Exists($resolvedPath)) {
        throw "Markdown file does not exist: $resolvedPath"
    }
    $extension = [IO.Path]::GetExtension($resolvedPath).ToLowerInvariant()
    if ($extension -notin @(".md", ".markdown")) {
        throw "Only .md and .markdown files are supported: $resolvedPath"
    }
    $resolvedPaths += $resolvedPath
}
if (($resolvedPaths | ForEach-Object { $_.ToLowerInvariant() } |
        Select-Object -Unique).Count -ne $resolvedPaths.Count) {
    throw "Each Markdown file path must be unique."
}

if ($null -ne $RelativePath -and $RelativePath.Count -ne $resolvedPaths.Count) {
    throw "RelativePath must contain one value for every Path."
}
$relativePaths = @()
if ($null -eq $RelativePath) {
    foreach ($resolvedPath in $resolvedPaths) {
        $relativePaths += [IO.Path]::GetFileName($resolvedPath)
    }
}
else {
    foreach ($candidate in $RelativePath) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            throw "RelativePath values cannot be empty."
        }
        $normalized = $candidate.Replace("\", "/").Trim()
        $segments = @($normalized -split "/")
        if ($normalized.StartsWith("/") -or $normalized -match "^[A-Za-z]:" -or
            $segments -contains "" -or $segments -contains "." -or
            $segments -contains "..") {
            throw "RelativePath values must be safe relative paths: $candidate"
        }
        $relativePaths += $normalized
    }
}
if (($relativePaths | ForEach-Object { $_.ToLowerInvariant() } | Select-Object -Unique).Count -ne
    $relativePaths.Count) {
    throw "RelativePath values must be unique. Use -RelativePath when file names collide."
}

$resolvedApiBaseUrl = Resolve-ApiBaseUrl -ExplicitUrl $ApiBaseUrl
$uploadUrl = "$resolvedApiBaseUrl/knowledge-routing/import"
$fileSummary = @()
for ($index = 0; $index -lt $resolvedPaths.Count; $index++) {
    $fileSummary += [pscustomobject]@{
        file = $resolvedPaths[$index]
        relative_path = $relativePaths[$index]
        size_bytes = ([IO.FileInfo]::new($resolvedPaths[$index])).Length
    }
}

if ($DryRun) {
    [pscustomobject]@{
        dry_run = $true
        upload_url = $uploadUrl
        reasoning_owner = "host_cli"
        files = $fileSummary
        trust_level = $TrustLevel
        confidentiality = $Confidentiality
        waits_for_jobs = -not $NoWait
        credential_supplied = -not [string]::IsNullOrWhiteSpace($Token)
        credential_value_printed = $false
        next_step = "Use the current CLI model with debug_get_knowledge_routing_context and debug_apply_knowledge_routing."
    } | ConvertTo-Json -Depth 10
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "Set DEBUGPLATFORM_MCP_TOKEN in this process or pass -Token before uploading."
}

Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromHours(2)
$multipart = $null
try {
    $client.DefaultRequestHeaders.Authorization =
        [System.Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Token)
    # Existing REST deployments use X-API-Key; the same scoped token may be
    # accepted during the migration to Bearer auth. The value is never printed.
    $client.DefaultRequestHeaders.Add("X-API-Key", $Token)

    $multipart = [System.Net.Http.MultipartFormDataContent]::new()
    for ($index = 0; $index -lt $resolvedPaths.Count; $index++) {
        $stream = [IO.File]::OpenRead($resolvedPaths[$index])
        $content = [System.Net.Http.StreamContent]::new($stream)
        $content.Headers.ContentType =
            [System.Net.Http.Headers.MediaTypeHeaderValue]::new("text/markdown")
        $multipart.Add(
            $content,
            "files",
            [IO.Path]::GetFileName($resolvedPaths[$index])
        )
    }
    New-StringPart -Value (
        ConvertTo-Json -InputObject @($relativePaths) -Compress
    ) -Name "relative_paths_json" -Multipart $multipart
    New-StringPart -Value "host_cli" -Name "reasoning_owner" -Multipart $multipart
    New-StringPart -Value $TrustLevel -Name "trust_level" -Multipart $multipart
    New-StringPart -Value $Confidentiality -Name "confidentiality" -Multipart $multipart

    $uploadResponse = $client.PostAsync($uploadUrl, $multipart).GetAwaiter().GetResult()
    $uploadBody = Assert-SuccessResponse -Response $uploadResponse -Operation "Markdown routing upload"
    $batch = $uploadBody | ConvertFrom-Json

    if ($NoWait) {
        [pscustomobject]@{
            batch = $batch
            jobs = @()
            waited = $false
            next_step = "After every job completes, pass batch.items[].document_id to debug_get_knowledge_routing_context."
        } | ConvertTo-Json -Depth 30
        exit 0
    }

    $jobIds = @(
        $batch.items |
            ForEach-Object { [string]$_.job.id } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($jobIds.Count -ne [int]$batch.file_count) {
        throw "Upload response did not contain one job ID per Markdown file."
    }
    $terminalStatuses = @("COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTER")
    $latestJobs = @{}
    $deadline = [DateTime]::UtcNow.AddSeconds($MaxWaitSeconds)
    while ($true) {
        $pending = 0
        foreach ($jobId in $jobIds) {
            $escapedJobId = [Uri]::EscapeDataString($jobId)
            $jobResponse = $client.GetAsync(
                "$resolvedApiBaseUrl/jobs/$escapedJobId"
            ).GetAwaiter().GetResult()
            $jobBody = Assert-SuccessResponse -Response $jobResponse -Operation "Job status"
            $job = $jobBody | ConvertFrom-Json
            $latestJobs[$jobId] = $job
            if ([string]$job.status -notin $terminalStatuses) {
                $pending += 1
            }
        }
        if ($pending -eq 0) {
            break
        }
        if ([DateTime]::UtcNow -ge $deadline) {
            $result = [pscustomobject]@{
                batch = $batch
                jobs = @($jobIds | ForEach-Object { $latestJobs[$_] })
                waited = $true
                timed_out = $true
                next_step = "Do not classify until every staging job has status COMPLETED."
            }
            $result | ConvertTo-Json -Depth 30
            exit 2
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }

    $jobs = @($jobIds | ForEach-Object { $latestJobs[$_] })
    $failedJobs = @($jobs | Where-Object { [string]$_.status -ne "COMPLETED" })
    [pscustomobject]@{
        batch = $batch
        jobs = $jobs
        waited = $true
        timed_out = $false
        all_jobs_completed = ($failedJobs.Count -eq 0)
        document_ids = @($batch.items | ForEach-Object { [string]$_.document_id })
        backend_chat_calls = 0
        draft_only = $true
        next_step = "Use the current CLI model with debug_get_knowledge_routing_context, then debug_apply_knowledge_routing."
    } | ConvertTo-Json -Depth 30
    if ($failedJobs.Count -gt 0) {
        exit 1
    }
}
finally {
    if ($null -ne $multipart) { $multipart.Dispose() }
    $client.Dispose()
    $handler.Dispose()
}
