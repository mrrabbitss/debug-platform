param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "file_hash.ps1")
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$uv = Join-Path $venvRoot "Scripts\uv.exe"
$backendRoot = Join-Path $projectRoot "backend"
$constraints = Join-Path $backendRoot "constraints.lock"
$uvVersion = "0.12.1"

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw "Python 3.11 or newer was not found. Run scripts\bootstrap_local.bat first."
        }
        & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -ne 0) {
            throw "Python 3.11 or newer is required."
        }
        & $python.Source -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create .venv."
        }
    }

    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        Write-Host "[INFO] Installing lock generator uv==$uvVersion..."
        & $venvPython -m pip install "uv==$uvVersion"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not install uv==$uvVersion."
        }
    }

    $installedUvVersion = (& $uv --version).Trim()
    if ($installedUvVersion -notmatch ("^uv {0}(?:\s|$)" -f [regex]::Escape($uvVersion))) {
        Write-Host "[INFO] Aligning lock generator to uv==$uvVersion..."
        & $venvPython -m pip install --upgrade "uv==$uvVersion"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not install uv==$uvVersion."
        }
    }

    if ($Check) {
        & $uv lock --project $backendRoot --check
        if ($LASTEXITCODE -ne 0) {
            throw "backend\uv.lock is not synchronized with backend\pyproject.toml."
        }

        $temporaryConstraints = Join-Path ([System.IO.Path]::GetTempPath()) (
            "gw-ap-constraints-{0}.lock" -f [guid]::NewGuid().ToString("N")
        )
        try {
            & $uv export `
                --project $backendRoot `
                --locked `
                --all-extras `
                --no-emit-project `
                --no-hashes `
                --no-header `
                --quiet `
                --format requirements.txt `
                --output-file $temporaryConstraints
            if ($LASTEXITCODE -ne 0) {
                throw "Could not export the pip constraints from backend\uv.lock."
            }
            $expectedHash = Get-Sha256Hex -Path $temporaryConstraints
            $actualHash = Get-Sha256Hex -Path $constraints
            if ($expectedHash -ne $actualHash) {
                throw "backend\constraints.lock does not match backend\uv.lock."
            }
        } finally {
            if (Test-Path -LiteralPath $temporaryConstraints) {
                Remove-Item -LiteralPath $temporaryConstraints -Force
            }
        }
        Write-Host "[OK] Python lock files are synchronized."
        exit 0
    }

    & $uv lock --project $backendRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Could not update backend\uv.lock."
    }
    & $uv export `
        --project $backendRoot `
        --locked `
        --all-extras `
        --no-emit-project `
        --no-hashes `
        --no-header `
        --quiet `
        --format requirements.txt `
        --output-file $constraints
    if ($LASTEXITCODE -ne 0) {
        throw "Could not update backend\constraints.lock."
    }
    Write-Host "[OK] Updated backend\uv.lock and backend\constraints.lock."
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
} finally {
    Pop-Location
}
