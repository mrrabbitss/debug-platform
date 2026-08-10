from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    data: Any


class EmptyInput(BaseModel):
    pass


class StatusInput(BaseModel):
    pass


class CreateCaseInput(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    device_type: Literal["GW", "AP", "OTHER"] = "GW"
    description: str = Field(default="", max_length=20_000)
    device_model: str | None = Field(default=None, max_length=255)
    firmware_version: str | None = Field(default=None, max_length=255)
    confirm_write: bool = False


class IngestInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    local_file: str = Field(min_length=1, max_length=4096)
    wait: bool = True
    timeout_seconds: int = Field(default=900, ge=5, le=3600)
    confirm_write: bool = False


class WaitJobInput(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=900, ge=5, le=3600)


class InspectInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    level: str | None = Field(default=None, max_length=32)
    module: str | None = Field(default=None, max_length=128)
    search: str | None = Field(default=None, max_length=2000)
    limit: int = Field(default=50, ge=1, le=100)


class SearchInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=2, max_length=20_000)
    top_k: int = Field(default=12, ge=1, le=20)
    max_hops: int = Field(default=2, ge=0, le=3)
    modules: list[str] | None = Field(default=None, max_length=8)


class EvidenceBundleInput(SearchInput):
    query: str = Field(default="", max_length=20_000)


class AttachWorkspaceInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=255)
    index: bool = True
    wait: bool = True
    timeout_seconds: int = Field(default=1200, ge=5, le=3600)
    confirm_write: bool = False


class CodeContextInput(BaseModel):
    repository_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=2, max_length=20_000)
    max_hops: int = Field(default=2, ge=0, le=3)
    limit: int = Field(default=20, ge=1, le=100)


class DiagnoseInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    wait: bool = True
    timeout_seconds: int = Field(default=1200, ge=5, le=3600)
    confirm_write: bool = False


class GenerateReportInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    format: Literal["html", "pdf", "docx"] = "html"
    confirm_write: bool = False


class OpenUiInput(BaseModel):
    case_id: str | None = Field(default=None, max_length=64)
    open_browser: bool = True
