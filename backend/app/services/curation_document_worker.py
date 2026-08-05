"""Subprocess entrypoint for document-to-text extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.curation_documents import (
    DocumentExtractionError,
    prepare_curation_document,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--relative-path", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        prepared = prepare_curation_document(
            Path(args.source),
            args.relative_path,
            Path(args.destination),
        )
        payload = None if prepared is None else {
            "path": str(prepared.path),
            "method": prepared.method,
            "sha256": prepared.sha256,
            "line_count": prepared.line_count,
            "page_count": prepared.page_count,
            "truncated": prepared.truncated,
        }
        print(json.dumps({"ok": True, "result": payload}))
        return 0
    except DocumentExtractionError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": str(exc)}))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "ok": False,
            "code": "document_worker_failed",
            "message": type(exc).__name__,
        }))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
