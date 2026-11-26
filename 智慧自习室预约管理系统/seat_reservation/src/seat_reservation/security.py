"""密码与令牌安全原语（仅标准库）。

* 密码：PBKDF2-HMAC-SHA256，随机 16 字节盐，默认 260000 轮。
* 会话令牌：32 字节随机数，数据库只存 SHA-256 摘要，泄库不可直接复用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 260_000

#: 管理员新建账号 / 重置密码时统一使用的临时密码。
#: 仅用于“下一次登录必须改密”的一次性入口，不作为长期密码。
DEFAULT_TEMP_PASSWORD = "12345678"


def password_change_error(new_password: str) -> str | None:
    """个人改密时的强密码校验；返回错误文案或 ``None``。

    临时密码 ``12345678`` 只允许由管理员生成，用户首次登录后不得继续沿用。
    复杂度要求：至少 8 位，且大写字母/小写字母/数字/符号至少包含 3 类。
    """
    if len(new_password) < 8:
        return "新密码至少 8 位"
    if new_password == DEFAULT_TEMP_PASSWORD:
        return "新密码不能继续使用临时密码 12345678，请设置个人新密码"
    classes = sum(
        (
            any(ch.islower() for ch in new_password),
            any(ch.isupper() for ch in new_password),
            any(ch.isdigit() for ch in new_password),
            any(not ch.isalnum() for ch in new_password),
        )
    )
    if classes < 3:
        return "新密码需包含大写字母、小写字母、数字、符号中的至少 3 类"
    return None


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"{ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations_text)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def generate_initial_password(length: int = 16) -> str:
    """生成随机初始密码（首次初始化、CLI 建号）。

    只在初始化渠道展示一次，不写入源码、仓库或长期明文文件。
    """
    return secrets.token_urlsafe(max(8, length))


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def constant_time_equals(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


#: 座位二维码签名密钥在 ``app_secrets`` 中的键名。
QR_SECRET_KEY = "seat_qr_secret"
QR_TOKEN_VERSION = "q1"


def build_seat_qr_token(seat_id: str, secret: str) -> str:
    """生成座位桌贴令牌：``q1.<base64url(seat_id)>.<签名>``。

    令牌只证明“这是哪个座位的桌贴”，不含用户身份；能否签到仍由服务端按
    预约归属、签到窗口与账号状态判定，因此照片转发拿不到别人的座位。
    """
    payload = base64.urlsafe_b64encode(seat_id.encode("utf-8")).decode("ascii").rstrip("=")
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{QR_TOKEN_VERSION}:{seat_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.urlsafe_b64encode(digest[:12]).decode("ascii").rstrip("=")
    return f"{QR_TOKEN_VERSION}.{payload}.{signature}"


def seat_id_from_qr_token(token: str, secret: str) -> str | None:
    """校验桌贴令牌并返回座位号；版本、格式或签名不符一律返回 ``None``。"""
    try:
        version, payload, signature = token.split(".")
    except (AttributeError, ValueError):
        return None
    if version != QR_TOKEN_VERSION:
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        seat_id = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    expected = build_seat_qr_token(seat_id, secret).rsplit(".", 1)[1]
    if not hmac.compare_digest(signature, expected):
        return None
    return seat_id
