"""Preflight admission; existing reads and cleanup remain available when full."""
from shutil import disk_usage

from app.core.config import get_settings


class StorageCapacityError(RuntimeError):
    pass


def require_storage_capacity(additional_bytes: int = 0) -> None:
    settings = get_settings()
    minimum = settings.minimum_free_storage_bytes
    if minimum and disk_usage(settings.storage_root).free < minimum + max(0, additional_bytes):
        raise StorageCapacityError("Insufficient free storage for new work; free disk space and retry")
