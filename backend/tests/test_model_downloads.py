import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import model_downloads as model_download_api
from app.core.db import Base, get_db
from app.models import Job
from app.services import model_downloads
from app.services.jobs import JobCancelledError


class FakeJobContext:
    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []

    def update(self, progress: int, message: str) -> None:
        self.updates.append((progress, message))

    def raise_if_cancelled(self) -> None:
        return None


class CancelAfterFirstFileContext(FakeJobContext):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    def update(self, progress: int, message: str) -> None:
        super().update(progress, message)
        if message == "正在下载 a.bin":
            self.cancelled = True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelledError("cancelled by test")


def download_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        model_download_root=tmp_path / "models",
        data_root=tmp_path,
        model_download_mirror_entries=["https://mirror.example"],
        model_download_max_files=100,
        model_download_max_file_bytes=1024 * 1024,
        model_download_max_total_bytes=2 * 1024 * 1024,
    )


def test_model_download_resumes_and_atomically_publishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    content = b"complete-model-content"
    digest = hashlib.sha256(content).hexdigest()
    resolved_revision = "a" * 40
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)
    target = (
        settings.model_download_root
        / "reranker"
        / "Qwen3-Reranker-0.6B"
    )
    target.mkdir(parents=True)
    partial = target / "model.safetensors.partial"
    partial.write_bytes(content[:8])
    seen_range = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_range
        if "/api/models/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "sha": resolved_revision,
                    "siblings": [{
                        "rfilename": "model.safetensors",
                        "size": len(content),
                        "lfs": {"size": len(content), "sha256": digest},
                    }],
                },
            )
        seen_range = request.headers.get("range", "")
        assert request.url.path.endswith(
            f"/resolve/{resolved_revision}/model.safetensors"
        )
        return httpx.Response(
            206,
            content=content[8:],
            headers={"Content-Range": f"bytes 8-{len(content) - 1}/{len(content)}"},
        )

    client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(handler),
    )
    context = FakeJobContext()
    try:
        result = model_downloads.download_model_repository(
            context,  # type: ignore[arg-type]
            model_id="Qwen/Qwen3-Reranker-0.6B",
            mirror_base="https://mirror.example",
            revision="main",
            proxy_url=None,
            client=client,
        )
    finally:
        client.close()

    assert seen_range == "bytes=8-"
    active = Path(result["target_directory"])
    assert active.parent.name == model_downloads.MODEL_DOWNLOAD_GENERATIONS_DIRECTORY
    assert (active / "model.safetensors").read_bytes() == content
    assert not partial.exists()
    marker = json.loads(
        (target / model_downloads.MODEL_DOWNLOAD_MANIFEST).read_text(encoding="utf-8")
    )
    assert marker["schema_version"] == 2
    assert marker["active_generation"] == active.name
    assert marker["resolved_revision"] == resolved_revision
    assert marker["files"][0]["sha256"] == digest
    assert result["file_count"] == 1
    assert result["runtime_installed"] is False
    assert result["tls_certificate_verification"] is True
    assert context.updates[-1][0] >= 98


def test_model_download_rejects_manifest_path_traversal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/models/" in request.url.path
        return httpx.Response(
            200,
            json={"sha": "unsafe", "siblings": [{"rfilename": "../escape.bin"}]},
        )

    client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(model_downloads.ModelDownloadError, match="unsafe file path"):
            model_downloads.download_model_repository(
                FakeJobContext(),  # type: ignore[arg-type]
                model_id="BAAI/bge-base-zh-v1.5",
                mirror_base="https://mirror.example",
                revision="main",
                proxy_url=None,
                client=client,
            )
    finally:
        client.close()
    assert not (tmp_path / "escape.bin").exists()


def test_model_download_failure_does_not_expose_endpoint_or_proxy_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "connection included user:password@secret-proxy.example",
            request=request,
        )

    client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(model_downloads.ModelDownloadError) as captured:
            model_downloads.download_model_repository(
                FakeJobContext(),  # type: ignore[arg-type]
                model_id="BAAI/bge-base-zh-v1.5",
                mirror_base="https://mirror.example",
                revision="main",
                proxy_url=None,
                client=client,
            )
    finally:
        client.close()
    rendered = str(captured.value)
    assert "password" not in rendered
    assert "secret-proxy" not in rendered
    assert "ConnectError" in rendered


