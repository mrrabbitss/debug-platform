"""New WinPS 5.1 failure cases for the server upgrade incident of 2026-09-10."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_installer_components import _bundle, _install, selection


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows directory rename semantics")


def _old_tree(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    target = tmp_path / "安装 [server]" / "app"
    target.mkdir(parents=True)
    original = {"a-first.txt": b"first old program file", "z-locked.txt": b"last old program file"}
    for name, content in original.items():
        (target / name).write_bytes(content)
    data = target.parent / "business-data.txt"
    data.write_bytes(b"approved user data stays untouched")
    return target, data, original


def _run_wrapper(source: Path, target: Path, script: str, *, shortcuts: bool = False):
    wrapper = source.parent / "fault-wrapper.ps1"
    wrapper.write_text(
        "param([string]$SourceRoot, [string]$InstallRoot)\n"
        "$ErrorActionPreference = 'Stop'\n" + script + "\n"
        "try { & (Join-Path $SourceRoot 'install_local.ps1') -InstallRoot $InstallRoot "
        "-NoLaunch -Components Full " + ("" if shortcuts else "-NoShortcuts ") + "\nexit 0 } "
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n",
        encoding="utf-8-sig",
    )
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(wrapper), "-SourceRoot", str(source), "-InstallRoot", str(target)],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )


def test_locked_old_file_never_leaves_a_partly_moved_application(tmp_path: Path) -> None:
    source = _bundle(tmp_path / "source", real_python=True)
    target, data, original = _old_tree(tmp_path)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    # Permit reads, deny delete/rename, as a remaining process or file handle can do.
    handle = api.CreateFileW(str(target / "z-locked.txt"), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    try:
        result = _install(source, target, "Full")
    finally:
        api.CloseHandle(handle)
    assert result.returncode != 0, result.stdout + result.stderr
    assert {p.name: p.read_bytes() for p in target.iterdir()} == original
    assert data.read_bytes() == b"approved user data stays untouched"
    assert not list(target.parent.glob("GWAPDebugPlatform.*-*"))


def test_partial_retired_tree_cleanup_does_not_undo_new_install(tmp_path: Path) -> None:
    source = _bundle(tmp_path / "source", real_python=True)
    target, data, _ = _old_tree(tmp_path)
    result = _run_wrapper(source, target, r"""
function Remove-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$Recurse, [switch]$Force)
    if ([IO.Path]::GetFileName($LiteralPath).StartsWith('GWAPDebugPlatform.retired-')) {
        Microsoft.PowerShell.Management\Remove-Item -LiteralPath (Join-Path $LiteralPath 'a-first.txt')
        throw 'Synthetic cleanup interruption after deleting one old file'
    }
    Microsoft.PowerShell.Management\Remove-Item @PSBoundParameters
}
""")
    assert result.returncode == 0, result.stdout + result.stderr
    selection._verified_entries(target)
    assert data.read_bytes() == b"approved user data stays untouched"
    assert "Old program cleanup was incomplete" in result.stdout
    assert not list(target.parent.glob("GWAPDebugPlatform.backup-*"))
    retained = list(target.parent.glob("GWAPDebugPlatform.retired-*"))
    assert len(retained) == 1
    assert not (retained[0] / "a-first.txt").exists()
    assert (retained[0] / "z-locked.txt").is_file()


def test_failure_after_publication_restores_whole_previous_directory(tmp_path: Path) -> None:
    source = _bundle(tmp_path / "source", real_python=True)
    target, data, original = _old_tree(tmp_path)
    result = _run_wrapper(source, target, r"""
function New-Object {
    param([string]$ComObject)
    throw 'Synthetic shortcut creation failure after publication'
}
""", shortcuts=True)
    assert result.returncode != 0
    assert "Synthetic shortcut creation failure" in result.stderr
    assert "complete previous application directory was restored" in result.stdout
    assert {p.name: p.read_bytes() for p in target.iterdir()} == original
    assert data.read_bytes() == b"approved user data stays untouched"
    assert not list(target.parent.glob("GWAPDebugPlatform.*-*"))
