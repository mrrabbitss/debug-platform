[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$FailureLog,
    [Parameter(Mandatory = $true)][string]$FailureSummary
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ExpectedLogDirectory {
    $localAppData = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::LocalApplicationData
    )
    if ([string]::IsNullOrWhiteSpace($localAppData)) {
        throw "LOCALAPPDATA is unavailable."
    }
    return [System.IO.Path]::GetFullPath((Join-Path $localAppData "GWAPDebugServer\install-logs"))
}

function Assert-DurableLogPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedDirectory
    )
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $resolved))
    if (-not [string]::Equals(
        $parent.TrimEnd('\'),
        $ExpectedDirectory.TrimEnd('\'),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to write the installer diagnostic outside the per-user log directory."
    }
    return $resolved
}

function Protect-InstallOutput {
    param([AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) {
        return ""
    }
    $Text = $Text.TrimStart([char]0xFEFF)

    # Payload diagnostics must remain useful without retaining credentials or
    # configuration values if a future publisher happens to write them.
    $redacted = [System.Text.RegularExpressions.Regex]::Replace(
        $Text,
        '(?is)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----',
        '[REDACTED PRIVATE KEY]'
    )
    $redacted = [System.Text.RegularExpressions.Regex]::Replace(
        $redacted,
        '(?i)(Bearer\s+)[^\s"'']+',
        '$1[REDACTED]'
    )
    $redacted = [System.Text.RegularExpressions.Regex]::Replace(
        $redacted,
        '(?im)^(\s*(?:api[_-]?key|token|secret|password|credential|authorization|private[_-]?key|server[_-]?url|base[_-]?url|endpoint|proxy)\s*(?:=|:)\s*).*$',
        '$1[REDACTED]'
    )
    $redacted = [System.Text.RegularExpressions.Regex]::Replace(
        $redacted,
        '(?i)("(?:api[_-]?key|token|secret|password|credential|authorization|private[_-]?key|server[_-]?url|base[_-]?url|endpoint|proxy)"\s*:\s*)"[^"]*"',
        '$1"[REDACTED]"'
    )
    $redacted = [System.Text.RegularExpressions.Regex]::Replace(
        $redacted,
        '(?im)^(\s*[A-Za-z_][A-Za-z0-9_.-]{0,127}\s*=\s*).*$',
        '$1[REDACTED]'
    )
    return $redacted
}

function Get-UsefulFailureReason {
    param(
        [AllowEmptyString()][string]$StandardError,
        [AllowEmptyString()][string]$StandardOutput,
        [Parameter(Mandatory = $true)][int]$ExitCode
    )
    foreach ($stream in @($StandardError, $StandardOutput)) {
        $lines = @($stream -split "`r?`n")
        for ($index = $lines.Count - 1; $index -ge 0; $index--) {
            $candidate = (Protect-InstallOutput -Text $lines[$index]).Trim()
            if (
                $candidate -and
                $candidate -notmatch '\[REDACTED' -and
                $candidate -notmatch '^\[(INFO|OK)\]' -and
                $candidate -notmatch '^<frozen site>:' -and
                $candidate -notmatch '^At .+:[0-9]+'
            ) {
                $candidate = [System.Text.RegularExpressions.Regex]::Replace($candidate, '\s+', ' ')
                if ($candidate.Length -gt 360) {
                    $candidate = $candidate.Substring(0, 357) + "..."
                }
                return $candidate
            }
        }
    }
    return "The offline payload publisher exited with code " + $ExitCode + "."
}

function Write-FailureArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$SummaryPath,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$Reason,
        [AllowEmptyString()][string]$StandardOutput,
        [AllowEmptyString()][string]$StandardError
    )
    $logEncoding = [System.Text.UTF8Encoding]::new($false)
    $summaryEncoding = [System.Text.UTF8Encoding]::new($false)
    $log = @(
        "GWAP Debug Server installer payload failure",
        "Exit code: $ExitCode",
        "Reason: $Reason",
        "",
        "--- publisher stdout (sanitized) ---",
        (Protect-InstallOutput -Text $StandardOutput),
        "",
        "--- publisher stderr (sanitized) ---",
        (Protect-InstallOutput -Text $StandardError)
    ) -join [Environment]::NewLine
    [System.IO.File]::WriteAllText($LogPath, $log, $logEncoding)
    [System.IO.File]::WriteAllText($SummaryPath, $Reason, $summaryEncoding)
}

$logDirectory = Get-ExpectedLogDirectory
[System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null
$failureLogPath = Assert-DurableLogPath -Path $FailureLog -ExpectedDirectory $logDirectory
$failureSummaryPath = Assert-DurableLogPath -Path $FailureSummary -ExpectedDirectory $logDirectory
$stdout = ""
$stderr = ""
$process = $null

try {
    $payload = [System.IO.Path]::GetFullPath($PayloadRoot)
    $installer = Join-Path $payload "install_local.ps1"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "The offline payload publisher is missing from the extracted package."
    }
    $installRoot = [System.IO.Path]::GetFullPath($InstallRoot)
    $powerShell = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $powerShell -PathType Leaf)) {
        throw "Windows PowerShell is unavailable for the offline payload publisher."
    }

    # A fixed UTF-8 child entry point avoids the console/OEM encoding mismatch
    # on Chinese Windows. Paths are process-local data, never interpolated code.
    $childCommand = @'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$global:LASTEXITCODE = 0
& $env:GWAP_SETUP_PUBLISHER -InstallRoot $env:GWAP_SETUP_TARGET -NoLaunch -NoShortcuts -Components Full
exit $LASTEXITCODE
'@
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $powerShell
    $startInfo.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -OutputFormat Text -Command "' + $childCommand + '"'
    $startInfo.EnvironmentVariables['GWAP_SETUP_PUBLISHER'] = $installer
    $startInfo.EnvironmentVariables['GWAP_SETUP_TARGET'] = $installRoot
    $startInfo.WorkingDirectory = $payload
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "Unable to start Windows PowerShell for the offline payload publisher."
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    if ($exitCode -eq 0) {
        exit 0
    }

    $reason = Get-UsefulFailureReason -StandardError $stderr -StandardOutput $stdout -ExitCode $exitCode
    Write-FailureArtifacts -LogPath $failureLogPath -SummaryPath $failureSummaryPath `
        -ExitCode $exitCode -Reason $reason -StandardOutput $stdout -StandardError $stderr
    exit $exitCode
} catch {
    $reason = Protect-InstallOutput -Text $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($reason)) {
        $reason = "The diagnostic wrapper could not start the offline payload publisher."
    }
    Write-FailureArtifacts -LogPath $failureLogPath -SummaryPath $failureSummaryPath `
        -ExitCode 1 -Reason $reason -StandardOutput $stdout -StandardError $stderr
    exit 1
} finally {
    if ($process) {
        $process.Dispose()
    }
}
