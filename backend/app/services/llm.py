import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.utils import json_loads
from app.models import ModelProfile
from app.services.model_profiles import (
    get_active_model_profile,
    get_profile_api_key,
    validate_model_endpoint,
)


logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    provider_id: str
    model_name: str
    is_mock: bool = False

    @abstractmethod
    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict[str, Any]: ...

    @abstractmethod
    async def generate_text(self, system: str, user: str) -> str: ...


class MockProvider(LLMProvider):
    provider_id = "mock"
    model_name = "rule-engine"
    is_mock = True

    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict[str, Any]:
        return {"mock": True, "schema": schema_name, "summary": "Mock provider does not replace deterministic diagnosis."}

    async def generate_text(self, system: str, user: str) -> str:
        return "当前使用 Mock 模型。系统已基于日志规则和知识库完成确定性分析；配置 Qwen/GLM API 后可获得更深入的综合推理。"


class OpenAICompatibleProvider(LLMProvider):
    provider_id = "openai_compatible"

    def __init__(self, profile: ModelProfile | None = None) -> None:
        settings = get_settings()
        config = json_loads(profile.config_json, {}) if profile else {}
        api_key = get_profile_api_key(profile) if profile else settings.llm_api_key
        base_url = profile.base_url if profile else settings.llm_base_url
        model_name = profile.model_name if profile else settings.llm_model
        if not api_key or not base_url or not model_name:
            raise LLMError("API key, Base URL and model name are required")
        try:
            validate_model_endpoint(base_url)
        except ValueError as exc:
            raise LLMError(str(exc)) from exc
        self.model_name = model_name
        self.temperature = float(config.get("temperature", settings.llm_temperature))
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(config.get("timeout_seconds", settings.llm_timeout_seconds)),
            max_retries=int(config.get("max_retries", settings.llm_max_retries)),
        )

    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict[str, Any]:
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system + "\n只输出合法 JSON，不要使用 Markdown 代码块。"},
                    {"role": "user", "content": user},
                ],
            )
            content = response.choices[0].message.content or "{}"
        except Exception as exc:
            logger.exception("OpenAI-compatible JSON request failed")
            raise LLMError(f"Model request failed ({type(exc).__name__})") from exc
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError("Model returned invalid JSON") from exc

    async def generate_text(self, system: str, user: str) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                temperature=self.temperature,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.exception("OpenAI-compatible text request failed")
            raise LLMError(f"Model request failed ({type(exc).__name__})") from exc


def get_llm_provider(profile: ModelProfile | None = None) -> LLMProvider:
    selected = profile or get_active_model_profile("chat")
    if selected:
        if selected.provider == "openai_compatible":
            return OpenAICompatibleProvider(selected)
        return MockProvider()
    if get_settings().llm_provider == "openai_compatible":
        return OpenAICompatibleProvider()
    return MockProvider()


def get_active_chat_model_info() -> dict[str, Any]:
    profile = get_active_model_profile("chat")
    if profile:
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "provider": profile.provider,
            "model": profile.model_name,
            "mode": profile.mode,
            "base_url": profile.base_url,
            "config": json_loads(profile.config_json, {}),
            "is_mock": profile.provider == "mock",
        }
    settings = get_settings()
    return {
        "profile_id": "environment",
        "profile_name": "Environment fallback",
        "provider": settings.llm_provider,
        "model": settings.llm_model or "rule-engine",
        "mode": "api" if settings.llm_provider == "openai_compatible" else "builtin",
        "base_url": settings.llm_base_url or None,
        "config": {
            "temperature": settings.llm_temperature,
            "timeout_seconds": settings.llm_timeout_seconds,
            "max_retries": settings.llm_max_retries,
        },
        "is_mock": settings.llm_provider == "mock",
    }
