param(
    [string]$BrowserChannel = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "frontend"
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules\@playwright\test") -PathType Container)) {
    throw "Frontend dependencies are missing. Run scripts\bootstrap_local.bat first."
}

$selectedChannel = $BrowserChannel.Trim()
if (-not $selectedChannel) {
    $edgeCandidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    $chromeCandidates = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
    if ($edgeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1) {
        $selectedChannel = "msedge"
    } elseif ($chromeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1) {
        $selectedChannel = "chrome"
    }
}

$previousChannel = $env:E2E_BROWSER_CHANNEL
try {
    if ($selectedChannel) {
        $env:E2E_BROWSER_CHANNEL = $selectedChannel
        Write-Host ("[INFO] Browser E2E channel: {0}" -f $selectedChannel)
    } else {
        Remove-Item Env:E2E_BROWSER_CHANNEL -ErrorAction SilentlyContinue
        Write-Host "[INFO] Browser E2E channel: bundled Chromium"
        Write-Host "[INFO] If Chromium is missing, run: cd frontend && npx playwright install chromium"
    }
    Push-Location $FrontendRoot
    try {
        & $Npm run test:e2e
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    if ($null -eq $previousChannel) {
        Remove-Item Env:E2E_BROWSER_CHANNEL -ErrorAction SilentlyContinue
    } else {
        $env:E2E_BROWSER_CHANNEL = $previousChannel
    }
}
