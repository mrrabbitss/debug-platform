[CmdletBinding()]
param([string]$RuntimeRoot = '')

$ErrorActionPreference = 'Stop'
if (-not $RuntimeRoot) {
  if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; pass -RuntimeRoot.' }
  $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$PidFile = Join-Path $RuntimeRoot 'runtime\agent-runtime.pid'
if (-not (Test-Path -LiteralPath $PidFile)) {
  [ordered]@{stopped = $false; reason = 'pid_file_missing'; pid_file = $PidFile} | ConvertTo-Json
  exit 0
}
$PidText = (Get-Content -Raw -LiteralPath $PidFile).Trim()
if ($PidText -notmatch '^\d+$') { throw "Invalid Runtime PID file: $PidFile" }
$RuntimePid = [int]$PidText
$Process = Get-Process -Id $RuntimePid -ErrorAction SilentlyContinue
if (-not $Process) {
  Remove-Item -LiteralPath $PidFile -Force
  [ordered]@{stopped = $false; reason = 'process_missing'; stale_pid = $RuntimePid} | ConvertTo-Json
  exit 0
}

$CommandLine = $null
try {
  $CimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $RuntimePid" -ErrorAction Stop
  $CommandLine = [string]$CimProcess.CommandLine
} catch {}
if ($CommandLine -and ($CommandLine -notmatch 'uvicorn' -or $CommandLine -notmatch 'app\.main:app')) {
  throw "PID $RuntimePid is not the expected GW/AP Debug Uvicorn process; refusing to stop it."
}
if (-not $CommandLine -and $Process.ProcessName -notmatch '(?i)^python(?:w)?$') {
  throw "PID $RuntimePid is not a Python process and its command line could not be verified; refusing to stop it."
}

Stop-Process -Id $RuntimePid -Force
$Deadline = (Get-Date).AddSeconds(10)
while ((Get-Process -Id $RuntimePid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $Deadline) {
  Start-Sleep -Milliseconds 100
}
if (Get-Process -Id $RuntimePid -ErrorAction SilentlyContinue) {
  throw "Runtime PID $RuntimePid did not stop within 10 seconds."
}
Remove-Item -LiteralPath $PidFile -Force
[ordered]@{stopped = $true; pid = $RuntimePid; runtime_root = $RuntimeRoot} | ConvertTo-Json