def test_model_download_client_uses_only_the_explicit_proxy(monkeypatch) -> None:
    captured: dict = {}
    sentinel = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return sentinel

    tls_context = object()
    monkeypatch.setattr(model_downloads.httpx, "Client", fake_client)
    monkeypatch.setattr(
        model_downloads,
        "verified_ssl_context_without_revocation",
        lambda: tls_context,
    )
    result = model_downloads.build_model_download_client(
        "http://user:secret@proxy.example:8080"
    )

    assert result is sentinel
    assert captured["proxy"] == "http://user:secret@proxy.example:8080"
    assert captured["verify"] is tls_context
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is True


def test_download_proxy_accepts_explicit_private_proxy_and_blocks_metadata() -> None:
    proxy = "http://user:secret@127.0.0.1:7890"
    assert model_downloads.validate_download_proxy(proxy) == proxy
    assert model_downloads.proxy_url_hint(proxy) == "http://127.0.0.1:7890"
    with pytest.raises(ValueError, match="metadata"):
        model_downloads.validate_download_proxy(
            "http://metadata.google.internal:8080"
        )


def test_model_download_api_encrypts_one_time_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'download-api.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    captured: dict = {}

    def override_db():
        with session_factory() as db:
            yield db

    def fake_submit(db, kind, function, *args, **kwargs):
        captured["args"] = args
        captured["input_data"] = kwargs["input_data"]
        captured["idempotency_key"] = kwargs["idempotency_key"]
        job = Job(
            id="JOB-model-download",
            kind=kind,
            status="QUEUED",
            progress=0,
            message="",
            input_json=json.dumps(kwargs["input_data"]),
            result_json="{}",
            max_attempts=3,
            timeout_seconds=3600,
            resource_limits_json="{}",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    monkeypatch.setattr(model_download_api, "validate_model_mirror", lambda value: value)
    monkeypatch.setattr(model_download_api, "validate_download_proxy", lambda value: value)
    monkeypatch.setattr(model_download_api, "encrypt_secret", lambda value: "encrypted-proxy")
    monkeypatch.setattr(model_download_api.job_runner, "submit", fake_submit)
    monkeypatch.setattr(model_download_api, "record_audit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        model_download_api,
        "get_settings",
        lambda: SimpleNamespace(
            model_download_timeout_seconds=3600,
            model_download_max_files=100,
            model_download_max_file_bytes=1024,
            model_download_max_total_bytes=2048,
        ),
    )

    app = FastAPI()
    app.include_router(model_download_api.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    proxy = "http://user:plain-secret@proxy.example:8080"
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/system/model-downloads",
            json={
                "model_id": "Qwen/Qwen3-Reranker-0.6B",
                "mirror_base": "https://mirror.example",
                "revision": "main",
                "proxy_url": proxy,
            },
        )
    assert response.status_code == 200, response.text
    assert "plain-secret" not in response.text
    assert captured["input_data"]["proxy_url_ciphertext"] == "encrypted-proxy"
    assert captured["idempotency_key"] == hashlib.sha256(
        (
            f"{model_downloads.MODEL_DOWNLOAD_JOB_KIND}\0"
            "Qwen/Qwen3-Reranker-0.6B"
        ).encode("utf-8")
    ).hexdigest()
    assert proxy not in json.dumps(captured)
    engine.dispose()


def test_model_download_catalog_reports_only_complete_manifests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)
    target = settings.model_download_root / "embedding" / "bge-base-zh-v1.5"
    target.mkdir(parents=True)
    (target / "config.json").write_text("{}", encoding="utf-8")
    partial_catalog = model_downloads.model_download_catalog()
    partial = next(
        item for item in partial_catalog["models"]
        if item["model_id"] == "BAAI/bge-base-zh-v1.5"
    )
    assert partial["status"] == "PARTIAL"

    marker = {
        "schema_version": 1,
        "model_id": "BAAI/bge-base-zh-v1.5",
        "requested_revision": "main",
        "resolved_revision": "commit",
        "files": [{
            "path": "config.json",
            "size": (target / "config.json").stat().st_size,
            "sha256": hashlib.sha256(b"{}").hexdigest(),
        }],
    }
    (target / model_downloads.MODEL_DOWNLOAD_MANIFEST).write_text(
        json.dumps(marker),
        encoding="utf-8",
    )
    ready_catalog = model_downloads.model_download_catalog()
    ready = next(
        item for item in ready_catalog["models"]
        if item["model_id"] == "BAAI/bge-base-zh-v1.5"
    )
    assert ready["status"] == "READY"


