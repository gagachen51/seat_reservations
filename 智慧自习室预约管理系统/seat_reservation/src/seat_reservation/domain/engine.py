"""领域引擎：全部业务用例与规则判定的唯一实现处。

约定
----
* 纯校验失败（参数、权限、对象不存在、状态不允许）抛 ``AppError``，事务回滚，
  保证“拒绝时不产生任何数据变化”（ST-12）。
* 需要“拒绝但已产生业务后果”的场景（超窗签到判违约、暂离超时自动释放、
  候补超时顺延），返回 ``Result.failure`` 并由上层提交当前事务。
* 所有时间由调用方以 ``now`` 注入，领域层不读系统时钟，保证可测试与可复现。
"""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from ..errors import (
    Conflict,
    Forbidden,
    NotFound,
    RuleViolation,
    SlotConflict,
    ValidationFailed,
)
from .models import (
    OCCUPYING_STATUSES,
    QUOTA_STATUSES,
    Actor,
    AuditLog,
    BlacklistRecord,
    Complaint,
    ComplaintStatus,
    ComplaintType,
    EndReason,
    LimitType,
    Notification,
    NotificationChannel,
    NotificationType,
    Page,
    Reservation,
    ReservationSource,
    ReservationStatus,
    ReservationView,
    Result,
    Review,
    Role,
    Room,
    Seat,
    SeatBlock,
    SeatType,
    User,
    Violation,
    ViolationSource,
    ViolationStatus,
    ViolationType,
    WaitlistEntry,
    WaitlistStatus,
    ROLE_LABELS,
    SEAT_STATE_LABELS,
)
from .params import PARAM_INDEX, ParamSet, check_consistency, validate_value
from .ports import Repository
from . import resource_rules
from .reservation_rules import (
    checkin_account_error,
    extended_end_slot,
    extension_account_error,
    extension_bounds_error,
    extension_minutes_error,
    extension_quota_error,
    extension_state_error,
    waitlist_cancel_state_error,
    waitlist_confirm_state_error,
    waitlist_target_error,
)
from .rules import (
    blacklist_threshold_reached,
    cancel_phase,
    checkin_phase,
    checkin_window,
    clamp_credit,
    consecutive_limit_active,
    credential_valid,
    implements,
    in_booking_window,
    quota_exceeded,
    reward_eligible,
    seat_state_from_reservation,
    validate_slot_range,
    within_open_hours,
)
from .timeutil import (
    SLOT_MINUTES,
    add_minutes,
    datetime_at,
    fmt_date,
    fmt_datetime,
    hhmm,
    minutes_of_slots,
    overlap,
    slot_of_time,
    time_of_slot,
)

REWARD_REASON = "按时签到签退奖励"
#: M-04 资源不可用取消的统一结束原因（B 的 release_affected_reservations 契约）。
RESOURCE_UNAVAILABLE_REASON = "resource_unavailable"
_CROCKFORD = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# FR-54 / FR-62 / FR-72 要求的导出类型；路由与引擎共用，避免"前端有链接、后端不认"。
EXPORT_KINDS = (
    "reservations",
    "violations",
    "audit",
    "complaints",
    "blacklist",
    "credit_logs",
    "waitlist",
)
ANALYTICS_DEFAULT_DAYS = 7
MAX_ANALYTICS_DAYS = 90
# L-10：导出最大跨度/行数。超过上限返回 4xx，不静默截断。
MAX_EXPORT_DAYS = 366
MAX_EXPORT_ROWS = 10_000

# 审计 details/before/after 的敏感 key 片段；新增模块统一走 audit_event。
_AUDIT_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "credential",
    "csrf",
    "authorization",
    "cookie",
)


def _redact_audit(value: Any) -> Any:
    """递归过滤审计数据中的敏感字段，不改变非敏感业务字段。"""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if any(part in text_key.lower() for part in _AUDIT_SENSITIVE_KEY_FRAGMENTS):
                result[text_key] = "[REDACTED]"
            else:
                result[text_key] = _redact_audit(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_audit(item) for item in value]
    return value


def _new_credential() -> str:
    return "".join(secrets.choice(_CROCKFORD) for _ in range(10))


