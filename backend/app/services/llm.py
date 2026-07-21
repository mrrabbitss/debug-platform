import json
import re
from abc import ABC, abstractmethod
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    @abstractmethod
    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict[str, Any]: ...

    @abstractmethod
    async def generate_text(self, system: str, user: str) -> str: ...


class MockProvider(LLMProvider):
    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict[str, Any]:
        return {"mock": True, "schema": schema_name, "summary": "Mock provider does not replace deterministic diagnosis."}

    async def generate_text(self, system: str, user: str) -> str:
        return "当前使用 Mock 模型。系统已基于日志规则和知识库完成确定性分析；配置 Qwen/GLM API 后可获得更深入的综合推理。"


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.llm_api_key or not settings.llm_base_url or not settings.llm_model:
            raise LLMError("LLM_API_KEY, LLM_BASE_URL and LLM_MODEL are required")
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict[str, Any]:
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system + "\n只输出合法 JSON，不要使用 Markdown 代码块。"},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or "{}"
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned invalid JSON: {content[:500]}") from exc

    async def generate_text(self, system: str, user: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""


def get_llm_provider() -> LLMProvider:
    if get_settings().llm_provider == "openai_compatible":
        return OpenAICompatibleProvider()
    return MockProvider()
