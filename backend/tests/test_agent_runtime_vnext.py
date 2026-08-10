from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_runtime.registry import build_agent_tool_registry, invoke_agent_tool
from app.core.config import Settings
from app.models import Case
from app.services import diagnosis, local_model_discovery
from app.services.agentic.tools import ToolNotAllowedError


def test_external_agent_mode_skips_second_chat_llm(monkeypatch) -> None:
    baseline = {
        "case": {"id": "CASE-ext", "title": "external"},
        "analysis_engine": "rule+routing+rag",
    }
    monkeypatch.setattr(
        diagnosis,
        "get_settings",
        lambda: SimpleNamespace(agent_mode="external"),
    )
    monkeypatch.setattr(
        diagnosis,
        "get_llm_provider",
        lambda: (_ for _ in ()).throw(AssertionError("second LLM must not be called")),
    )
    result = asyncio.run(diagnosis._augment_with_llm(
        Case(id="CASE-ext", title="external", description=""),
        baseline,
        [{"evidence_id": "EVT-1", "content": "data"}],
    ))
    assert result["analysis_engine"] == "rule+agentic-evidence-external"
    assert "skipped" in result["warnings"][0].lower()


def test_agent_tool_surface_is_small_and_write_requires_confirmation() -> None:
    registry = build_agent_tool_registry()
    manifest = registry.manifest(role="ENGINEER")
    names = {item["name"] for item in manifest}
    assert 8 <= len(names) <= 12
    assert "debug_evidence_bundle" in names
    assert "debug_open_ui" in names
    with pytest.raises(ToolNotAllowedError):
        invoke_agent_tool(
            registry,
            "debug_create_case",
            {"title": "AP auth issue", "confirm_write": False},
        )
    with pytest.raises(ToolNotAllowedError):
        invoke_agent_tool(
            registry,
            "debug_generate_report",
            {"case_id": "CASE-ext", "format": "html", "confirm_write": False},
        )


def test_local_model_metadata_classifies_embedding_reranker_and_chat() -> None:
    embedding = local_model_discovery.classify_model_metadata({
        "folder_name": "bge-large-zh-v1.5",
        "files": ["config.json", "modules.json"],
        "config.json": {"architectures": ["BertModel"], "hidden_size": 1024},
        "modules.json": [{"type": "sentence_transformers.models.Pooling"}],
    })
    reranker = local_model_discovery.classify_model_metadata({
        "folder_name": "Qwen3-Reranker-0.6B",
        "files": ["config.json", "modules.json", "config_sentence_transformers.json"],
        "config.json": {"architectures": ["Qwen3ForCausalLM"]},
        "modules.json": [{"type": "sentence_transformers.cross_encoder.models.LogitScore"}],
    })
    chat = local_model_discovery.classify_model_metadata({
        "folder_name": "Qwen2.5-7B-Instruct",
        "files": ["config.json", "tokenizer_config.json"],
        "config.json": {"architectures": ["Qwen2ForCausalLM"]},
        "tokenizer_config.json": {"chat_template": "{{ messages }}"},
    })
    assert embedding["task_type"] == "embedding"
    assert embedding["loader"] == "sentence_transformers"
    assert reranker["task_type"] == "reranker"
    assert reranker["loader"] == "sentence_transformers_cross_encoder"
    assert chat["task_type"] == "chat"
    assert chat["loader"] == "transformers_causal_lm"


def test_model_scan_reads_metadata_not_weight_contents(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "models"
    model = root / "embedding-model"
    model.mkdir(parents=True)
    (model / "config.json").write_text(json.dumps({"architectures": ["BertModel"], "hidden_size": 8}), encoding="utf-8")
    (model / "modules.json").write_text(json.dumps([{"type": "sentence_transformers.models.Pooling"}]), encoding="utf-8")
    # Deliberately invalid/non-readable-as-text bytes: the scanner must only stat this file.
    (model / "model.safetensors").write_bytes(b"\x00\xffWEIGHT_BYTES_MUST_NOT_BE_PARSED")
    data_root = tmp_path / "data"
    settings = SimpleNamespace(
        model_root_paths=[root],
        local_model_scan_max_depth=4,
        local_model_metadata_max_bytes=1024 * 1024,
        data_root=data_root,
    )
    monkeypatch.setattr(local_model_discovery, "get_settings", lambda: settings)
    items = asyncio.run(local_model_discovery.scan_local_models(None, use_llm=False))  # type: ignore[arg-type]
    assert len(items) == 1
    assert items[0]["task_type"] == "embedding"
    assert items[0]["weight_bytes"] == (model / "model.safetensors").stat().st_size
    assert "metadata_summary" in items[0]
    assert "WEIGHT_BYTES" not in json.dumps(items[0])


def test_agent_settings_resolve_model_roots_and_external_mode(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        agent_mode="external",
        model_roots=f"{tmp_path / 'a'};{tmp_path / 'b'}",
        data_root_path=tmp_path / "data",
        storage_root=tmp_path / "storage",
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
    )
    assert settings.agent_mode == "external"
    assert settings.model_root_paths == [(tmp_path / "a").resolve(), (tmp_path / "b").resolve()]
    assert settings.data_root == (tmp_path / "data").resolve()


def test_skill_contract_and_mcp_stdio_initialize() -> None:
    repo = Path(__file__).resolve().parents[2]
    skill = repo / ".claude" / "skills" / "gw-ap-debug" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "name: gw-ap-debug" in text
    assert "External Agent Mode" in text
    assert "debug_evidence_bundle" in text

    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
    }) + "\n"
    result = subprocess.run(
        [sys.executable, "-m", "app.agent_runtime.mcp_server"],
        cwd=repo / "backend",
        input=request,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout.splitlines()[0])
    assert response["result"]["serverInfo"]["name"] == "gw-ap-debug"
    assert response["result"]["capabilities"]["tools"] == {"listChanged": False}


