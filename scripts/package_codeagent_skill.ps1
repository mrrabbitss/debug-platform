[CmdletBinding()]
param(
  [string]$OutputPath = '',
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SourceSkill = Join-Path $RepositoryRoot '.claude\skills\gw-ap-debug'
if (-not (Test-Path -LiteralPath (Join-Path $SourceSkill 'SKILL.md'))) {
  throw "Skill source is incomplete: $SourceSkill"
}
if (-not $OutputPath) {
  $OutputPath = Join-Path $RepositoryRoot 'artifacts\codeagent\gw-ap-debug-codeagent-skill.zip'
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
  throw "Package already exists: $OutputPath. Pass -Force to replace this exact package."
}
$OutputParent = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $OutputParent | Out-Null

$TempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$TempRoot = Join-Path $TempBase ('gwap-codeagent-skill-' + [Guid]::NewGuid().ToString('N'))
$ResolvedTempRoot = [System.IO.Path]::GetFullPath($TempRoot)
if (-not $ResolvedTempRoot.StartsWith($TempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to use a package staging directory outside the OS temp directory: $ResolvedTempRoot"
}
try {
  $StagedSkill = Join-Path $ResolvedTempRoot 'gw-ap-debug'
  New-Item -ItemType Directory -Force -Path $ResolvedTempRoot | Out-Null
  Copy-Item -LiteralPath $SourceSkill -Destination $StagedSkill -Recurse -Force
  Remove-Item -LiteralPath (Join-Path $StagedSkill 'runtime-config.json') -Force -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $OutputPath) { Remove-Item -LiteralPath $OutputPath -Force }
  Compress-Archive -Path (Join-Path $ResolvedTempRoot 'gw-ap-debug') -DestinationPath $OutputPath -CompressionLevel Optimal
  $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputPath).Hash.ToLowerInvariant()
  [ordered]@{
    status = 'PASS'
    package = $OutputPath
    sha256 = $Hash
    root_directory = 'gw-ap-debug'
    skill_file = 'gw-ap-debug/SKILL.md'
  } | ConvertTo-Json -Depth 3
} finally {
  if (Test-Path -LiteralPath $ResolvedTempRoot) {
    Remove-Item -LiteralPath $ResolvedTempRoot -Recurse -Force
  }
}
