"""Content-free discovery for every authenticated client role."""

from app.core.config import Settings


def client_capabilities(settings: Settings) -> dict:
    return {
        "app": settings.app_name,
        "server": "gw-ap-debug-platform",
        "server_id": settings.server_instance_id or "standalone",
        "deployment_mode": settings.deployment_mode,
        "client_contract_version": "1.0",
        "supported_client_contract_majors": [1],
        "mcp_path": "/mcp",
        "transport": "streamable_http",
        "reasoning_owner": "host_cli",
        "backend_chat_allowed": False,
        "knowledge_writes": "draft_only",
        "upload_transport": "rest_multipart",
        "max_upload_bytes": settings.max_upload_bytes,
    }
