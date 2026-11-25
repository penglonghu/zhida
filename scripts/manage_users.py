"""账号运维 CLI：增删改、密码轮换、租户绑定。

推荐在容器内执行，保证 Redis 地址与日志目录和服务一致：
    docker compose exec zhida python scripts/manage_users.py list
    docker compose exec zhida python scripts/manage_users.py add --user alice --tenant tenantA

退出码：0 成功；1 存储/未知错误；2 参数非法；3 账号不存在；4 账号已存在；5 密码过弱。
"""
import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from core import user_store
from core.auth import hash_password, validate_password
from core.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_CONFLICT = 4
EXIT_WEAK_PASSWORD = 5

logger = logging.getLogger("audit")


def _audit(action: str, username: str, tenant: str = "-", result: str = "ok") -> None:
    logger.info(
        "account action=%s user=%s tenant=%s result=%s",
        action, username or "-", tenant or "-", result,
    )


def _read_password(provider) -> tuple:
    password = provider("密码: ")
    if not password:
        return None, "密码不能为空"
    if provider("再输入一次: ") != password:
        return None, "两次输入不一致"
    return password, None


async def _add(args, provider) -> int:
    password, error = _read_password(provider)
    if error:
        print(error, file=sys.stderr)
        _audit("add", args.user, args.tenant, "invalid")
        return EXIT_USAGE
    reason = validate_password(password, args.user)
    if reason:
        print(reason, file=sys.stderr)
        _audit("add", args.user, args.tenant, "weak_password")
        return EXIT_WEAK_PASSWORD
    try:
        await user_store.add_user(
            args.user, hash_password(password), args.tenant, force=args.force
        )
    except user_store.UserExistsError:
        print(f"账号已存在: {args.user}（如需覆盖请加 --force）", file=sys.stderr)
        _audit("add", args.user, args.tenant, "conflict")
        return EXIT_CONFLICT
    except ValueError as e:
        print(str(e), file=sys.stderr)
        _audit("add", args.user, args.tenant, "invalid")
        return EXIT_USAGE
    except Exception as e:
        print(f"写入账号失败: {e}", file=sys.stderr)
        _audit("add", args.user, args.tenant, "error")
        return EXIT_ERROR
    _audit("add", args.user, args.tenant, "ok")
    action = "覆盖" if args.force else "新增"
    print(f"已{action}账号 {args.user} tenant={args.tenant}")
    return EXIT_OK


async def _passwd(args, provider) -> int:
    password, error = _read_password(provider)
    if error:
        print(error, file=sys.stderr)
        _audit("passwd", args.user, result="invalid")
        return EXIT_USAGE
    reason = validate_password(password, args.user)
    if reason:
        print(reason, file=sys.stderr)
        _audit("passwd", args.user, result="weak_password")
        return EXIT_WEAK_PASSWORD
    try:
        version = await user_store.set_password(args.user, hash_password(password))
    except user_store.UserNotFoundError:
        print(f"账号不存在: {args.user}", file=sys.stderr)
        _audit("passwd", args.user, result="not_found")
        return EXIT_NOT_FOUND
    except ValueError as e:
        print(str(e), file=sys.stderr)
        _audit("passwd", args.user, result="invalid")
        return EXIT_USAGE
    except Exception as e:
        print(f"修改密码失败: {e}", file=sys.stderr)
        _audit("passwd", args.user, result="error")
        return EXIT_ERROR
    _audit("passwd", args.user, result="ok")
    print(f"已修改密码 {args.user}（版本 {version}）")
    return EXIT_OK


async def _set_tenant(args) -> int:
    try:
        version = await user_store.set_tenant(args.user, args.tenant)
    except user_store.UserNotFoundError:
        print(f"账号不存在: {args.user}", file=sys.stderr)
        _audit("set-tenant", args.user, args.tenant, "not_found")
        return EXIT_NOT_FOUND
    except ValueError as e:
        print(str(e), file=sys.stderr)
        _audit("set-tenant", args.user, args.tenant, "invalid")
        return EXIT_USAGE
    except Exception as e:
        print(f"修改租户失败: {e}", file=sys.stderr)
        _audit("set-tenant", args.user, args.tenant, "error")
        return EXIT_ERROR
    _audit("set-tenant", args.user, args.tenant, "ok")
    print(f"已修改租户 {args.user} -> {args.tenant}（版本 {version}）")
    return EXIT_OK


async def _remove(args) -> int:
    try:
        await user_store.remove_user(args.user)
    except user_store.UserNotFoundError:
        print(f"账号不存在: {args.user}", file=sys.stderr)
        _audit("remove", args.user, result="not_found")
        return EXIT_NOT_FOUND
    except Exception as e:
        print(f"删除账号失败: {e}", file=sys.stderr)
        _audit("remove", args.user, result="error")
        return EXIT_ERROR
    _audit("remove", args.user, result="ok")
    print(f"已删除账号 {args.user}")
    return EXIT_OK


async def _list() -> int:
    users = await user_store.list_users()
    if not users:
        print("（无账号）")
        return EXIT_OK
    for user in users:
        print(f"{user.username}\t{user.tenant}\tversion={user.version}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="知答账号管理")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="新增账号（重名默认拒绝）")
    add.add_argument("--user", required=True)
    add.add_argument("--tenant", required=True)
    add.add_argument("--force", action="store_true", help="账号已存在时覆盖")

    passwd = sub.add_parser("passwd", help="轮换密码（旧会话立即失效）")
    passwd.add_argument("--user", required=True)

    tenant = sub.add_parser("set-tenant", help="修改租户绑定（旧会话立即失效）")
    tenant.add_argument("--user", required=True)
    tenant.add_argument("--tenant", required=True)

    remove = sub.add_parser("remove", help="删除账号（旧会话立即失效）")
    remove.add_argument("--user", required=True)

    sub.add_parser("list", help="列出账号（不显示密码哈希）")
    return parser


async def _dispatch(args, provider) -> int:
    if args.command == "add":
        return await _add(args, provider)
    if args.command == "passwd":
        return await _passwd(args, provider)
    if args.command == "set-tenant":
        return await _set_tenant(args)
    if args.command == "remove":
        return await _remove(args)
    return await _list()


def main(argv=None, password_provider=getpass.getpass) -> int:
    setup_logging(settings.log_level)
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args, password_provider))
    except Exception as e:
        print(f"操作失败: {e}", file=sys.stderr)
        _audit(args.command, getattr(args, "user", "-"), result="error")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