class DomainEngine:
    def __init__(self, repo: Repository, params: ParamSet | None = None) -> None:
        self.repo = repo
        self.params = params or repo.load_params()

    # ------------------------------------------------------------------
    # 通用辅助
    # ------------------------------------------------------------------
    def _require(self, actor: Actor, roles: Iterable[Role], rule: str) -> None:
        if actor.role not in set(roles):
            raise Forbidden(f"该操作需要 {rule} 规定的角色权限", rule=rule)

    def _user(self, actor: Actor) -> User:
        user = self.repo.get_user(actor.id)
        if user is None:
            raise NotFound("账号不存在")
        return user

    def _view(self, reservation: Reservation) -> ReservationView:
        seat = self.repo.get_seat(reservation.seat_id)
        room = self.repo.get_room(seat.room_id) if seat else None
        user = self.repo.get_user(reservation.user_id)
        return ReservationView(
            reservation=reservation,
            seat_id=reservation.seat_id,
            room_id=room.id if room else "",
            room_name=room.name if room else "",
            user_name=user.name if user else reservation.user_id,
            has_review=self.repo.review_of_reservation(reservation.id) is not None,
        )

    def _reservation(self, reservation_id: str) -> Reservation:
        reservation = self.repo.get_reservation(reservation_id)
        if reservation is None:
            raise NotFound(f"预约 {reservation_id} 不存在")
        return reservation

    def _own_reservation(self, actor: Actor, reservation_id: str, rule: str = "PR-03") -> Reservation:
        reservation = self._reservation(reservation_id)
        if actor.role != Role.STUDENT or reservation.user_id != actor.id:
            raise Forbidden("只能操作本人的预约记录", rule=rule)
        return reservation

    def _audit(
        self,
        actor: Actor | None,
        action: str,
        target_type: str,
        target_id: str,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        now: datetime,
        ip: str = "",
    ) -> None:
        actor_id = actor.id if actor else "SYSTEM"
        actor_role = actor.role.value if actor else "system"
        self.repo.insert_audit(
            AuditLog(
                id=self.repo.next_id("audit", "A-", 7),
                actor_id=actor_id,
                actor_role=actor_role,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before=before,
                after=after,
                ip=ip,
                created_at=now,
            )
        )

    def audit_event(
        self,
        actor: Actor | None,
        action: str,
        target_type: str,
        target_id: str,
        *,
        result: str = "success",
        details: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        now: datetime | None = None,
        ip: str = "",
    ) -> None:
        """共享审计入口：B/C/D 统一调用，不修改 _audit 的既有签名。

        参数：
        - actor: 操作者；系统任务可为 None。
        - action: 稳定事件名，例如 export_csv / reservation_cancel。
        - target_type/target_id: 审计对象。
        - result: success / denied / failed / noop。
        - details: 事件附加信息（自动脱敏）。
        - before/after: 可选状态快照（自动脱敏）。
        - now/ip: 测试可注入时间与来源。
        """
        clean_details = _redact_audit(details or {})
        clean_before = _redact_audit(before) if before is not None else None
        clean_after = _redact_audit(after) if after is not None else None
        payload: dict[str, Any] = {"result": result}
        if clean_details:
            payload["details"] = clean_details
        if clean_after is not None:
            payload["after"] = clean_after
        self._audit(
            actor,
            action,
            target_type,
            target_id,
            before=clean_before,
            after=payload,
            now=now or datetime.now(),
            ip=ip,
        )

    def _notify(
        self,
        user_id: str,
        notification_type: str,
        content: str,
        now: datetime,
        *,
        dedupe_key: str | None = None,
        channel: str = NotificationChannel.INBOX.value,
    ) -> None:
        if dedupe_key and self.repo.has_notification(user_id, dedupe_key):
            return
        self.repo.insert_notification(
            Notification(
                id=self.repo.next_id("notification", "N-", 7),
                user_id=user_id,
                notification_type=notification_type,
                content=content,
                channel=channel,
                created_at=now,
                dedupe_key=dedupe_key,
            )
        )

    # ------------------------------------------------------------------
    # 信用分、限制与黑名单
    # ------------------------------------------------------------------
    def _credit_delta(
        self,
        user: User,
        delta: int,
        reason: str,
        now: datetime,
        *,
        violation_id: str | None = None,
        operator_id: str | None = None,
    ) -> int:
        applied = clamp_credit(
            user.credit + delta,
            self.params.int("credit.min"),
            self.params.int("credit.max"),
        )
        real_delta = applied - user.credit
        if real_delta == 0:
            return 0
        user.credit = applied
        user.updated_at = now
        self.repo.save_user(user)
        self.repo.insert_credit_log(
            log_id=self.repo.next_id("credit", "CL-", 7),
            user_id=user.id,
            delta=real_delta,
            balance_after=applied,
            reason=reason,
            violation_id=violation_id,
            operator_id=operator_id,
            created_at=now,
        )
        return real_delta

    def _refresh_limit(self, user: User, now: datetime) -> None:
        """按 BR-07/BR-15/BR-20 重新计算限制状态（三者独立，任一成立即限制）。"""
        changed = False
        active = self.repo.active_blacklist(user.id, now)
        if active is not None:
            if user.limit_type != LimitType.BLACKLIST.value or user.limit_until != active.end_at:
                user.limit_type = LimitType.BLACKLIST.value
                user.limit_until = active.end_at
                changed = True
        elif user.credit < self.params.int("credit.threshold"):
            if user.limit_type != LimitType.CREDIT.value:
                user.limit_type = LimitType.CREDIT.value
                user.limit_until = None
                changed = True
        elif consecutive_limit_active(
            user.consecutive_no_show,
            self.params.int("consecutive.limit_count"),
            user.limit_until,
            now,
        ):
            if user.limit_type != LimitType.CONSECUTIVE.value:
                user.limit_type = LimitType.CONSECUTIVE.value
                changed = True
        elif user.limit_type != LimitType.NONE.value:
            user.limit_type = LimitType.NONE.value
            user.limit_until = None
            changed = True
        if changed:
            user.updated_at = now
            self.repo.save_user(user)

    def _apply_blacklist(self, user: User, now: datetime) -> None:
        if self.repo.active_blacklist(user.id, now) is not None:
            return
        window_start = now - timedelta(days=self.params.int("blacklist.window_days"))
        active_count = self.repo.count_active_violations(user.id, window_start)
        if not blacklist_threshold_reached(
            active_count, self.params.int("blacklist.threshold")
        ):
            return
        end_at = now + timedelta(days=self.params.int("blacklist.days"))
        record = BlacklistRecord(
            id=self.repo.next_id("blacklist", "BL-", 6),
            user_id=user.id,
            reason=f"{self.params.int('blacklist.window_days')} 天内有效违约达到 "
            f"{active_count} 次，系统自动拉黑",
            source="auto",
            start_at=now,
            end_at=end_at,
            created_by=None,
            created_at=now,
        )
        self.repo.insert_blacklist(record)
        user.limit_type = LimitType.BLACKLIST.value
        user.limit_until = end_at
        user.updated_at = now
        self.repo.save_user(user)
        self._notify(
            user.id,
            NotificationType.VIOLATION.value,
            f"因近期违约次数达到阈值，账号被限制预约至 {fmt_datetime(end_at)}。",
            now,
        )
        self._audit(
            None,
            "auto_blacklist",
            "user",
            user.id,
            before={"limit_type": LimitType.NONE.value},
            after={"limit_type": LimitType.BLACKLIST.value, "until": fmt_datetime(end_at)},
            now=now,
        )

    def _release_auto_blacklist_if_eligible(
        self, actor: Actor, user: User, now: datetime, *, ip: str = ""
    ) -> None:
        """申诉撤销违规后，若自动拉黑依据已不足，解除自动黑名单。

        人工拉黑（source=manual）不在此处自动解除，必须由管理员显式操作。
        """
        record = self.repo.active_blacklist(user.id, now)
        if record is None or record.source != "auto":
            return
        window_start = now - timedelta(days=self.params.int("blacklist.window_days"))
        remaining = self.repo.count_active_violations(user.id, window_start)
        if remaining >= self.params.int("blacklist.threshold"):
            return
        record.released_at = now
        record.released_by = actor.id
        self.repo.update_blacklist(record)
        user.limit_type = LimitType.NONE.value
        user.limit_until = None
        user.updated_at = now
        self.repo.save_user(user)
        self._refresh_limit(user, now)
        self._audit(
            actor,
            "blacklist_release",
            "user",
            user.id,
            before={"source": "auto", "violations": remaining + 1},
            after={"source": "auto", "violations": remaining, "reason": "申诉成立，自动解除"},
            now=now,
            ip=ip,
        )

    def _booking_limit(self, user: User, now: datetime) -> tuple[str, str] | None:
        """返回 (规则编号, 原因)；None 表示可以预约。"""
        if not user.is_active:
            return "PR-02", "账号已停用，无法创建预约"
        # 限制到期自动解除
        if user.limit_type in {LimitType.CONSECUTIVE.value, LimitType.BLACKLIST.value}:
            if user.limit_until is not None and now >= user.limit_until:
                user.limit_type = LimitType.NONE.value
                user.limit_until = None
                user.updated_at = now
                self.repo.save_user(user)
        if user.limit_type == LimitType.BLACKLIST.value:
            return "BR-20", f"账号在黑名单中，限制至 {fmt_datetime(user.limit_until)}"
        if user.credit < self.params.int("credit.threshold"):
            return (
                "BR-07",
                f"信用分 {user.credit} 低于 {self.params.int('credit.threshold')}，"
                "可通过申诉恢复后再次预约",
            )
        if user.limit_type == LimitType.CONSECUTIVE.value and (
            user.limit_until is None or now < user.limit_until
        ):
            return "BR-15", f"连续未签到达到 {self.params.int('consecutive.limit_count')} 次，限制至 {fmt_datetime(user.limit_until)}"
        return None

    def _record_violation(
        self,
        reservation: Reservation,
        violation_type: str,
        now: datetime,
        *,
        source: str = ViolationSource.SYSTEM.value,
        note: str = "",
        operator: Actor | None = None,
    ) -> Violation:
        policy = ParamSet.from_snapshot(reservation.policy)
        penalty_map = {
            ViolationType.NO_SHOW.value: ("credit.penalty.no_show", "未签到"),
            ViolationType.AWAY_TIMEOUT.value: ("credit.penalty.away_timeout", "暂离超时未返回"),
            ViolationType.LATE_CHECKOUT.value: ("credit.penalty.late_checkout", "超时未签退"),
            ViolationType.LATE_CANCEL.value: ("credit.penalty.late_cancel", "临近开始取消"),
            ViolationType.FALSE_REPORT.value: ("credit.penalty.false_report", "虚假上报"),
            ViolationType.CREDENTIAL_MISUSE.value: ("credit.penalty.false_report", "冒用凭证"),
            ViolationType.MANUAL_INSPECTION.value: (
                "credit.penalty.manual_inspection",
                "现场巡检登记违约",
            ),
        }
        param_code, label = penalty_map[violation_type]
        points = policy.int(param_code)
        violation = Violation(
            id=self.repo.next_id("violation", "VIO-", 6),
            reservation_id=reservation.id,
            user_id=reservation.user_id,
            violation_type=violation_type,
            points=points,
            source=source,
            status=ViolationStatus.ACTIVE.value,
            occurred_at=now,
            note=note,
            operator_id=operator.id if operator else None,
            created_at=now,
        )
        self.repo.insert_violation(violation)

        user = self.repo.get_user(reservation.user_id)
        if user is not None:
            real_delta = self._credit_delta(
                user,
                -points,
                f"违规：{label}",
                now,
                violation_id=violation.id,
                operator_id=operator.id if operator else None,
            )
            # 信用分触底时实际扣分为 0，也要留一条 0 分流水；
            # 否则申诉通过时无法判断"当时到底扣了多少"，容易把分加回去造成通胀。
            if real_delta == 0 and self.repo.credit_delta_for_violation(violation.id) is None:
                self.repo.insert_credit_log(
                    log_id=self.repo.next_id("credit", "CL-", 7),
                    user_id=user.id,
                    delta=0,
                    balance_after=user.credit,
                    reason=f"违规：{label}（信用分已触底，实际扣 0 分）",
                    violation_id=violation.id,
                    operator_id=operator.id if operator else None,
                    created_at=now,
                )
            if violation_type == ViolationType.NO_SHOW.value:
                user.consecutive_no_show += 1
                if user.consecutive_no_show >= policy.int("consecutive.limit_count"):
                    user.limit_type = LimitType.CONSECUTIVE.value
                    user.limit_until = now + timedelta(days=policy.int("consecutive.limit_days"))
            user.updated_at = now
            self.repo.save_user(user)
            self._apply_blacklist(user, now)
            self._refresh_limit(user, now)
            self._notify(
                user.id,
                NotificationType.VIOLATION.value,
                f"{label}：记违规并扣 {points} 分，当前信用分 {user.credit}。",
                now,
            )
        return violation

    # ------------------------------------------------------------------
    # 座位占用与释放
    # ------------------------------------------------------------------
    def _release_reservation(
        self,
        reservation: Reservation,
        status: str,
        end_reason: str,
        now: datetime,
        *,
        offer_waitlist: bool = True,
    ) -> None:
        reservation.status = status
        reservation.end_reason = end_reason
        if reservation.checked_out_at is None:
            reservation.checked_out_at = now
        reservation.updated_at = now
        reservation.version += 1
        self.repo.update_reservation(reservation)
        self.repo.release(reservation.id)
        if offer_waitlist:
            self._offer_waitlist(
                reservation.seat_id,
                reservation.day,
                reservation.start_slot,
                reservation.end_slot,
                now,
            )

    def _resource_unavailable_error(
        self, reservation: Reservation
    ) -> tuple[str, str] | None:
        """判定预约目标资源当前是否已不可用（M-04，只读，不写库）。

        资源停用的**入口**归 B（``update_room`` / ``update_seat``）；C 只在
        预约状态/扫描侧读取最新状态：房间关闭、座位停用/维护、或预约时段
        落在未释放封禁内，均视为不可履约，不能按用户未签到处理。
        """
        seat = self.repo.get_seat(reservation.seat_id)
        if seat is None:
            return ("BR-09", f"座位 {reservation.seat_id} 已不存在")
        room = self.repo.get_room(seat.room_id)
        if room is None:
            return ("FR-21", f"自习室 {seat.room_id} 已不存在")
        if room.status != "open":
            return ("FR-21", f"自习室 {room.name or room.id} 已停用")
        if seat.admin_status == "disabled":
            return ("BR-09", f"座位 {seat.id} 已停用")
        if seat.admin_status == "maintenance" and not seat.maintenance_by_block:
            # 手工维护没有明确结束时间，按长期不可用处理；封禁管理的维护
            # 只在其封禁时间窗与预约时段重叠时才取消（见下方 block 判定）。
            return ("BR-09", f"座位 {seat.id} 当前维护中")
        if self.repo.seat_block_overlaps(
            seat.id, reservation.start_at, reservation.end_at
        ):
            return ("BR-09", f"座位 {seat.id} 在该时段处于维护封禁")
        return None

    def _cancel_resource_unavailable(
        self,
        reservation: Reservation,
        reason: str,
        now: datetime,
        *,
        actor: Actor | None = None,
    ) -> bool:
        """资源不可用时统一取消预约：不记 no_show、不扣信用、不重复处理。

        与 B 的 ``release_affected_reservations`` 契约一致：预约置为
        ``cancelled``、``end_reason="resource_unavailable"``，写通知与审计。
        """
        if reservation.status not in OCCUPYING_STATUSES:
            return False
        before = {
            "status": reservation.status,
            "end_reason": reservation.end_reason,
            "version": reservation.version,
        }
        self._release_reservation(
            reservation,
            ReservationStatus.CANCELLED.value,
            RESOURCE_UNAVAILABLE_REASON,
            now,
        )
        self._notify(
            reservation.user_id,
            NotificationType.SEAT_BLOCKED.value,
            f"因资源不可用（{reason}）取消您 {reservation.seat_id} "
            f"{fmt_date(reservation.day)} {hhmm(reservation.start_slot)}-"
            f"{hhmm(reservation.end_slot)} 的预约；不记违约、不扣信用分。",
            now,
            dedupe_key=f"resource_unavailable:{reservation.id}",
        )
        self._audit(
            actor,
            "reservation_resource_unavailable",
            "reservation",
            reservation.id,
            before=before,
            after={
                "status": reservation.status,
                "end_reason": reservation.end_reason,
                "reason": reason,
            },
            now=now,
        )
        return True

    def release_affected_reservations(
        self,
        room_id: str | None = None,
        seat_id: str | None = None,
        reason: str = "",
        actor: Actor | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        """B 的资源变更统一 helper：释放房间/座位下所有仍有效的预约。

        契约（M-04）：受影响预约统一 ``cancelled``、
        ``end_reason="resource_unavailable"``；不记 no_show、不扣信用；写通知
        与审计；重复调用幂等（第二次已无占用中的预约）。B 的 ``update_room`` /
        ``update_seat`` 在状态变更后调用本方法；C 不在资源入口重复实现。
        """
        if not room_id and not seat_id:
            return []
        current = now or datetime.now()
        label = reason.strip() or "资源状态变更"
        affected: list[str] = []
        for reservation in self.repo.list_active_reservations(
            room_id=room_id, seat_id=seat_id
        ):
            if self._cancel_resource_unavailable(
                reservation, label, current, actor=actor
            ):
                affected.append(reservation.id)
        return affected

    def _offer_waitlist(
        self, seat_id: str, day: date, start_slot: int, end_slot: int, now: datetime
    ) -> list[str]:
        # 只对尚未开始的时段发候补通知：已开始/已结束的时段确认后无法使用，
        # 反而会被下一次扫描判成未签到并扣分。
        start_at, end_at = datetime_at(day, start_slot), datetime_at(day, end_slot)
        if start_at <= now:
            return []
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            return []
        room = self.repo.get_room(seat.room_id)
        if room is None or room.status != "open" or seat.admin_status != "available":
            return []
        if self.repo.seat_block_overlaps(seat_id, start_at, end_at):
            return []
        candidates = self.repo.waitlist_candidates(
            seat_id,
            day,
            start_slot,
            end_slot,
            self.params.int("waitlist.notify_count"),
            self.params.int("waitlist.max_defer"),
        )
        notified: list[str] = []
        deadline = add_minutes(now, self.params.int("waitlist.confirm_minutes"))
        for entry in candidates:
            entry.status = WaitlistStatus.NOTIFIED.value
            entry.notified_at = now
            entry.confirm_deadline = deadline
            self.repo.update_waitlist(entry)
            self._notify(
                entry.user_id,
                NotificationType.WAITLIST_AVAILABLE.value,
                f"座位 {seat_id} 在 {fmt_date(day)} {hhmm(start_slot)}-{hhmm(end_slot)} "
                f"已释放，请在 {self.params.int('waitlist.confirm_minutes')} 分钟内确认，逾期顺延。",
                now,
            )
            notified.append(entry.id)
        return notified

    def _expire_waitlist(
        self, entry: WaitlistEntry, now: datetime, *, offer_next: bool = True
    ) -> None:
        """把候补置为过期；需要顺延时立即通知下一位。

        调用方必须返回 ``Result.failure``（而不是抛异常），否则外层写事务会回滚，
        ``EXPIRED`` 和顺延通知都不会落库。
        """
        entry.status = WaitlistStatus.EXPIRED.value
        self.repo.update_waitlist(entry)
        if offer_next:
            self._offer_waitlist(entry.seat_id, entry.day, entry.start_slot, entry.end_slot, now)

    # ------------------------------------------------------------------
    # 查询座位
    # ------------------------------------------------------------------
    @implements("PR-01", "FR-24", "SM-02")
    def list_seats(
        self,
        actor: Actor,
        day: date,
        start: time,
        end: time,
        *,
        room_id: str | None = None,
        seat_type: str | None = None,
        has_power: bool | None = None,
        is_window: bool | None = None,
        only_available: bool = False,
    ) -> list[dict[str, Any]]:
        self._require(
            actor,
            {Role.STUDENT, Role.TEACHER, Role.ROOM_ADMIN, Role.SYS_ADMIN},
            "PR-01",
        )
        try:
            start_slot, end_slot = slot_of_time(start), slot_of_time(end)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        if end_slot <= start_slot:
            raise ValidationFailed("结束时间必须晚于开始时间")
        views = self.repo.seat_views(
            day,
            start_slot,
            end_slot,
            room_id=room_id,
            seat_type=seat_type,
            has_power=has_power,
            is_window=is_window,
            only_available=only_available,
            # L-08：学生不下发他人预约 ID（holder_reservation_id），管理员现场处置需要
            include_holder=resource_rules.can_expose_holder(actor.role.value),
        )
        return [view.as_dict() for view in views]

    @implements("FR-24", "PR-01")
    def seat_room_totals(
        self, actor: Actor, *, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """每个自习室的物理启用座位数（排除 ``disabled`` 的座位）。

        学生的座位列表会按筛选条件（仅看空闲/电源/靠窗）过滤，前端不能用过滤后
        的数组长度当分母，否则会出现 ``7 / 7 空闲``。这里提供稳定的房间分母。
        """
        self._require(
            actor,
            {Role.STUDENT, Role.TEACHER, Role.ROOM_ADMIN, Role.SYS_ADMIN},
            "PR-01",
        )
        seats = self.repo.list_seats(room_id=room_id)
        enabled: dict[str, int] = {}
        for seat in seats:
            if seat.admin_status == "disabled":
                continue
            enabled[seat.room_id] = enabled.get(seat.room_id, 0) + 1
        items: list[dict[str, Any]] = []
        for room in self.repo.list_rooms():
            if room_id and room.id != room_id:
                continue
            items.append(
                {
                    "id": room.id,
                    "name": room.name,
                    "total_seats": enabled.get(room.id, 0),
                }
            )
        return items

    # ------------------------------------------------------------------
    # 预约主流程
    # ------------------------------------------------------------------
    @implements("BR-01", "BR-02", "BR-03", "BR-07", "BR-09", "BR-11", "BR-15", "BR-20", "ST-00", "ST-13", "PR-02")
    def create_reservation(
        self,
        actor: Actor,
        seat_id: str,
        day: date,
        start: time,
        end: time,
        now: datetime,
    ) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-02")
        user = self._user(actor)
        limit = self._booking_limit(user, now)
        if limit is not None:
            rule, message = limit
            raise RuleViolation(message, rule=rule)

        if not in_booking_window(day, now.date(), self.params.int("booking.window_days")):
            raise RuleViolation(
                f"只能预约 {self.params.int('booking.window_days')} 天内（含当日）的时段",
                rule="BR-03",
            )

        try:
            start_slot, end_slot = slot_of_time(start), slot_of_time(end)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc

        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        room = self.repo.get_room(seat.room_id)
        if room is None:
            raise NotFound(f"自习室 {seat.room_id} 不存在")
        if room.status != "open":
            raise RuleViolation("该自习室已停用", rule="FR-21", code="ROOM_UNAVAILABLE")

        ok, message = validate_slot_range(
            start_slot,
            end_slot,
            self.params.int("booking.min_minutes"),
            self.params.int("booking.max_minutes"),
            SLOT_MINUTES,
        )
        if not ok:
            raise RuleViolation(message, rule="BR-01")
        if not within_open_hours(start_slot, end_slot, room.open_slot, room.close_slot):
            raise RuleViolation(
                f"预约时段须在开放时间 {hhmm(room.open_slot)}-{hhmm(room.close_slot)} 内",
                rule="BR-03",
            )

        minutes = minutes_of_slots(start_slot, end_slot)
        count, used_minutes = self.repo.daily_usage(user.id, day)
        quota_error = quota_exceeded(
            count,
            used_minutes,
            minutes,
            self.params.int("booking.daily_max_count"),
            self.params.int("booking.daily_max_minutes"),
        )
        if quota_error:
            raise RuleViolation(quota_error, rule="BR-02")

        if seat.admin_status != "available":
            code = "SEAT_UNAVAILABLE" if seat.admin_status == "maintenance" else "SEAT_DISABLED"
            raise RuleViolation(
                "该座位已停用维护，暂不支持预约",
                rule="BR-09",
                code=code,
            )

        start_at, end_at = datetime_at(day, start_slot), datetime_at(day, end_slot)
        if self.repo.seat_block_overlaps(seat_id, start_at, end_at):
            raise RuleViolation(
                "该座位已停用维护，暂不支持预约",
                rule="BR-09",
                code="SEAT_UNAVAILABLE",
            )
        if start_at <= now:
            raise RuleViolation("预约开始时间必须晚于当前时间", rule="BR-03")

        # 先判断用户自身冲突：即使目标座位恰好也被他人预约，也应提示“本人时段冲突”，
        # 而不是误导为“座位被占用、可加入候补”。
        user_conflict = self.repo.find_user_conflict(user.id, day, start_slot, end_slot)
        if user_conflict:
            raise Conflict(
                "您在当前时段已有预约，每人同时段仅限预约 1 个座位",
                rule="BR-11",
                code="USER_TIME_CONFLICT",
            )
        conflict = self.repo.find_seat_conflict(seat_id, day, start_slot, end_slot)
        if conflict:
            raise Conflict(
                f"座位 {seat_id} 在 {hhmm(start_slot)}-{hhmm(end_slot)} 已被占用",
                rule="BR-11",
                code="SEAT_OCCUPIED",
            )

        policy = self.params
        reservation = Reservation(
            id=self.repo.next_reservation_id(day),
            user_id=user.id,
            seat_id=seat_id,
            day=day,
            start_slot=start_slot,
            end_slot=end_slot,
            start_at=start_at,
            end_at=end_at,
            status=ReservationStatus.PENDING.value,
            source=ReservationSource.SELF.value,
            created_at=now,
            updated_at=now,
            checkin_deadline=checkin_window(
                start_at,
                policy.int("checkin.before_minutes"),
                policy.int("checkin.after_minutes"),
            )[1],
            policy=policy.snapshot(),
        )
        try:
            self.repo.insert_reservation(reservation)
            self.repo.occupy(reservation)
        except SlotConflict as exc:
            raise Conflict(str(exc), rule="BR-11") from exc

        opens, closes = checkin_window(
            start_at, policy.int("checkin.before_minutes"), policy.int("checkin.after_minutes")
        )
        self._notify(
            user.id,
            NotificationType.RESERVATION_CREATED.value,
            f"预约成功：{seat_id}（{room.name}）{fmt_date(day)} {hhmm(start_slot)}-{hhmm(end_slot)}，"
            f"请在 {fmt_datetime(opens)[11:]} 至 {fmt_datetime(closes)[11:]} 之间签到。",
            now,
        )
        data = self._view(reservation).as_dict()
        data["checkin_window"] = {
            "opens_at": fmt_datetime(opens),
            "closes_at": fmt_datetime(closes),
        }
        return Result.success(data, "预约创建成功")

    @implements("BR-13", "PR-03")
    def issue_credential(self, actor: Actor, reservation_id: str, now: datetime) -> Result:
        reservation = self._own_reservation(actor, reservation_id)
        if reservation.status != ReservationStatus.PENDING.value:
            raise RuleViolation("只有待签到的预约可以签发凭证", rule="ST-12")
        policy = ParamSet.from_snapshot(reservation.policy)
        ttl = policy.int("checkin.credential_ttl_seconds")

        # L-07：同一预约的未使用凭证在 TTL 内复用，避免并发请求互相覆盖，
        # 导致先返回的 code 在数据库中已失效。
        if (
            reservation.credential_code
            and not reservation.credential_used
            and reservation.credential_issued_at is not None
            and now <= reservation.credential_issued_at + timedelta(seconds=ttl)
        ):
            return Result.success(
                self._credential_payload(
                    reservation, reservation.credential_issued_at, ttl
                ),
                "凭证已签发",
            )

        code = _new_credential()
        expected_version = reservation.version
        reservation.credential_code = code
        reservation.credential_issued_at = now
        reservation.credential_used = False
        reservation.updated_at = now
        if not self.repo.update_credential(reservation, code, now, expected_version):
            # 另一请求已抢先签发：返回其已落库的凭证，而不是覆盖成已失效的 code。
            latest = self.repo.get_reservation(reservation.id)
            if (
                latest is not None
                and latest.status == ReservationStatus.PENDING.value
                and latest.credential_code
                and not latest.credential_used
            ):
                return Result.success(
                    self._credential_payload(
                        latest, latest.credential_issued_at or now, ttl
                    ),
                    "凭证已由其他请求签发",
                )
            raise Conflict("凭证已被其他操作更新，请刷新后重试", rule="BR-13")
        reservation.version = expected_version + 1
        return Result.success(
            self._credential_payload(reservation, now, ttl),
            "凭证已签发",
        )

    @staticmethod
    def _credential_payload(
        reservation: Reservation, issued_at: datetime, ttl: int
    ) -> dict[str, Any]:
        return {
            "reservation_id": reservation.id,
            "code": reservation.credential_code,
            "issued_at": fmt_datetime(issued_at),
            "expires_at": fmt_datetime(issued_at + timedelta(seconds=ttl)),
            "ttl_seconds": ttl,
        }

    @implements("BR-04", "BR-06", "BR-13", "ST-01", "ST-02", "ST-12", "PR-03")
    def check_in(
        self,
        actor: Actor,
        reservation_id: str,
        code: str | None,
        now: datetime,
        *,
        method: str = "qr",
    ) -> Result:
        reservation = self._own_reservation(actor, reservation_id)
        return self._check_in_core(reservation, now, method=method, provided_code=code)

    def check_in_by_credential(self, code: str, now: datetime, *, method: str = "acs") -> Result:
        """ACS/闸机通道：以一次性凭证定位预约；凭证即身份，不依赖登录会话。"""
        reservation = self.repo.find_reservation_by_credential(code)
        if reservation is None:
            raise RuleViolation("凭证无效或未签发", rule="BR-13")
        return self._check_in_core(reservation, now, method=method, provided_code=code)

    def _check_in_core(
        self,
        reservation: Reservation,
        now: datetime,
        *,
        method: str,
        provided_code: str | None,
    ) -> Result:
        if reservation.status != ReservationStatus.PENDING.value:
            raise RuleViolation(
                f"当前状态为 {reservation.status}，不能签到", rule="ST-12"
            )
        # M-06：普通签到、扫码签到、ACS 凭证签到共用同一账号复核。
        # ACS 不依赖登录会话，因此不能只校验凭证 TTL，必须在这里复核账号状态
        # 与 _booking_limit（黑名单/信用/连续违约）。
        user = self.repo.get_user(reservation.user_id)
        if user is None:
            raise NotFound("账号不存在")
        account_error = checkin_account_error(
            user.is_active, self._booking_limit(user, now)
        )
        if account_error:
            rule, message = account_error
            raise Forbidden(message, rule=rule)
        seat = self.repo.get_seat(reservation.seat_id)
        if seat is None:
            raise NotFound(f"座位 {reservation.seat_id} 不存在")
        room = self.repo.get_room(seat.room_id)
        if room is None:
            raise NotFound(f"自习室 {seat.room_id} 不存在")
        if seat.admin_status == "disabled" or room.status != "open":
            raise RuleViolation("座位或自习室已停用，无法签到", rule="BR-09")
        if seat.admin_status == "maintenance" or self.repo.seat_block_overlaps(
            seat.id, reservation.start_at, reservation.end_at
        ):
            raise RuleViolation("座位当前处于维护/封禁，无法签到", rule="BR-09")
        policy = ParamSet.from_snapshot(reservation.policy)
        if method != "gate":
            valid, message = credential_valid(
                reservation.credential_issued_at,
                reservation.credential_used,
                policy.int("checkin.credential_ttl_seconds"),
                now,
                reservation.credential_code,
                provided_code,
            )
            if not valid:
                raise RuleViolation(message, rule="BR-13")

        phase = checkin_phase(
            now,
            reservation.start_at,
            policy.int("checkin.before_minutes"),
            policy.int("checkin.after_minutes"),
        )
        if phase == "early":
            opens, _ = checkin_window(
                reservation.start_at,
                policy.int("checkin.before_minutes"),
                policy.int("checkin.after_minutes"),
            )
            raise RuleViolation(f"未到签到时间，窗口自 {fmt_datetime(opens)} 开始", rule="BR-04")

        if phase == "late":
            violation = self._record_violation(
                reservation,
                ViolationType.NO_SHOW.value,
                now,
                note="超过签到窗口未签到，系统判违约",
            )
            self._release_reservation(
                reservation,
                ReservationStatus.NO_SHOW.value,
                EndReason.NO_SHOW.value,
                now,
            )
            return Result.failure(
                "ST-02",
                "签到窗口已过，预约已判违约并释放座位",
                rule="ST-02",
                data={"violation_id": violation.id, "reservation": self._view(reservation).as_dict()},
            )

        busy = self.repo.find_user_active_reservation(user.id, exclude=reservation.id)
        if busy is not None:
            raise RuleViolation(
                f"本人已有使用中的座位 {busy.seat_id}，须先结束原占用", rule="BR-13"
            )

        reservation.status = ReservationStatus.IN_USE.value
        reservation.checked_in_at = now
        reservation.credential_used = True
        reservation.updated_at = now
        reservation.version += 1
        self.repo.update_reservation(reservation)

        if user.consecutive_no_show:
            user.consecutive_no_show = 0
            if user.limit_type == LimitType.CONSECUTIVE.value:
                user.limit_type = LimitType.NONE.value
                user.limit_until = None
            user.updated_at = now
            self.repo.save_user(user)

        return Result.success(self._view(reservation).as_dict(), "签到成功")

    @implements("BR-05", "ST-05", "PR-03")
    def start_away(self, actor: Actor, reservation_id: str, now: datetime) -> Result:
        reservation = self._own_reservation(actor, reservation_id)
        if reservation.status != ReservationStatus.IN_USE.value:
            raise RuleViolation("只有使用中的预约可以发起暂离", rule="ST-12")
        policy = ParamSet.from_snapshot(reservation.policy)
        deadline = add_minutes(now, policy.int("away.max_minutes"))
        if deadline > reservation.end_at:
            deadline = reservation.end_at
        reservation.status = ReservationStatus.AWAY.value
        reservation.away_at = now
        reservation.away_deadline = deadline
        reservation.updated_at = now
        reservation.version += 1
        self.repo.update_reservation(reservation)
        return Result.success(
            {**self._view(reservation).as_dict(), "away_deadline": fmt_datetime(deadline)},
            "已进入暂离，座位保留至 " + (fmt_datetime(deadline) or ""),
        )

    @implements("BR-05", "ST-06", "ST-07", "PR-03")
    def resume_use(self, actor: Actor, reservation_id: str, now: datetime) -> Result:
        reservation = self._own_reservation(actor, reservation_id)
        if reservation.status != ReservationStatus.AWAY.value:
            raise RuleViolation("只有暂离中的预约可以返座", rule="ST-12")
        if reservation.away_deadline is not None and now > reservation.away_deadline:
            self._record_violation(
                reservation,
                ViolationType.AWAY_TIMEOUT.value,
                now,
                note="暂离超过保留期未返回",
            )
            self._release_reservation(
                reservation,
                ReservationStatus.RELEASED.value,
                EndReason.AWAY_TIMEOUT.value,
                now,
            )
            return Result.failure(
                "ST-07", "已超过暂离保留期，座位已释放并记违约", rule="BR-05"
            )
        reservation.status = ReservationStatus.IN_USE.value
        reservation.away_at = None
        reservation.away_deadline = None
        reservation.updated_at = now
        reservation.version += 1
        self.repo.update_reservation(reservation)
        return Result.success(self._view(reservation).as_dict(), "已返座")

    @implements("BR-06", "ST-08", "ST-09", "PR-03")
    def check_out(self, actor: Actor, reservation_id: str, now: datetime) -> Result:
        reservation = self._own_reservation(actor, reservation_id)
        if reservation.status not in {
            ReservationStatus.IN_USE.value,
            ReservationStatus.AWAY.value,
        }:
            raise RuleViolation("当前状态不允许签退", rule="ST-12")
        policy = ParamSet.from_snapshot(reservation.policy)
        user = self.repo.get_user(reservation.user_id)
        if user is None:
            raise NotFound("账号不存在")

        late = now > reservation.end_at
        if late:
            self._record_violation(
                reservation,
                ViolationType.LATE_CHECKOUT.value,
                now,
                note="超过预约结束时间签退",
            )
        else:
            if reward_eligible(
                reservation.checked_in_at, reservation.start_at, now, reservation.end_at
            ):
                if self.repo.count_rewards(user.id, reservation.day) < policy.int(
                    "credit.reward.daily_cap"
                ):
                    self._credit_delta(
                        user,
                        policy.int("credit.reward.on_time"),
                        REWARD_REASON,
                        now,
                    )

        self._release_reservation(
            reservation,
            ReservationStatus.COMPLETED.value,
            EndReason.TIMEOUT_CHECKOUT.value if late else EndReason.NORMAL_CHECKOUT.value,
            now,
        )
        return Result.success(
            self._view(reservation).as_dict(),
            "已超时签退，记违约" if late else "签退成功",
        )

    @implements("BR-10", "ST-03", "ST-04", "PR-03")
    def cancel_reservation(self, actor: Actor, reservation_id: str, now: datetime) -> Result:
        reservation = self._own_reservation(actor, reservation_id)
        if reservation.status != ReservationStatus.PENDING.value:
            raise RuleViolation("只有待签到的预约可以自助取消", rule="ST-12")
        policy = ParamSet.from_snapshot(reservation.policy)
        phase = cancel_phase(now, reservation.start_at, policy.int("cancel.free_minutes"))
        if phase == "closed":
            raise RuleViolation("时段已开始，无法自助取消，请走申诉或联系管理员", rule="BR-10")
        if phase == "late":
            self._record_violation(
                reservation,
                ViolationType.LATE_CANCEL.value,
                now,
                note=f"距开始不足 {policy.int('cancel.free_minutes')} 分钟取消",
            )
        self._release_reservation(
            reservation,
            ReservationStatus.CANCELLED.value,
            EndReason.SELF_CANCEL.value,
            now,
        )
        message = "已取消，不记违规" if phase == "free" else "已取消，记临近取消违规"
        return Result.success(self._view(reservation).as_dict(), message)

    @implements("BR-01", "BR-02", "BR-09", "BR-11", "ST-12", "PR-03")
    def extend_reservation(
        self,
        actor: Actor,
        reservation_id: str,
        extra_minutes: int,
        now: datetime,
    ) -> Result:
        """延长使用中/暂离中的预约（v1.2 保守范围，不新增业务规则）。

        校验顺序与创建预约一致：本人/状态 → 时长粒度 → 座位与自习室可用性
        → BR-01 边界 → BR-02 当日额度 → BR-09 封禁 → BR-11 冲突。全部通过后
        在同一写事务内先占用新增时段，再按版本号更新预约；任一步失败整体回滚。
        """
        reservation = self._own_reservation(actor, reservation_id)

        state_error = extension_state_error(reservation.status, now, reservation.end_at)
        if state_error:
            rule, message = state_error
            raise RuleViolation(message, rule=rule)

        minutes_error = extension_minutes_error(extra_minutes)
        if minutes_error:
            raise ValidationFailed(minutes_error)

        # M-06：续约同样要复核账号状态与限制，不能只看预约状态/时段。
        user = self._user(actor)
        account_error = extension_account_error(
            user.is_active, self._booking_limit(user, now)
        )
        if account_error:
            rule, message = account_error
            raise Forbidden(message, rule=rule)

        seat = self.repo.get_seat(reservation.seat_id)
        if seat is None:
            raise NotFound(f"座位 {reservation.seat_id} 不存在")
        room = self.repo.get_room(seat.room_id)
        if room is None:
            raise NotFound(f"自习室 {seat.room_id} 不存在")
        if room.status != "open":
            raise RuleViolation("该自习室已停用，不能延长时段", rule="BR-09")

        new_end_slot = extended_end_slot(reservation.end_slot, extra_minutes)
        bounds_error = extension_bounds_error(
            reservation.start_slot,
            new_end_slot,
            room.close_slot,
            self.params.int("booking.max_minutes"),
        )
        if bounds_error:
            rule, message = bounds_error
            raise RuleViolation(message, rule=rule)

        _, used_minutes = self.repo.daily_usage(reservation.user_id, reservation.day)
        quota_error = extension_quota_error(
            used_minutes,
            extra_minutes,
            self.params.int("booking.daily_max_minutes"),
        )
        if quota_error:
            rule, message = quota_error
            raise RuleViolation(message, rule=rule)

        if seat.admin_status != "available":
            label = "维护中" if seat.admin_status == "maintenance" else "已停用"
            raise RuleViolation(
                f"座位 {seat.id} 当前{label}，不能延长时段", rule="BR-09"
            )

        new_end_at = add_minutes(reservation.end_at, extra_minutes)
        if self.repo.seat_block_overlaps(seat.id, reservation.end_at, new_end_at):
            raise RuleViolation(
                f"座位 {seat.id} 在延长时段处于维护封禁", rule="BR-09"
            )

        seat_conflict = self.repo.find_seat_conflict(
            seat.id, reservation.day, reservation.end_slot, new_end_slot
        )
        if seat_conflict:
            raise Conflict(
                f"座位 {seat.id} 在 {hhmm(reservation.end_slot)}-{hhmm(new_end_slot)} 已被占用",
                rule="BR-11",
            )
        user_conflict = self.repo.find_user_conflict(
            reservation.user_id, reservation.day, reservation.end_slot, new_end_slot
        )
        if user_conflict:
            raise Conflict("本人延长时段已有其他有效预约，不允许重叠", rule="BR-11")

        before = {
            "end_slot": reservation.end_slot,
            "end_at": fmt_datetime(reservation.end_at),
        }
        expected_version = reservation.version
        reservation.updated_at = now
        try:
            self.repo.occupy_extension(
                reservation, reservation.end_slot, new_end_slot
            )
            updated = self.repo.extend_reservation(
                reservation, new_end_slot, new_end_at, expected_version
            )
        except SlotConflict as exc:
            raise Conflict(str(exc), rule=exc.rule) from exc
        if not updated:
            raise Conflict("预约已被其他操作修改，请刷新后重试")

        reservation.end_slot = new_end_slot
        reservation.end_at = new_end_at
        reservation.version = expected_version + 1
        self._audit(
            actor,
            "reservation_extend",
            "reservation",
            reservation.id,
            before=before,
            after={
                "end_slot": new_end_slot,
                "end_at": fmt_datetime(new_end_at),
                "extra_minutes": extra_minutes,
            },
            now=now,
        )
        return Result.success(
            self._view(reservation).as_dict(),
            f"已延长 {extra_minutes} 分钟",
        )

    # ------------------------------------------------------------------
    # 候补
    # ------------------------------------------------------------------
    @implements("BR-08", "PR-04")
    def join_waitlist(
        self,
        actor: Actor,
        seat_id: str,
        day: date,
        start: time,
        end: time,
        now: datetime,
    ) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-04")
        user = self._user(actor)
        limit = self._booking_limit(user, now)
        if limit is not None:
            rule, message = limit
            raise RuleViolation(message, rule=rule)
        if not in_booking_window(day, now.date(), self.params.int("booking.window_days")):
            raise RuleViolation("目标日期超出可预约窗口", rule="BR-03")
        try:
            start_slot, end_slot = slot_of_time(start), slot_of_time(end)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        ok, message = validate_slot_range(
            start_slot,
            end_slot,
            self.params.int("booking.min_minutes"),
            self.params.int("booking.max_minutes"),
        )
        if not ok:
            raise RuleViolation(message, rule="BR-01")
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        room = self.repo.get_room(seat.room_id)
        if room is None:
            raise NotFound(f"自习室 {seat.room_id} 不存在")
        start_at, end_at = datetime_at(day, start_slot), datetime_at(day, end_slot)
        blocked = self.repo.seat_block_overlaps(seat.id, start_at, end_at)
        target_error = waitlist_target_error(
            room.status,
            seat.admin_status,
            blocked,
            start_slot,
            end_slot,
            room.open_slot,
            room.close_slot,
            start_at,
            now,
        )
        if target_error:
            rule, target_message = target_error
            code = None
            if room.status != "open":
                code = "ROOM_UNAVAILABLE"
            elif seat.admin_status != "available":
                code = (
                    "SEAT_DISABLED"
                    if seat.admin_status == "disabled"
                    else "SEAT_UNAVAILABLE"
                )
            elif blocked:
                code = "SEAT_UNAVAILABLE"
            raise RuleViolation(target_message, rule=rule, code=code)
        if self.repo.find_seat_conflict(seat_id, day, start_slot, end_slot) is None:
            raise RuleViolation("该时段座位仍可预约，无需候补", rule="BR-08")
        if self.repo.find_user_conflict(user.id, day, start_slot, end_slot):
            raise Conflict(
                "您在当前时段已有预约，每人同时段仅限预约 1 个座位",
                rule="BR-11",
                code="USER_TIME_CONFLICT",
            )
        count, used_minutes = self.repo.daily_usage(user.id, day)
        quota_error = quota_exceeded(
            count,
            used_minutes,
            minutes_of_slots(start_slot, end_slot),
            self.params.int("booking.daily_max_count"),
            self.params.int("booking.daily_max_minutes"),
        )
        if quota_error:
            raise RuleViolation(quota_error, rule="BR-02")
        existing = self.repo.find_waitlist(user.id, seat_id, day, start_slot, end_slot)
        if existing is not None and existing.status in {
            WaitlistStatus.QUEUED.value,
            WaitlistStatus.NOTIFIED.value,
        }:
            raise Conflict("已在该座位候补队列中", rule="BR-08")

        entry = WaitlistEntry(
            id=self.repo.next_id("waitlist", "W-", 7),
            user_id=user.id,
            seat_id=seat_id,
            day=day,
            start_slot=start_slot,
            end_slot=end_slot,
            status=WaitlistStatus.QUEUED.value,
            seq=self.repo.count_waitlist_queue(seat_id, day, start_slot, end_slot) + 1,
            created_at=now,
        )
        self.repo.insert_waitlist(entry)
        return Result.success(entry.as_dict(), "已加入候补队列")

    @implements("BR-08", "PR-04")
    def confirm_waitlist(self, actor: Actor, entry_id: str, now: datetime) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-04")
        entry = self.repo.get_waitlist(entry_id)
        if entry is None:
            raise NotFound("候补记录不存在")
        if entry.user_id != actor.id:
            raise Forbidden("只能确认本人的候补", rule="PR-04")
        state_error = waitlist_confirm_state_error(entry.status)
        if state_error:
            rule, message = state_error
            raise RuleViolation(message, rule=rule)
        if entry.confirm_deadline is not None and now > entry.confirm_deadline:
            entry.defer_count += 1
            self._expire_waitlist(entry, now)
            return Result.failure("BR-08", "候补确认已超时，已顺延至下一位", rule="BR-08")

        start_at, end_at = datetime_at(entry.day, entry.start_slot), datetime_at(entry.day, entry.end_slot)
        if start_at <= now:
            # 时段已开始，候补确认后也无法使用；直接失效，不再顺延。
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure("BR-08", "该时段已开始，候补已失效", rule="BR-08")
        seat = self.repo.get_seat(entry.seat_id)
        if seat is None:
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure("BR-08", "座位不存在，候补已失效", rule="BR-08")
        room = self.repo.get_room(seat.room_id)
        if room is None:
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure("FR-21", "自习室不存在，候补已失效", rule="FR-21")
        # M-07：确认前重新读取最新房间/座位/封禁/开放时间，配置变更后不能
        # 生成越界预约；失败时先置 expired 再返回 Result.failure，保证
        # 状态与 reservation/slot 的回滚口径一致。
        target_error = waitlist_target_error(
            room.status,
            seat.admin_status,
            self.repo.seat_block_overlaps(seat.id, start_at, end_at),
            entry.start_slot,
            entry.end_slot,
            room.open_slot,
            room.close_slot,
            start_at,
            now,
        )
        if target_error:
            rule, message = target_error
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure(rule, f"{message}，候补已失效", rule=rule)

        user = self._user(actor)
        limit = self._booking_limit(user, now)
        if limit is not None:
            self._expire_waitlist(entry, now)
            return Result.failure(limit[0], limit[1], rule=limit[0])

        if self.repo.find_user_conflict(user.id, entry.day, entry.start_slot, entry.end_slot):
            self._expire_waitlist(entry, now)
            return Result.failure(
                "BR-11", "本人该时段已有其他有效预约，候补已失效", rule="BR-11"
            )

        conflict = self.repo.find_seat_conflict(
            entry.seat_id, entry.day, entry.start_slot, entry.end_slot
        )
        if conflict:
            entry.defer_count += 1
            self._expire_waitlist(entry, now)
            return Result.failure("BR-11", "座位已被他人占用，已顺延至下一位", rule="BR-11")

        count, used_minutes = self.repo.daily_usage(user.id, entry.day)
        minutes = minutes_of_slots(entry.start_slot, entry.end_slot)
        quota_error = quota_exceeded(
            count,
            used_minutes,
            minutes,
            self.params.int("booking.daily_max_count"),
            self.params.int("booking.daily_max_minutes"),
        )
        if quota_error:
            self._expire_waitlist(entry, now)
            return Result.failure("BR-02", quota_error, rule="BR-02")

        start_at, end_at = datetime_at(entry.day, entry.start_slot), datetime_at(entry.day, entry.end_slot)
        reservation = Reservation(
            id=self.repo.next_reservation_id(entry.day),
            user_id=user.id,
            seat_id=entry.seat_id,
            day=entry.day,
            start_slot=entry.start_slot,
            end_slot=entry.end_slot,
            start_at=start_at,
            end_at=end_at,
            status=ReservationStatus.PENDING.value,
            source=ReservationSource.WAITLIST.value,
            created_at=now,
            updated_at=now,
            checkin_deadline=checkin_window(
                start_at,
                self.params.int("checkin.before_minutes"),
                self.params.int("checkin.after_minutes"),
            )[1],
            policy=self.params.snapshot(),
        )
        try:
            self.repo.insert_reservation(reservation)
            self.repo.occupy(reservation)
        except SlotConflict as exc:
            # L-07：reservation 行已插入而 slot 占用失败，不能带着半截数据
            # 返回 failure（外层写事务会正常提交）。显式清理本次插入的行，
            # 再把候补置为 expired 并在同一事务内提交。
            self.repo.delete_reservation(reservation.id)
            entry.defer_count += 1
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure("BR-11", str(exc), rule="BR-11")

        entry.status = WaitlistStatus.CONFIRMED.value
        self.repo.update_waitlist(entry)
        self._notify(
            user.id,
            NotificationType.RESERVATION_CREATED.value,
            f"候补补位成功：{entry.seat_id} {fmt_date(entry.day)} "
            f"{hhmm(entry.start_slot)}-{hhmm(entry.end_slot)}。",
            now,
        )
        return Result.success(self._view(reservation).as_dict(), "候补确认成功，已生成预约")

    @implements("ST-12", "PR-04")
    def cancel_waitlist(self, actor: Actor, entry_id: str, now: datetime) -> Result:
        """学生取消本人候补。

        v1.2 口径（Q-05 确认前）：只把状态改为 ``cancelled``，不调用
        ``_offer_waitlist`` 顺延/通知下一位；不记违规、不扣信用分。
        """
        self._require(actor, {Role.STUDENT}, "PR-04")
        entry = self.repo.get_waitlist(entry_id)
        if entry is None:
            raise NotFound("候补记录不存在")
        if entry.user_id != actor.id:
            raise Forbidden("只能取消本人的候补", rule="PR-04")

        state_error = waitlist_cancel_state_error(entry.status)
        if state_error:
            rule, message = state_error
            raise RuleViolation(message, rule=rule)

        entry.status = WaitlistStatus.CANCELLED.value
        self.repo.update_waitlist(entry)
        return Result.success({"entry": entry.as_dict()}, "候补已取消")

    # ------------------------------------------------------------------
    # 定时任务
    # ------------------------------------------------------------------
    @implements("BR-04", "BR-05", "BR-06", "BR-08", "BR-09", "BR-16", "NFR-02", "ST-02", "ST-07", "ST-09")
    def run_timeout_sweep(self, now: datetime) -> dict[str, int]:
        summary = {
            "no_show": 0,
            "away_timeout": 0,
            "late_checkout": 0,
            "waitlist_expired": 0,
            "seat_blocks_expired": 0,
            "resource_unavailable": 0,
            "reminders": 0,
        }
        # M-04：先处理“资源已不可用”的预约，再判 no_show。房间关闭/座位停用
        # 后用户即使想签到也无法履约，不能按未签到扣信用；统一 cancelled +
        # resource_unavailable，重复扫描不再处理。
        for reservation in self.repo.list_active_reservations():
            if reservation.status not in OCCUPYING_STATUSES:
                continue
            resource_error = self._resource_unavailable_error(reservation)
            if resource_error is None:
                continue
            if self._cancel_resource_unavailable(
                reservation, resource_error[1], now
            ):
                summary["resource_unavailable"] += 1

        for reservation in self.repo.list_due_pending(now):
            if reservation.status != ReservationStatus.PENDING.value:
                continue
            # 防御性复核：同一事务内资源状态被并发改写时，仍不得误判 no_show。
            if self._resource_unavailable_error(reservation) is not None:
                continue
            self._record_violation(
                reservation,
                ViolationType.NO_SHOW.value,
                now,
                note="定时任务：超过签到窗口未签到",
            )
            self._release_reservation(
                reservation,
                ReservationStatus.NO_SHOW.value,
                EndReason.NO_SHOW.value,
                now,
            )
            summary["no_show"] += 1

        for reservation in self.repo.list_due_away(now):
            if reservation.status != ReservationStatus.AWAY.value:
                continue
            self._record_violation(
                reservation,
                ViolationType.AWAY_TIMEOUT.value,
                now,
                note="定时任务：暂离超过保留期",
            )
            self._release_reservation(
                reservation,
                ReservationStatus.RELEASED.value,
                EndReason.AWAY_TIMEOUT.value,
                now,
            )
            summary["away_timeout"] += 1

        for reservation in self.repo.list_due_in_use(now):
            if reservation.status not in {
                ReservationStatus.IN_USE.value,
                ReservationStatus.AWAY.value,
            }:
                continue
            self._record_violation(
                reservation,
                ViolationType.LATE_CHECKOUT.value,
                now,
                note="定时任务：超过预约结束时间未签退",
            )
            self._release_reservation(
                reservation,
                ReservationStatus.COMPLETED.value,
                EndReason.TIMEOUT_CHECKOUT.value,
                now,
            )
            summary["late_checkout"] += 1

        for entry in self.repo.due_waitlist(now):
            if entry.status != WaitlistStatus.NOTIFIED.value:
                continue
            entry.status = WaitlistStatus.EXPIRED.value
            entry.defer_count += 1
            self.repo.update_waitlist(entry)
            self._offer_waitlist(entry.seat_id, entry.day, entry.start_slot, entry.end_slot, now)
            summary["waitlist_expired"] += 1

        # 封禁到期：关闭记录，并且只在“维护状态由封禁流程设置”时才恢复座位。
        # 手工维护、停用（disabled）座位一律保持原状态，避免误恢复。
        expired_blocks = self.repo.expire_seat_blocks(now)
        restored_seats: set[str] = set()
        for block in expired_blocks:
            if block.seat_id in restored_seats:
                continue
            restored_seats.add(block.seat_id)
            seat = self.repo.get_seat(block.seat_id)
            if seat is None:  # pragma: no cover - 外键保证存在
                continue
            remaining = self.repo.active_seat_blocks(seat.id)
            target_status, target_marker = resource_rules.seat_state_after_blocks_closed(
                seat, remaining
            )
            if (target_status, target_marker) == (
                seat.admin_status,
                seat.maintenance_by_block,
            ):
                continue
            before = {
                "seat_status": seat.admin_status,
                "maintenance_by_block": seat.maintenance_by_block,
            }
            seat.admin_status, seat.maintenance_by_block = target_status, target_marker
            self.repo.save_seat(seat)
            self._audit(
                None,
                "seat_block_expired",
                "seat",
                seat.id,
                before=before,
                after={
                    "seat_status": seat.admin_status,
                    "maintenance_by_block": seat.maintenance_by_block,
                    "expired_blocks": [
                        item.id for item in expired_blocks if item.seat_id == seat.id
                    ],
                },
                now=now,
            )
        summary["seat_blocks_expired"] = len(expired_blocks)

        summary["reminders"] = self.send_reminders(now)
        return summary

    @implements("BR-16", "FR-36")
    def send_reminders(self, now: datetime) -> int:
        count = 0
        for reservation in self.repo.list_due_reminders(
            now, self.params.int("reminder.before_minutes")
        ):
            if reservation.status != ReservationStatus.PENDING.value:
                continue
            key = f"reminder:{reservation.id}"
            if self.repo.has_notification(reservation.user_id, key):
                continue
            self._notify(
                reservation.user_id,
                NotificationType.CHECKIN_REMINDER.value,
                f"提醒：您预约的 {reservation.seat_id} 将于 {reservation.start_time} 开始，请及时签到。",
                now,
                dedupe_key=key,
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # 管理操作
    # ------------------------------------------------------------------
    @implements("BR-09", "PR-06", "ST-11")
    def block_seat(
        self,
        actor: Actor,
        seat_id: str,
        reason: str,
        block_start: datetime,
        block_end: datetime,
        now: datetime,
        *,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-06")
        if len(reason.strip()) < 2:
            raise ValidationFailed("封禁必须填写原因", rule="BR-09")
        if block_end <= block_start:
            raise ValidationFailed("封禁影响时段不合法", rule="BR-09")
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        before = {
            "seat_status": seat.admin_status,
            "maintenance_by_block": seat.maintenance_by_block,
        }
        block = SeatBlock(
            id=self.repo.next_id("seatblock", "SB-", 6),
            seat_id=seat_id,
            reason=reason.strip(),
            start_at=block_start,
            end_at=block_end,
            created_by=actor.id,
            created_at=now,
        )
        self.repo.insert_seat_block(block)
        # 状态迁移交给纯规则：手工维护/停用不改状态，未来封禁不立即生效，
        # 只有“封禁流程管理的维护中座位”才会在到期/释放时自动恢复。
        seat.admin_status, seat.maintenance_by_block = resource_rules.seat_state_after_block(
            seat, block, now
        )
        self.repo.save_seat(seat)

        affected: list[str] = []
        for reservation in self.repo.list_active_overlapping(seat_id, block_start, block_end):
            self._release_reservation(
                reservation,
                ReservationStatus.CANCELLED.value,
                EndReason.SEAT_BLOCKED.value,
                now,
            )
            self._notify(
                reservation.user_id,
                NotificationType.SEAT_BLOCKED.value,
                f"座位 {seat_id} 因“{reason.strip()}”封禁，预约已取消，不记违规、不扣信用分。",
                now,
            )
            affected.append(reservation.id)

        self._audit(
            actor,
            "seat_block",
            "seat",
            seat_id,
            before=before,
            after={
                "seat_status": seat.admin_status,
                "maintenance_by_block": seat.maintenance_by_block,
                "block_id": block.id,
                "block_start": fmt_datetime(block_start),
                "block_end": fmt_datetime(block_end),
                "affected": affected,
            },
            now=now,
            ip=ip,
        )
        return Result.success(
            {
                "seat_id": seat_id,
                "block": block.as_dict(),
                "seat_status": seat.admin_status,
                "maintenance_by_block": seat.maintenance_by_block,
                "affected_reservations": affected,
            },
            f"已封禁，取消 {len(affected)} 条受影响预约",
        )

    @implements("PR-06")
    def unblock_seat(self, actor: Actor, seat_id: str, now: datetime, *, ip: str = "") -> Result:
        """解封整个座位：关闭该座位全部未释放封禁（既有 DELETE 接口的行为）。

        只有“由封禁流程管理的维护中座位”才恢复 ``available``；手工维护保持维护、
        停用（``disabled``）座位保持停用，必须走座位管理显式启用。
        """
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-06")
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        blocks = [b for b in self.repo.active_seat_blocks(seat_id)]
        if not blocks:
            raise RuleViolation("该座位当前没有生效中的封禁", rule="ST-12")
        for block in blocks:
            self.repo.close_seat_block(block.id, now)
        before = {
            "seat_status": seat.admin_status,
            "maintenance_by_block": seat.maintenance_by_block,
        }
        seat.admin_status, seat.maintenance_by_block = (
            resource_rules.seat_state_after_blocks_closed(seat, [])
        )
        restored = (
            seat.admin_status == "available"
            and before["seat_status"] != "available"
        )
        self.repo.save_seat(seat)
        after = {
            "seat_status": seat.admin_status,
            "maintenance_by_block": seat.maintenance_by_block,
            "released_blocks": [block.id for block in blocks],
        }
        self._audit(
            actor,
            "seat_unblock",
            "seat",
            seat_id,
            before=before,
            after=after,
            now=now,
            ip=ip,
        )
        message = (
            "已解封，座位恢复可预约"
            if restored
            else f"已解封，座位保持{resource_rules.seat_status_label(seat.admin_status)}（未自动恢复）"
        )
        return Result.success(
            {
                "seat_id": seat_id,
                "seat_status": seat.admin_status,
                "maintenance_by_block": seat.maintenance_by_block,
                "restored": restored,
                "released_blocks": [block.id for block in blocks],
            },
            message,
        )

    # ------------------------------------------------------------------
    # 座位封禁生命周期（v2 / FR-21，PR-06，ST-11）
    # ------------------------------------------------------------------
    @implements("FR-21", "PR-06")
    def list_seat_blocks(
        self,
        actor: Actor,
        *,
        seat_id: str | None = None,
        room_id: str | None = None,
        active_only: bool = False,
        page: int = 1,
        page_size: int = 20,
        now: datetime | None = None,
    ) -> Result:
        """封禁记录列表（含座位、自习室、原因、起止、是否此刻生效）。

        未来封禁期间 ``admin_status`` 可能仍是 ``available``，因此管理端必须
        同时展示封禁记录与 ``seat_views`` 的派生状态，不能只看 ``admin_status``。
        """
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-06")
        current = now or datetime.now()
        result = self.repo.list_seat_blocks(
            seat_id=seat_id,
            room_id=room_id,
            active_only=active_only,
            page=page,
            page_size=page_size,
        )
        return Result.success(
            {
                "items": result.items,
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "generated_at": fmt_datetime(current),
            }
        )

    @implements("PR-06", "ST-11", "ST-12")
    def release_seat_block(
        self,
        actor: Actor,
        block_id: str,
        reason: str,
        now: datetime,
        *,
        restore_seat: bool = False,
        ip: str = "",
    ) -> Result:
        """按记录解封：只关闭指定封禁；``restore_seat`` 才会显式恢复座位。

        默认 ``restore_seat=False``（不猜管理员意图，不动座位状态）。
        ``disabled`` 座位即便传 ``True`` 也不会恢复，必须走 ``PUT /admin/seats/{id}``。
        """
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-06")
        if len(reason.strip()) < 2:
            raise ValidationFailed("按记录解封必须填写原因", rule="PR-06")
        block = self.repo.get_seat_block(block_id)
        if block is None:
            raise NotFound(f"封禁记录 {block_id} 不存在")
        if block.released_at is not None:
            raise RuleViolation("该封禁记录已释放，不能重复解封", rule="ST-12")
        seat = self.repo.get_seat(block.seat_id)
        if seat is None:
            raise NotFound(f"座位 {block.seat_id} 不存在")
        before = {
            "seat_status": seat.admin_status,
            "maintenance_by_block": seat.maintenance_by_block,
            "block_released_at": None,
        }
        # 停用座位不能借“解封恢复”变成可预约：先拒绝，避免出现半个副作用。
        if restore_seat and seat.admin_status == "disabled":
            raise RuleViolation(
                "停用座位不能通过解封恢复，请到座位管理显式启用", rule="PR-06"
            )
        self.repo.release_seat_block(block_id, now)
        remaining = [item for item in self.repo.active_seat_blocks(seat.id)]
        restorable = resource_rules.can_restore_seat(seat, remaining)
        restored = False
        if restore_seat:
            if restorable:
                seat.admin_status = "available"
                seat.maintenance_by_block = False
                self.repo.save_seat(seat)
                restored = True
        elif not remaining:
            # 默认只关闭记录、不改座位状态：仅把“封禁来源”标记归零，
            # 需要恢复可用必须显式传 restore_seat=true 或走座位管理。
            seat.maintenance_by_block = False
            self.repo.save_seat(seat)
        self._audit(
            actor,
            "seat_block_release",
            "seat",
            seat.id,
            before=before,
            after={
                "block_id": block_id,
                "reason": reason.strip(),
                "seat_status": seat.admin_status,
                "maintenance_by_block": seat.maintenance_by_block,
                "restored": restored,
                "remaining_blocks": [item.id for item in remaining],
            },
            now=now,
            ip=ip,
        )
        block.released_at = now
        return Result.success(
            {
                "block": block.as_dict(),
                "seat_id": seat.id,
                "seat_status": seat.admin_status,
                "maintenance_by_block": seat.maintenance_by_block,
                "restored": restored,
                "remaining_blocks": len(remaining),
            },
            "已按记录解封" + ("，座位恢复可预约" if restored else ""),
        )

    @implements("FR-21", "PR-05")
    def bulk_create_seats(
        self,
        actor: Actor,
        room_id: str,
        seats: Iterable[dict[str, Any]],
        now: datetime,
        *,
        ip: str = "",
    ) -> Result:
        """批量建座：全量成功或全量回滚（任一条非法则整批不落库）。"""
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        items = list(seats)
        limit_error = resource_rules.bulk_limit_violation(len(items))
        if limit_error:
            raise ValidationFailed(limit_error, rule="FR-21")
        if self.repo.get_room(room_id) is None:
            raise NotFound(f"自习室 {room_id} 不存在")
        raw_ids = [str(item.get("id", "")).strip() for item in items]
        if any(not seat_id for seat_id in raw_ids):
            raise ValidationFailed("座位编号不能为空")
        repeated = resource_rules.duplicate_ids(raw_ids)
        if repeated:
            raise Conflict(f"批量提交中座位编号重复：{'、'.join(repeated)}", rule="PR-05")
        # 座位编号是全局主键：必须检查所有自习室，否则 save_seat 的 UPSERT 会把
        # 其他房间的同号座位静默“搬”过来。
        existing_ids = {seat.id for seat in self.repo.list_seats()}
        already = [seat_id for seat_id in raw_ids if seat_id in existing_ids]
        if already:
            raise Conflict(f"座位已存在：{'、'.join(already)}", rule="PR-05")

        created: list[Seat] = []
        for item in items:
            seat_type = str(item.get("type") or SeatType.STANDARD.value)
            capacity = int(item.get("capacity") or 1)
            self._validate_seat_shape(seat_type, capacity)
            seat_id = str(item.get("id", "")).strip()
            seat = Seat(
                id=seat_id,
                room_id=room_id,
                seat_type=seat_type,
                capacity=capacity,
                has_power=bool(item.get("has_power", False)),
                is_window=bool(item.get("is_window", False)),
                note=str(item.get("note") or "").strip(),
                created_at=now,
            )
            self.repo.save_seat(seat)
            created.append(seat)

        payload = []
        for seat in created:
            data = seat.as_dict()
            data["admin_status"] = seat.admin_status
            payload.append(data)
        self._audit(
            actor,
            "seat_bulk_create",
            "room",
            room_id,
            before=None,
            after={"room_id": room_id, "created": [seat.id for seat in created]},
            now=now,
            ip=ip,
        )
        return Result.success(
            {"room_id": room_id, "created": payload, "count": len(payload)},
            f"已批量新增 {len(payload)} 个座位",
        )

    @implements("FR-21", "PR-05")
    def room_impact(
        self, actor: Actor, room_id: str, status: str | None = None
    ) -> Result:
        """停用/关闭自习室前的影响预览（只读，不修改任何数据）。

        统计口径：该自习室下 ``pending/in_use/away`` 的预约（会被停用影响的对象）；
        ``status`` 只用于回显管理员打算切换到的目标状态，不参与统计。
        """
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        room = self.repo.get_room(room_id)
        if room is None:
            raise NotFound(f"自习室 {room_id} 不存在")
        target = status or "closed"
        if target not in {"open", "closed"}:
            raise ValidationFailed("自习室状态只能是 open 或 closed")
        items: list[dict[str, Any]] = []
        summary = {"pending": 0, "in_use": 0, "away": 0}
        for state in summary:
            page = self.repo.list_reservations(
                room_id=room_id, status=state, page_size=20
            )
            summary[state] = page.total
            items.extend(view.as_dict() for view in page.items)
        items.sort(key=lambda item: (item.get("date") or "", item.get("start") or ""))
        return Result.success(
            {
                "room_id": room_id,
                "room_name": room.name,
                "current_status": room.status,
                "target_status": target,
                "affected_count": sum(summary.values()),
                "by_status": summary,
                "items": items[:20],
                "total": sum(summary.values()),
                "page": 1,
                "page_size": 20,
            }
        )

    @implements("PR-07", "ST-10", "NFR-09")
    def force_release(
        self, actor: Actor, reservation_id: str, reason: str, now: datetime, *, ip: str = ""
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-07")
        if len(reason.strip()) < 2:
            raise ValidationFailed("强制释放必须填写原因", rule="PR-07")
        reservation = self._reservation(reservation_id)
        if reservation.status not in OCCUPYING_STATUSES:
            raise RuleViolation("该预约已结束，无需释放", rule="ST-12")
        before = {"status": reservation.status}
        self._release_reservation(
            reservation,
            ReservationStatus.RELEASED.value,
            EndReason.ADMIN_RELEASE.value,
            now,
        )
        self._notify(
            reservation.user_id,
            NotificationType.ADMIN_RELEASED.value,
            f"管理员已释放您的预约 {reservation.id}，原因：{reason.strip()}。",
            now,
        )
        self._audit(
            actor,
            "force_release",
            "reservation",
            reservation.id,
            before=before,
            after={"status": "released", "reason": reason.strip()},
            now=now,
            ip=ip,
        )
        return Result.success(self._view(reservation).as_dict(), "已强制释放")

    @implements("BR-18", "PR-17", "ST-10", "ST-12")
    def manual_violation(
        self,
        actor: Actor,
        reservation_id: str,
        mode: str,
        note: str,
        now: datetime,
        *,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-17")
        if mode not in {"warn", "release"}:
            raise ValidationFailed("处置方式只能是 warn 或 release")
        if len(note.strip()) < 4:
            raise ValidationFailed("必须填写现场核实说明")
        reservation = self._reservation(reservation_id)
        if reservation.status not in OCCUPYING_STATUSES:
            raise RuleViolation("该预约已结束，无法登记现场违约", rule="ST-12")
        # M-05：同一预约的同类现场违约只允许登记一次，避免重复扣信用
        existing = self.repo.list_violations_of_reservation(
            reservation_id, violation_type=ViolationType.MANUAL_INSPECTION.value
        )
        if resource_rules.is_duplicate_manual_violation(
            existing, reservation_id, ViolationType.MANUAL_INSPECTION.value
        ):
            self.audit_event(
                actor,
                "manual_violation_duplicate",
                "reservation",
                reservation_id,
                result="denied",
                details={
                    "mode": mode,
                    "violation_ids": [item.id for item in existing],
                },
                now=now,
                ip=ip,
            )
            # 用 Result.failure 而不是抛异常：需要把“重复被拒绝”的审计落库
            # （抛 AppError 会让外层写事务回滚，拒绝记录随之丢失）。
            return Result.failure(
                "DUPLICATE_VIOLATION",
                "该预约已登记现场违约，不能重复登记；如需追加处置请走违规申诉复核",
                rule="BR-18",
            )
        violation = self._record_violation(
            reservation,
            ViolationType.MANUAL_INSPECTION.value,
            now,
            source=ViolationSource.MANUAL.value,
            note=note.strip(),
            operator=actor,
        )
        if mode == "release":
            self._release_reservation(
                reservation,
                ReservationStatus.RELEASED.value,
                EndReason.ADMIN_RELEASE.value,
                now,
            )
        self._audit(
            actor,
            "manual_violation",
            "reservation",
            reservation.id,
            before={"status": reservation.status},
            after={
                "mode": mode,
                "violation_id": violation.id,
                "status": reservation.status,
            },
            now=now,
            ip=ip,
        )
        message = "已登记违约并强制释放" if mode == "release" else "已登记违约（座位保持）"
        return Result.success(
            {"violation": violation.as_dict(), "reservation": self._view(reservation).as_dict()},
            message,
        )

    @implements("BR-17", "PR-09")
    def submit_appeal(
        self, actor: Actor, violation_id: str, reason: str, now: datetime
    ) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-09")
        if len(reason.strip()) < 4:
            raise ValidationFailed("请填写申诉理由")
        violation = self.repo.get_violation(violation_id)
        if violation is None:
            raise NotFound("违规记录不存在")
        if violation.user_id != actor.id:
            raise Forbidden("只能申诉本人的违规记录", rule="PR-09")
        if violation.status != ViolationStatus.ACTIVE.value:
            raise RuleViolation("该违规已申诉或已撤销", rule="ST-12")
        occurred = violation.occurred_at or now
        if now - occurred > timedelta(days=self.params.int("appeal.window_days")):
            raise RuleViolation(
                f"超过 {self.params.int('appeal.window_days')} 日申诉时限", rule="BR-17"
            )
        violation.status = ViolationStatus.APPEALING.value
        violation.appeal_reason = reason.strip()
        violation.appeal_at = now
        self.repo.update_violation(violation)
        self._audit(
            actor,
            "appeal_submit",
            "violation",
            violation.id,
            before={"status": "active"},
            after={"status": "appealing"},
            now=now,
        )
        return Result.success(violation.as_dict(), "申诉已提交，等待管理员审核")

    @implements("BR-17", "PR-10")
    def review_appeal(
        self,
        actor: Actor,
        violation_id: str,
        decision: str,
        note: str,
        now: datetime,
        *,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-10")
        if decision not in {"approve", "reject"}:
            raise ValidationFailed("审核结论只能是 approve 或 reject")
        if len(note.strip()) < 2:
            raise ValidationFailed("审核必须填写说明")
        violation = self.repo.get_violation(violation_id)
        if violation is None:
            raise NotFound("违规记录不存在")
        if violation.status != ViolationStatus.APPEALING.value:
            raise RuleViolation("该违规不处于申诉中", rule="ST-12")
        before = {"status": violation.status, "credit": None}
        user = self.repo.get_user(violation.user_id)
        if decision == "approve":
            violation.status = ViolationStatus.REVOKED.value
            violation.review_note = note.strip()
            violation.reviewed_by = actor.id
            violation.reviewed_at = now
            self.repo.update_violation(violation)
            if user is not None:
                before["credit"] = user.credit
                # 按实际扣分回退：信用分触底时可能只扣了部分分数甚至 0 分，
                # 直接恢复 violation.points 会造成信用分通胀。
                log_delta = self.repo.credit_delta_for_violation(violation.id)
                if log_delta is None:
                    # 旧数据（历史导入/演示种子）没有关联流水，按违规分值回退。
                    restored = violation.points
                else:
                    restored = -log_delta if log_delta < 0 else 0
                if restored:
                    self._credit_delta(
                        user,
                        restored,
                        f"申诉成立，撤销违规 {violation.id}",
                        now,
                        violation_id=violation.id,
                        operator_id=actor.id,
                    )
                if (
                    violation.violation_type == ViolationType.NO_SHOW.value
                    and user.consecutive_no_show > 0
                ):
                    user.consecutive_no_show -= 1
                    user.updated_at = now
                    self.repo.save_user(user)
                self._apply_blacklist(user, now)
                self._refresh_limit(user, now)
                self._release_auto_blacklist_if_eligible(actor, user, now, ip=ip)
                self._notify(
                    user.id,
                    NotificationType.APPEAL_RESULT.value,
                    f"申诉成立：违规 {violation.id} 已撤销，信用分恢复 {restored} 分。",
                    now,
                )
            message = "申诉成立，已撤销违规并恢复信用分"
        else:
            violation.status = ViolationStatus.ACTIVE.value
            violation.review_note = note.strip()
            violation.reviewed_by = actor.id
            violation.reviewed_at = now
            self.repo.update_violation(violation)
            if user is not None:
                before["credit"] = user.credit
                self._notify(
                    user.id,
                    NotificationType.APPEAL_RESULT.value,
                    f"申诉未通过：{note.strip()}",
                    now,
                )
            message = "申诉不成立，维持原判"
        self._audit(
            actor,
            "appeal_review",
            "violation",
            violation.id,
            before=before,
            after={
                "status": violation.status,
                "decision": decision,
                "note": note.strip(),
                "credit": user.credit if user is not None else None,
            },
            now=now,
            ip=ip,
        )
        return Result.success(violation.as_dict(), message)

    # ------------------------------------------------------------------
    # 参数与角色
    # ------------------------------------------------------------------
    @implements("BR-14", "PR-13")
    def set_param(
        self, actor: Actor, code: str, value: str, now: datetime, *, ip: str = ""
    ) -> Result:
        self._require(actor, {Role.SYS_ADMIN}, "PR-13")
        if code not in PARAM_INDEX:
            raise NotFound(f"参数 {code} 不存在")
        try:
            normalised = validate_value(code, value)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        current = self.params.snapshot()
        before_value = current.get(code)
        candidate = dict(current)
        candidate[code] = normalised
        problems = check_consistency(candidate)
        if problems:
            raise ValidationFailed("；".join(problems))
        self.repo.save_params({code: normalised}, actor.id, now)
        self.params = self.repo.load_params()
        self._audit(
            actor,
            "param_change",
            "param",
            code,
            before={code: before_value},
            after={code: normalised},
            now=now,
            ip=ip,
        )
        return Result.success(
            {"code": code, "value": normalised, "label": PARAM_INDEX[code].label},
            "参数已更新，仅对新预约生效",
        )

    @implements("PR-14")
    def set_user_role(
        self, actor: Actor, user_id: str, role: str, now: datetime, *, ip: str = ""
    ) -> Result:
        self._require(actor, {Role.SYS_ADMIN}, "PR-14")
        try:
            new_role = Role(role)
        except ValueError as exc:
            raise ValidationFailed("角色取值不合法") from exc
        user = self.repo.get_user(user_id)
        if user is None:
            raise NotFound("账号不存在")
        if user.role == Role.SYS_ADMIN.value and new_role != Role.SYS_ADMIN:
            if self.repo.count_sys_admins(exclude=user.id) == 0:
                raise RuleViolation("不能移除最后一名系统管理员", rule="PR-14")
        before = {"role": user.role}
        user.role = new_role.value
        user.updated_at = now
        self.repo.save_user(user)
        self._audit(
            actor,
            "role_change",
            "user",
            user.id,
            before=before,
            after={"role": user.role},
            now=now,
            ip=ip,
        )
        return Result.success(user.as_dict(), f"角色已调整为{ROLE_LABELS[user.role]}")

    # ------------------------------------------------------------------
    # A：账号生命周期与个人中心（PR-14）
    # ------------------------------------------------------------------
    @implements("PR-14")
    def create_user(
        self, actor: Actor, now: datetime, *, user_id: str, name: str, role: str,
        department: str = "", class_name: str = "", password: str | None = None,
        ip: str = "",
    ) -> Result:
        from ..security import hash_password

        self._require(actor, {Role.SYS_ADMIN}, "PR-14")
        user_id, name = user_id.strip(), name.strip()
        department, class_name = department.strip(), class_name.strip()
        if (not user_id or len(user_id) > 64
                or any(ch.isspace() or ch in "/\\" for ch in user_id)):
            raise ValidationFailed("账号须为 1～64 字符，不能包含空白或斜杠")
        if not name or len(name) > 100 or len(department) > 100 or len(class_name) > 100:
            raise ValidationFailed("姓名必填，姓名、院系和班级不能超过 100 字符")
        try:
            new_role = Role(role)
        except ValueError as exc:
            raise ValidationFailed("角色取值不合法") from exc
        if password is not None and not 8 <= len(password) <= 128:
            raise ValidationFailed("初始密码须为 8～128 字符")
        if self.repo.get_user(user_id) is not None:
            raise Conflict("账号已存在")
        initial_password = password if password is not None else secrets.token_urlsafe(12)
        user = User(
            id=user_id, name=name, role=new_role.value, department=department,
            class_name=class_name, password_hash=hash_password(initial_password),
            must_change_password=True, created_at=now, updated_at=now,
        )
        self.repo.save_user(user)
        self._audit(actor, "user_create", "user", user.id, before=None,
                    after=user.as_dict(), now=now, ip=ip)
        data = {"user": user.as_dict()}
        if password is None:
            data["initial_password"] = initial_password
        return Result.success(data, "账号已创建，首次登录需修改密码")

    @implements("PR-14")
    def set_user_status(
        self, actor: Actor, user_id: str, status: str, reason: str, now: datetime,
        *, ip: str = "",
    ) -> Result:
        self._require(actor, {Role.SYS_ADMIN}, "PR-14")
        if status not in {"active", "disabled"}:
            raise ValidationFailed("账号状态不合法")
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise ValidationFailed("请填写 1～500 字符的操作原因")
        user = self.repo.get_user(user_id)
        if user is None:
            raise NotFound("账号不存在")
        if status == "disabled":
            if user.id == actor.id:
                raise RuleViolation("不能停用自己的账号", rule="PR-14")
            if (user.is_active and user.role == Role.SYS_ADMIN.value
                    and self.repo.count_sys_admins(exclude=user.id) == 0):
                raise RuleViolation("不能停用最后一名启用中的系统管理员", rule="PR-14")
        before = {"status": user.status}
        user.status, user.updated_at = status, now
        self.repo.save_user(user)
        if status == "disabled":
            self.repo.delete_sessions(user.id)
        self._audit(actor, "user_status_change", "user", user.id, before=before,
                    after={"status": status, "reason": reason}, now=now, ip=ip)
        return Result.success({"user": user.as_dict()}, "账号已启用" if user.is_active else "账号已停用")

    @implements("PR-14")
    def reset_user_password(
        self, actor: Actor, user_id: str, now: datetime, *, ip: str = "",
    ) -> Result:
        from ..security import hash_password

        self._require(actor, {Role.SYS_ADMIN}, "PR-14")
        user = self.repo.get_user(user_id)
        if user is None:
            raise NotFound("账号不存在")
        initial_password = secrets.token_urlsafe(12)
        user.password_hash = hash_password(initial_password)
        user.must_change_password, user.updated_at = True, now
        self.repo.save_user(user)
        revoked = self.repo.delete_sessions(user.id)
        # 与 AuthService.reset_password 相同的原语，在本次引擎事务中原子完成，避免嵌套写锁。
        self._audit(actor, "user_reset_password", "user", user.id, before=None,
                    after={"must_change_password": True, "revoked": revoked}, now=now, ip=ip)
        return Result.success({"initial_password": initial_password}, "密码已重置，所有设备需重新登录")

    def list_user_sessions(
        self, actor: Actor, user_id: str, now: datetime, *, current_token_hash: str,
        idle_minutes: int = 30,
    ) -> dict[str, Any]:
        if actor.id != user_id:
            self._require(actor, {Role.SYS_ADMIN}, "PR-14")
        if self.repo.get_user(user_id) is None:
            raise NotFound("账号不存在")
        items = []
        for row in self.repo.list_sessions(user_id):
            absolute = datetime.fromisoformat(row["expires_at"])
            idle = datetime.fromisoformat(row["last_seen_at"]) + timedelta(minutes=idle_minutes)
            if now >= min(absolute, idle):
                continue
            items.append({
                **{key: row[key] for key in ("created_at", "last_seen_at", "expires_at", "ip", "user_agent")},
                "current": secrets.compare_digest(row["token_hash"], current_token_hash),
            })
        return {"items": items, "current": any(item["current"] for item in items)}

    def revoke_user_sessions(
        self, actor: Actor, user_id: str, now: datetime, *,
        except_token_hash: str | None = None, ip: str = "",
    ) -> Result:
        if actor.id != user_id or except_token_hash is None:
            self._require(actor, {Role.SYS_ADMIN}, "PR-14")
        if self.repo.get_user(user_id) is None:
            raise NotFound("账号不存在")
        if except_token_hash is not None and not any(
            row["token_hash"] == except_token_hash for row in self.repo.list_sessions(user_id)
        ):
            raise RuleViolation("当前会话已失效，请重新登录")
        revoked = self.repo.delete_sessions(user_id, except_token_hash=except_token_hash)
        self._audit(actor, "session_revoke", "user", user_id, before=None,
                    after={"revoked": revoked, "keep_current": except_token_hash is not None},
                    now=now, ip=ip)
        return Result.success({"revoked": revoked}, "登录会话已注销")

    def update_my_profile(
        self, actor: Actor, now: datetime, *, name: str | None = None,
        department: str | None = None, ip: str = "",
    ) -> Result:
        user = self._user(actor)
        if name is None and department is None:
            raise ValidationFailed("请至少填写一项资料")
        if name is not None and (not name.strip() or len(name.strip()) > 100):
            raise ValidationFailed("姓名须为 1～100 字符")
        if department is not None and len(department.strip()) > 100:
            raise ValidationFailed("院系不能超过 100 字符")
        before = {"name": user.name, "department": user.department}
        if name is not None:
            user.name = name.strip()
        if department is not None:
            user.department = department.strip()
        user.updated_at = now
        self.repo.save_user(user)
        self._audit(actor, "profile_update", "user", user.id, before=before,
                    after={"name": user.name, "department": user.department}, now=now, ip=ip)
        return Result.success({"user": user.as_dict()}, "个人资料已更新")

    # ------------------------------------------------------------------
    # 自习室 / 座位资源管理（FR-21 / PR-05）
    # ------------------------------------------------------------------
    @implements("FR-21", "PR-05")
    def list_rooms_admin(self, actor: Actor) -> list[dict[str, Any]]:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        seats = self.repo.list_seats()
        seat_counts: dict[str, int] = {}
        for seat in seats:
            seat_counts[seat.room_id] = seat_counts.get(seat.room_id, 0) + 1
        items: list[dict[str, Any]] = []
        for room in self.repo.list_rooms():
            data = room.as_dict()
            data["seat_count"] = seat_counts.get(room.id, 0)
            items.append(data)
        return items

    @staticmethod
    def _room_slots(open_time: time, close_time: time) -> tuple[int, int]:
        try:
            open_slot, close_slot = slot_of_time(open_time), slot_of_time(close_time)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        if close_slot <= open_slot:
            raise ValidationFailed("关闭时间必须晚于开放时间")
        return open_slot, close_slot

    @implements("FR-21", "PR-05")
    def create_room(
        self,
        actor: Actor,
        room_id: str,
        name: str,
        location: str,
        open_time: time,
        close_time: time,
        status: str,
        now: datetime,
        *,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        room_id = room_id.strip()
        name = name.strip()
        if not room_id:
            raise ValidationFailed("自习室编号不能为空")
        if not name:
            raise ValidationFailed("自习室名称不能为空")
        if status not in {"open", "closed"}:
            raise ValidationFailed("自习室状态只能是 open 或 closed")
        if self.repo.get_room(room_id) is not None:
            raise Conflict(f"自习室 {room_id} 已存在", rule="PR-05")
        open_slot, close_slot = self._room_slots(open_time, close_time)
        room = Room(
            id=room_id,
            name=name,
            location=location.strip(),
            status=status,
            open_slot=open_slot,
            close_slot=close_slot,
            created_at=now,
        )
        self.repo.save_room(room)
        self._audit(
            actor,
            "room_create",
            "room",
            room.id,
            before=None,
            after=room.as_dict(),
            now=now,
            ip=ip,
        )
        return Result.success(room.as_dict(), "自习室已创建")

    @implements("FR-21", "PR-05")
    def update_room(
        self,
        actor: Actor,
        room_id: str,
        *,
        name: str | None,
        location: str | None,
        status: str | None,
        open_time: time | None,
        close_time: time | None,
        now: datetime,
        ip: str = "",
        cascade_release: bool = False,
    ) -> Result:
        """修改自习室。

        ``cascade_release=True`` 时（资源 mutation 入口使用），关闭自习室会在
        同一事务内调用 ``release_affected_reservations`` 取消仍有效的预约（M-04）；
        默认 ``False`` 保持既有契约，供 C 等调用方自行决定是否级联。
        """
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        room = self.repo.get_room(room_id)
        if room is None:
            raise NotFound(f"自习室 {room_id} 不存在")
        before = room.as_dict()
        if name is not None:
            if not name.strip():
                raise ValidationFailed("自习室名称不能为空")
            room.name = name.strip()
        if location is not None:
            room.location = location.strip()
        if status is not None:
            if status not in {"open", "closed"}:
                raise ValidationFailed("自习室状态只能是 open 或 closed")
            room.status = status
        if open_time is not None or close_time is not None:
            open_slot, close_slot = self._room_slots(
                open_time or time_of_slot(room.open_slot),
                close_time or time_of_slot(room.close_slot),
            )
            room.open_slot, room.close_slot = open_slot, close_slot
        self.repo.save_room(room)
        released: list[str] = []
        if cascade_release and room.status == "closed":
            released = self.release_affected_reservations(
                room_id=room.id,
                seat_id=None,
                reason=f"自习室 {room.name or room.id} 关闭",
                actor=actor,
                now=now,
            )
            self.audit_event(
                actor,
                "room_close_release",
                "room",
                room.id,
                result="success" if released else "noop",
                details={"affected": released, "target_status": "closed"},
                now=now,
                ip=ip,
            )
        data = room.as_dict()
        data["released_reservations"] = released
        self._audit(
            actor,
            "room_update",
            "room",
            room.id,
            before=before,
            after=room.as_dict(),
            now=now,
            ip=ip,
        )
        return Result.success(data, "自习室已更新")

    @implements("FR-21", "PR-05")
    def list_seats_admin(self, actor: Actor, room_id: str | None = None) -> list[dict[str, Any]]:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        rooms = {room.id: room.name for room in self.repo.list_rooms()}
        items: list[dict[str, Any]] = []
        for seat in self.repo.list_seats(room_id=room_id):
            data = seat.as_dict()
            data["room_name"] = rooms.get(seat.room_id, "")
            data["admin_status"] = seat.admin_status
            items.append(data)
        return items

    @staticmethod
    def _validate_seat_shape(seat_type: str, capacity: int) -> None:
        try:
            kind = SeatType(seat_type)
        except ValueError as exc:
            raise ValidationFailed("座位类型不合法") from exc
        if kind == SeatType.DISCUSSION:
            if not 4 <= capacity <= 8:
                raise ValidationFailed("讨论间容量必须在 4~8 人之间")
        elif capacity != 1:
            raise ValidationFailed("普通座位容量必须为 1")

    @implements("FR-21", "PR-05")
    def create_seat(
        self,
        actor: Actor,
        seat_id: str,
        room_id: str,
        seat_type: str,
        capacity: int,
        has_power: bool,
        is_window: bool,
        note: str,
        now: datetime,
        *,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        seat_id = seat_id.strip()
        if not seat_id:
            raise ValidationFailed("座位编号不能为空")
        if self.repo.get_room(room_id) is None:
            raise NotFound(f"自习室 {room_id} 不存在")
        if self.repo.get_seat(seat_id) is not None:
            raise Conflict(f"座位 {seat_id} 已存在", rule="PR-05")
        self._validate_seat_shape(seat_type, capacity)
        seat = Seat(
            id=seat_id,
            room_id=room_id,
            seat_type=seat_type,
            capacity=capacity,
            has_power=has_power,
            is_window=is_window,
            note=note.strip(),
            created_at=now,
        )
        self.repo.save_seat(seat)
        after = seat.as_dict()
        after["admin_status"] = seat.admin_status
        self._audit(
            actor,
            "seat_create",
            "seat",
            seat.id,
            before=None,
            after=after,
            now=now,
            ip=ip,
        )
        return Result.success(after, "座位已创建")

    @implements("FR-21", "PR-05")
    def update_seat(
        self,
        actor: Actor,
        seat_id: str,
        *,
        room_id: str | None,
        seat_type: str | None,
        capacity: int | None,
        admin_status: str | None,
        has_power: bool | None,
        is_window: bool | None,
        note: str | None,
        now: datetime,
        ip: str = "",
        cascade_release: bool = False,
    ) -> Result:
        """修改座位。

        ``cascade_release=True`` 时（资源 mutation 入口使用），把座位改为
        ``disabled`` / ``maintenance`` 会在同一事务内调用
        ``release_affected_reservations`` 取消该座位仍有效的预约（M-04）；
        默认 ``False`` 保持既有契约。
        """
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        before = seat.as_dict()
        before["admin_status"] = seat.admin_status
        if room_id is not None and room_id != seat.room_id:
            if self.repo.get_room(room_id) is None:
                raise NotFound(f"自习室 {room_id} 不存在")
            # 跨自习室移动保护：已有待签到/使用中/暂离预约时禁止改写房间归属，
            # 否则历史预约的房间统计会被改写（PR-05）。
            occupied = self.repo.list_active_overlapping(
                seat_id,
                now - timedelta(days=self.params.int("booking.window_days")),
                now + timedelta(days=self.params.int("booking.window_days")),
            )
            if resource_rules.has_occupying_reservation(occupied):
                raise Conflict(
                    f"座位 {seat_id} 仍有进行中或未开始的预约，不能移动到其他自习室",
                    rule="PR-05",
                )
        if room_id is not None:
            seat.room_id = room_id
        if seat_type is not None:
            seat.seat_type = seat_type
        if capacity is not None:
            seat.capacity = capacity
        self._validate_seat_shape(seat.seat_type, seat.capacity)
        if admin_status is not None:
            if admin_status not in set(resource_rules.SEAT_ADMIN_STATUSES):
                raise ValidationFailed("座位状态只能是 available / maintenance / disabled")
            seat.admin_status = admin_status
            # 管理员显式设置状态即“手工意图优先”：清零封禁来源标记，
            # 之后该座位不会再被到期封禁自动恢复。
            seat.maintenance_by_block = False
        if has_power is not None:
            seat.has_power = has_power
        if is_window is not None:
            seat.is_window = is_window
        if note is not None:
            seat.note = note.strip()
        self.repo.save_seat(seat)
        released: list[str] = []
        if cascade_release and seat.admin_status in {"disabled", "maintenance"}:
            released = self.release_affected_reservations(
                room_id=None,
                seat_id=seat.id,
                reason=f"座位 {seat.id} 状态改为 {seat.admin_status}",
                actor=actor,
                now=now,
            )
            self.audit_event(
                actor,
                "seat_unavailable_release",
                "seat",
                seat.id,
                result="success" if released else "noop",
                details={"affected": released, "target_status": seat.admin_status},
                now=now,
                ip=ip,
            )
        after = seat.as_dict()
        after["admin_status"] = seat.admin_status
        after["released_reservations"] = released
        self._audit(
            actor,
            "seat_update",
            "seat",
            seat.id,
            before=before,
            after=after,
            now=now,
            ip=ip,
        )
        return Result.success(after, "座位已更新")

    # ------------------------------------------------------------------
    # 投诉 / 评价 / 黑名单
    # ------------------------------------------------------------------
    @implements("BR-19")
    def submit_complaint(
        self,
        actor: Actor,
        complaint_type: str,
        content: str,
        seat_id: str | None,
        now: datetime,
    ) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-18")
        if complaint_type not in {item.value for item in ComplaintType}:
            raise ValidationFailed("投诉类型不合法")
        if len(content.strip()) < 5:
            raise ValidationFailed("请填写至少 5 个字的投诉内容")
        if seat_id and self.repo.get_seat(seat_id) is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        complaint = Complaint(
            id=self.repo.next_id("complaint", "CP-", 6),
            user_id=actor.id,
            complaint_type=complaint_type,
            content=content.strip(),
            seat_id=seat_id,
            status=ComplaintStatus.PENDING.value,
            created_at=now,
        )
        self.repo.insert_complaint(complaint)
        return Result.success(complaint.as_dict(), "投诉已提交")

    @implements("BR-19", "PR-18")
    def resolve_complaint(
        self,
        actor: Actor,
        complaint_id: str,
        feedback: str,
        now: datetime,
        *,
        linked_reservation_id: str | None = None,
        linked_mode: str | None = None,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-18")
        if len(feedback.strip()) < 2:
            raise ValidationFailed("办结前必须填写反馈信息（学生端可见）")
        complaint = self.repo.get_complaint(complaint_id)
        if complaint is None:
            raise NotFound("投诉不存在")
        if complaint.status != ComplaintStatus.PENDING.value:
            raise RuleViolation("该投诉已办结", rule="ST-12")
        linked_violation: str | None = None
        if linked_reservation_id:
            mode = linked_mode or "warn"
            result = self.manual_violation(
                actor,
                linked_reservation_id,
                mode,
                f"投诉 {complaint.id} 联动登记：{feedback.strip()}",
                now,
                ip=ip,
            )
            linked_violation = result.data["violation"]["id"]
        before = {"status": complaint.status}
        complaint.status = ComplaintStatus.RESOLVED.value
        complaint.feedback = feedback.strip()
        complaint.resolved_by = actor.id
        complaint.resolved_at = now
        self.repo.update_complaint(complaint)
        self._notify(
            complaint.user_id,
            NotificationType.COMPLAINT_RESULT.value,
            f"您的投诉 {complaint.id} 已处理：{feedback.strip()}",
            now,
        )
        self._audit(
            actor,
            "complaint_resolve",
            "complaint",
            complaint.id,
            before=before,
            after={
                "status": complaint.status,
                "feedback": complaint.feedback,
                "linked_violation": linked_violation,
            },
            now=now,
            ip=ip,
        )
        return Result.success(complaint.as_dict(), "投诉已办结")

    @implements("BR-19")
    def rate_complaint(
        self, actor: Actor, complaint_id: str, rating: int, comment: str, now: datetime
    ) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-18")
        complaint = self.repo.get_complaint(complaint_id)
        if complaint is None:
            raise NotFound("投诉不存在")
        if complaint.user_id != actor.id:
            raise Forbidden("只能评价本人的投诉", rule="PR-18")
        if complaint.status != ComplaintStatus.RESOLVED.value:
            raise RuleViolation("投诉尚未办结，不能评价", rule="ST-12")
        if not 1 <= rating <= 5:
            raise ValidationFailed("评分必须在 1~5 之间")
        complaint.status = ComplaintStatus.RATED.value
        complaint.rating = rating
        complaint.rating_comment = comment.strip()
        self.repo.update_complaint(complaint)
        return Result.success(complaint.as_dict(), "评价已提交")

    @implements("BR-21")
    def submit_review(
        self,
        actor: Actor,
        reservation_id: str,
        rating: int,
        tags: list[str],
        content: str,
        now: datetime,
    ) -> Result:
        self._require(actor, {Role.STUDENT}, "PR-18")
        if not 1 <= rating <= 5:
            raise ValidationFailed("评分必须在 1~5 星之间")
        reservation = self._own_reservation(actor, reservation_id)
        if reservation.status != ReservationStatus.COMPLETED.value:
            raise RuleViolation("只有已完成的预约可以评价", rule="BR-21")
        if self.repo.review_of_reservation(reservation_id) is not None:
            raise RuleViolation("该预约已评价，不能重复提交", rule="BR-21")
        review = Review(
            id=self.repo.next_id("review", "RV-", 6),
            reservation_id=reservation_id,
            user_id=actor.id,
            seat_id=reservation.seat_id,
            rating=rating,
            tags=[tag.strip()[:12] for tag in tags[:5] if tag.strip()],
            content=content.strip()[:500],
            created_at=now,
        )
        self.repo.insert_review(review)
        return Result.success(review.as_dict(), "评价已提交")

    @implements("BR-21", "PR-18")
    def reply_review(
        self,
        actor: Actor,
        review_id: str,
        reply: str,
        now: datetime,
        *,
        block_seat: bool = False,
        ip: str = "",
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-18")
        if len(reply.strip()) < 2:
            raise ValidationFailed("回复内容不能为空")
        review = self.repo.get_review(review_id)
        if review is None:
            raise NotFound("评价不存在")
        review.reply = reply.strip()
        review.replied_by = actor.id
        review.replied_at = now
        self.repo.update_review(review)
        blocked: list[str] = []
        if block_seat:
            if review.rating > 2:
                raise RuleViolation("只有 1~2 星差评可联动停用座位", rule="BR-21")
            result = self.block_seat(
                actor,
                review.seat_id,
                f"差评联动：评价 {review.id}（{review.rating} 星）",
                now,
                now + timedelta(days=1),
                now,
                ip=ip,
            )
            blocked = result.data["affected_reservations"]
        self._audit(
            actor,
            "review_reply",
            "review",
            review.id,
            before={"reply": None},
            after={"reply": review.reply, "block_seat": block_seat},
            now=now,
            ip=ip,
        )
        return Result.success(
            {"review": review.as_dict(), "affected_reservations": blocked},
            "回复已提交" + ("，座位已停用" if block_seat else ""),
        )

    @implements("BR-20", "PR-18")
    def manual_blacklist(
        self, actor: Actor, user_id: str, reason: str, days: int | None, now: datetime, *, ip: str = ""
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-18")
        if len(reason.strip()) < 2:
            raise ValidationFailed("拉黑必须填写原因")
        user = self.repo.get_user(user_id)
        if user is None:
            raise NotFound("账号不存在")
        if user.role != Role.STUDENT.value:
            raise RuleViolation("只能对学生账号执行黑名单操作", rule="BR-20")
        if self.repo.active_blacklist(user_id, now) is not None:
            raise Conflict("该账号已在黑名单中", rule="BR-20")
        duration = days or self.params.int("blacklist.days")
        if not 1 <= duration <= 365:
            raise ValidationFailed("拉黑天数必须在 1~365 之间")
        end_at = now + timedelta(days=duration)
        record = BlacklistRecord(
            id=self.repo.next_id("blacklist", "BL-", 6),
            user_id=user_id,
            reason=reason.strip(),
            source="manual",
            start_at=now,
            end_at=end_at,
            created_by=actor.id,
            created_at=now,
        )
        self.repo.insert_blacklist(record)
        before_limit = user.limit_type
        before_until = user.limit_until
        user.limit_type = LimitType.BLACKLIST.value
        user.limit_until = end_at
        user.updated_at = now
        self.repo.save_user(user)
        self._notify(
            user.id,
            NotificationType.VIOLATION.value,
            f"账号被限制预约至 {fmt_datetime(end_at)}，原因：{reason.strip()}。",
            now,
        )
        self._audit(
            actor,
            "blacklist_add",
            "user",
            user_id,
            before={"limit_type": before_limit, "until": fmt_datetime(before_until)},
            after={"limit_type": "blacklist", "until": fmt_datetime(end_at), "reason": reason.strip()},
            now=now,
            ip=ip,
        )
        return Result.success(record.as_dict(), "已加入黑名单")

    @implements("BR-20", "PR-18")
    def release_blacklist(
        self, actor: Actor, user_id: str, reason: str, now: datetime, *, ip: str = ""
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN}, "PR-18")
        if len(reason.strip()) < 2:
            raise ValidationFailed("解封必须填写原因")
        record = self.repo.active_blacklist(user_id, now)
        if record is None:
            raise RuleViolation("该账号当前不在黑名单中", rule="ST-12")
        user = self.repo.get_user(user_id)
        before_limit = user.limit_type if user is not None else "blacklist"
        before_until = user.limit_until if user is not None else record.end_at
        record.released_at = now
        record.released_by = actor.id
        self.repo.update_blacklist(record)
        if user is not None:
            user.limit_type = LimitType.NONE.value
            user.limit_until = None
            user.updated_at = now
            self.repo.save_user(user)
            self._refresh_limit(user, now)
            self._notify(
                user.id,
                NotificationType.APPEAL_RESULT.value,
                f"黑名单已解除，原因：{reason.strip()}。",
                now,
            )
        self._audit(
            actor,
            "blacklist_release",
            "user",
            user_id,
            before={"limit_type": before_limit, "until": fmt_datetime(before_until)},
            after={"limit_type": user.limit_type if user is not None else "none", "reason": reason.strip()},
            now=now,
            ip=ip,
        )
        return Result.success({"user_id": user_id}, "已解除黑名单")

    # ------------------------------------------------------------------
    # 查询与看板
    # ------------------------------------------------------------------
    @implements("PR-11")
    def get_reservation(self, actor: Actor, reservation_id: str) -> dict[str, Any]:
        reservation = self._reservation(reservation_id)
        if actor.role == Role.TEACHER:
            raise Forbidden("教师仅可查看本班汇总，不含个人明细", rule="PR-11")
        if actor.role == Role.STUDENT and reservation.user_id != actor.id:
            raise Forbidden("只能查看本人的预约记录", rule="PR-11")
        return self._view(reservation).as_dict()

    @implements("PR-08")
    def adjust_credit(self, actor: Actor, user_id: str, delta: int, now: datetime) -> Result:
        """PR-08：任何角色都不能直接修改信用分，只能经申诉审核按 BR-06 恢复。"""
        raise Forbidden("任何角色都不能直接修改信用分，只能经申诉审核恢复", rule="PR-08")

    @implements("PR-11")
    def list_reservations(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        day: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        seat_id: str | None = None,
        room_id: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        if actor.role == Role.TEACHER:
            raise Forbidden("教师仅可查看本班汇总，不含个人明细", rule="PR-11")
        user_id = actor.id if actor.role == Role.STUDENT else None
        return self.repo.list_reservations(
            user_id=user_id,
            status=status,
            day=day,
            date_from=date_from,
            date_to=date_to,
            seat_id=seat_id,
            room_id=room_id,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )

    @implements("PR-11")
    def list_violations(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        source: str | None = None,
        violation_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        if actor.role == Role.TEACHER:
            raise Forbidden("教师仅可查看本班汇总，不含个人明细", rule="PR-11")
        user_id = actor.id if actor.role == Role.STUDENT else None
        return self.repo.list_violations(
            user_id=user_id,
            status=status,
            source=source,
            violation_type=violation_type,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )

    @implements("PR-15")
    def list_audit(
        self,
        actor: Actor,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        self._require(actor, {Role.SYS_ADMIN}, "PR-15")
        return self.repo.list_audit(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )

    def list_users(
        self,
        actor: Actor,
        *,
        role: str | None = None,
        class_name: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-18")
        if actor.role == Role.ROOM_ADMIN:
            requested = role.strip() if role else None
            if requested and requested != Role.STUDENT.value:
                raise Forbidden(
                    "自习室管理员只能查询学生账号", rule="PR-18"
                )
            role = Role.STUDENT.value
        return self.repo.list_users(
            role=role, class_name=class_name, keyword=keyword, page=page, page_size=page_size
        )

    def list_notifications(self, actor: Actor, page: int = 1, page_size: int = 20) -> Page:
        return self.repo.list_notifications(actor.id, page=page, page_size=page_size)

    @implements("FR-54", "PR-11")
    def list_credit_logs(self, actor: Actor, page: int = 1, page_size: int = 20) -> Page:
        return self.repo.list_credit_logs(actor.id, page=page, page_size=page_size)

    def unread_notification_count(self, actor: Actor) -> int:
        return self.repo.unread_notification_count(actor.id)

    def mark_notifications_read(self, actor: Actor, ids: list[str] | None, now: datetime) -> int:
        return self.repo.mark_notifications_read(actor.id, ids, now)

    def list_waitlist(self, actor: Actor, status: str | None = None, page: int = 1, page_size: int = 20) -> Page:
        user_id = None if actor.is_admin else actor.id
        return self.repo.list_waitlist(
            user_id=user_id, status=status, page=page, page_size=page_size
        )

    def list_complaints(
        self, actor: Actor, status: str | None = None, page: int = 1, page_size: int = 20
    ) -> Page:
        if actor.role == Role.STUDENT:
            return self.repo.list_complaints(
                user_id=actor.id, status=status, page=page, page_size=page_size
            )
        if actor.role != Role.ROOM_ADMIN:
            raise Forbidden("该操作需要自习室管理员权限", rule="PR-18")
        return self.repo.list_complaints(status=status, page=page, page_size=page_size)

    def list_reviews(
        self,
        actor: Actor,
        *,
        seat_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page:
        # PR-18：评价的查看与回复归自习室管理员；学生仅可取本人评价。
        if actor.role == Role.STUDENT:
            user_id: str | None = actor.id
        elif actor.role == Role.ROOM_ADMIN:
            user_id = None
        elif actor.role == Role.TEACHER:
            raise Forbidden("教师无权查看评价明细", rule="PR-12")
        else:
            raise Forbidden("评价明细归自习室管理员处理，系统管理员无权查看", rule="PR-18")
        return self.repo.list_reviews(
            seat_id=seat_id, user_id=user_id, page=page, page_size=page_size
        )

    def list_blacklist(
        self,
        actor: Actor,
        active_only: bool = False,
        page: int = 1,
        page_size: int = 20,
        now: datetime | None = None,
    ) -> Page:
        # PR-18 / §2.6：黑名单归自习室管理员，系统管理员不含黑名单。
        self._require(actor, {Role.ROOM_ADMIN}, "PR-18")
        return self.repo.list_blacklist(
            active_only=active_only, page=page, page_size=page_size, now=now
        )

    def list_params(self, actor: Actor) -> list[dict[str, Any]]:
        self._require(actor, {Role.SYS_ADMIN}, "PR-13")
        return self.params.as_list()

    @implements("FR-03")
    def student_dashboard(self, actor: Actor, now: datetime) -> dict[str, Any]:
        self._require(actor, {Role.STUDENT}, "PR-11")
        stats = self.repo.student_stats(actor.id, now)
        unread = self.repo.unread_notification_count(actor.id)
        user = self._user(actor)
        return {
            "user": user.as_dict(),
            "credit": user.credit,
            "limit": {
                "type": user.limit_type,
                "until": fmt_datetime(user.limit_until),
                "can_book": self._booking_limit(user, now) is None,
                "reason": (self._booking_limit(user, now) or (None, None))[1],
            },
            "stats": stats,
            "unread_notifications": unread,
        }

    @implements("PR-12", "AC-13")
    def teacher_dashboard(self, actor: Actor, now: datetime) -> dict[str, Any]:
        self._require(actor, {Role.TEACHER}, "PR-12")
        if not actor.class_name:
            raise RuleViolation("教师账号未绑定班级", rule="PR-12")
        return self.repo.teacher_stats(actor.class_name, now)

    @implements("FR-61", "FR-69", "PR-12")
    def admin_stats(self, actor: Actor, room_id: str | None, now: datetime) -> dict[str, Any]:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-12")
        return self.repo.admin_stats(now, room_id)

    @staticmethod
    def _validate_date_range(
        date_from: date | None,
        date_to: date | None,
        *,
        max_days: int | None = None,
    ) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValidationFailed("开始日期不能晚于结束日期")
        if max_days is not None and date_from is not None and date_to is not None:
            days = (date_to - date_from).days + 1
            if days > max_days:
                raise ValidationFailed(f"日期跨度不能超过 {max_days} 天")

    def _resolve_analytics_dates(
        self,
        date_from: date | None,
        date_to: date | None,
        now: datetime,
    ) -> tuple[date, date]:
        """补齐分析日期：未给出的一端按 7 天窗口对称补齐。

        L-09：最小/最大日期附近加减窗口会触发 OverflowError，必须转为 4xx。
        """
        try:
            if date_from is None and date_to is None:
                date_to = now.date()
                date_from = date_to - timedelta(days=ANALYTICS_DEFAULT_DAYS - 1)
            elif date_from is None:
                assert date_to is not None
                date_from = date_to - timedelta(days=ANALYTICS_DEFAULT_DAYS - 1)
            elif date_to is None:
                date_to = date_from + timedelta(days=ANALYTICS_DEFAULT_DAYS - 1)
        except (OverflowError, ValueError) as exc:
            raise ValidationFailed("日期超出支持范围，请调整日期区间") from exc
        self._validate_date_range(date_from, date_to, max_days=MAX_ANALYTICS_DAYS)
        return date_from, date_to

    @implements("FR-61", "FR-69", "PR-12")
    def admin_analytics(
        self,
        actor: Actor,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        room_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """按 SRS PR-12 返回全校/指定自习室的运营分析。"""
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-12")
        now = now or datetime.now()
        resolved_from, resolved_to = self._resolve_analytics_dates(date_from, date_to, now)
        room_id = room_id.strip() if room_id else None
        if room_id and self.repo.get_room(room_id) is None:
            raise NotFound(f"自习室 {room_id} 不存在")
        data = self.repo.analytics_range(
            date_from=resolved_from,
            date_to=resolved_to,
            room_id=room_id,
        )
        data["generated_at"] = fmt_datetime(now)
        return data

    def export_rows(
        self,
        actor: Actor,
        kind: str,
        *,
        date_from: date | None,
        date_to: date | None,
        audit: bool = False,
        now: datetime | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        # PR-16：导出权限集中在 DomainEngine，Repository 只做查询。
        self._require(actor, {Role.SYS_ADMIN}, "PR-16")
        if kind not in EXPORT_KINDS:
            raise ValidationFailed(
                f"不支持的导出类型：{kind}；可选值为 {', '.join(sorted(EXPORT_KINDS))}"
            )
        # L-10：导出单独定义跨度上限，不与分析 90 天规则混用。
        self._validate_date_range(date_from, date_to, max_days=MAX_EXPORT_DAYS)
        headers, rows = self.repo.export_rows(
            kind,
            date_from=date_from,
            date_to=date_to,
        )
        if len(rows) > MAX_EXPORT_ROWS:
            raise ValidationFailed(
                f"导出行数超过 {MAX_EXPORT_ROWS} 条，请缩小日期范围",
                rule="L-10",
            )
        if audit:
            self.audit_event(
                actor,
                "export_csv",
                "export",
                kind,
                result="success",
                details={
                    "kind": kind,
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "rows": len(rows),
                },
                now=now or datetime.now(),
            )
        return headers, rows

    def param_snapshot_json(self) -> str:
        return json.dumps(self.params.snapshot(), ensure_ascii=False, sort_keys=True)
