from pathlib import Path

from app.core.config import BACKEND_ROOT, PROJECT_ROOT, Settings


def test_repository_env_file_is_absolute() -> None:
    assert Path(Settings.model_config["env_file"]) == PROJECT_ROOT / ".env"


def test_relative_local_paths_do_not_depend_on_working_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///./data/gw_ap_debug.db",
        storage_root=Path("./data/storage"),
    )

    expected_database = (BACKEND_ROOT / "data" / "gw_ap_debug.db").resolve().as_posix()
    assert settings.database_url == f"sqlite:///{expected_database}"
    assert settings.storage_root == (BACKEND_ROOT / "data" / "storage").resolve()
    assert settings.model_secret_key_path == (BACKEND_ROOT / "data" / "model_secret.key").resolve()


def test_absolute_local_paths_are_preserved(tmp_path: Path) -> None:
    database_path = (tmp_path / "database.db").resolve()
    storage_path = (tmp_path / "storage").resolve()

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{database_path.as_posix()}",
        storage_root=storage_path,
    )

    assert settings.database_url == f"sqlite:///{database_path.as_posix()}"
    assert settings.storage_root == storage_path
