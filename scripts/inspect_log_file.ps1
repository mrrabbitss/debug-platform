[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$LogPath,

    [string]$OutputPath,

    [switch]$SkipLineCount
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Get-EncodingHint {
    param([byte[]]$Bytes)

    $byteCount = $Bytes.Length
    if ($byteCount -eq 0) { return "Empty file" }
    if ($byteCount -ge 4) {
        if ($Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE -and $Bytes[2] -eq 0x00 -and $Bytes[3] -eq 0x00) {
            return "UTF-32 LE with BOM"
        }
        if ($Bytes[0] -eq 0x00 -and $Bytes[1] -eq 0x00 -and $Bytes[2] -eq 0xFE -and $Bytes[3] -eq 0xFF) {
            return "UTF-32 BE with BOM"
        }
    }
    if ($byteCount -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        return "UTF-8 with BOM"
    }
    if ($byteCount -ge 2) {
        if ($Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) { return "UTF-16 LE with BOM" }
        if ($Bytes[0] -eq 0xFE -and $Bytes[1] -eq 0xFF) { return "UTF-16 BE with BOM" }
    }

    $evenSlots = [Math]::Ceiling($byteCount / 2.0)
    $oddSlots = [Math]::Floor($byteCount / 2.0)
    $evenNuls = 0
    $oddNuls = 0
    for ($index = 0; $index -lt $byteCount; $index++) {
        if ($Bytes[$index] -ne 0) { continue }
        if (($index % 2) -eq 0) { $evenNuls++ } else { $oddNuls++ }
    }
    $evenRatio = if ($evenSlots -gt 0) { $evenNuls / $evenSlots } else { 0 }
    $oddRatio = if ($oddSlots -gt 0) { $oddNuls / $oddSlots } else { 0 }
    if ($oddRatio -ge 0.30 -and $evenRatio -le 0.05) { return "Likely UTF-16 LE without BOM" }
    if ($evenRatio -ge 0.30 -and $oddRatio -le 0.05) { return "Likely UTF-16 BE without BOM" }
    $nulTotal = $evenNuls + $oddNuls
    $nulRatio = if ($byteCount -gt 0) { $nulTotal / $byteCount } else { 0 }
    if ($nulTotal -gt 0 -and $nulRatio -le 0.01) { return "8-bit text with sparse NUL bytes" }
    if ($nulTotal -gt 0) { return "Mixed/binary or unsupported Unicode without BOM" }
    return "UTF-8/ASCII, ANSI/GBK, or another 8-bit encoding without BOM"
}

function Get-SampleText {
    param(
        [byte[]]$Bytes,
        [string]$EncodingHint
    )

    if ($Bytes.Length -eq 0) { return "" }
    if ($EncodingHint -like "UTF-32 LE*") {
        return ([System.Text.UTF32Encoding]::new($false, $true, $false)).GetString($Bytes)
    }
    if ($EncodingHint -like "UTF-32 BE*") {
        return ([System.Text.UTF32Encoding]::new($true, $true, $false)).GetString($Bytes)
    }
    if ($EncodingHint -like "UTF-16 LE*") {
        return ([System.Text.UnicodeEncoding]::new($false, $true, $false)).GetString($Bytes)
    }
    if ($EncodingHint -like "UTF-16 BE*") {
        return ([System.Text.UnicodeEncoding]::new($true, $true, $false)).GetString($Bytes)
    }
    if ($EncodingHint -like "UTF-8*") {
        return ([System.Text.UTF8Encoding]::new($false, $false)).GetString($Bytes)
    }
    return [System.Text.Encoding]::Default.GetString($Bytes)
}

function Convert-ToYesNo {
    param([bool]$Value)
    if ($Value) { return "YES" }
    return "NO"
}

if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
    Write-Error "Log file was not found: $LogPath"
    exit 2
}

