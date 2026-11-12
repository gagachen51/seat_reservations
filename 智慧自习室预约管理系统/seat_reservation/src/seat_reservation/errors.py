"""统一错误模型。

约定
----
* 领域层不抛 HTTP 异常，只抛 ``AppError`` 子类或返回 ``Result``。
* 每个错误都有稳定的 ``code``（前端可判断）、``message``（给人看）与可选 ``rule``
  （对应 SRS 的 BR/PR/ST 编号），便于验收追溯。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有可预期错误的基类。"""

    status_code = 400
    code = "BAD_REQUEST"
    default_message = "请求无法处理"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        rule: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.code = code or self.code
        self.rule = rule
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.rule:
            error["rule"] = self.rule
        if self.details:
            error["details"] = self.details
        return {"ok": False, "error": error}


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    default_message = "参数不合法"


class Unauthenticated(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    default_message = "请先登录"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    default_message = "没有执行该操作的权限"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "对象不存在"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    default_message = "资源冲突"


class RuleViolation(AppError):
    status_code = 400
    code = "RULE_VIOLATION"
    default_message = "违反业务规则"


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "操作过于频繁，请稍后再试"


class InternalError(AppError):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "服务器内部错误"


class SlotConflict(Exception):
    """数据库唯一约束捕获到的并发冲突，由仓储层转译为 ``Conflict``。"""

    def __init__(self, message: str, *, rule: str = "BR-11") -> None:
        self.rule = rule
        super().__init__(message)