def test_failed_update_preserves_the_previous_active_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)
    old_content = b"known-good-generation"
    old_revision = "b" * 40

    def old_handler(request: httpx.Request) -> httpx.Response:
        if "/api/models/" in request.url.path:
            return httpx.Response(200, json={
                "sha": old_revision,
                "siblings": [{
                    "rfilename": "model.safetensors",
                    "size": len(old_content),
                    "lfs": {
                        "size": len(old_content),
                        "sha256": hashlib.sha256(old_content).hexdigest(),
                    },
                }],
            })
        return httpx.Response(200, content=old_content)

    old_client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(old_handler),
    )
    try:
        old_result = model_downloads.download_model_repository(
            FakeJobContext(),  # type: ignore[arg-type]
            model_id="BAAI/bge-base-zh-v1.5",
            mirror_base="https://mirror.example",
            revision="main",
            proxy_url=None,
            client=old_client,
        )
    finally:
        old_client.close()

    target = settings.model_download_root / "embedding" / "bge-base-zh-v1.5"
    pointer_before = (target / model_downloads.MODEL_DOWNLOAD_MANIFEST).read_bytes()
    old_active = Path(old_result["target_directory"])
    new_revision = "c" * 40
    first_new_file = b"new-first-file"
    second_new_file = b"new-second-file"

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if "/api/models/" in request.url.path:
            return httpx.Response(200, json={
                "sha": new_revision,
                "siblings": [
                    {
                        "rfilename": "a.bin",
                        "size": len(first_new_file),
                        "lfs": {
                            "size": len(first_new_file),
                            "sha256": hashlib.sha256(first_new_file).hexdigest(),
                        },
                    },
                    {
                        "rfilename": "z.bin",
                        "size": len(second_new_file),
                        "lfs": {
                            "size": len(second_new_file),
                            "sha256": hashlib.sha256(second_new_file).hexdigest(),
                        },
                    },
                ],
            })
        if request.url.path.endswith("/a.bin"):
            return httpx.Response(200, content=first_new_file)
        raise httpx.ConnectError("interrupted", request=request)

    failing_client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(failing_handler),
    )
    try:
        with pytest.raises(model_downloads.ModelDownloadError, match="connection failed"):
            model_downloads.download_model_repository(
                FakeJobContext(),  # type: ignore[arg-type]
                model_id="BAAI/bge-base-zh-v1.5",
                mirror_base="https://mirror.example",
                revision="next",
                proxy_url=None,
                client=failing_client,
            )
    finally:
        failing_client.close()

    assert (target / model_downloads.MODEL_DOWNLOAD_MANIFEST).read_bytes() == pointer_before
    assert (old_active / "model.safetensors").read_bytes() == old_content
    catalog = model_downloads.model_download_catalog()
    installed = next(
        item for item in catalog["models"]
        if item["model_id"] == "BAAI/bge-base-zh-v1.5"
    )
    assert installed["status"] == "READY"
    assert installed["resolved_revision"] == old_revision
    assert installed["target_directory"] == str(old_active)


def test_same_model_downloads_are_serialized_across_threads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)
    first_started = threading.Event()
    release_first = threading.Event()
    second_manifest_seen = threading.Event()
    revisions = {"first": "d" * 40, "second": "e" * 40}
    contents = {"first": b"first-generation", "second": b"second-generation"}
    results: dict[str, dict] = {}
    errors: list[Exception] = []

    def run(label: str) -> None:
        content = contents[label]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/api/models/" in request.url.path:
                if label == "second":
                    second_manifest_seen.set()
                return httpx.Response(200, json={
                    "sha": revisions[label],
                    "siblings": [{
                        "rfilename": "model.bin",
                        "size": len(content),
                        "lfs": {
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        },
                    }],
                })
            if label == "first":
                first_started.set()
                assert release_first.wait(5)
            return httpx.Response(200, content=content)

        client = model_downloads.build_model_download_client(
            None,
            transport=httpx.MockTransport(handler),
        )
        try:
            results[label] = model_downloads.download_model_repository(
                FakeJobContext(),  # type: ignore[arg-type]
                model_id="Qwen/Qwen3-Reranker-0.6B",
                mirror_base="https://mirror.example",
                revision=label,
                proxy_url=None,
                client=client,
            )
        except Exception as exc:  # pragma: no cover - assertion reports the exception.
            errors.append(exc)
        finally:
            client.close()

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    assert first_started.wait(5)
    second.start()
    assert not second_manifest_seen.wait(0.3)
    release_first.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert second_manifest_seen.is_set()
    assert set(results) == {"first", "second"}
    catalog = model_downloads.model_download_catalog()
    installed = next(
        item for item in catalog["models"]
        if item["model_id"] == "Qwen/Qwen3-Reranker-0.6B"
    )
    assert installed["resolved_revision"] == revisions["second"]
    assert (
        Path(installed["target_directory"]) / "model.bin"
    ).read_bytes() == contents["second"]


