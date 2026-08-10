from __future__ import annotations

import os
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx


class RuntimeClientError(RuntimeError):
    pass


class RuntimeClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        runtime = (base_url or os.environ.get("GWAP_RUNTIME_URL") or "http://127.0.0.1:8765").rstrip("/")
        self.runtime_url = runtime
        self.api_base = f"{runtime}/api/v1"
        key = api_key if api_key is not None else os.environ.get("GWAP_API_KEY", "")
        self.headers = {"X-API-Key": key} if key else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                f"{self.api_base}{path}",
                headers=self.headers,
                timeout=kwargs.pop("timeout", 180.0),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise RuntimeClientError(f"Runtime request failed: {exc}") from exc
        if response.is_error:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text[:2000]
            raise RuntimeClientError(f"Runtime HTTP {response.status_code}: {detail}")
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        response = self._request("GET", path, **kwargs)
        ctype = response.headers.get("content-type", "")
        return response.json() if "json" in ctype else response.text

    def post(self, path: str, **kwargs: Any) -> Any:
        response = self._request("POST", path, **kwargs)
        ctype = response.headers.get("content-type", "")
        return response.json() if "json" in ctype else response.text

    def status(self) -> dict[str, Any]:
        return {
            "health": self.get("/health"),
            "agent_runtime": self.get("/system/agent-runtime"),
        }

    def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/cases", json=payload)

    def ingest(self, case_id: str, local_file: str) -> dict[str, Any]:
        path = Path(local_file).expanduser().resolve(strict=True)
        if not path.is_file():
            raise RuntimeClientError("local_file must be an existing file")
        with path.open("rb") as stream:
            upload = self._request(
                "POST",
                f"/cases/{case_id}/artifacts",
                files={"file": (path.name, stream, "application/octet-stream")},
                data={"kind": "debug_log"},
                timeout=900.0,
            ).json()
        job = self.post(f"/cases/{case_id}/artifacts/{upload['id']}/parse", json={})
        return {"artifact": upload, "job": job}

    def wait_job(self, job_id: str, timeout_seconds: int = 900) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.get(f"/jobs/{job_id}")
            status = str(last.get("status") or "")
            if status == "COMPLETED":
                return last
            if status in {"FAILED", "CANCELLED", "DEAD_LETTER"}:
                raise RuntimeClientError(last.get("error_message") or f"Job ended as {status}")
            time.sleep(0.5)
        raise RuntimeClientError(f"Timed out waiting for job {job_id}; last status={last.get('status')}")

    def inspect(self, case_id: str, **params: Any) -> list[dict[str, Any]]:
        clean = {key: value for key, value in params.items() if value not in (None, "")}
        return self.get(f"/cases/{case_id}/events", params=clean)

    def search(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/cases/{case_id}/agentic-search", json=payload)

    def evidence_bundle(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/cases/{case_id}/evidence-bundle", json=payload)

    def attach_workspace(self, case_id: str, path: str, name: str | None = None) -> dict[str, Any]:
        return self.post(
            f"/cases/{case_id}/workspaces/attach",
            json={"path": path, "name": name},
        )

    def index_workspace(self, repository_id: str) -> dict[str, Any]:
        return self.post(f"/workspaces/{repository_id}/index", json={})

    def code_context(self, repository_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/workspaces/{repository_id}/code-context", json=payload)

    def diagnose(self, case_id: str) -> dict[str, Any]:
        return self.post(f"/cases/{case_id}/analyses", json={})

    def latest_analysis(self, case_id: str) -> dict[str, Any] | None:
        rows = self.get(f"/cases/{case_id}/analyses")
        return rows[0] if rows else None

    def generate_report(self, case_id: str, fmt: str) -> dict[str, Any]:
        analysis = self.latest_analysis(case_id)
        if not analysis:
            raise RuntimeClientError("No completed analysis exists for this case; run debug_diagnose first")
        return self.post(
            f"/cases/{case_id}/analyses/{analysis['id']}/reports/{fmt}",
            json={},
        )

    def ui_url(self, case_id: str | None = None) -> str:
        if case_id:
            return f"{self.runtime_url}/ui/cases/{case_id}"
        return f"{self.runtime_url}/ui/"

    def open_ui(self, case_id: str | None = None) -> dict[str, Any]:
        url = self.ui_url(case_id)
        opened = bool(webbrowser.open(url))
        return {"url": url, "opened": opened}
