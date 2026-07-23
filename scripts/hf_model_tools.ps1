Set-StrictMode -Version Latest

function Normalize-HfEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint
    )

    $value = $Endpoint.Trim().TrimEnd("/")
    try {
        $uri = [System.Uri]$value
    } catch {
        throw "Mirror must be a valid HTTP(S) URL."
    }
    if (
        -not $uri.IsAbsoluteUri -or
        $uri.Scheme -notin @("http", "https") -or
        [string]::IsNullOrWhiteSpace($uri.Host) -or
        -not [string]::IsNullOrWhiteSpace($uri.UserInfo) -or
        -not [string]::IsNullOrWhiteSpace($uri.Query) -or
        -not [string]::IsNullOrWhiteSpace($uri.Fragment)
    ) {
        throw (
            "Mirror must be an HTTP(S) URL with a hostname and no credentials, " +
            "query, or fragment."
        )
    }
    return $value
}

function Set-HfOnlineEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,
        [Parameter(Mandatory = $true)]
        [string]$CacheRoot
    )

    $env:PYTHONUTF8 = "1"
    $env:HF_ENDPOINT = $Endpoint
    $env:HF_HOME = $CacheRoot
    $env:HF_HUB_DOWNLOAD_TIMEOUT = "120"
    $env:HF_HUB_ETAG_TIMEOUT = "30"
    $env:HF_HUB_OFFLINE = "0"
    $env:TRANSFORMERS_OFFLINE = "0"
    $env:HF_HUB_DISABLE_IMPLICIT_TOKEN = "1"
    $env:HF_HUB_DISABLE_TELEMETRY = "1"
    # Corporate networks and mirrors are more likely to allow normal HTTPS
    # downloads than direct Xet CAS endpoints.
    $env:HF_HUB_DISABLE_XET = "1"
    $env:DO_NOT_TRACK = "1"
}

function ConvertTo-HfUrlPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $parts = $Value.Replace("\", "/").Split("/")
    if (
        $parts.Count -eq 0 -or
        $parts | Where-Object { [string]::IsNullOrWhiteSpace($_) -or $_ -in @(".", "..") }
    ) {
        throw "Invalid Hugging Face path: $Value"
    }
    return (($parts | ForEach-Object { [System.Uri]::EscapeDataString($_) }) -join "/")
}

function Resolve-HfSafeChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $normalized = $RelativePath.Replace("\", "/")
    $parts = $normalized.Split("/")
    if (
        $normalized.StartsWith("/") -or
        $parts.Count -eq 0 -or
        $parts | Where-Object { $_ -in @("", ".", "..") }
    ) {
        throw "Unsafe path returned by the model repository: $RelativePath"
    }
    $invalidCharacters = [System.IO.Path]::GetInvalidFileNameChars()
    if ($parts | Where-Object { $_.IndexOfAny($invalidCharacters) -ge 0 }) {
        throw "Unsafe path returned by the model repository: $RelativePath"
    }
    $rootPath = [System.IO.Path]::GetFullPath($Root)
    $relativeLocalPath = $parts -join [System.IO.Path]::DirectorySeparatorChar
    $target = [System.IO.Path]::GetFullPath((Join-Path $rootPath $relativeLocalPath))
    $rootPrefix = $rootPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path returned by the model repository: $RelativePath"
    }
    return $target
}

function Get-HfCurlPath {
    $command = Get-Command "curl.exe" -ErrorAction SilentlyContinue
    if (-not $command) {
        throw (
            "curl.exe was not found. It is included with supported Windows 11 installations. " +
            "Ask the administrator to restore it or use -DownloadMode HfCli."
        )
    }
    return $command.Source
}

