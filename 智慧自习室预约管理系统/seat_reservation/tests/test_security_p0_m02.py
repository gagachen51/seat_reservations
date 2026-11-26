"""P0 M-02：环境配置与生产安全默认值回归测试。

覆盖：
* SEAT_ENV 规范化（strip/lower/别名）与未知值拒绝；
* 生产 Cookie Secure、Demo 互斥、Docs/OpenAPI 关闭；
* .env 加载与“进程环境优先”的优先级；
* 配置日志摘要不含秘密与完整数据库路径；
* 桌面启动器的环境设置不回归。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seat_reservation.api.app import create_app
from seat_reservation.config import (
    DEFAULT_DB_PATH,
    ConfigError,
    Settings,
    normalize_env,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """清空外部 SEAT_* 环境，并默认指向不存在的 .env，保证用例可重复。"""
    snapshot = dict(os.environ)
    for key in [name for name in os.environ if name.startswith("SEAT_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SEAT_ENV_FILE", str(tmp_path / "absent.env"))
    yield
    # load_env_file 会直接写 os.environ，这里还原，避免污染其他测试模块。
    for key in set(os.environ) - set(snapshot):
        os.environ.pop(key, None)
    os.environ.update(snapshot)


def _write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "test.env"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# SEAT_ENV 规范化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "canonical", "production"),
    [
        ("development", "development", False),
        (" DEV ", "development", False),
        ("Production", "production", True),
        (" prod ", "production", True),
        ("TEST", "test", False),
    ],
)
def test_env_values_are_normalised(
    monkeypatch: pytest.MonkeyPatch, raw: str, canonical: str, production: bool
) -> None:
    monkeypatch.setenv("SEAT_ENV", raw)
    monkeypatch.setenv("SEAT_COOKIE_SECURE", "true")  # 生产环境需要，其他环境无害
    settings = Settings.from_env()
    assert settings.env == canonical
    assert settings.is_production is production


@pytest.mark.parametrize("raw", ["staging", "  ", "prod2", "develop"])
def test_unknown_env_is_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SEAT_ENV", raw)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_normalize_env_rejects_unknown_directly() -> None:
    with pytest.raises(ConfigError):
        normalize_env("staging")
    assert normalize_env(" PROD ") == "production"


def test_blank_env_value_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAT_ENV", "")
    assert Settings.from_env().env == "development"


# ---------------------------------------------------------------------------
# 生产安全默认值
# ---------------------------------------------------------------------------
def test_production_requires_secure_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAT_ENV", "production")
    with pytest.raises(ConfigError):
        Settings.from_env()

    monkeypatch.setenv("SEAT_COOKIE_SECURE", "false")
    with pytest.raises(ConfigError):
        Settings.from_env()

    monkeypatch.setenv("SEAT_COOKIE_SECURE", "true")
    assert Settings.from_env().is_production


def test_production_rejects_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAT_ENV", "production")
    monkeypatch.setenv("SEAT_COOKIE_SECURE", "true")
    monkeypatch.setenv("SEAT_DEMO_MODE", "true")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_create_app_validates_direct_settings(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        create_app(Settings(env="production", db_path=tmp_path / "x.db"))
    with pytest.raises(ConfigError):
        create_app(
            Settings(
                env="production",
                cookie_secure=True,
                demo_mode=True,
                db_path=tmp_path / "x.db",
            )
        )


def test_production_disables_docs_and_openapi(tmp_path: Path) -> None:
    settings = Settings(
        env="production",
        cookie_secure=True,
        db_path=tmp_path / "prod.db",
        scheduler_enabled=False,
    )
    app = create_app(settings)
    assert app.docs_url is None
    assert app.openapi_url is None

    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_development_keeps_docs(tmp_path: Path) -> None:
    settings = Settings(
        env="development", db_path=tmp_path / "dev.db", scheduler_enabled=False
    )
    app = create_app(settings)
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"


def test_development_defaults_unchanged() -> None:
    settings = Settings.from_env()
    assert settings.env == "development"
    assert settings.port == 8010
    assert settings.session_idle_minutes == 30
    assert settings.session_absolute_hours == 8
    assert settings.cookie_secure is False
    assert settings.demo_mode is False
    assert settings.db_path == DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# .env 加载与优先级
# ---------------------------------------------------------------------------
def test_dotenv_is_loaded_from_configured_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_env(
        tmp_path,
        "\n".join(
            [
                "# 注释行",
                "SEAT_ENV=production",
                "SEAT_COOKIE_SECURE=true",
                "SEAT_PORT=8123",
                'SEAT_HOST="127.0.0.2"',
                "",
            ]
        ),
    )
    monkeypatch.setenv("SEAT_ENV_FILE", str(path))

    settings = Settings.from_env()
    assert settings.env == "production"
    assert settings.cookie_secure is True
    assert settings.port == 8123
    assert settings.host == "127.0.0.2"


def test_process_env_wins_over_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_env(tmp_path, "SEAT_PORT=8123\nSEAT_LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("SEAT_ENV_FILE", str(path))
    monkeypatch.setenv("SEAT_PORT", "8555")

    settings = Settings.from_env()
    assert settings.port == 8555
    assert settings.log_level == "DEBUG"  # 未被进程环境覆盖的项仍来自 .env


def test_dotenv_parsing_variants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _write_env(
        tmp_path,
        "export SEAT_LOG_LEVEL=debug\n"
        "没有等号的行\n"
        "SEAT_LOG_JSON='true'\n"
        "# SEAT_PORT=9999\n",
    )
    monkeypatch.setenv("SEAT_ENV_FILE", str(path))

    settings = Settings.from_env()
    assert settings.log_level == "DEBUG"
    assert settings.log_json is True
    assert settings.port == 8010


def test_missing_dotenv_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEAT_ENV_FILE", str(tmp_path / "does-not-exist.env"))
    assert Settings.from_env().env == "development"


# ---------------------------------------------------------------------------
# 配置日志不含秘密
# ---------------------------------------------------------------------------
def test_public_summary_contains_no_secrets(tmp_path: Path) -> None:
    settings = Settings(
        env="production",
        cookie_secure=True,
        acs_token="ACS-TOPSECRET-TOKEN",
        db_path=Path("C:/very/secret/location/seat_reservation.db"),
        scheduler_enabled=False,
    )
    summary = settings.public_summary()

    assert "ACS-TOPSECRET-TOKEN" not in summary
    assert "very/secret/location" not in summary
    assert "C:/very" not in summary
    assert "db_file=seat_reservation.db" in summary
    assert "acs_enabled=True" in summary


def test_startup_log_contains_no_secrets(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "seat.db"
    settings = Settings(
        env="development",
        db_path=db_path,
        acs_token="ACS-TOPSECRET-TOKEN",
        scheduler_enabled=False,
    )
    app = create_app(settings)

    messages: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("seat_reservation.http")
    handler = Capture()
    logger.addHandler(handler)
    try:
        with TestClient(app):
            pass
    finally:
        logger.removeHandler(handler)

    text = "\n".join(messages)
    assert messages, "启动日志应至少包含一条记录"
    assert "ACS-TOPSECRET-TOKEN" not in text
    assert str(db_path) not in text
    assert "seat.db" in text


# ---------------------------------------------------------------------------
# 桌面启动器环境设置
# ---------------------------------------------------------------------------
