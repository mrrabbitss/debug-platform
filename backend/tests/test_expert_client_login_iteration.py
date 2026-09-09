"""Run the packaged onboarding boundary with synthetic HTTP/token functions."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows client boundary")
def test_client_reuses_and_accepts_admin_promoted_expert_without_role_escalation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    client = tmp_path / "client"
    (client / "scripts").mkdir(parents=True)
    shutil.copy2(root / "deploy/windows-client/client_onboarding.ps1", client)
    for name in ("file_hash.ps1", "codeagent_launcher_support.ps1", "codeagent_launcher_http.ps1"):
        shutil.copy2(root / "scripts" / name, client / "scripts")
    driver = tmp_path / "identity-check.ps1"
    driver.write_text(r'''
param([string]$ClientRoot, [string]$StateDirectory)
$ErrorActionPreference = 'Stop'
. (Join-Path $ClientRoot 'client_onboarding.ps1')
function Assert-Check($Condition, $Description) { if (-not $Condition) { throw $Description }; $script:checks++ }
function Read-LauncherToken { return 'synthetic-cached-token' }
function Read-Host { throw 'Cached identity unexpectedly required interactive login' }
function Read-LauncherJson { return @{cli_command='synthetic-cli'} }
function Write-LauncherJson { $script:writes++ }
function Save-LauncherToken { $script:saves++ }
function Invoke-LauncherHttp {
    param($Method, $Url, $Headers, $Body)
    if ($Method -eq 'GET') {
        $script:gets++
        return @{status=200;body=(@{role=$script:role} | ConvertTo-Json -Compress)}
    }
    $script:posts++
    return @{status=200;body=(@{role=$script:role;personal_code=$script:code;token=$script:token;user_id='synthetic-user'} | ConvertTo-Json -Compress)}
}
$checks = 0
foreach ($role in @('ENGINEER', 'EXPERT')) {
    $gets = 0; $posts = 0; $saves = 0; $writes = 0
    $result = Initialize-ClientIdentity -ServerUrl 'https://synthetic.invalid' -StateDirectory $StateDirectory
    Assert-Check ($result -eq 'synthetic-cached-token' -and $gets -eq 1 -and $posts -eq 0 -and $saves -eq 0 -and $writes -eq 0) 'Existing credentials were not reused'
    $code = 'e12345678'; $token = 'synthetic-new-token'
    $result = Initialize-ClientIdentity -ServerUrl 'https://synthetic.invalid' -StateDirectory $StateDirectory -PersonalCode $code -Configure
    Assert-Check ($result -eq $token -and $posts -eq 1 -and $saves -eq 1 -and $writes -eq 2) 'Valid appointed role failed onboarding'
}
foreach ($scenario in @('ADMIN', 'VIEWER', 'UNKNOWN', 'wrong-code', 'missing-token')) {
    $role = $scenario; $code = 'e12345678'; $token = 'synthetic-new-token'
    if ($scenario -eq 'wrong-code') { $role = 'EXPERT'; $code = 'e87654321' }
    if ($scenario -eq 'missing-token') { $role = 'EXPERT'; $token = '' }
    $saves = 0; $writes = 0; $rejected = $false
    try { Initialize-ClientIdentity -ServerUrl 'https://synthetic.invalid' -StateDirectory $StateDirectory -PersonalCode 'e12345678' -Configure | Out-Null }
    catch { $rejected = $_.Exception.Message -eq 'The server returned an unexpected identity.' }
    Assert-Check ($rejected -and $saves -eq 0 -and $writes -eq 0) 'Unexpected role or identity was persisted'
}
@{checks=$checks;network_requests=0;credential_writes=0} | ConvertTo-Json -Compress
''', encoding="utf-8-sig")
    result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(driver), "-ClientRoot", str(client), "-StateDirectory", str(tmp_path / "state")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"checks": 9, "network_requests": 0, "credential_writes": 0}
