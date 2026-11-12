"""业务参数（BR-01~BR-08 等数值）的定义、校验与快照。

* 参数存库为字符串，读取时按声明类型转换；非法值在写入前拒绝。
* 每条预约保存创建时的参数快照（BR-14），后续变更只影响新预约。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .timeutil import fmt_time, parse_time


@dataclass(frozen=True, slots=True)
class ParamSpec:
    code: str
    label: str
    kind: str  # "int" | "time"
    default: str
    minimum: int | None = None
    maximum: int | None = None
    unit: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unit": self.unit,
        }


PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec("booking.min_minutes", "单次最短时长", "int", "30", 30, 120, "分钟"),
    ParamSpec("booking.max_minutes", "单次最长时长", "int", "240", 30, 480, "分钟"),
    ParamSpec("booking.daily_max_count", "每日预约个数上限", "int", "2", 1, 10, "个"),
    ParamSpec("booking.daily_max_minutes", "每日预约总时长上限", "int", "240", 30, 720, "分钟"),
    ParamSpec("booking.window_days", "可预约窗口（含当日）", "int", "7", 1, 30, "天"),
    ParamSpec("checkin.before_minutes", "签到窗口：开始前", "int", "15", 0, 60, "分钟"),
    ParamSpec("checkin.after_minutes", "签到窗口：开始后", "int", "15", 0, 60, "分钟"),
    ParamSpec("checkin.credential_ttl_seconds", "动态凭证有效期", "int", "60", 30, 600, "秒"),
    ParamSpec("away.max_minutes", "暂离保留时长", "int", "30", 5, 180, "分钟"),
    ParamSpec("credit.initial", "信用分初始值", "int", "100", 0, 100, "分"),
    ParamSpec("credit.min", "信用分下限", "int", "0", 0, 100, "分"),
    ParamSpec("credit.max", "信用分上限", "int", "100", 0, 1000, "分"),
    ParamSpec("credit.threshold", "信用分预约阈值", "int", "60", 0, 1000, "分"),
    ParamSpec("credit.penalty.no_show", "未签到扣分", "int", "10", 0, 100, "分"),
    ParamSpec("credit.penalty.away_timeout", "暂离超时扣分", "int", "5", 0, 100, "分"),
    ParamSpec("credit.penalty.late_checkout", "超时未签退扣分", "int", "5", 0, 100, "分"),
    ParamSpec("credit.penalty.late_cancel", "临近取消扣分", "int", "5", 0, 100, "分"),
    ParamSpec("credit.penalty.false_report", "虚假上报扣分", "int", "20", 0, 100, "分"),
    ParamSpec("credit.penalty.manual_inspection", "现场巡检扣分", "int", "5", 0, 100, "分"),
    ParamSpec("credit.reward.on_time", "按时签到签退加分", "int", "1", 0, 10, "分"),
    ParamSpec("credit.reward.daily_cap", "每日加分上限", "int", "2", 0, 20, "分"),
    ParamSpec("cancel.free_minutes", "免费取消提前量", "int", "30", 0, 240, "分钟"),
    ParamSpec("consecutive.limit_count", "连续未签到次数阈值", "int", "3", 1, 10, "次"),
    ParamSpec("consecutive.limit_days", "连续违约限制时长", "int", "7", 1, 90, "天"),
    ParamSpec("waitlist.notify_count", "候补一次通知人数", "int", "3", 1, 10, "人"),
    ParamSpec("waitlist.confirm_minutes", "候补确认时限", "int", "5", 1, 60, "分钟"),
    ParamSpec("waitlist.max_defer", "候补顺延次数上限", "int", "1", 0, 5, "次"),
    ParamSpec("blacklist.window_days", "黑名单统计窗口", "int", "30", 1, 365, "天"),
    ParamSpec("blacklist.threshold", "自动拉黑违规次数", "int", "3", 1, 20, "次"),
    ParamSpec("blacklist.days", "黑名单限制时长", "int", "7", 1, 365, "天"),
    ParamSpec("appeal.window_days", "申诉时限", "int", "3", 1, 30, "天"),
    ParamSpec("reminder.before_minutes", "开始前提醒提前量", "int", "15", 0, 120, "分钟"),
    ParamSpec("room.open_time", "默认开放时间", "time", "07:00"),
    ParamSpec("room.close_time", "默认关闭时间", "time", "23:00"),
)

PARAM_INDEX: dict[str, ParamSpec] = {spec.code: spec for spec in PARAM_SPECS}


def validate_value(code: str, raw: Any) -> str:
    """校验并规范化单个参数值；非法时抛 ``ValueError``（带中文原因）。"""
    spec = PARAM_INDEX.get(code)
    if spec is None:
        raise ValueError(f"未知参数：{code}")
    if spec.kind == "int":
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{spec.label} 必须是整数") from exc
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{spec.label} 不能小于 {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{spec.label} 不能大于 {spec.maximum}")
        return str(value)
    if spec.kind == "time":
        try:
            return fmt_time(parse_time(str(raw)))
        except ValueError as exc:
            raise ValueError(f"{spec.label} 必须是 HH:MM 格式") from exc
    raise ValueError(f"未支持的参数类型：{spec.kind}")  # pragma: no cover


def check_consistency(values: Mapping[str, str]) -> list[str]:
    """跨字段一致性校验，返回问题列表（空列表表示通过）。"""

    def as_int(code: str) -> int:
        return int(values.get(code, PARAM_INDEX[code].default))

    problems: list[str] = []
    if as_int("booking.min_minutes") > as_int("booking.max_minutes"):
        problems.append("单次最短时长不能大于最长时长")
    if as_int("booking.daily_max_minutes") < as_int("booking.min_minutes"):
        problems.append("每日总时长上限不能小于单次最短时长")
    if not (
        as_int("credit.min")
        <= as_int("credit.threshold")
        <= as_int("credit.max")
    ):
        problems.append("信用分下限 ≤ 阈值 ≤ 上限 必须成立")
    if values.get("room.open_time", "07:00") >= values.get("room.close_time", "23:00"):
        problems.append("开放时间必须早于关闭时间")
    return problems


class ParamSet:
    """一组参数值；不可变语义（修改请构造新实例）。"""

    __slots__ = ("_values", "_ints")

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        merged: dict[str, str] = {}
        for spec in PARAM_SPECS:
            raw = (values or {}).get(spec.code, spec.default)
            merged[spec.code] = validate_value(spec.code, raw)
        self._values = merged
        self._ints: dict[str, int] = {}

    def int(self, code: str) -> int:
        cached = self._ints.get(code)
        if cached is None:
            cached = int(self._values[code])
            self._ints[code] = cached
        return cached

    def text(self, code: str) -> str:
        return self._values[code]

    def snapshot(self) -> dict[str, str]:
        return dict(self._values)

    def overridden(self, **overrides: Any) -> "ParamSet":
        values = dict(self._values)
        for code, value in overrides.items():
            key = code.replace("__", ".")
            values[key] = str(value)
        return ParamSet(values)

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, str] | None) -> "ParamSet":
        return cls(snapshot or {})

    def as_list(self) -> list[dict[str, Any]]:
        items = []
        for spec in PARAM_SPECS:
            item = spec.describe()
            item["value"] = self._values[spec.code]
            items.append(item)
        return items

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ParamSet) and self._values == other._values

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ParamSet({len(self._values)} values)"
