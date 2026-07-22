from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_ROOT, get_settings


def run_database_migrations(database_url: str | None = None) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url or get_settings().database_url
    command.upgrade(config, "head")
