"""Shared waiting limits for slow Chat endpoints and durable AI work."""

CHAT_REQUEST_TIMEOUT_SECONDS = 15 * 60
AI_JOB_TIMEOUT_SECONDS = 2 * 60 * 60


def chat_timeout_seconds(value: float, *, default: float = CHAT_REQUEST_TIMEOUT_SECONDS) -> float:
    """Upgrade the previous five-minute default without rewriting saved settings.

    Other explicit values remain operator controlled. This also covers old .env
    files and model profiles, which otherwise hide a changed application default.
    """
    seconds = float(value)
    return float(default) if seconds == 300 else seconds
