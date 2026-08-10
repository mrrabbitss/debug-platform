param([string]$CaseId = "")
if ($CaseId) { gwap open --case-id $CaseId --human } else { gwap open --human }
