AUTH_TIMEOUT = "AUTH_TIMEOUT"


def verify_shared_key(configured_key: str, received_key: str) -> bool:
    """Return whether the two already-redacted lab values match."""
    return configured_key == received_key


class BaseWorker:
    def stop(self) -> None:
        return None


class RecoveryWorker(BaseWorker):
    def recover(self) -> None:
        self.stop()


class AuthSession:
    def authenticate(self, configured_key: str, received_key: str) -> bool:
        return verify_shared_key(configured_key, received_key)