function Get-HfRepositoryMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [string]$Revision = ""
    )

    $curl = Get-HfCurlPath
    $repositoryPath = ConvertTo-HfUrlPath $Repository
    $metadataUrl = if ([string]::IsNullOrWhiteSpace($Revision)) {
        "$Endpoint/api/models/$repositoryPath" + "?blobs=true"
    } else {
        $revisionPath = [System.Uri]::EscapeDataString($Revision)
        "$Endpoint/api/models/$repositoryPath/revision/$revisionPath" + "?blobs=true"
    }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $response = @(
            & $curl `
                --silent `
                --show-error `
                --fail `
                --location `
                --retry 5 `
                --retry-delay 3 `
                --retry-all-errors `
                --connect-timeout 30 `
                --max-time 180 `
                $metadataUrl 2>&1
        )
        $curlExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($curlExitCode -ne 0) {
        $detail = (($response | ForEach-Object { [string]$_ }) -join " ").Trim()
        if ($detail.Length -gt 1000) {
            $detail = $detail.Substring(0, 1000)
        }
        throw "Mirror metadata request failed for $Repository via curl.exe: $detail"
    }
    try {
        $metadata = (($response | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json
    } catch {
        throw "Mirror returned invalid model metadata for $Repository."
    }
    if (
        $metadata.id -ne $Repository -or
        [string]::IsNullOrWhiteSpace([string]$metadata.sha) -or
        -not $metadata.siblings
    ) {
        throw "Mirror returned incomplete model metadata for $Repository."
    }
    if (
        -not [string]::IsNullOrWhiteSpace($Revision) -and
        ([string]$metadata.sha).ToLowerInvariant() -ne $Revision.ToLowerInvariant()
    ) {
        throw (
            "Mirror resolved an unexpected revision for ${Repository}: " +
            "$($metadata.sha), expected $Revision."
        )
    }
    return $metadata
}

function Get-HfRuntimeFiles {
    param(
        [Parameter(Mandatory = $true)]
        $Metadata
    )

    $files = [System.Collections.Generic.List[object]]::new()
    foreach ($sibling in $Metadata.siblings) {
        $relativePath = [string]$sibling.rfilename
        $lowerPath = $relativePath.Replace("\", "/").ToLowerInvariant()
        if (
            $lowerPath.StartsWith("onnx/") -or
            $lowerPath.StartsWith("openvino/") -or
            $lowerPath.EndsWith(".onnx") -or
            $lowerPath.EndsWith(".h5") -or
            $lowerPath.EndsWith(".msgpack") -or
            $lowerPath.EndsWith(".tflite") -or
            $lowerPath.EndsWith(".ot")
        ) {
            continue
        }
        $sizeProperty = $sibling.PSObject.Properties["size"]
        $lfsProperty = $sibling.PSObject.Properties["lfs"]
        $lfs = if ($null -ne $lfsProperty) { $lfsProperty.Value } else { $null }
        $size = [long]0
        if ($null -ne $sizeProperty -and $null -ne $sizeProperty.Value) {
            $size = [long]$sizeProperty.Value
        } elseif (
            $null -ne $lfs -and
            $null -ne $lfs.PSObject.Properties["size"]
        ) {
            $size = [long]$lfs.PSObject.Properties["size"].Value
        }
        $sha256 = ""
        if (
            $null -ne $lfs -and
            $null -ne $lfs.PSObject.Properties["sha256"]
        ) {
            $sha256 = ([string]$lfs.PSObject.Properties["sha256"].Value).ToLowerInvariant()
        }
        $files.Add([pscustomobject]@{
            RelativePath = $relativePath
            Size = $size
            Sha256 = $sha256
        })
    }
    if ($files.Count -eq 0) {
        throw "No runtime files were returned for model repository $($Metadata.id)."
    }
    return $files
}

function Test-HfDownloadedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [long]$ExpectedSize,
        [string]$ExpectedSha256 = ""
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $actualSize = (Get-Item -LiteralPath $Path).Length
    if ($ExpectedSize -gt 0 -and $actualSize -ne $ExpectedSize) {
        return $false
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSha256)) {
        $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
            return $false
        }
    }
    return $true
}