$resolvedLogPath = (Resolve-Path -LiteralPath $LogPath).Path
$logInfo = Get-Item -LiteralPath $resolvedLogPath
$repoRoot = Split-Path -Parent $PSScriptRoot
$parserMaxBytes = 134217728L
$maxSingleFileBytes = 536870912L
$envPath = Join-Path $repoRoot ".env"
if (Test-Path -LiteralPath $envPath -PathType Leaf) {
    foreach ($envLine in [System.IO.File]::ReadLines($envPath)) {
        if ($envLine -match "^\s*PARSER_MAX_TEXT_BYTES\s*=\s*(\d+)\s*$") {
            $parserMaxBytes = [long]$Matches[1]
            break
        }
    }
}

$sampleCount = [int][Math]::Min(1048576L, $logInfo.Length)
$sampleBytes = [byte[]]@()
if ($sampleCount -gt 0) {
    $sampleBuffer = New-Object byte[] $sampleCount
    $sampleStream = [System.IO.File]::Open(
        $logInfo.FullName,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        $totalRead = 0
        while ($totalRead -lt $sampleCount) {
            $currentRead = $sampleStream.Read($sampleBuffer, $totalRead, $sampleCount - $totalRead)
            if ($currentRead -eq 0) { break }
            $totalRead += $currentRead
        }
    } finally {
        $sampleStream.Dispose()
    }
    $sampleBytes = New-Object byte[] $totalRead
    [Array]::Copy($sampleBuffer, $sampleBytes, $totalRead)
}

$probeCount = [Math]::Min(65536, $sampleBytes.Length)
$nulCount = 0
$controlCount = 0
$firstFour = @()
for ($index = 0; $index -lt $probeCount; $index++) {
    $currentByte = $sampleBytes[$index]
    if ($currentByte -eq 0) { $nulCount++ }
    if ($currentByte -lt 32 -and $currentByte -notin @(8, 9, 10, 12, 13)) { $controlCount++ }
    if ($index -lt 4) { $firstFour += $currentByte.ToString("X2") }
}
$controlPercent = if ($probeCount -gt 0) {
    [Math]::Round(100.0 * $controlCount / $probeCount, 3)
} else {
    0
}
$nulPercent = if ($probeCount -gt 0) {
    [Math]::Round(100.0 * $nulCount / $probeCount, 3)
} else {
    0
}
$encodingHint = Get-EncodingHint -Bytes $sampleBytes
$hasSupportedBom = $encodingHint -match "with BOM$"
$appContentProbe = $hasSupportedBom -or $controlPercent -le 1.0
$withinParserLimit = $logInfo.Length -le $parserMaxBytes
$withinSingleFileLimit = $logInfo.Length -le $maxSingleFileBytes

$lineCount = "SKIPPED"
if (-not $SkipLineCount) {
    $countedLines = 0L
    if ($logInfo.Length -gt 0) {
        $lineReader = [System.IO.StreamReader]::new(
            $logInfo.FullName,
            [System.Text.Encoding]::UTF8,
            $true,
            65536
        )
        try {
            while ($null -ne $lineReader.ReadLine()) { $countedLines++ }
        } finally {
            $lineReader.Dispose()
        }
    }
    $lineCount = $countedLines.ToString()
}

$sampleText = Get-SampleText -Bytes $sampleBytes -EncodingHint $encodingHint
$hasCommandMarker = $sampleText.IndexOf(
    "Start run collect command:",
    [System.StringComparison]::OrdinalIgnoreCase
) -ge 0
$hasWlanConfigMarker = $sampleText.IndexOf(
    "get WLANConfiguration!",
    [System.StringComparison]::OrdinalIgnoreCase
) -ge 0
$hasRuntimeMarker = [regex]::IsMatch(
    $sampleText,
    "(?im)^\s*(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERR(?:OR)?|CRIT(?:ICAL)?|FATAL)\s+20\d{2}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}[:;]\d{2}"
)
$filenameMarker = $logInfo.Name.IndexOf(
    "collectDebuginfo",
    [System.StringComparison]::OrdinalIgnoreCase
) -ge 0
$normalizedUploadName = if ([string]::IsNullOrWhiteSpace($logInfo.Extension)) {
    "$($logInfo.Name).txt"
} else {
    $logInfo.Name
}
$predictedParser = if ($filenameMarker -or $hasCommandMarker -or $hasWlanConfigMarker) {
    "huawei-collectdebuginfo"
} elseif ($hasRuntimeMarker) {
    "generic-log or huawei-collectdebuginfo"
} else {
    "generic-log / unknown"
}

