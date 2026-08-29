#requires -Version 5.1
<# Shared Python 3.11-3.14 discovery for Windows launch and setup scripts. #>

function Test-GwApPython {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$PrefixArguments = @()
    )
    try {
        & $Command @PrefixArguments -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 3)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Resolve-GwApPython {
    param([string]$Requested = "")

    $candidates = [Collections.Generic.List[object]]::new()
    if ($Requested) {
        $candidates.Add([pscustomobject]@{ Command = $Requested; PrefixArguments = @() })
    }
    else {
        foreach ($version in @("3.14", "3.13", "3.12", "3.11")) {
            $candidates.Add([pscustomobject]@{ Command = "py"; PrefixArguments = @("-$version") })
        }
        $candidates.Add([pscustomobject]@{ Command = "python"; PrefixArguments = @() })
        $candidates.Add([pscustomobject]@{ Command = "python3"; PrefixArguments = @() })
    }

    foreach ($candidate in $candidates) {
        $commandInfo = Get-Command $candidate.Command -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $commandInfo) { continue }
        $commandPath = if ($commandInfo.Path) { $commandInfo.Path } else { $commandInfo.Source }
        if (-not $commandPath) { $commandPath = $candidate.Command }
        $prefix = @($candidate.PrefixArguments)
        if (-not (Test-GwApPython -Command $commandPath -PrefixArguments $prefix)) { continue }
        $version = (& $commandPath @prefix -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null | Select-Object -Last 1)
        return [pscustomobject]@{
            Command = $commandPath
            PrefixArguments = $prefix
            Version = [string]$version
            Display = ((@($commandPath) + $prefix) -join " ")
        }
    }

    if ($Requested) {
        throw "The requested Python is missing or outside the supported range 3.11-3.14: $Requested"
    }
    throw "Python 3.11-3.14 was not found. Install a supported 64-bit Python and rerun this command."
}
