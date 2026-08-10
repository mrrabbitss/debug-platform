$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'gwap.ps1') stop @args
exit $LASTEXITCODE
