"""Bounded in-process model admission for the single-worker LAN server."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from threading import Condition, Lock
from time import monotonic

from app.core.config import get_settings


class ModelCapacityError(RuntimeError):
    """Retryable model saturation; no model request has been sent."""


class CapacityGate:
    def __init__(self, concurrency: int, queue_limit: int) -> None:
        self.concurrency = concurrency
        self.queue_limit = queue_limit
        self.active = 0
        self.waiting = 0
        self.rejected = 0
        self.condition = Condition()

    @contextmanager
    def acquire(self, timeout: float):
        deadline = monotonic() + timeout
        with self.condition:
            if self.active >= self.concurrency and self.waiting >= self.queue_limit:
                self.rejected += 1
                raise ModelCapacityError("Model queue is full; retry later")
            self.waiting += 1
            try:
                while self.active >= self.concurrency:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        self.rejected += 1
                        raise ModelCapacityError("Model queue wait timed out; retry later")
                    self.condition.wait(remaining)
                self.active += 1
            finally:
                self.waiting -= 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self.condition:
            return {name: getattr(self, name) for name in (
                "concurrency", "queue_limit", "active", "waiting", "rejected",
            )}


_gates: dict[tuple[str, int, int], CapacityGate] = {}
_lock = Lock()


def _gate(kind: str) -> CapacityGate:
    settings = get_settings()
    concurrency = getattr(settings, f"{kind}_concurrency")
    key = (kind, concurrency, settings.model_queue_limit)
    with _lock:
        return _gates.setdefault(key, CapacityGate(concurrency, settings.model_queue_limit))


def bounded_model_call(kind: str):
    if kind not in {"embedding", "reranker"}:
        raise ValueError("Unsupported model capacity kind")

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            settings = get_settings()
            if settings.deployment_mode != "lan_server":
                return function(*args, **kwargs)
            with _gate(kind).acquire(settings.model_queue_timeout_seconds):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def model_capacity_status() -> dict:
    settings = get_settings()
    return {
        "enabled": settings.deployment_mode == "lan_server",
        "scope": "single_backend_process",
        "embedding": _gate("embedding").snapshot(),
        "reranker": _gate("reranker").snapshot(),
    }
