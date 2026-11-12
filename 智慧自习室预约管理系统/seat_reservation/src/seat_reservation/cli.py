"""命令行入口：``seat-reservation <command>``。

命令覆盖部署与运维常用动作：迁移、种子、服务、扫描、账号、参数、导出、清理、自检。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .config import Settings
from .domain.models import Actor, Role
from .domain.timeutil import parse_datetime
from .errors import AppError
from .logging_setup import configure_logging
from .security import hash_password
from .service.container import AppContext
from .storage.repository import SqliteRepository
from .storage.seed import DEMO_PASSWORD


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if getattr(args, "db", None):
        settings = settings.with_db(args.db)
    if getattr(args, "demo", None) is not None:
        from dataclasses import replace

        settings = replace(settings, demo_mode=args.demo)
    return settings


def _ctx(args: argparse.Namespace) -> AppContext:
    settings = _settings(args)
    configure_logging(settings.log_level, settings.log_json)
    return AppContext(settings)


def _cli_actor(conn) -> Actor:
    repo = SqliteRepository(conn)
    admin = repo.get_user("Y0001")
    if admin and admin.role == Role.SYS_ADMIN.value:
        return Actor(id=admin.id, name=admin.name, role=Role.SYS_ADMIN)
    return Actor(id="CLI", name="命令行", role=Role.SYS_ADMIN)


def cmd_init_db(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    applied = ctx.db.migrate()
    print(f"数据库：{ctx.settings.db_path}")
    print(f"迁移完成，新增版本：{applied or '无（已是最新）'}")
    if args.demo:
        counts = ctx.seed_demo(datetime.now())
        print(f"演示数据：{json.dumps(counts, ensure_ascii=False)}，密码 {DEMO_PASSWORD}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    now = parse_datetime(args.at) if args.at else datetime.now()
    counts = ctx.seed_demo(now)
    print(f"演示数据已写入：{json.dumps(counts, ensure_ascii=False)}")
    print(f"演示密码：{DEMO_PASSWORD}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.db:
        os.environ["SEAT_DB_PATH"] = str(Path(args.db))
    os.environ["SEAT_DEMO_MODE"] = "true" if args.demo else "false"
    uvicorn.run(
        "seat_reservation.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    now = parse_datetime(args.at) if args.at else datetime.now()
    with ctx.engine(write=True) as engine:
        summary = engine.run_timeout_sweep(now)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def cmd_user(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    ctx.db.migrate()
    if args.user_command == "list":
        with ctx.db.read() as conn:
            rows = conn.execute(
                "SELECT id, name, role, credit, limit_type, class_name FROM users ORDER BY role, id"
            ).fetchall()
        for row in rows:
            print(
                f"{row['id']:<12} {row['name']:<6} {row['role']:<12} "
                f"信用 {row['credit']:>3}  限制 {row['limit_type']:<10} {row['class_name']}"
            )
        return 0
    if args.user_command == "create":
        # 需求 §7.4：不指定密码时由系统生成随机初始密码，并强制首次登录修改。
        generated = not args.password
        password = args.password or secrets.token_urlsafe(9)
        with ctx.db.transaction() as conn:
            repo = SqliteRepository(conn)
            if repo.get_user(args.id):
                print(f"账号 {args.id} 已存在", file=sys.stderr)
                return 1
            from .domain.models import User

            now = datetime.now()
            repo.save_user(
                User(
                    id=args.id,
                    name=args.name,
                    role=args.role,
                    department=args.department or "",
                    class_name=args.class_name or "",
                    credit=100,
                    password_hash=hash_password(password),
                    must_change_password=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        if generated:
            print(f"已创建 {args.id}（{args.role}），系统生成初始密码：{password}")
        else:
            print(f"已创建 {args.id}（{args.role}），初始密码为指定值")
        print("该账号首次登录会被要求修改密码")
        return 0
    if args.user_command == "passwd":
        generated = not args.password
        password = args.password or secrets.token_urlsafe(9)
        ctx.auth.reset_password(args.id, password)
        print(
            f"已重置 {args.id} 的密码"
            + (f"，系统生成新密码：{password}" if generated else "")
            + "，并要求下次登录修改"
        )
        return 0
    if args.user_command == "role":
        with ctx.db.transaction() as conn:
            repo = SqliteRepository(conn)
            user = repo.get_user(args.id)
            if user is None:
                print("账号不存在", file=sys.stderr)
                return 1
            user.role = args.role
            user.updated_at = datetime.now()
            repo.save_user(user)
        print(f"已将 {args.id} 角色改为 {args.role}")
        return 0
    return 1


def cmd_params(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    if args.params_command == "list":
        for item in ctx.params().as_list():
            print(f"{item['code']:<32} {item['value']:>6} {item['unit']:<4} {item['label']}")
        return 0
    if args.params_command == "set":
        with ctx.engine(write=True) as engine:
            result = engine.set_param(
                Actor(id="CLI", name="命令行", role=Role.SYS_ADMIN),
                args.code,
                args.value,
                datetime.now(),
            )
        print(result.message)
        return 0 if result.ok else 1
    return 1


def cmd_export(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    out = Path(args.out)
    with ctx.engine(write=False) as engine:
        headers, rows = engine.export_rows(
            _cli_actor_dummy(), args.kind, date_from=None, date_to=None
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"已导出 {len(rows)} 行到 {out}")
    return 0


def _cli_actor_dummy() -> Actor:
    return Actor(id="CLI", name="命令行", role=Role.SYS_ADMIN)


def cmd_prune(args: argparse.Namespace) -> int:
    ctx = _ctx(args)
    cutoff = datetime.now() - timedelta(days=args.days)
    terminal = ("completed", "cancelled", "no_show", "released")
    with ctx.db.transaction() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM reservations
            WHERE status IN ({','.join('?' for _ in terminal)}) AND end_at < ?
            """,
            (*terminal, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()
        count = int(row["c"])
        if args.dry_run:
            print(f"[dry-run] 将清理 {count} 条 {args.days} 天前的终态预约")
            return 0
        conn.execute(
            f"""
            DELETE FROM reservations
            WHERE status IN ({','.join('?' for _ in terminal)}) AND end_at < ?
            """,
            (*terminal, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
        )
    print(f"已清理 {count} 条终态预约（保留审计与信用流水）")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    print(f"版本        {__version__}")
    print(f"Python      {sys.version.split()[0]}")
    print(f"环境        {settings.env}")
    print(f"数据库      {settings.db_path}")
    print(f"数据库存在  {settings.db_path.exists()}")
    print(f"目录可写    {os.access(settings.db_path.parent, os.W_OK)}")
    if settings.db_path.exists():
        ctx = AppContext(settings)
        print(f"Schema 版本 {ctx.db.schema_version()}")
        with ctx.db.read() as conn:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            reservations = conn.execute("SELECT COUNT(*) AS c FROM reservations").fetchone()["c"]
        print(f"账号 / 预约 {users} / {reservations}")
    else:
        print("提示        运行 `seat-reservation init-db --demo` 初始化")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seat-reservation", description="智慧自习室预约管理系统")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", help="覆盖数据库路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="执行数据库迁移")
    p.add_argument("--demo", action="store_true", help="同时写入演示数据")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("seed", help="写入演示数据（会清空业务表）")
    p.add_argument("--at", help="固定基准时间，例如 '2025-10-20 08:00'")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("serve", help="启动 HTTP 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--demo", action="store_true", help="首次启动写入演示数据")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("sweep", help="执行一次超时扫描")
    p.add_argument("--at", help="指定当前时间，便于复现")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("user", help="账号管理")
    user_sub = p.add_subparsers(dest="user_command", required=True)
    user_sub.add_parser("list", help="列出账号")
    create = user_sub.add_parser("create", help="创建账号")
    create.add_argument("id")
    create.add_argument("name")
    create.add_argument("--role", default="student", choices=[role.value for role in Role])
    create.add_argument("--password", help="不填则由系统生成随机初始密码")
    create.add_argument("--department", default="")
    create.add_argument("--class-name", dest="class_name", default="")
    passwd = user_sub.add_parser("passwd", help="重置密码")
    passwd.add_argument("id")
    passwd.add_argument("--password", help="不填则由系统生成随机密码")
    role = user_sub.add_parser("role", help="调整角色")
    role.add_argument("id")
    role.add_argument("role", choices=[item.value for item in Role])
    p.set_defaults(func=cmd_user)

    p = sub.add_parser("params", help="业务参数")
    param_sub = p.add_subparsers(dest="params_command", required=True)
    param_sub.add_parser("list", help="列出参数")
    setter = param_sub.add_parser("set", help="修改参数")
    setter.add_argument("code")
    setter.add_argument("value")
    p.set_defaults(func=cmd_params)

    p = sub.add_parser("export", help="导出 CSV")
    p.add_argument("kind", choices=["reservations", "violations", "audit"])
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("prune", help="清理超期终态预约")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("doctor", help="自检")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
