from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "GW/AP Intelligent Debug Platform"
    app_env: Literal["dev", "test", "prod"] = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./data/gw_ap_debug.db"
    storage_root: Path = Path("./data/storage")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    api_key: str | None = None
    auth_mode: Literal["local", "api_key", "rbac"] = "local"
    auth_allow_legacy_admin: bool = True

    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_extracted_bytes: int = 8 * 1024 * 1024 * 1024
    max_archive_files: int = 20000
    max_single_file_bytes: int = 512 * 1024 * 1024
    max_archive_depth: int = 20
    parser_max_text_bytes: int = 128 * 1024 * 1024
    text_line_index_stride: int = 500
    text_search_max_scan_lines: int = 250000
    curation_max_files: int = 500
    curation_max_total_bytes: int = 512 * 1024 * 1024
    curation_max_file_bytes: int = 128 * 1024 * 1024
    curation_max_prompt_chars: int = 120_000
    curation_max_draft_chars: int = 500_000
    curation_max_extracted_text_chars: int = 4_000_000
    curation_max_document_uncompressed_bytes: int = 256 * 1024 * 1024
    curation_pdf_max_pages: int = 500
    curation_pdf_max_content_stream_bytes: int = 64 * 1024 * 1024
    curation_document_timeout_seconds: int = 60
    curation_document_cpu_seconds: int = 45
    curation_document_memory_bytes: int = 1024 * 1024 * 1024
    job_workers: int = 4
    job_lease_seconds: int = 90
    job_heartbeat_seconds: int = 15
    job_dispatch_seconds: float = 1.0
    job_max_attempts: int = 3
    job_retry_base_seconds: float = 1.0
    job_default_timeout_seconds: int = 1800
    tool_timeout_seconds: int = 300

    # Agent integration. ``platform`` preserves the standalone Web product;
    # ``external`` keeps Claude Code/OpenCode/CodeArts as the final reasoner and prevents
    # the platform diagnosis/chat path from invoking a second chat LLM.
    agent_mode: Literal["platform", "external"] = "platform"
    agent_runtime_host: str = "127.0.0.1"
    agent_runtime_port: int = 8765
    serve_frontend: bool = True
    frontend_dist: Path = Path("../frontend/dist")

    # Local runtime state and discovery roots. DATA_ROOT_PATH can be pointed at
    # %LOCALAPPDATA%\GWAPDebug\data by the Win11 agent launcher.
    data_root_path: Path = Path("./data")
    model_roots: str = "models"
    local_model_scan_max_depth: int = 4
    local_model_metadata_max_bytes: int = 2 * 1024 * 1024
    workspace_attach_enabled: bool = True
    workspace_roots: str = ""

    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2
    model_secret_key: str = ""
    model_endpoint_allowlist: str = ""
    model_allow_private_endpoints: bool = False

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "gw_ap_knowledge"
    retrieval_top_k: int = 10

    report_title: str = "GW/AP 智能故障诊断报告"

    @model_validator(mode="after")
    def resolve_local_paths(self) -> "Settings":
        """Keep local data paths stable regardless of the process working directory.

        Older releases started Uvicorn from ``backend`` and documented paths such as
        ``sqlite:///./data/gw_ap_debug.db``. Resolve those relative paths against the
        backend directory so existing installations continue to use the same files,
        while the repository-level .env works from every supported launcher.
        """
        sqlite_prefix = "sqlite:///"
        if self.database_url.startswith(sqlite_prefix):
            database_path = self.database_url[len(sqlite_prefix):]
            if database_path != ":memory:":
                path = Path(database_path)
                if not path.is_absolute():
                    path = (BACKEND_ROOT / path).resolve()
                    self.database_url = f"{sqlite_prefix}{path.as_posix()}"
        if not self.storage_root.is_absolute():
            self.storage_root = (BACKEND_ROOT / self.storage_root).resolve()
        else:
            self.storage_root = self.storage_root.resolve()
        if not self.data_root_path.is_absolute():
            self.data_root_path = (BACKEND_ROOT / self.data_root_path).resolve()
        else:
            self.data_root_path = self.data_root_path.resolve()
        if not self.frontend_dist.is_absolute():
            self.frontend_dist = (BACKEND_ROOT / self.frontend_dist).resolve()
        else:
            self.frontend_dist = self.frontend_dist.resolve()
        if self.app_env == "prod" and self.auth_mode == "local":
            raise ValueError("AUTH_MODE=local is not allowed in APP_ENV=prod")
        if self.auth_mode == "api_key" and not self.api_key:
            raise ValueError("API_KEY is required when AUTH_MODE=api_key")
        return self

    @property
    def data_root(self) -> Path:
        return self.data_root_path

    @property
    def model_secret_key_path(self) -> Path:
        return self.data_root / "model_secret.key"

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def model_root_paths(self) -> list[Path]:
        """Return deduplicated, absolute local model roots.

        Semicolon is accepted on every platform so a Windows MODEL_ROOTS value
        such as ``D:\\models;E:\\models`` is never split on the drive colon.
        Relative paths are resolved from the repository root.
        """
        raw = self.model_roots.strip()
        entries = [item.strip() for item in raw.split(";") if item.strip()]
        if not entries and raw:
            entries = [raw]
        roots: list[Path] = []
        for entry in entries or ["models"]:
            path = Path(entry).expanduser()
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            resolved = path.resolve()
            if resolved not in roots:
                roots.append(resolved)
        return roots

    @property
    def workspace_root_paths(self) -> list[Path]:
        entries = [item.strip() for item in self.workspace_roots.split(";") if item.strip()]
        return [Path(item).expanduser().resolve() for item in entries]

    @property
    def model_endpoint_allowlist_entries(self) -> list[str]:
        return [x.strip().lower() for x in self.model_endpoint_allowlist.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    settings.data_root.mkdir(parents=True, exist_ok=True)
    return settings
