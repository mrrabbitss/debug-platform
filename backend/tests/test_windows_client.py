from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")


def builder():
    spec = importlib.util.spec_from_file_location("client_builder_test", ROOT / "scripts/build_windows_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_client_package_contains_no_platform_runtime(tmp_path):
    result = builder().build(tmp_path)
    package = Path(result["directory"])
    manifest = json.loads((package / "client-manifest.json").read_text())
    assert not manifest["backend_bundled"] and not manifest["models_bundled"]
    assert (package / "agent-skills/gw-ap-debug/scripts/upload-debug-artifact.ps1").is_file()
    assert (package / "scripts/start_codeagent.ps1").is_file()
    assert (package / "分机使用指南.md").read_text(encoding="utf-8") == (ROOT / "docs/分机使用指南.md").read_text(encoding="utf-8")
    assert {path.suffix for path in package.rglob("*") if path.is_file()} <= {".ps1", ".bat", ".md", ".json"}
    assert result["archive_bytes"] < 256 * 1024
    with pytest.raises(FileExistsError):
        builder().build(tmp_path)


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell client")
def test_client_installer_verifies_and_launcher_never_bootstraps(tmp_path):
    result = builder().build(tmp_path / "distribution")
    package = Path(result["directory"])
    state = tmp_path / "state not created"
    install_root = tmp_path / "client versions 中文"
    environment = {**os.environ, "LOCALAPPDATA": str(tmp_path / "local app data")}
    def run(script, *args):
        return subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(package / script), *args], env=environment, cwd=tmp_path,
                              capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=25)
    dry = run("install_client.ps1", "-InstallRoot", str(install_root), "-NoShortcut", "-DryRun")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert not install_root.exists()
    installed = run("install_client.ps1", "-InstallRoot", str(install_root), "-NoShortcut")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    target = install_root / result["package_id"]
    assert (target / "Start.bat").is_file()
    for url in ("https://debug.example.test", "https://debug.example.test/mcp"):
        dry = run("start_client.ps1", "-ServerUrl", url, "-StateDirectory", str(state), "-DryRun")
        assert dry.returncode == 0, dry.stdout + dry.stderr
        data = json.loads(dry.stdout)
        assert data["connect_only"] and not data["local_backend_start_allowed"]
        assert data["mcp_url"] == "https://debug.example.test/mcp"
        assert not data["changes_global_cli_config"]
    assert not state.exists()
    insecure = run("start_client.ps1", "-ServerUrl", "http://192.0.2.1", "-DryRun")
    assert insecure.returncode != 0
    (package / "Start.bat").write_text("tampered")
    invalid = run("install_client.ps1", "-InstallRoot", str(tmp_path / "never created"), "-DryRun")
    assert invalid.returncode != 0
    assert not (tmp_path / "never created").exists()


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows certificate verifier")
def test_client_root_certificate_dry_run_pins_public_ca(tmp_path):
    import hashlib
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Isolated client certificate test")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
                   .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    path = tmp_path / "root.crt"
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    package = Path(builder().build(tmp_path / "package")["directory"])
    state = tmp_path / "state not created"
    command = [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               str(package / "trust_server_certificate.ps1"), "-CertificatePath", str(path), "-ExpectedSha256"]
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    for value, success in ((fingerprint, True), ("0" * 64, False)):
        result = subprocess.run([*command, value, "-StateDirectory", str(state), "-DryRun"], capture_output=True,
                                text=True, encoding="utf-8-sig", errors="replace", timeout=15)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
    assert not state.exists()
