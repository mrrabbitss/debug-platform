"""Deterministic and auditable HTTP transport policy for Chat model calls."""

from __future__ import annotations

import ssl

import httpx
from openai import DefaultAsyncHttpxClient


def verified_ssl_context_without_revocation() -> ssl.SSLContext:
    """Keep CA/hostname verification while explicitly disabling CRL checks."""
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    revocation_flags = int(getattr(ssl, "VERIFY_CRL_CHECK_LEAF", 0)) | int(
        getattr(ssl, "VERIFY_CRL_CHECK_CHAIN", 0)
    )
    if revocation_flags:
        context.verify_flags = int(context.verify_flags) & ~revocation_flags
    return context


def build_chat_http_client(
    *,
    proxy_url: str | None,
    timeout_seconds: float,
    trust_environment: bool,
) -> httpx.AsyncClient:
    kwargs: dict[str, object] = {
        "timeout": timeout_seconds,
        "trust_env": trust_environment,
    }
    if proxy_url:
        kwargs.update({
            "proxy": proxy_url,
            "verify": verified_ssl_context_without_revocation(),
            "trust_env": False,
        })
    return DefaultAsyncHttpxClient(**kwargs)


def safe_model_connection_error(error: Exception, *, proxy_configured: bool) -> str:
    """Classify connection failures without returning endpoints or credentials."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    names = {type(item).__name__ for item in chain}
    rendered = " ".join(str(item).casefold() for item in chain)
    route = " through the configured proxy" if proxy_configured else ""
    if "ProxyError" in names:
        return f"Model connection{route} failed because the proxy rejected the connection"
    if any("Timeout" in name for name in names):
        return f"Model connection{route} timed out"
    if "SSLCertVerificationError" in names or "certificate verify failed" in rendered:
        return (
            f"Model connection{route} failed TLS certificate verification; "
            "certificate chain and hostname checks remain required"
        )
    connection_names = {"APIConnectionError", "ConnectError", "ConnectErrorOSError"}
    if names.intersection(connection_names):
        return f"Model connection{route} failed ({type(error).__name__})"
    return f"Model request failed ({type(error).__name__})"
