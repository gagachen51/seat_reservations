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
_CROCKFORD = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# FR-54 / FR-62 / FR-72 要求的导出类型；路由与引擎共用，避免"前端有链接、后端不认"。
EXPORT_KINDS = ("reservations", "violations", "audit")


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
        )
        return [view.as_dict() for view in views]

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
            raise RuleViolation("该自习室已停用", rule="FR-21")

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
            label = "维护中" if seat.admin_status == "maintenance" else "已停用"
            raise RuleViolation(f"座位 {seat_id} 当前{label}，不可预约", rule="BR-09")

        start_at, end_at = datetime_at(day, start_slot), datetime_at(day, end_slot)
        if self.repo.seat_block_overlaps(seat_id, start_at, end_at):
            raise RuleViolation(f"座位 {seat_id} 在该时段处于维护封禁", rule="BR-09")
        if start_at <= now:
            raise RuleViolation("预约开始时间必须晚于当前时间", rule="BR-03")

        conflict = self.repo.find_seat_conflict(seat_id, day, start_slot, end_slot)
        if conflict:
            raise Conflict(f"座位 {seat_id} 在 {hhmm(start_slot)}-{hhmm(end_slot)} 已被占用", rule="BR-11")
        user_conflict = self.repo.find_user_conflict(user.id, day, start_slot, end_slot)
        if user_conflict:
            raise Conflict("本人该时段已有其他有效预约，不允许重叠", rule="BR-11")

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
        code = _new_credential()
        reservation.credential_code = code
        reservation.credential_issued_at = now
        reservation.credential_used = False
        reservation.updated_at = now
        reservation.version += 1
        self.repo.update_reservation(reservation)
        return Result.success(
            {
                "reservation_id": reservation.id,
                "code": code,
                "issued_at": fmt_datetime(now),
                "expires_at": fmt_datetime(add_minutes(now, ttl // 60) if ttl >= 60 else now + timedelta(seconds=ttl)),
                "ttl_seconds": ttl,
            },
            "凭证已签发",
        )

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

        user = self.repo.get_user(reservation.user_id)
        if user is None:
            raise NotFound("账号不存在")

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
        start_slot, end_slot = slot_of_time(start), slot_of_time(end)
        ok, message = validate_slot_range(
            start_slot,
            end_slot,
            self.params.int("booking.min_minutes"),
            self.params.int("booking.max_minutes"),
        )
        if not ok:
            raise RuleViolation(message, rule="BR-01")
        if self.repo.get_seat(seat_id) is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        if self.repo.find_seat_conflict(seat_id, day, start_slot, end_slot) is None:
            raise RuleViolation("该时段座位仍可预约，无需候补", rule="BR-08")
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
        if entry.status != WaitlistStatus.NOTIFIED.value:
            raise RuleViolation("该候补当前不可确认", rule="ST-12")
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
        if room is None or room.status != "open" or seat.admin_status != "available":
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure("BR-09", "座位或自习室当前不可用，候补已失效", rule="BR-09")
        if self.repo.seat_block_overlaps(seat.id, start_at, end_at):
            self._expire_waitlist(entry, now, offer_next=False)
            return Result.failure("BR-09", "座位在该时段处于维护封禁，候补已失效", rule="BR-09")

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
            self._expire_waitlist(entry, now)
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

    # ------------------------------------------------------------------
    # 定时任务
    # ------------------------------------------------------------------
    @implements("BR-04", "BR-05", "BR-06", "BR-08", "BR-16", "NFR-02", "ST-02", "ST-07", "ST-09")
    def run_timeout_sweep(self, now: datetime) -> dict[str, int]:
        summary = {
            "no_show": 0,
            "away_timeout": 0,
            "late_checkout": 0,
            "waitlist_expired": 0,
            "reminders": 0,
        }
        for reservation in self.repo.list_due_pending(now):
            if reservation.status != ReservationStatus.PENDING.value:
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
        before = {"seat_status": seat.admin_status}
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
        seat.admin_status = "maintenance"
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
            after={"seat_status": "maintenance", "block_id": block.id, "affected": affected},
            now=now,
            ip=ip,
        )
        return Result.success(
            {"seat_id": seat_id, "block": block.as_dict(), "affected_reservations": affected},
            f"已封禁，取消 {len(affected)} 条受影响预约",
        )

    @implements("PR-06")
    def unblock_seat(self, actor: Actor, seat_id: str, now: datetime, *, ip: str = "") -> Result:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-06")
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        blocks = [b for b in self.repo.active_seat_blocks(seat_id)]
        if not blocks:
            raise RuleViolation("该座位当前没有生效中的封禁", rule="ST-12")
        for block in blocks:
            self.repo.close_seat_block(block.id, now)
        before_status = seat.admin_status
        # 仅当座位是因本次封禁进入 maintenance 时才恢复为可预约；
        # 原本处于 disabled（停用）的座位不得被解封操作降级。
        if seat.admin_status == "maintenance":
            seat.admin_status = "available"
        self.repo.save_seat(seat)
        self._audit(
            actor,
            "seat_unblock",
            "seat",
            seat_id,
            before={"seat_status": before_status},
            after={"seat_status": seat.admin_status},
            now=now,
            ip=ip,
        )
        return Result.success({"seat_id": seat_id}, "已解封")

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
    ) -> Result:
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
        return Result.success(room.as_dict(), "自习室已更新")

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
    ) -> Result:
        self._require(actor, {Role.ROOM_ADMIN, Role.SYS_ADMIN}, "PR-05")
        seat = self.repo.get_seat(seat_id)
        if seat is None:
            raise NotFound(f"座位 {seat_id} 不存在")
        before = seat.as_dict()
        before["admin_status"] = seat.admin_status
        if room_id is not None:
            if self.repo.get_room(room_id) is None:
                raise NotFound(f"自习室 {room_id} 不存在")
            seat.room_id = room_id
        if seat_type is not None:
            seat.seat_type = seat_type
        if capacity is not None:
            seat.capacity = capacity
        self._validate_seat_shape(seat.seat_type, seat.capacity)
        if admin_status is not None:
            if admin_status not in {"available", "maintenance", "disabled"}:
                raise ValidationFailed("座位状态只能是 available / maintenance / disabled")
            seat.admin_status = admin_status
        if has_power is not None:
            seat.has_power = has_power
        if is_window is not None:
            seat.is_window = is_window
        if note is not None:
            seat.note = note.strip()
        self.repo.save_seat(seat)
        after = seat.as_dict()
        after["admin_status"] = seat.admin_status
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

    def export_rows(
        self, actor: Actor, kind: str, *, date_from: date | None, date_to: date | None
    ) -> tuple[list[str], list[list[Any]]]:
        self._require(actor, {Role.SYS_ADMIN}, "PR-16")
        if kind == "reservations":
            page = self.repo.list_reservations(
                date_from=date_from, date_to=date_to, page=1, page_size=10000
            )
            headers = ["预约号", "日期", "开始", "结束", "座位", "自习室", "学生", "状态", "来源", "结束原因"]
            rows = [
                [
                    item.reservation.id,
                    item.reservation.day.isoformat(),
                    item.reservation.start_time,
                    item.reservation.end_time,
                    item.seat_id,
                    item.room_name,
                    item.user_name,
                    item.reservation.status,
                    item.reservation.source,
                    item.reservation.end_reason or "",
                ]
                for item in page.items
            ]
            return headers, rows
        if kind == "violations":
            page = self.repo.list_violations(page=1, page_size=10000)
            headers = ["违规号", "预约号", "学号", "类型", "扣分", "来源", "状态", "发生时间", "说明"]
            rows = [
                [
                    item.id,
                    item.reservation_id,
                    item.user_id,
                    item.violation_type,
                    item.points,
                    item.source,
                    item.status,
                    fmt_datetime(item.occurred_at) or "",
                    item.note,
                ]
                for item in page.items
            ]
            return headers, rows
        if kind == "audit":
            self._require(actor, {Role.SYS_ADMIN}, "PR-15")
            page = self.repo.list_audit(page=1, page_size=10000)
            headers = ["日志号", "操作人", "角色", "动作", "对象类型", "对象", "时间", "IP"]
            rows = [
                [
                    item.id,
                    item.actor_id,
                    item.actor_role,
                    item.action,
                    item.target_type,
                    item.target_id,
                    fmt_datetime(item.created_at) or "",
                    item.ip,
                ]
                for item in page.items
            ]
            return headers, rows
        raise ValidationFailed(
            f"不支持的导出类型：{kind}；可选值为 {', '.join(sorted(EXPORT_KINDS))}"
        )

    def param_snapshot_json(self) -> str:
        return json.dumps(self.params.snapshot(), ensure_ascii=False, sort_keys=True)
