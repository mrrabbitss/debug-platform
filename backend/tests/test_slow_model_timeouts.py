"""Slow-model settings reach both SDK and HTTP transport without real requests."""
import asyncio
import json

import pytest

from app.core.config import Settings
from app.models import ModelProfile
from app.services import llm


@pytest.mark.parametrize("configured,expected", [(300, 900), (900, 900), (1200, 1200), (45, 45)])
def test_saved_env_timeout_upgrade_preserves_other_operator_values(tmp_path, monkeypatch, configured, expected):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    env_file = tmp_path / "server.env"
    env_file.write_text(f"LLM_TIMEOUT_SECONDS={configured}\n", encoding="utf-8")
    assert Settings(_env_file=env_file).llm_timeout_seconds == expected


@pytest.mark.parametrize("profile_timeout,expected", [(None, 1200), (300, 1200), (1500, 1500)])
def test_saved_profile_uses_effective_timeout_in_sdk_and_transport(monkeypatch, profile_timeout, expected):
    settings = Settings(_env_file=None, llm_timeout_seconds=1200)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "get_profile_api_key", lambda profile: "validation-placeholder")
    config = {} if profile_timeout is None else {"timeout_seconds": profile_timeout}
    profile = ModelProfile(id="MODEL-timeout-validation", name="Synthetic slow model", task_type="chat",
        mode="api", provider="openai_compatible", model_name="synthetic", base_url="https://slow-model.example.test/v1",
        config_json=json.dumps(config))
    provider = llm.OpenAICompatibleProvider(profile)
    try:
        assert provider.client.timeout == expected
        assert provider.client._client.timeout.read == expected
        assert provider.client._client.timeout.connect == expected
        assert provider.client.max_retries == settings.llm_max_retries
    finally:
        asyncio.run(provider.client.close())
