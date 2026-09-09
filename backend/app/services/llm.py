import json
import logging
import re
from abc import ABC, abstractmethod
from time import perf_counter
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.timeouts import chat_timeout_seconds
from app.core.utils import json_loads
from app.models import ModelProfile
from app.services.audit import record_model_egress
from app.services.model_profiles import (
    get_active_model_profile,
    get_profile_api_key,
    get_profile_proxy_url,
    profile_uses_proxy,
    validate_model_endpoint,
    validate_model_proxy_url,
)
from app.services.model_transport import build_chat_http_client, safe_model_error_details


logger = logging.getLogger(__name__)


def _thinking_mode(config: dict[str, Any]) -> str:
    configured = str(config.get("thinking_mode") or "").strip().lower()
    if configured in {"inherit", "enabled", "disabled"}:
        return configured
    # Preserve the intent of profiles created before the tri-state control was
    # introduced. A stored false value meant that the user explicitly chose
    # "off", even though older adapters accidentally omitted the parameter.
    if "thinking_enabled" in config:
        return "enabled" if bool(config.get("thinking_enabled")) else "disabled"
    return "inherit"


class LLMError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "MODEL_REQUEST_FAILED",
        upstream_error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.upstream_error_type = upstream_error_type


def _json_mode_unsupported(exc: Exception) -> bool:
    rendered = str(exc).casefold()
    return "response_format" in rendered and any(
        marker in rendered
        for marker in ("unsupported", "not support", "unknown", "unrecognized", "not permitted")
    )