function Invoke-GitValue {
    param([string[]]$Arguments)
    try {
        $gitValue = & git -C $repoRoot @Arguments 2>$null
        if ($LASTEXITCODE -eq 0 -and $null -ne $gitValue) {
            return [string]($gitValue | Select-Object -First 1)
        }
    } catch {
        return "UNAVAILABLE"
    }
    return "UNAVAILABLE"
}

$repoHead = Invoke-GitValue -Arguments @("rev-parse", "--short", "HEAD")
$repoHeadFull = Invoke-GitValue -Arguments @("rev-parse", "HEAD")
$originMain = Invoke-GitValue -Arguments @("rev-parse", "--short", "origin/main")
$originMainFull = Invoke-GitValue -Arguments @("rev-parse", "origin/main")
$repoBranch = Invoke-GitValue -Arguments @("branch", "--show-current")
$dirtyCount = 0
try {
    $dirtyLines = @(& git -C $repoRoot status --porcelain 2>$null)
    if ($LASTEXITCODE -eq 0) { $dirtyCount = $dirtyLines.Count }
} catch {
    $dirtyCount = -1
}
$repoSync = $repoHeadFull -ne "UNAVAILABLE" -and $repoHeadFull -eq $originMainFull

$portState = "NOT_LISTENING"
$backendExecutable = "N/A"
$backendCommand = "N/A"
$backendEntryCheck = "N/A"
try {
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $listener) {
        $portState = "LISTENING"
        $backendProcessId = $listener.OwningProcess
        $backendProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $backendProcessId"
        if ($null -ne $backendProcess) {
            $backendExecutable = [string]$backendProcess.ExecutablePath
            $backendCommand = [string]$backendProcess.CommandLine
            $expectedPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
            $usesRepoPython = $backendExecutable -ieq $expectedPython
            $usesExpectedEntry = $backendCommand -match "(?:^|\s)app\.main:app(?:\s|$)"
            $backendEntryCheck = if ($usesRepoPython -and $usesExpectedEntry) { "PASS" } else { "CHECK_REQUIRED" }
        }
    }
} catch {
    $portState = "CHECK_UNAVAILABLE"
}

$serviceRootName = "UNAVAILABLE"
$serviceRootCheck = "UNAVAILABLE"
if ($portState -eq "LISTENING") {
    try {
        $rootResponse = Invoke-RestMethod -Uri "http://127.0.0.1:8000/" -TimeoutSec 3
        if ($null -ne $rootResponse.PSObject.Properties["name"]) {
            $serviceRootName = [string]$rootResponse.name
        }
        $hasExpectedApi = $null -ne $rootResponse.PSObject.Properties["api"] -and $rootResponse.api -eq "/api/v1"
        $hasExpectedDocs = $null -ne $rootResponse.PSObject.Properties["docs"] -and $rootResponse.docs -eq "/docs"
        $serviceRootCheck = if ($hasExpectedApi -and $hasExpectedDocs) { "PASS" } else { "CHECK_REQUIRED" }
    } catch {
        $serviceRootCheck = "REQUEST_FAILED"
    }
}

