"""Deterministic and auditable HTTP transport policy for Chat model calls."""

from __future__ import annotations

from dataclasses import dataclass
import ssl

import httpx
from openai import DefaultAsyncHttpxClient


@dataclass(frozen=True)
class SafeModelError:
    code: str
    message: str
    upstream_error_type: str


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


def safe_model_error_details(
    error: Exception,
    *,
    proxy_configured: bool,
) -> SafeModelError:
    """Classify model failures without returning endpoints, bodies or credentials."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    names = {type(item).__name__ for item in chain}
    rendered = " ".join(str(item).casefold() for item in chain)
    route = " through the configured proxy" if proxy_configured else ""
    upstream_error_type = type(error).__name__
    if "ProxyError" in names:
        return SafeModelError(
            "MODEL_PROXY_CONNECTION_FAILED",
            f"Model connection{route} failed because the proxy rejected the connection",
            upstream_error_type,
        )
    if any("Timeout" in name for name in names):
        return SafeModelError(
            "MODEL_TIMEOUT",
            f"Model connection{route} timed out",
            upstream_error_type,
        )
    if "SSLCertVerificationError" in names or "certificate verify failed" in rendered:
        return SafeModelError(
            "MODEL_TLS_VERIFICATION_FAILED",
            f"Model connection{route} failed TLS certificate verification; "
            "certificate chain and hostname checks remain required",
            upstream_error_type,
        )
    status_errors = (
        ("AuthenticationError", "MODEL_AUTHENTICATION_FAILED", "Model authentication failed"),
        ("PermissionDeniedError", "MODEL_PERMISSION_DENIED", "Model request was denied"),
        ("RateLimitError", "MODEL_RATE_LIMITED", "Model request was rate limited"),
        ("BadRequestError", "MODEL_BAD_REQUEST", "Model gateway rejected the request"),
        ("NotFoundError", "MODEL_NOT_FOUND", "Model or endpoint was not found"),
        ("InternalServerError", "MODEL_UPSTREAM_ERROR", "Model gateway returned a server error"),
        ("APIStatusError", "MODEL_UPSTREAM_ERROR", "Model gateway returned an HTTP error"),
    )
    for error_name, code, message in status_errors:
        if error_name in names:
            return SafeModelError(code, message, upstream_error_type)
    connection_names = {"APIConnectionError", "ConnectError", "ConnectErrorOSError"}
    if names.intersection(connection_names):
        return SafeModelError(
            "MODEL_CONNECTION_FAILED",
            f"Model connection{route} failed ({upstream_error_type})",
            upstream_error_type,
        )
    return SafeModelError(
        "MODEL_REQUEST_FAILED",
        f"Model request failed ({upstream_error_type})",
        upstream_error_type,
    )


def safe_model_connection_error(error: Exception, *, proxy_configured: bool) -> str:
    """Backward-compatible safe message for callers that do not need the code."""
    return safe_model_error_details(
        error,
        proxy_configured=proxy_configured,
    ).message
