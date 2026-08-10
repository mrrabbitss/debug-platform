$RuntimeRoot=Join-Path $env:LOCALAPPDATA 'GWAPDebug'
$PidFile=Join-Path $RuntimeRoot 'runtime\agent-runtime.pid'
if (-not (Test-Path $PidFile)) { Write-Host 'No runtime PID file.'; exit 0 }
$pidValue=[int](Get-Content $PidFile -Raw)
Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "Stopped runtime PID $pidValue"
