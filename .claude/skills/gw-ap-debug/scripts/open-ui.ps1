param([string]$CaseId = "")
$Gwap = Join-Path $PSScriptRoot 'gwap.ps1'
if ($CaseId) { & $Gwap open --case-id $CaseId --human } else { & $Gwap open --human }
exit $LASTEXITCODE
