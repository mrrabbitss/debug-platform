import ssl
from types import SimpleNamespace

from app.models import ModelProfile
from app.services import llm, model_transport


def test_proxied_transport_keeps_tls_verification_and_clears_revocation(monkeypatch):
    captured: dict[str, object] = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(model_transport, "DefaultAsyncHttpxClient", fake_client)
    result = model_transport.build_chat_http_client(
        proxy_url="http://proxy.example.com:8080",
        timeout_seconds=30,
        trust_environment=True,
    )

    context = captured["verify"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    revocation_flags = int(getattr(ssl, "VERIFY_CRL_CHECK_LEAF", 0)) | int(
        getattr(ssl, "VERIFY_CRL_CHECK_CHAIN", 0)
    )
    assert int(context.verify_flags) & revocation_flags == 0
    assert captured["proxy"] == "http://proxy.example.com:8080"
    assert captured["trust_env"] is False
    assert result is not None


def test_profile_without_proxy_is_direct_and_ignores_environment(monkeypatch):
    captured: dict[str, object] = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(model_transport, "DefaultAsyncHttpxClient", fake_client)
    model_transport.build_chat_http_client(
        proxy_url=None,
        timeout_seconds=30,
        trust_environment=False,
    )

    assert captured["trust_env"] is False
    assert "proxy" not in captured
    assert "verify" not in captured


def test_openai_profile_passes_saved_proxy_to_explicit_transport(monkeypatch):
    captured: dict[str, object] = {}
    transport = SimpleNamespace()

    def fake_transport(**kwargs):
        captured["transport"] = kwargs
        return transport

    def fake_openai(**kwargs):
        captured["openai"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(llm, "get_profile_api_key", lambda profile: "sk-test")
    monkeypatch.setattr(
        llm,
        "get_profile_proxy_url",
        lambda profile: "http://proxy.example.com:8080",
    )
    monkeypatch.setattr(llm, "validate_model_endpoint", lambda value: None)
    monkeypatch.setattr(llm, "validate_model_proxy_url", lambda *args: None)
    monkeypatch.setattr(llm, "build_chat_http_client", fake_transport)
    monkeypatch.setattr(llm, "AsyncOpenAI", fake_openai)
    profile = ModelProfile(
        id="MODEL-proxied-chat",
        name="Proxied Chat",
        task_type="chat",
        mode="api",
        provider="openai_compatible",
        model_name="glm-5.2",
        base_url="https://model.example.com/v1",
        proxy_url_ciphertext="encrypted",
        config_json='{"timeout_seconds":45,"max_retries":1}',
    )

    provider = llm.OpenAICompatibleProvider(profile)

    assert captured["transport"] == {
        "proxy_url": "http://proxy.example.com:8080",
        "timeout_seconds": 45.0,
        "trust_environment": False,
    }
    assert captured["openai"]["http_client"] is transport
    assert captured["openai"]["base_url"] == "https://model.example.com/v1"
    assert provider.proxy_configured is True
    assert provider.certificate_revocation_check_skipped is True
