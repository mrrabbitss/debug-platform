from __future__ import annotations

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILL = REPOSITORY_ROOT / "agent-skills" / "gw-ap-debug"
SKILL_MIRRORS = (
    REPOSITORY_ROOT / ".agents" / "skills" / "gw-ap-debug",
    REPOSITORY_ROOT / ".claude" / "skills" / "gw-ap-debug",
)
EXPECTED_TOOLS = {
    "debug_status",
    "debug_list_cases",
    "debug_get_case_context",
    "debug_begin_host_diagnosis",
    "debug_get_host_run",
    "debug_list_diagnostic_documents",
    "debug_read_diagnostic_documents",
    "debug_search_knowledge",
    "debug_search_log",
    "debug_get_evidence",
    "debug_submit_planning_round",
    "debug_finalize_diagnosis",
    "debug_cancel_host_run",
    "debug_generate_report",
    "debug_open_ui",
    "debug_get_knowledge_routing_context",
    "debug_read_knowledge_sections",
    "debug_apply_knowledge_routing",
}
WRITE_TOOLS = {
    "debug_begin_host_diagnosis",
    "debug_list_diagnostic_documents",
    "debug_read_diagnostic_documents",
    "debug_search_knowledge",
    "debug_search_log",
    "debug_get_evidence",
    "debug_submit_planning_round",
    "debug_finalize_diagnosis",
    "debug_cancel_host_run",
    "debug_generate_report",
    "debug_apply_knowledge_routing",
}


def _file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_cross_client_skill_mirrors_are_exact_generated_copies() -> None:
    canonical = _file_map(CANONICAL_SKILL)
    assert "SKILL.md" in canonical
    assert canonical
    for mirror in SKILL_MIRRORS:
        assert not mirror.is_symlink()
        assert not any(path.is_symlink() for path in mirror.rglob("*"))
        assert _file_map(mirror) == canonical


def test_skill_is_thin_and_references_every_public_mcp_tool() -> None:
    files = _file_map(CANONICAL_SKILL)
    assert not any("runtime/backend" in path.casefold() for path in files)
    assert not any(path.endswith((".py", ".whl", ".exe", ".dll")) for path in files)
    assert sum(len(content) for content in files.values()) < 100_000

    instruction_text = b"\n".join(files.values()).decode("utf-8")
    missing = sorted(tool for tool in EXPECTED_TOOLS if tool not in instruction_text)
    assert not missing, f"Skill does not document MCP tools: {missing}"


def test_client_setup_keeps_the_bearer_value_out_of_configuration() -> None:
    installer = (REPOSITORY_ROOT / "scripts" / "install_agent_skill_mcp.ps1").read_text(
        encoding="utf-8"
    )
    assert "DEBUGPLATFORM_MCP_URL" in installer
    assert "DEBUGPLATFORM_MCP_TOKEN" in installer
    assert "bearer-token-env-var" in installer
    assert "Bearer ${DEBUGPLATFORM_MCP_TOKEN}" in installer
    assert "Authorization: Bearer " not in installer


def test_skill_references_resolve_inside_each_package() -> None:
    for root in (CANONICAL_SKILL, *SKILL_MIRRORS):
        for relative in (
            "references/workflow.md",
            "references/tool-reference.md",
            "references/schemas.md",
            "references/external-skill-composition.md",
            "references/knowledge-routing.md",
            "scripts/upload-debug-artifact.ps1",
            "scripts/upload-knowledge-markdown.ps1",
        ):
            assert (root / relative).is_file(), f"Missing {relative} in {root}"


def test_mcp_workflow_contract_freezes_transport_ownership_and_scopes() -> None:
    contract = yaml.safe_load(
        (REPOSITORY_ROOT / "workflow" / "mcp-tools.yaml").read_text(encoding="utf-8")
    )
    assert contract["status"] == "IN_PROGRESS"
    assert contract["transport"]["protocol"] == "mcp"
    assert contract["transport"]["type"] == "streamable_http"
    assert contract["transport"]["path"] == "/mcp"
    assert contract["transport"]["authentication"] == "bearer"
    assert (
        contract["transport"]["large_file_upload"]
        == "/api/v1/cases/{case_id}/artifacts"
    )
    assert (
        contract["transport"]["knowledge_markdown_upload"]
        == "/api/v1/knowledge-routing/import"
    )
    assert contract["reasoning"]["owner"] == "host_cli"
    assert contract["reasoning"]["backend_generative_llm_calls"] == "forbidden"
    assert (
        contract["implementation_status"]["business_tool_registry"]
        == "focused_tests_passed"
    )

    tools = contract["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    assert len(by_name) == len(tools)
    assert set(by_name) == EXPECTED_TOOLS
    assert {name for name, tool in by_name.items() if tool["access"] == "write"} == WRITE_TOOLS
    assert all(tool["access"] in {"read", "write"} for tool in tools)
    assert all(tool["case_scope"] for tool in tools)
    assert all(tool.get("write_effect") for tool in tools if tool["access"] == "write")

    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / "workflow" / "skill.yaml").read_text(encoding="utf-8")
    )
    assert workflow["mcp"]["status"] == "IN_PROGRESS"
    assert workflow["mcp"]["contract"] == "mcp-tools.yaml"
    assert workflow["mcp"]["path"] == "/mcp"
    assert workflow["mcp"]["reasoning_owner"] == "host_cli"
