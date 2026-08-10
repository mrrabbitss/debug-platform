$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'gwap.ps1') status @args
exit $LASTEXITCODE
