"""Focused role and endpoint drift checks; this is not the repository-wide harness."""
from pathlib import Path

from fastapi import Depends, FastAPI
import yaml

from app.api.routes import router
from app.core.security import verify_api_key
from app.mcp.debugplatform_registry import DEBUGPLATFORM_MCP_TOOL_NAMES


ROOT = Path(__file__).resolve().parents[2]


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    assert len(keys) == len(set(keys)), f"Duplicate workflow YAML keys: {keys}"
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _contracts():
    skill = yaml.load((ROOT / "workflow/skill.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    documented = yaml.load((ROOT / "workflow/openapi.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(verify_api_key)])
    return skill, documented, app.openapi()


def test_every_allowlisted_endpoint_exists_and_has_consistent_roles():
    skill, documented, runtime = _contracts()
    assert skill["version"] == documented["info"]["version"]
    for operation in skill["entrypoints"].values():
        path, method = operation["path"], operation["method"].lower()
        contract = documented["paths"][path.removeprefix("/api/v1")][method]
        assert method in runtime["paths"][path]
        assert operation["allowed_roles"] == contract["x-allowed-roles"]
        if path.startswith(("/api/v1/knowledge-curations", "/api/v1/agent-runs")) or (path.startswith("/api/v1/knowledge") and method != "get"):
            assert operation["allowed_roles"] == ["ADMIN"]


def test_all_workbench_paths_match_runtime_and_admin_routes_stay_rest_only():
    skill, documented, runtime = _contracts()
    expected = {(path, method) for path, operations in runtime["paths"].items()
        if path.startswith("/api/v1/workbench/") for method in operations}
    declarations = {key: {(value["path"], value["method"].lower()) for value in skill[key].values()}
        for key in ("entrypoints", "rest_only_endpoints")}
    assert not declarations["entrypoints"] & declarations["rest_only_endpoints"]
    assert expected == {(path, method) for path, method in declarations["entrypoints"] | declarations["rest_only_endpoints"]
        if path.startswith("/api/v1/workbench/")}
    assert len(expected) >= 19
    for path, method in expected:
        contract = documented["paths"][path.removeprefix("/api/v1")][method]
        live = runtime["paths"][path][method]
        assert contract.get("parameters", []) == live.get("parameters", [])
        assert contract.get("requestBody") == live.get("requestBody")
        if (path, method) in declarations["rest_only_endpoints"]:
            assert contract["x-allowed-roles"] == ["ADMIN"]
            assert contract["x-agent-callable"] is False
        else:
            assert contract["x-agent-callable"] is True
            assert "ENGINEER" in contract["x-allowed-roles"]
    for suffix, method in (("cancel", "post"), ("pause", "post"), ("retry", "post"), ("consent", "patch"),
                           ("source", "get"), ("readings", "get")):
        assert (f"/api/v1/workbench/assistant/{{session_id}}/{suffix}", method) in declarations["rest_only_endpoints"]
    for name, schema in documented["components"]["schemas"].items():
        assert schema == runtime["components"]["schemas"][name], name
    # The workbench REST additions must not introduce model-callable MCP write tools.
    assert len(DEBUGPLATFORM_MCP_TOOL_NAMES) == 18
    assert not any("assistant" in name or "library" in name for name in DEBUGPLATFORM_MCP_TOOL_NAMES)