class LLMProvider(ABC):
    provider_id: str
    model_name: str
    is_mock: bool = False

    @abstractmethod
    async def generate_json(
        self,
        system: str,
        user: str,
        schema_name: str = "diagnosis",
        purpose: str = "case_diagnosis",
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def generate_text(self, system: str, user: str, purpose: str = "case_assistance") -> str: ...


class MockProvider(LLMProvider):
    provider_id = "mock"
    model_name = "rule-engine"
    is_mock = True

    async def generate_json(
        self,
        system: str,
        user: str,
        schema_name: str = "diagnosis",
        purpose: str = "case_diagnosis",
    ) -> dict[str, Any]:
        return {"mock": True, "schema": schema_name, "summary": "Mock provider does not replace deterministic diagnosis."}

    async def generate_text(self, system: str, user: str, purpose: str = "case_assistance") -> str:
        return "当前使用 Mock 模型。系统已基于日志规则和知识库完成确定性分析；配置 Qwen/GLM API 后可获得更深入的综合推理。"


class OpenAICompatibleProvider(LLMProvider):
    provider_id = "openai_compatible"

    def __init__(self, profile: ModelProfile | None = None) -> None:
        settings = get_settings()
        config = json_loads(profile.config_json, {}) if profile else {}
        api_key = get_profile_api_key(profile) if profile else settings.llm_api_key
        base_url = profile.base_url if profile else settings.llm_base_url
        model_name = profile.model_name if profile else settings.llm_model
        proxy_url = get_profile_proxy_url(profile) if profile and profile.proxy_url_ciphertext else None
        if not api_key or not base_url or not model_name:
            raise LLMError(
                "API key, Base URL and model name are required",
                code="MODEL_CONFIGURATION_MISSING",
            )
        try:
            validate_model_endpoint(base_url)
            validate_model_proxy_url("chat", "api", proxy_url)
        except ValueError as exc:
            raise LLMError(
                str(exc),
                code="MODEL_ENDPOINT_CONFIGURATION_INVALID",
                upstream_error_type=type(exc).__name__,
            ) from exc
        self.model_name = model_name
        self.profile = profile
        self.base_url = base_url
        self.proxy_configured = bool(proxy_url)
        self.certificate_revocation_check_skipped = bool(proxy_url)
        self.temperature = float(config.get("temperature", settings.llm_temperature))
        configured_max_tokens = int(config.get("max_tokens") or 0)
        self.max_tokens = configured_max_tokens if configured_max_tokens > 0 else None
        self.thinking_mode = _thinking_mode(config)
        self.thinking_enabled = self.thinking_mode == "enabled"
        timeout_seconds = chat_timeout_seconds(
            config.get("timeout_seconds", settings.llm_timeout_seconds),
            default=settings.llm_timeout_seconds,
        )
        self.last_usage: dict[str, int | None] = {}
        self.last_duration_ms = 0
        self.last_outcome = "NOT_CALLED"
        self.last_finish_reason: str | None = None
        trust_environment = profile is None or profile.id == "MODEL-chat-env"
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=int(config.get("max_retries", settings.llm_max_retries)),
            http_client=build_chat_http_client(
                proxy_url=proxy_url,
                timeout_seconds=timeout_seconds,
                trust_environment=trust_environment,
            ),
        )

    def _record_egress(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        started: float,
        outcome: str,
        response: Any = None,
        error_type: str | None = None,
    ) -> None:
        usage_object = getattr(response, "usage", None)
        prompt_tokens = getattr(usage_object, "prompt_tokens", None)
        completion_tokens = getattr(usage_object, "completion_tokens", None)
        total_tokens = getattr(usage_object, "total_tokens", None)
        prompt_details = getattr(usage_object, "prompt_tokens_details", None)
        completion_details = getattr(
            usage_object, "completion_tokens_details", None
        )
        cached_tokens = getattr(prompt_details, "cached_tokens", None)
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)
        if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
        }
        duration_ms = int((perf_counter() - started) * 1000)
        self.last_usage = usage
        self.last_duration_ms = duration_ms
        self.last_outcome = outcome
        choices = getattr(response, "choices", None) or []
        finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
        self.last_finish_reason = str(finish_reason)[:128] if finish_reason else None
        record_model_egress(
            getattr(self, "profile", None),
            base_url=getattr(self, "base_url", None),
            model_name=self.model_name,
            task_type="chat",
            purpose=purpose,
            request_items=2,
            request_chars=len(system) + len(user),
            duration_ms=duration_ms,
            outcome=outcome,
            error_type=error_type,
            usage=usage,
        )

    async def generate_json(
        self,
        system: str,
        user: str,
        schema_name: str = "diagnosis",
        purpose: str = "case_diagnosis",
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            request_options: dict[str, Any] = {
                "response_format": {"type": "json_object"},
            }
            max_tokens = getattr(self, "max_tokens", None)
            if max_tokens:
                request_options["max_tokens"] = max_tokens
            thinking_mode = getattr(self, "thinking_mode", None)
            if thinking_mode is None and hasattr(self, "thinking_enabled"):
                thinking_mode = "enabled" if self.thinking_enabled else "disabled"
            # Tool selection and log-keyword extraction are bounded JSON planning
            # tasks. GLM Thinking can consume the whole response window before it
            # emits the JSON object and exceed common corporate-proxy deadlines.
            # Keep the profile preference for final synthesis and interactive chat,
            # while making these structured control-plane calls deterministic.
            if (
                purpose == "log_triage_planning"
                or purpose.startswith("diagnostic_planning_round_")
            ):
                thinking_mode = "disabled"
            self.last_thinking_mode = thinking_mode or "inherit"
            if thinking_mode in {"enabled", "disabled"}:
                request_options["extra_body"] = {"thinking": {"type": thinking_mode}}
            messages = [
                {
                    "role": "system",
                    "content": (
                        system
                        + "\n只输出合法 JSON 对象，不要使用 Markdown 代码块。"
                        + f"\n响应结构名称：{schema_name}。它仅用于标识结构，不是 JSON 外层字段。"
                        + "直接输出调用方所列字段组成的对象，并严格保留字段名和字段类型。"
                    ),
                },
                {"role": "user", "content": user},
            ]
            try:
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    temperature=self.temperature,
                    messages=messages,
                    **request_options,
                )
            except Exception as exc:
                if not _json_mode_unsupported(exc):
                    raise
                # Some older OpenAI-compatible gateways reject JSON mode.
                # Retry once with the same explicit JSON instructions.
                request_options.pop("response_format", None)
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    temperature=self.temperature,
                    messages=messages,
                    **request_options,
                )
            content = response.choices[0].message.content or "{}"
        except Exception as exc:
            self._record_egress(
                purpose=purpose,
                system=system,
                user=user,
                started=started,
                outcome="FAILED",
                error_type=type(exc).__name__,
            )
            logger.exception("OpenAI-compatible JSON request failed")
            details = safe_model_error_details(
                exc,
                proxy_configured=getattr(self, "proxy_configured", False),
            )
            raise LLMError(
                details.message,
                code=details.code,
                upstream_error_type=details.upstream_error_type,
            ) from exc
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            self._record_egress(
                purpose=purpose,
                system=system,
                user=user,
                started=started,
                outcome="FAILED",
                response=response,
                error_type=type(exc).__name__,
            )
            finish_reason = getattr(self, "last_finish_reason", None)
            truncated = str(finish_reason or "").casefold() == "length"
            raise LLMError(
                (
                    "Model output reached the token limit before completing JSON"
                    if truncated
                    else "Model returned invalid JSON"
                ),
                code="MODEL_OUTPUT_TRUNCATED" if truncated else "MODEL_INVALID_JSON",
                upstream_error_type=type(exc).__name__,
            ) from exc
        if (
            isinstance(parsed, dict)
            and len(parsed) == 1
            and isinstance(parsed.get(schema_name), dict)
        ):
            parsed = parsed[schema_name]
        self._record_egress(
            purpose=purpose,
            system=system,
            user=user,
            started=started,
            outcome="SUCCESS",
            response=response,
        )
        return parsed

    async def generate_text(self, system: str, user: str, purpose: str = "case_assistance") -> str:
        started = perf_counter()
        try:
            request_options: dict[str, Any] = {}
            max_tokens = getattr(self, "max_tokens", None)
            if max_tokens:
                request_options["max_tokens"] = max_tokens
            thinking_mode = getattr(self, "thinking_mode", None)
            if thinking_mode is None and hasattr(self, "thinking_enabled"):
                thinking_mode = "enabled" if self.thinking_enabled else "disabled"
            self.last_thinking_mode = thinking_mode or "inherit"
            if thinking_mode in {"enabled", "disabled"}:
                request_options["extra_body"] = {"thinking": {"type": thinking_mode}}
            response = await self.client.chat.completions.create(
                model=self.model_name,
                temperature=self.temperature,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                **request_options,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            self._record_egress(
                purpose=purpose,
                system=system,
                user=user,
                started=started,
                outcome="FAILED",
                error_type=type(exc).__name__,
            )
            logger.exception("OpenAI-compatible text request failed")
            details = safe_model_error_details(
                exc,
                proxy_configured=getattr(self, "proxy_configured", False),
            )
            raise LLMError(
                details.message,
                code=details.code,
                upstream_error_type=details.upstream_error_type,
            ) from exc
        self._record_egress(
            purpose=purpose,
            system=system,
            user=user,
            started=started,
            outcome="SUCCESS",
            response=response,
        )
        return content


def get_llm_provider(profile: ModelProfile | None = None) -> LLMProvider:
    from app.services.workbench import selected_profile
    selected = profile or selected_profile() or get_active_model_profile("chat")
    if selected:
        if selected.provider == "openai_compatible":
            return OpenAICompatibleProvider(selected)
        return MockProvider()
    if get_settings().llm_provider == "openai_compatible":
        return OpenAICompatibleProvider()
    return MockProvider()


def get_active_chat_model_info() -> dict[str, Any]:
    from app.services.workbench import selected_profile
    profile = selected_profile() or get_active_model_profile("chat")
    if profile:
        proxy_enabled = profile_uses_proxy(profile)
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "provider": profile.provider,
            "model": profile.model_name,
            "mode": profile.mode,
            "base_url": profile.base_url,
            "config": json_loads(profile.config_json, {}),
            "proxy_url_configured": proxy_enabled,
            "certificate_revocation_check_skipped": proxy_enabled,
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
        "proxy_url_configured": False,
        "certificate_revocation_check_skipped": False,
        "is_mock": settings.llm_provider == "mock",
    }