def test_cancelled_update_does_not_publish_incomplete_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)
    old_content = b"active-before-cancel"
    old_revision = "1" * 40

    def initial_handler(request: httpx.Request) -> httpx.Response:
        if "/api/models/" in request.url.path:
            return httpx.Response(200, json={
                "sha": old_revision,
                "siblings": [{
                    "rfilename": "model.bin",
                    "size": len(old_content),
                    "lfs": {
                        "size": len(old_content),
                        "sha256": hashlib.sha256(old_content).hexdigest(),
                    },
                }],
            })
        return httpx.Response(200, content=old_content)

    initial_client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(initial_handler),
    )
    try:
        initial = model_downloads.download_model_repository(
            FakeJobContext(),  # type: ignore[arg-type]
            model_id="BAAI/bge-base-zh-v1.5",
            mirror_base="https://mirror.example",
            revision="main",
            proxy_url=None,
            client=initial_client,
        )
    finally:
        initial_client.close()

    target = settings.model_download_root / "embedding" / "bge-base-zh-v1.5"
    pointer_before = (target / model_downloads.MODEL_DOWNLOAD_MANIFEST).read_bytes()
    new_content = {"a.bin": b"first", "z.bin": b"second"}

    def update_handler(request: httpx.Request) -> httpx.Response:
        if "/api/models/" in request.url.path:
            return httpx.Response(200, json={
                "sha": "2" * 40,
                "siblings": [
                    {
                        "rfilename": name,
                        "size": len(content),
                        "lfs": {
                            "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        },
                    }
                    for name, content in new_content.items()
                ],
            })
        return httpx.Response(200, content=new_content[request.url.path.rsplit("/", 1)[-1]])

    update_client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(update_handler),
    )
    try:
        with pytest.raises(JobCancelledError):
            model_downloads.download_model_repository(
                CancelAfterFirstFileContext(),  # type: ignore[arg-type]
                model_id="BAAI/bge-base-zh-v1.5",
                mirror_base="https://mirror.example",
                revision="next",
                proxy_url=None,
                client=update_client,
            )
    finally:
        update_client.close()

    assert (target / model_downloads.MODEL_DOWNLOAD_MANIFEST).read_bytes() == pointer_before
    assert (
        Path(initial["target_directory"]) / "model.bin"
    ).read_bytes() == old_content


def test_catalog_rejects_same_size_content_corruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = download_settings(tmp_path)
    monkeypatch.setattr(model_downloads, "get_settings", lambda: settings)
    content = b"verified-model-data"
    resolved_revision = "f" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/models/" in request.url.path:
            return httpx.Response(200, json={
                "sha": resolved_revision,
                "siblings": [{
                    "rfilename": "model.bin",
                    "size": len(content),
                    "lfs": {
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    },
                }],
            })
        return httpx.Response(200, content=content)

    client = model_downloads.build_model_download_client(
        None,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = model_downloads.download_model_repository(
            FakeJobContext(),  # type: ignore[arg-type]
            model_id="BAAI/bge-base-zh-v1.5",
            mirror_base="https://mirror.example",
            revision="main",
            proxy_url=None,
            client=client,
        )
    finally:
        client.close()

    model_file = Path(result["target_directory"]) / "model.bin"
    original_stat = model_file.stat()
    model_file.write_bytes(b"x" * len(content))
    os.utime(
        model_file,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 10_000_000),
    )
    catalog = model_downloads.model_download_catalog()
    installed = next(
        item for item in catalog["models"]
        if item["model_id"] == "BAAI/bge-base-zh-v1.5"
    )
    assert installed["status"] == "PARTIAL"