function Invoke-HfCurlFileDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$Revision,
        [Parameter(Mandatory = $true)]
        [string]$RelativePath,
        [Parameter(Mandatory = $true)]
        [string]$DestinationRoot,
        [Parameter(Mandatory = $true)]
        [long]$ExpectedSize,
        [string]$ExpectedSha256 = ""
    )

    $curl = Get-HfCurlPath
    $target = Resolve-HfSafeChildPath -Root $DestinationRoot -RelativePath $RelativePath
    if (Test-HfDownloadedFile `
        -Path $target `
        -ExpectedSize $ExpectedSize `
        -ExpectedSha256 $ExpectedSha256
    ) {
        Write-Host "[OK] Reusing verified file: $RelativePath"
        return
    }

    $targetDirectory = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
    $partial = "$target.partial"
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $targetSize = (Get-Item -LiteralPath $target).Length
        if ($ExpectedSize -gt 0 -and $targetSize -ge $ExpectedSize) {
            [System.IO.File]::Delete($target)
        } elseif (-not (Test-Path -LiteralPath $partial -PathType Leaf)) {
            Move-Item -LiteralPath $target -Destination $partial
        } else {
            [System.IO.File]::Delete($target)
        }
    }
    if (Test-Path -LiteralPath $partial -PathType Leaf) {
        if (Test-HfDownloadedFile `
            -Path $partial `
            -ExpectedSize $ExpectedSize `
            -ExpectedSha256 $ExpectedSha256
        ) {
            [System.IO.File]::Move($partial, $target)
            Write-Host "[OK] Recovered completed partial file: $RelativePath"
            return
        }
        if (
            $ExpectedSize -gt 0 -and
            (Get-Item -LiteralPath $partial).Length -ge $ExpectedSize
        ) {
            [System.IO.File]::Delete($partial)
        }
    }

    $repositoryPath = ConvertTo-HfUrlPath $Repository
    $revisionPath = [System.Uri]::EscapeDataString($Revision)
    $filePath = ConvertTo-HfUrlPath $RelativePath
    $downloadUrl = "$Endpoint/$repositoryPath/resolve/$revisionPath/$filePath" + "?download=true"
    Write-Host "[INFO] curl fallback: $Repository/$RelativePath"
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $curl `
            --fail `
            --location `
            --retry 5 `
            --retry-delay 3 `
            --retry-all-errors `
            --connect-timeout 30 `
            --speed-limit 1024 `
            --speed-time 90 `
            --continue-at - `
            --output $partial `
            $downloadUrl
        $curlExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($curlExitCode -ne 0) {
        throw "curl.exe failed to download $Repository/$RelativePath (exit $curlExitCode)."
    }
    if (-not (Test-HfDownloadedFile `
        -Path $partial `
        -ExpectedSize $ExpectedSize `
        -ExpectedSha256 $ExpectedSha256
    )) {
        throw "Downloaded file failed size or SHA-256 validation: $Repository/$RelativePath"
    }
    if (Test-Path -LiteralPath $target) {
        [System.IO.File]::Delete($target)
    }
    [System.IO.File]::Move($partial, $target)
    Write-Host "[OK] Downloaded and verified: $RelativePath"
}

function Invoke-HfCurlRepositoryDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Endpoint,
        [Parameter(Mandatory = $true)]
        [string]$Repository,
        [Parameter(Mandatory = $true)]
        [string]$Revision,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Write-Host "[INFO] Reading repository manifest through curl.exe: $Repository"
    $metadata = Get-HfRepositoryMetadata `
        -Endpoint $Endpoint `
        -Repository $Repository `
        -Revision $Revision
    $files = @(Get-HfRuntimeFiles -Metadata $metadata)
    $totalBytes = [long](($files | Measure-Object -Property Size -Sum).Sum)
    Write-Host (
        "[INFO] curl fallback plan: $($files.Count) files, " +
        "$([Math]::Round($totalBytes / 1GB, 2)) GiB, revision $($metadata.sha)"
    )
    foreach ($file in $files) {
        Invoke-HfCurlFileDownload `
            -Endpoint $Endpoint `
            -Repository $Repository `
            -Revision ([string]$metadata.sha) `
            -RelativePath $file.RelativePath `
            -DestinationRoot $Destination `
            -ExpectedSize $file.Size `
            -ExpectedSha256 $file.Sha256
    }
    return [pscustomobject]@{
        Repository = $Repository
        Revision = [string]$metadata.sha
        FileCount = $files.Count
        Bytes = $totalBytes
    }
}
