param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9._-]{2,48}$")]
    [string]$TaskName,
    [string]$BaseRef = "HEAD",
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoName = Split-Path $RepoRoot -Leaf
$ParentRoot = Split-Path $RepoRoot -Parent
$Slug = ($TaskName.ToLowerInvariant() -replace "[^a-z0-9._-]", "-").Trim("-")
$Branch = "codex/task-$Slug"
$WorktreeRoot = Join-Path $ParentRoot "$RepoName-worktrees"
$WorktreePath = Join-Path $WorktreeRoot $Slug

$Sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $Digest = $Sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Slug))
} finally {
    $Sha.Dispose()
}
$Offset = (([int]$Digest[0] * 256) + [int]$Digest[1]) % 800
$BackendPort = 18100 + $Offset
$FrontendPort = 15100 + $Offset
$FakeModelPort = 20100 + $Offset
$RuntimeRoot = Join-Path $WorktreePath ".agent-runtime\$Slug"
$DatabasePath = Join-Path $RuntimeRoot "agent.db"
$StoragePath = Join-Path $RuntimeRoot "storage"
$LogsPath = Join-Path $RuntimeRoot "logs"

$Plan = [ordered]@{
    task = $TaskName
    slug = $Slug
    base_ref = $BaseRef
    branch = $Branch
    worktree = $WorktreePath
    backend_port = $BackendPort
    frontend_port = $FrontendPort
    fake_model_port = $FakeModelPort
    database = $DatabasePath
    storage = $StoragePath
    logs = $LogsPath
}

if ($PlanOnly) {
    $Plan | ConvertTo-Json -Depth 4
    exit 0
}

if (Test-Path -LiteralPath $WorktreePath) {
    throw "Worktree target already exists: $WorktreePath"
}
& git -C $RepoRoot rev-parse --verify $BaseRef 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Base ref does not exist: $BaseRef"
}
New-Item -ItemType Directory -Path $WorktreeRoot -Force | Out-Null
& git -C $RepoRoot worktree add -b $Branch $WorktreePath $BaseRef
if ($LASTEXITCODE -ne 0) {
    throw "git worktree add failed"
}

New-Item -ItemType Directory -Path $StoragePath -Force | Out-Null
New-Item -ItemType Directory -Path $LogsPath -Force | Out-Null
$EnvironmentPath = Join-Path $RuntimeRoot "agent.env"
@(
    "AGENT_TASK_NAME=$TaskName"
    "BACKEND_PORT=$BackendPort"
    "FRONTEND_PORT=$FrontendPort"
    "FAKE_MODEL_PORT=$FakeModelPort"
    "DATABASE_URL=sqlite:///$($DatabasePath.Replace('\', '/'))"
    "STORAGE_ROOT=$($StoragePath.Replace('\', '/'))"
    "AGENT_LOG_ROOT=$($LogsPath.Replace('\', '/'))"
) | Set-Content -LiteralPath $EnvironmentPath -Encoding utf8

$Plan["environment_file"] = $EnvironmentPath
$Plan | ConvertTo-Json -Depth 4