$recommendations = @()
if ([string]::IsNullOrWhiteSpace($logInfo.Extension)) {
    $recommendations += "The new upload flow will store this file as $normalizedUploadName."
}
if (-not $withinParserLimit) {
    $recommendations += "File exceeds PARSER_MAX_TEXT_BYTES. Split it or increase the configured limit before parsing."
}
if (-not $withinSingleFileLimit) {
    $recommendations += "File exceeds the single extracted-file safety limit."
}
if (-not $appContentProbe) {
    $recommendations += "Current parser text probe will reject this encoding/content even after .txt is appended."
}
if ($nulCount -gt 0 -and $appContentProbe) {
    $recommendations += "Sparse NUL bytes are supported and will be removed from decoded text; the uploaded original is preserved."
}
if ($encodingHint -like "Likely UTF-16*without BOM") {
    $recommendations += "BOM-less UTF-16 is likely. Convert a copy to UTF-8, or add BOM-less UTF-16 support."
}
if (-not ($filenameMarker -or $hasCommandMarker -or $hasWlanConfigMarker -or $hasRuntimeMarker)) {
    $recommendations += "No known Huawei collect/runtime marker was found in the first 1 MiB."
}
if (-not $repoSync) {
    $recommendations += "Local HEAD does not match the local origin/main tracking ref. Run git fetch/pull when allowed."
}
if ($portState -eq "LISTENING" -and ($backendEntryCheck -ne "PASS" -or $serviceRootCheck -ne "PASS")) {
    $recommendations += "Port 8000 may be using another or stale backend. Verify it before starting the frontend."
}
if ($recommendations.Count -eq 0) {
    $recommendations += "No blocking condition was detected by this offline check."
}

$reportLines = @(
    "GW/AP Log Inspection Report",
    "GeneratedAt              : $([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz'))",
    "Privacy                  : No log content is included in this report.",
    "",
    "[File]",
    "Path                     : $resolvedLogPath",
    "Name                     : $($logInfo.Name)",
    "Extension                : $($logInfo.Extension)",
    "NormalizedUploadName     : $normalizedUploadName",
    "SizeBytes                : $($logInfo.Length)",
    "SizeMiB                  : $([Math]::Round($logInfo.Length / 1MB, 2))",
    "LineCount                : $lineCount",
    "First4Hex                : $($firstFour -join ' ')",
    "EncodingHint             : $encodingHint",
    "NulBytesFirst64KiB       : $nulCount",
    "NulPercentFirst64KiB     : $nulPercent",
    "ControlPercentFirst64KiB : $controlPercent",
    "AppTextContentProbe      : $(if ($appContentProbe) { 'PASS' } else { 'FAIL' })",
    "WithinParserSizeLimit    : $(Convert-ToYesNo $withinParserLimit) ($parserMaxBytes bytes)",
    "WithinSingleFileLimit    : $(Convert-ToYesNo $withinSingleFileLimit) ($maxSingleFileBytes bytes)",
    "",
    "[Format markers: first 1 MiB]",
    "FilenameCollectMarker    : $(Convert-ToYesNo $filenameMarker)",
    "CollectCommandMarker     : $(Convert-ToYesNo $hasCommandMarker)",
    "WlanConfigurationMarker  : $(Convert-ToYesNo $hasWlanConfigMarker)",
    "RuntimeLevelMarker       : $(Convert-ToYesNo $hasRuntimeMarker)",
    "PredictedParser          : $predictedParser",
    "",
    "[Repository]",
    "RepoRoot                 : $repoRoot",
    "Branch                   : $repoBranch",
    "HEAD                     : $repoHead",
    "OriginMain               : $originMain",
    "HeadMatchesOriginMain    : $(Convert-ToYesNo $repoSync)",
    "DirtyPathCount           : $dirtyCount",
    "",
    "[Backend port 8000]",
    "PortState                : $portState",
    "Executable               : $backendExecutable",
    "CommandLine              : $backendCommand",
    "RepoPythonAndEntryCheck  : $backendEntryCheck",
    "ServiceRootName          : $serviceRootName",
    "ServiceRootCheck         : $serviceRootCheck",
    "",
    "[Recommendations]"
)
foreach ($recommendation in $recommendations) {
    $reportLines += "- $recommendation"
}
$reportText = $reportLines -join [Environment]::NewLine

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "log_check_result.txt"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repoRoot $OutputPath
}
$outputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
[System.IO.File]::WriteAllText(
    $OutputPath,
    $reportText,
    [System.Text.UTF8Encoding]::new($true)
)

Write-Output $reportText
Write-Output ""
Write-Output "Report saved to: $OutputPath"
