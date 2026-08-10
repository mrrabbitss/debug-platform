$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'gwap.ps1') start @args
exit $LASTEXITCODE
