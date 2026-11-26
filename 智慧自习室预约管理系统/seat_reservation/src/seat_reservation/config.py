"""应用配置：环境变量 + 可选 ``.env``，带类型与范围校验。

设计原则
--------
* 配置只在进程启动时解析一次，运行期不可变（``frozen=True``）。
* 任何非法值立即抛 ``ConfigError``，避免“带病启动”。
* 读取优先级：**进程环境变量 > ``.env`` 文件 > 代码默认值**。
  ``.env`` 只从 ``SEAT_ENV_FILE`` 或项目根目录 ``.env`` 读取，不存在的文件直接忽略；
  已存在的进程环境变量永远不会被文件覆盖。
* ``SEAT_ENV`` 只接受 development/dev/test/production/prod，未知值直接报错，
  不会静默回退为开发环境。
* 测试通过 ``Settings.for_tests()`` 构造隔离配置，不读环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "var" / "delivery.db"
# 演示库与正式库默认分离：未显式指定 SEAT_DB_PATH 时，演示模式落到独立文件，
# 避免演示种子数据（含演示系统管理员）写进正式库。
DEFAULT_DEMO_DB_PATH = PROJECT_ROOT / "var" / "seat_demo.db"

ENV_ALIASES = {
    "development": "development",
    "dev": "development",
    "test": "test",
    "production": "production",
    "prod": "production",
}


class ConfigError(RuntimeError):
    """配置非法，进程应直接退出并打印原因。"""


def normalize_env(raw: str | None) -> str:
    """规范化 ``SEAT_ENV``：去空白 + 小写 + 别名折叠；未知值报错。"""
    value = (raw or "").strip().lower()
    canonical = ENV_ALIASES.get(value)
    if canonical is None:
        allowed = "/".join(ENV_ALIASES)
        raise ConfigError(f"SEAT_ENV 非法：{raw!r}；允许值：{allowed}")
    return canonical


def env_file_path() -> Path:
    override = os.environ.get("SEAT_ENV_FILE", "").strip()
    return Path(override).expanduser() if override else PROJECT_ROOT / ".env"


def load_env_file(path: Path | None = None) -> None:
    """加载简单 ``KEY=VALUE`` 格式的 ``.env``（仅标准库，无 shell 展开）。

    * 不覆盖已存在的进程环境变量（进程环境优先级最高）。
    * 忽略空行与 ``#`` 注释，支持 ``export KEY=VALUE`` 与成对引号。
    * 文件不存在时静默跳过；解析失败的行忽略，不猜测语义。
    """
    target = path or env_file_path()
    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


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
    #: 打印座位桌贴时写入二维码的对外地址（如 https://seat.example.edu）。
    #: 留空则使用当前请求的 base_url；经反向代理或 ngrok 时应显式设置。
    public_base_url: str = ""

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    @property
    def is_test(self) -> bool:
        return self.env.lower() == "test"

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        demo_mode = _env_bool("SEAT_DEMO_MODE", False)
        default_db = DEFAULT_DEMO_DB_PATH if demo_mode else DEFAULT_DB_PATH
        settings = cls(
            env=normalize_env(_env_str("SEAT_ENV", "development")),
            db_path=Path(_env_str("SEAT_DB_PATH", str(default_db))).expanduser(),
            host=_env_str("SEAT_HOST", "127.0.0.1"),
            port=_env_int("SEAT_PORT", 8010, minimum=1, maximum=65535),
            log_level=_env_str("SEAT_LOG_LEVEL", "INFO").strip().upper(),
            log_json=_env_bool("SEAT_LOG_JSON", False),
            session_idle_minutes=_env_int("SEAT_SESSION_IDLE_MINUTES", 30, minimum=5, maximum=720),
            session_absolute_hours=_env_int("SEAT_SESSION_ABSOLUTE_HOURS", 8, minimum=1, maximum=72),
            cookie_secure=_env_bool("SEAT_COOKIE_SECURE", False),
            trust_proxy=_env_bool("SEAT_TRUST_PROXY", False),
            demo_mode=demo_mode,
            scheduler_enabled=_env_bool("SEAT_SCHEDULER_ENABLED", True),
            scheduler_interval_seconds=_env_int(
                "SEAT_SCHEDULER_INTERVAL_SECONDS", 60, minimum=10, maximum=3600
            ),
            acs_token=_env_str("SEAT_ACS_TOKEN", ""),
            public_base_url=_env_str("SEAT_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.env not in set(ENV_ALIASES.values()):
            raise ConfigError(f"SEAT_ENV 非法：{self.env!r}")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(f"SEAT_LOG_LEVEL 非法：{self.log_level}")
        if self.is_production and not self.cookie_secure:
            raise ConfigError("生产环境必须开启 SEAT_COOKIE_SECURE=true（全站 HTTPS）")
        if self.is_production and self.demo_mode:
            raise ConfigError("生产环境不得启用演示模式（SEAT_DEMO_MODE 必须关闭）")
        if self.is_production and self.acs_token == "":
            # 不是致命错误，但必须显式知情：ACS 端点关闭时只能扫码签到。
            pass
        if self.public_base_url and not self.public_base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"SEAT_PUBLIC_BASE_URL 必须是 http(s) 地址，当前值 {self.public_base_url!r}"
            )

    def public_summary(self) -> str:
        """可安全写入日志的配置摘要：不含令牌、密码与数据库完整路径。"""
        return (
            f"env={self.env} demo={self.demo_mode} host={self.host} port={self.port} "
            f"db_file={self.db_path.name} cookie_secure={self.cookie_secure} "
            f"trust_proxy={self.trust_proxy} scheduler={self.scheduler_enabled} "
            f"acs_enabled={bool(self.acs_token)} log_level={self.log_level}"
        )

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
