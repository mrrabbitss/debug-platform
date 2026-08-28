from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope


class SPAStaticFiles(StaticFiles):
    """Serve built assets and fall back to index.html for client-side routes."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def mount_static_frontend(app: FastAPI, frontend_root: Path) -> None:
    frontend_root = frontend_root.resolve()
    if not (frontend_root / "index.html").is_file():
        raise RuntimeError(
            "STATIC_FRONTEND_ROOT must contain a built frontend index.html: "
            f"{frontend_root}"
        )
    app.mount(
        "/",
        SPAStaticFiles(directory=frontend_root, html=True),
        name="frontend",
    )
