from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.demo_case_contract import load_demo_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORTER_PATH = PROJECT_ROOT / "scripts" / "export_glm_demo_snapshot.py"
_SPEC = importlib.util.spec_from_file_location("demo_snapshot_export_for_test", EXPORTER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
exporter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(exporter)

# Locally administered test-only addresses; no real source identifiers belong here.
SOURCE_MAC = "06:00:00:00:02:ab"
TARGET_MAC = "02:00:00:00:01:01"
SOURCE_IP = "10.23.45.67"
TARGET_IP = "192.0.2.10"


def test_example_mapping_handles_spelling_and_preserves_input() -> None:
    source = {
        "description": f"example MAC={SOURCE_MAC.upper()} IPv4={SOURCE_IP}",
        "forms": ["0600000002AB", "0600.0000.02ab", "06-00-00-00-02-ab"],
        "nested": {SOURCE_MAC: [SOURCE_IP, TARGET_MAC]},
    }
    original = copy.deepcopy(source)
    mappings = exporter._validate_redaction_map({SOURCE_MAC: TARGET_MAC, SOURCE_IP: TARGET_IP})
    actual = exporter._redact_example_identifiers(source, mappings)
    assert source == original
    assert actual == {
        "description": f"example MAC={TARGET_MAC} IPv4={TARGET_IP}",
        "forms": ["020000000101", "0200.0000.0101", "02-00-00-00-01-01"],
        "nested": {TARGET_MAC: [TARGET_IP, TARGET_MAC]},
    }


@pytest.mark.parametrize("identifier", [SOURCE_MAC, "0600000002ab", SOURCE_IP])
def test_unmapped_source_identifier_fails_without_echoing_it(identifier: str) -> None:
    with pytest.raises(ValueError, match="supply --redaction-map") as caught:
        exporter._redact_example_identifiers({"example": identifier}, {})
    assert identifier not in str(caught.value)


@pytest.mark.parametrize("mapping", [
    [],
    {SOURCE_MAC: 123},
    {"arbitrary source text": "arbitrary target text"},
    {SOURCE_MAC: SOURCE_MAC},
    {SOURCE_IP: "8.8.8.8"},
    {"8.8.8.8": TARGET_IP},
    {SOURCE_MAC: TARGET_MAC, "0600000002AB": "02:00:00:00:00:33"},
])
def test_redaction_map_rejects_unbounded_or_conflicting_rewrites(mapping: object) -> None:
    with pytest.raises(ValueError):
        exporter._validate_redaction_map(mapping)


def test_redaction_does_not_change_the_pinned_public_snapshot() -> None:
    # load_demo_snapshot verifies byte-level integrity and the existing privacy gate.
    snapshot = load_demo_snapshot()
    assert exporter._redact_example_identifiers(snapshot, {}) == snapshot


def test_redaction_does_not_merge_object_keys() -> None:
    mappings = exporter._validate_redaction_map({SOURCE_MAC: TARGET_MAC})
    with pytest.raises(ValueError, match="merge distinct object keys"):
        exporter._redact_example_identifiers({SOURCE_MAC: 1, TARGET_MAC: 2}, mappings)


def test_uuid_device_tail_is_redacted_but_policy_hash_is_preserved() -> None:
    source = "uuid:00000000-0000-0000-0000-0600000002ab"
    mappings = exporter._validate_redaction_map({SOURCE_MAC: TARGET_MAC})
    actual = exporter._redact_example_identifiers(
        [source, "policy-check-abcdefabcdef"], mappings,
    )
    assert actual == [
        "uuid:00000000-0000-0000-0000-020000000101", "policy-check-abcdefabcdef",
    ]
    with pytest.raises(ValueError, match="UUID device tail"):
        exporter._redact_example_identifiers(source, {})


def test_cli_requires_explicit_run_before_accessing_a_database(tmp_path: Path) -> None:
    database = tmp_path / "must-not-be-created.db"
    result = subprocess.run(
        [sys.executable, str(EXPORTER_PATH), "--database", str(database)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "--run-id/--analysis-run-id" in result.stderr
    assert not database.exists()
    with pytest.raises(ValueError, match="explicit analysis run ID"):
        exporter.export_snapshot(database, " ")
    assert not database.exists()


@pytest.mark.parametrize("run_flag", ["--run-id", "--analysis-run-id"])
def test_cli_accepts_both_explicit_run_flags_and_runtime_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_flag: str,
) -> None:
    runtime_map = tmp_path / "private-map.json"
    runtime_map.write_text(json.dumps({SOURCE_MAC: TARGET_MAC}), encoding="utf-8")
    output = tmp_path / "snapshot.json"
    database = tmp_path / "runtime.db"
    calls = []

    def fake_export(path: Path, run_id: str, *, redaction_map: dict[str, str]):
        calls.append((path, run_id, redaction_map))
        return {"synthetic_test": True}

    monkeypatch.setattr(exporter, "export_snapshot", fake_export)
    monkeypatch.setattr(sys, "argv", [
        str(EXPORTER_PATH), "--database", str(database),
        run_flag, "RUN-synthetic-test", "--redaction-map", str(runtime_map),
        "--output", str(output),
    ])
    assert exporter.main() == 0
    assert calls == [(database, "RUN-synthetic-test", {SOURCE_MAC: TARGET_MAC})]
    assert json.loads(output.read_text(encoding="utf-8")) == {"synthetic_test": True}


def test_runtime_map_loader_rejects_invalid_json_without_echoing_content(tmp_path: Path) -> None:
    path = tmp_path / "invalid-map.json"
    path.write_text("private-source-content", encoding="utf-8")
    with pytest.raises(ValueError, match="valid UTF-8 JSON") as caught:
        exporter._load_redaction_map(path)
    assert "private-source-content" not in str(caught.value)


def test_runtime_map_loader_has_a_size_bound(tmp_path: Path) -> None:
    path = tmp_path / "oversized-map.json"
    path.write_text(" " * 65_537, encoding="utf-8")
    with pytest.raises(ValueError, match="64 KiB"):
        exporter._load_redaction_map(path)