def test_low_confidence_local_model_uses_llm_metadata_review_only(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeProvider:
        is_mock = False

        async def generate_json(self, system: str, user: str, schema_name: str, purpose: str):
            captured["system"] = system
            captured["user"] = user
            return {
                "task_type": "reranker",
                "confidence": 0.91,
                "loader": "transformers_sequence_classifier",
                "reason": "safe metadata indicates pair classification",
            }

    monkeypatch.setattr(local_model_discovery, "get_active_model_profile", lambda *_: object())
    monkeypatch.setattr(local_model_discovery, "get_llm_provider", lambda *_: FakeProvider())
    metadata = {
        "folder_name": "ambiguous-model",
        "files": ["config.json", "model.safetensors"],
        "config.json": {
            "architectures": ["CustomSequenceClassification"],
            "api_key": "SHOULD_BE_REDACTED",
        },
        "weight_bytes": 123456789,
        "secret_weight_probe": "MUST_NOT_LEAVE_LOCAL_PROCESS",
    }
    deterministic = {
        "task_type": "unknown",
        "confidence": 0.35,
        "loader": "unknown",
        "reason": "ambiguous",
    }
    reviewed = asyncio.run(local_model_discovery._review_with_llm(None, metadata, deterministic))  # type: ignore[arg-type]
    assert reviewed is not None
    assert reviewed["task_type"] == "reranker"
    assert "MUST_NOT_LEAVE_LOCAL_PROCESS" not in captured["user"]
    assert "weight_bytes" not in captured["user"]
    assert "SHOULD_BE_REDACTED" not in captured["user"]
    assert "<redacted>" in captured["user"]
    assert "model.safetensors" in captured["user"]  # filename is safe metadata
    assert "untrusted" in captured["system"].lower()


def test_workspace_attach_requires_allowlist_when_auth_enabled(tmp_path: Path, monkeypatch) -> None:
    from app.api import agent_runtime as agent_runtime_api

    workspace = tmp_path / "工作 区"
    workspace.mkdir()
    monkeypatch.setattr(
        agent_runtime_api,
        "get_settings",
        lambda: SimpleNamespace(
            workspace_attach_enabled=True,
            auth_mode="api_key",
            workspace_root_paths=[],
        ),
    )
    with pytest.raises(ValueError, match="WORKSPACE_ROOTS"):
        agent_runtime_api._validate_workspace_path(str(workspace.resolve()))

    monkeypatch.setattr(
        agent_runtime_api,
        "get_settings",
        lambda: SimpleNamespace(
            workspace_attach_enabled=True,
            auth_mode="api_key",
            workspace_root_paths=[tmp_path.resolve()],
        ),
    )
    assert agent_runtime_api._validate_workspace_path(str(workspace.resolve())) == workspace.resolve()


def test_win11_agent_installer_and_launcher_contracts() -> None:
    repo = Path(__file__).resolve().parents[2]
    installer = (repo / "scripts" / "install_agent_runtime.ps1").read_text(encoding="utf-8")
    launcher = (repo / "scripts" / "start_agent_runtime.ps1").read_text(encoding="utf-8")
    integrations = (repo / "scripts" / "configure_agent_integrations.ps1").read_text(encoding="utf-8")
    opencode_e2e = (repo / "scripts" / "test_opencode_integration.ps1").read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%" not in installer  # PowerShell uses the environment value, not a hard-coded path.
    assert "$env:LOCALAPPDATA" in installer
    assert "backend\\constraints.lock" in installer
    assert '"${Target}[local-models]"' in installer
    for contract in (
        "$env:AGENT_MODE='external'",
        "$env:SERVE_FRONTEND='true'",
        "$env:DATA_ROOT_PATH",
        "$env:MODEL_ROOTS",
        "127.0.0.1:$Port",
    ):
        assert contract in launcher
    assert ".mcp.json" in integrations
    assert "opencode.mcp.json" in integrations
    assert "app.agent_runtime.mcp_server" in integrations
    for contract in (
        "OPENCODE_CONFIG",
        "XDG_CONFIG_HOME",
        "opencode/deepseek-v4-flash-free",
        "run --pure --auto",
        "OPENCODE_GWAP_E2E_PASS",
        "sample_data\\collectDebuginfo_demo.zip",
    ):
        assert contract in opencode_e2e

    claude_example = json.loads((repo / "agent-integrations" / "claude.mcp.example.json").read_text(encoding="utf-8"))
    assert claude_example["mcpServers"]["gw-ap-debug"]["command"] == "gwap-mcp"
    opencode_example = json.loads((repo / "agent-integrations" / "opencode.mcp.example.json").read_text(encoding="utf-8"))
    assert opencode_example["$schema"] == "https://opencode.ai/config.json"
    assert opencode_example["mcp"]["gw-ap-debug"]["type"] == "local"
    assert opencode_example["mcp"]["gw-ap-debug"]["command"] == ["gwap-mcp"]
    assert opencode_example["mcp"]["gw-ap-debug"]["enabled"] is True


def test_codeagent_skill_setup_and_probe_contracts() -> None:
    repo = Path(__file__).resolve().parents[2]
    setup = (repo / "scripts" / "setup_codeagent_vnext.ps1").read_text(encoding="utf-8")
    probe = (repo / "scripts" / "probe_codeagent_compatibility.ps1").read_text(encoding="utf-8")
    collector = (repo / "scripts" / "collect_codeagent_diagnostics.ps1").read_text(
        encoding="utf-8"
    )
    package = (repo / "scripts" / "package_codeagent_skill.ps1").read_text(encoding="utf-8")
    wrapper = (repo / ".claude" / "skills" / "gw-ap-debug" / "scripts" / "gwap.ps1").read_text(
        encoding="utf-8"
    )
    skill = (repo / ".claude" / "skills" / "gw-ap-debug" / "SKILL.md").read_text(encoding="utf-8")

    for contract in (
        "GWAPDebugVNext",
        ".codeartsdoer\\skills",
        "ProjectSkillStatus.txt",
        "runtime-config.json",
        "CodeArtsNative",
        "CodeArtsClaude",
        "OpenCodeV1",
        "OpenCodeV2",
        "gw-ap-debug-vnext",
        "TargetClient",
    ):
        assert contract in setup
    for contract in (
        "codearts",
        "codeagent",
        "opencode",
        "initialize",
        "tools/list",
        "FULL_SKILL_MCP",
        "mcp_tcp_port = $null",
        "ClientFamily",
        "debug_evidence_bundle",
    ):
        assert contract in probe
    assert "runtime-config.json" in wrapper
    assert "app.agent_runtime.cli" in wrapper
    assert "GWAP_RUNTIME_URL" in wrapper
    assert "CodeArts/CodeAgent" in skill
    assert "codeagent-compatibility.md" in skill
    assert "Compress-Archive" in package
    assert "runtime-config.json" in package
    collector.encode("ascii")
    for contract in (
        "nga-root-bin",
        "codeagent.exe",
        "Invoke-ProbeForCommand",
        "debug', 'config",
        "debug', 'paths",
        "FULL_NGA_MCP",
        "DIRECT_MCP_OK_CLIENT_CONFIG_NOT_ACTIVE",
        "codeagent-diagnostics-shareable.json",
        "local_only_files",
        "RuntimeUrl must use HTTP(S) on loopback",
    ):
        assert contract in collector
    assert (repo / "scripts" / "collect_codeagent_diagnostics.bat").is_file()

    native = json.loads(
        (repo / "agent-integrations" / "codearts.native.mcp.example.json").read_text(encoding="utf-8")
    )
    native_server = native["mcp"]["gw-ap-debug-vnext"]
    assert native_server["type"] == "local"
    assert native_server["command"][-2:] == ["-m", "app.agent_runtime.mcp_server"]
    assert native_server["enabled"] is True
    assert native_server["environment"]["GWAP_RUNTIME_URL"] == "http://127.0.0.1:8766"

    claude = json.loads(
        (repo / "agent-integrations" / "codearts.claude.mcp.example.json").read_text(encoding="utf-8")
    )
    claude_server = claude["mcpServers"]["gw-ap-debug-vnext"]
    assert claude_server["transportType"] == "stdio"
    assert claude_server["disabled"] is False
    assert claude_server["args"] == ["-m", "app.agent_runtime.mcp_server"]


def test_agent_runtime_routes_are_integrated_with_existing_rbac() -> None:
    from app.services import access_control

    assert "/system/local-models" in access_control.ENGINEER_READ_PREFIXES
    assert "/system/agent-runtime" in access_control.ENGINEER_READ_PREFIXES
    assert "workspaces" in access_control.CASE_SCOPED_RESOURCES
