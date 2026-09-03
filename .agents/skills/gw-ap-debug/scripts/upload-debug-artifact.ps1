[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$CaseId,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Path,

    [string]$ApiBaseUrl = $env:DEBUGPLATFORM_API_BASE_URL,

    [ValidateSet("GW", "AP", "UNKNOWN")]
    [string]$SourceDeviceType = "UNKNOWN",

    [ValidateSet("PRIMARY", "SECONDARY", "UNKNOWN")]
    [string]$SourceDeviceRole = "UNKNOWN",

    [switch]$NoParse,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-ApiBaseUrl {
    param([string]$ExplicitUrl)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitUrl)) {
        return $ExplicitUrl.TrimEnd("/")
    }
    $mcpUrl = $env:DEBUGPLATFORM_MCP_URL
    if ([string]::IsNullOrWhiteSpace($mcpUrl)) {
        throw "Set DEBUGPLATFORM_MCP_URL or pass -ApiBaseUrl."
    }
    $mcpUri = $null
    if (-not [Uri]::TryCreate($mcpUrl, [UriKind]::Absolute, [ref]$mcpUri)) {
        throw "DEBUGPLATFORM_MCP_URL must be an absolute HTTP(S) URL."
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

$resolvedPath = [IO.Path]::GetFullPath($Path)
if (-not [IO.File]::Exists($resolvedPath)) {
    throw "Upload file does not exist: $resolvedPath"
}
$resolvedApiBaseUrl = Resolve-ApiBaseUrl -ExplicitUrl $ApiBaseUrl
$escapedCaseId = [Uri]::EscapeDataString($CaseId)
$uploadUrl = "$resolvedApiBaseUrl/cases/$escapedCaseId/artifacts"

if ($DryRun) {
    [pscustomobject]@{
        dry_run = $true
        upload_url = $uploadUrl
        file = $resolvedPath
        size_bytes = ([IO.FileInfo]::new($resolvedPath)).Length
        source_device_type = $SourceDeviceType
        source_device_role = $SourceDeviceRole
        starts_parse = -not $NoParse
        credential_source = "DEBUGPLATFORM_MCP_TOKEN"
    } | ConvertTo-Json -Depth 5
    exit 0
}

$token = $env:DEBUGPLATFORM_MCP_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "Set DEBUGPLATFORM_MCP_TOKEN in this process before uploading."
}

Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$client = [System.Net.Http.HttpClient]::new($handler)
$fileStream = $null
$multipart = $null
try {
    $client.DefaultRequestHeaders.Authorization =
        [System.Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $token)
    # Existing REST deployments use X-API-Key; the same scoped token may be
    # accepted during the migration to Bearer auth. The value is never printed.
    $client.DefaultRequestHeaders.Add("X-API-Key", $token)

    $multipart = [System.Net.Http.MultipartFormDataContent]::new()
    $fileStream = [IO.File]::OpenRead($resolvedPath)
    $fileContent = [System.Net.Http.StreamContent]::new($fileStream)
    $fileContent.Headers.ContentType =
        [System.Net.Http.Headers.MediaTypeHeaderValue]::new("application/octet-stream")
    $multipart.Add($fileContent, "file", [IO.Path]::GetFileName($resolvedPath))
    $multipart.Add([System.Net.Http.StringContent]::new("debug_log"), "kind")
    $multipart.Add([System.Net.Http.StringContent]::new($SourceDeviceType), "source_device_type")
    $multipart.Add([System.Net.Http.StringContent]::new($SourceDeviceRole), "source_device_role")

    $uploadResponse = $client.PostAsync($uploadUrl, $multipart).GetAwaiter().GetResult()
    $uploadBody = Assert-SuccessResponse -Response $uploadResponse -Operation "Artifact upload"
    $artifact = $uploadBody | ConvertFrom-Json

    $parseJob = $null
    if (-not $NoParse) {
        if ([string]::IsNullOrWhiteSpace([string]$artifact.id)) {
            throw "Upload response did not contain artifact.id."
        }
        $escapedArtifactId = [Uri]::EscapeDataString([string]$artifact.id)
        $parseUrl = "$resolvedApiBaseUrl/cases/$escapedCaseId/artifacts/$escapedArtifactId/parse"
        $emptyContent = [System.Net.Http.StringContent]::new("")
        $parseResponse = $client.PostAsync($parseUrl, $emptyContent).GetAwaiter().GetResult()
        $parseBody = Assert-SuccessResponse -Response $parseResponse -Operation "Parse start"
        $parseJob = $parseBody | ConvertFrom-Json
    }

    [pscustomobject]@{
        artifact = $artifact
        parse_job = $parseJob
    } | ConvertTo-Json -Depth 20
}
finally {
    if ($null -ne $fileStream) { $fileStream.Dispose() }
    if ($null -ne $multipart) { $multipart.Dispose() }
    $client.Dispose()
    $handler.Dispose()
}

