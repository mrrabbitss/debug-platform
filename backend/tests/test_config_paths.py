from pathlib import Path

from app.core.config import BACKEND_ROOT, PROJECT_ROOT, Settings, get_settings


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
    assert settings.data_root == (BACKEND_ROOT / "data").resolve()
    assert settings.storage_root == (BACKEND_ROOT / "data" / "storage").resolve()
    assert settings.model_download_root == (BACKEND_ROOT / "data" / "models").resolve()
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


def test_portable_env_file_and_data_paths_can_be_overridden(
    tmp_path: Path, monkeypatch
) -> None:
    data_root = tmp_path / "portable-data"
    storage_root = data_root / "storage"
    frontend_root = tmp_path / "web"
    frontend_root.mkdir()
    env_path = tmp_path / "portable.env"
    env_path.write_text(
        "APP_ENV=test\n"
        "DATABASE_URL=sqlite:///:memory:\n"
        f"DATA_ROOT={data_root.as_posix()}\n"
        f"STORAGE_ROOT={storage_root.as_posix()}\n"
        f"STATIC_FRONTEND_ROOT={frontend_root.as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEBUG_PLATFORM_ENV_FILE", str(env_path))
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.data_root == data_root.resolve()
        assert settings.storage_root == storage_root.resolve()
        assert settings.model_download_root == (data_root / "models").resolve()
        assert settings.static_frontend_root == frontend_root.resolve()
        assert settings.database_url == "sqlite:///:memory:"
    finally:
        get_settings.cache_clear()
