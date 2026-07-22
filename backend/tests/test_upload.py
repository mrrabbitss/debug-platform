import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile

from app.api.routes import upload_artifact
from app.core.utils import json_loads
from app.models import Case
from app.services.storage import storage


class FakeDb:
    def __init__(self) -> None:
        self.case = Case(id="CASE-test", title="test", description="")
        self.added = []

    def get(self, model, item_id):
        if model is Case and item_id == self.case.id:
            return self.case
        return None

    def add(self, value) -> None:
        self.added.append(value)

    def commit(self) -> None:
        pass

    def refresh(self, value) -> None:
        pass


def test_upload_artifact_stores_extensionless_debug_log_with_txt_name(tmp_path: Path):
    db = FakeDb()
    upload = UploadFile(filename="device_collectDebuginfo", file=BytesIO(b"NOTICE test"))
    stored_path = tmp_path / "device_collectDebuginfo.txt"

    with patch.object(storage, "save_upload", new_callable=AsyncMock) as save_upload:
        save_upload.return_value = (stored_path, 11, "a" * 64)
        artifact = asyncio.run(upload_artifact(db.case.id, db, upload, "debug_log"))

    save_upload.assert_awaited_once_with(
        upload,
        artifact.id,
        target_name="device_collectDebuginfo.txt",
    )
    assert artifact.original_name == "device_collectDebuginfo.txt"
    assert artifact.status == "UPLOADED"
    assert json_loads(artifact.metadata_json) == {
        "uploaded_original_name": "device_collectDebuginfo",
        "filename_normalized": True,
    }
