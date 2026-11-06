"""生成 AUTH_USERS 用的密码哈希。

用法：
    python scripts/hash_password.py --user alice --tenant tenantA
    # 按提示输入密码（不回显），输出一行配置，粘到 .env 的 AUTH_USERS
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.auth import hash_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--tenant", required=True)
    args = parser.parse_args()

    password = getpass.getpass("密码: ")
    if len(password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        sys.exit(1)
    confirm = getpass.getpass("再输入一次: ")
    if password != confirm:
        print("两次输入不一致", file=sys.stderr)
        sys.exit(1)

    print(f"{args.user}:{hash_password(password)}:{args.tenant}")


if __name__ == "__main__":
    main()
