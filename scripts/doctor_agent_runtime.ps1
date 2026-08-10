$ErrorActionPreference='Continue'
$RuntimeRoot=Join-Path $env:LOCALAPPDATA 'GWAPDebug'
$Python=Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
Write-Host "Runtime root: $RuntimeRoot"
Write-Host "Python installed: $(Test-Path $Python)"
Write-Host "Skill installed: $(Test-Path (Join-Path $HOME '.claude\skills\gw-ap-debug\SKILL.md'))"
if (Test-Path $Python) { & $Python -m app.agent_runtime.cli doctor --human }
exit $LASTEXITCODE
