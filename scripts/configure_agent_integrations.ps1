param(
  [string]$VenvPython = "",
  [switch]$ConfigureClaude
)
$ErrorActionPreference='Stop'
$Root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $VenvPython) { $VenvPython=Join-Path $env:LOCALAPPDATA 'GWAPDebug\venv\Scripts\python.exe' }
$RuntimeUrl='http://127.0.0.1:8765'
$ProjectMcp=@{
  mcpServers=@{
    'gw-ap-debug'=@{
      command=$VenvPython
      args=@('-m','app.agent_runtime.mcp_server')
      env=@{GWAP_RUNTIME_URL=$RuntimeUrl;GWAP_AGENT_ROLE='ENGINEER'}
    }
  }
} | ConvertTo-Json -Depth 8
Set-Content -LiteralPath (Join-Path $Root '.mcp.json') -Value $ProjectMcp -Encoding UTF8
$IntegrationDir=Join-Path $Root 'agent-integrations'
New-Item -ItemType Directory -Force -Path $IntegrationDir | Out-Null
$OpenCode=@{
  '$schema'='https://opencode.ai/config.json'
  mcp=@{
    'gw-ap-debug'=@{
      type='local'
      command=@($VenvPython,'-m','app.agent_runtime.mcp_server')
      enabled=$true
      environment=@{GWAP_RUNTIME_URL=$RuntimeUrl;GWAP_AGENT_ROLE='ENGINEER'}
    }
  }
} | ConvertTo-Json -Depth 8
Set-Content -LiteralPath (Join-Path $IntegrationDir 'opencode.mcp.json') -Value $OpenCode -Encoding UTF8
if ($ConfigureClaude -and (Get-Command claude -ErrorAction SilentlyContinue)) {
  & claude mcp remove gw-ap-debug --scope user 2>$null
  & claude mcp add --transport stdio --env "GWAP_RUNTIME_URL=$RuntimeUrl" --env "GWAP_AGENT_ROLE=ENGINEER" --scope user gw-ap-debug -- $VenvPython -m app.agent_runtime.mcp_server
  if ($LASTEXITCODE -ne 0) { throw "Claude MCP configuration failed with exit code $LASTEXITCODE" }
}
Write-Host "Claude project MCP: $(Join-Path $Root '.mcp.json')"
Write-Host "OpenCode MCP snippet: $(Join-Path $IntegrationDir 'opencode.mcp.json')"
Write-Host "OpenCode usage: set OPENCODE_CONFIG to that file, or merge its mcp.gw-ap-debug entry into opencode.json."
