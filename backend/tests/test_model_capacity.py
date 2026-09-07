import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.model_capacity import CapacityGate, ModelCapacityError


def test_model_capacity_releases_after_failure():
    gate = CapacityGate(1, 1)
    with pytest.raises(ValueError), gate.acquire(1):
        raise ValueError("synthetic failure")
    assert gate.snapshot()["active"] == 0
    with gate.acquire(1):
        assert gate.snapshot()["active"] == 1


def test_model_capacity_queue_saturation_and_timeout():
    gate = CapacityGate(1, 1)
    entered = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with gate.acquire(1):
            def waiter():
                entered.set()
                with gate.acquire(0.3):
                    return "unexpected"
            future = pool.submit(waiter)
            assert entered.wait(1)
            # Wait under the same condition as the gate, avoiding scheduling assumptions.
            import time
            deadline = time.monotonic() + 1
            while not gate.snapshot()["waiting"] and time.monotonic() < deadline:
                time.sleep(0.001)
            with pytest.raises(ModelCapacityError, match="full"), gate.acquire(0.1):
                pass
            with pytest.raises(ModelCapacityError, match="timed out"):
                future.result(timeout=2)
        assert gate.snapshot() == {"concurrency": 1, "queue_limit": 1, "active": 0, "waiting": 0, "rejected": 2}


def test_model_capacity_never_exceeds_limit():
    gate = CapacityGate(2, 10)
    def work(_):
        import time
        with gate.acquire(3):
            active = gate.snapshot()["active"]
            time.sleep(0.01)
            return active
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert max(pool.map(work, range(24))) <= 2
    assert gate.snapshot()["waiting"] == gate.snapshot()["active"] == 0
