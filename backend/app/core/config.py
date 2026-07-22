from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "GW/AP Intelligent Debug Platform"
    app_env: Literal["dev", "test", "prod"] = "dev"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./data/gw_ap_debug.db"
    storage_root: Path = Path("./data/storage")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    api_key: str | None = None

    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    max_extracted_bytes: int = 8 * 1024 * 1024 * 1024
    max_archive_files: int = 20000
    max_single_file_bytes: int = 512 * 1024 * 1024
    max_archive_depth: int = 20
    parser_max_text_bytes: int = 128 * 1024 * 1024
    job_workers: int = 4
    tool_timeout_seconds: int = 300

    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2
    model_secret_key: str = ""

    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "gw_ap_knowledge"
    retrieval_top_k: int = 10

    report_title: str = "GW/AP 智能故障诊断报告"

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache

def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(parents=True, exist_ok=True)
    return settings
