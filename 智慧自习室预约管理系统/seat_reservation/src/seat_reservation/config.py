"""应用配置：全部来自环境变量，带类型与范围校验。

设计原则
--------
* 配置只在进程启动时解析一次，运行期不可变（``frozen=True``）。
* 任何非法值立即抛 ``ConfigError``，避免“带病启动”。
* 测试通过 ``Settings.for_tests()`` 构造隔离配置，不读环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "var" / "seat_reservation.db"


class ConfigError(RuntimeError):
    """配置非法，进程应直接退出并打印原因。"""


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:  # pragma: no cover - 防御性分支
        raise ConfigError(f"{name} 必须是整数，当前值 {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} 必须在 [{minimum}, {maximum}] 内，当前值 {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalised = raw.strip().lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} 必须是布尔值（true/false），当前值 {raw!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    """运行期配置。字段语义见 ``.env.example``。"""

    env: str = "development"
    db_path: Path = DEFAULT_DB_PATH
    host: str = "127.0.0.1"
    port: int = 8010
    log_level: str = "INFO"
    log_json: bool = False
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8
    cookie_name: str = "seat_session"
    csrf_cookie_name: str = "seat_csrf"
    cookie_secure: bool = False
    trust_proxy: bool = False
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 60
    acs_token: str = ""
    demo_mode: bool = False
    max_page_size: int = 100

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    @property
    def is_test(self) -> bool:
        return self.env.lower() == "test"

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            env=_env_str("SEAT_ENV", "development"),
            db_path=Path(_env_str("SEAT_DB_PATH", str(DEFAULT_DB_PATH))).expanduser(),
            host=_env_str("SEAT_HOST", "127.0.0.1"),
            port=_env_int("SEAT_PORT", 8010, minimum=1, maximum=65535),
            log_level=_env_str("SEAT_LOG_LEVEL", "INFO").upper(),
            log_json=_env_bool("SEAT_LOG_JSON", False),
            session_idle_minutes=_env_int("SEAT_SESSION_IDLE_MINUTES", 30, minimum=5, maximum=720),
            session_absolute_hours=_env_int("SEAT_SESSION_ABSOLUTE_HOURS", 8, minimum=1, maximum=72),
            cookie_secure=_env_bool("SEAT_COOKIE_SECURE", False),
            trust_proxy=_env_bool("SEAT_TRUST_PROXY", False),
            demo_mode=_env_bool("SEAT_DEMO_MODE", False),
            scheduler_enabled=_env_bool("SEAT_SCHEDULER_ENABLED", True),
            scheduler_interval_seconds=_env_int(
                "SEAT_SCHEDULER_INTERVAL_SECONDS", 60, minimum=10, maximum=3600
            ),
            acs_token=_env_str("SEAT_ACS_TOKEN", ""),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(f"SEAT_LOG_LEVEL 非法：{self.log_level}")
        if self.is_production and not self.cookie_secure:
            raise ConfigError("生产环境必须开启 SEAT_COOKIE_SECURE=true（全站 HTTPS）")
        if self.is_production and self.acs_token == "":
            # 不是致命错误，但必须显式知情：ACS 端点关闭时只能扫码签到。
            pass

    def with_db(self, path: Path | str) -> "Settings":
        return replace(self, db_path=Path(path))

    def with_env(self, env: str) -> "Settings":
        return replace(self, env=env)

    @classmethod
    def for_tests(cls, db_path: Path | str) -> "Settings":
        return cls(
            env="test",
            db_path=Path(db_path),
            scheduler_enabled=False,
            session_idle_minutes=30,
            demo_mode=True,
        )
