"""auth 管理工具（P21）：邀请码发放 / 管理员提权（内测期 CLI，后台 UI 属 P23）。

用法（Makefile 薄封装）：
  make invite USES=10 DAYS=14 NOTE=内测一批   # 生成并打印邀请码
  make admin EMAIL=a@b.com                    # 已注册账号提权为 admin
"""

from __future__ import annotations

import argparse
import sys

from gewu.auth.store import AuthStore
from gewu.config import Settings, load_dotenv


def main() -> int:
    load_dotenv()
    settings = Settings.load()
    store = AuthStore(settings.pg_dsn)
    parser = argparse.ArgumentParser(description="gewu auth 管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_invite = sub.add_parser("invite", help="生成邀请码")
    p_invite.add_argument("--uses", type=int, default=1, help="可用次数（默认 1）")
    p_invite.add_argument("--days", type=int, default=None, help="有效天数（缺省永久）")
    p_invite.add_argument("--note", default="", help="备注（发放批次）")

    p_admin = sub.add_parser("admin", help="提权为 admin")
    p_admin.add_argument("--email", required=True)

    args = parser.parse_args()
    try:
        if args.cmd == "invite":
            code = store.create_invite(
                uses=args.uses, days=args.days, note=args.note, created_by="cli"
            )
            print(
                f"[auth] 邀请码：{code}（{args.uses} 次"
                f"{'，' + str(args.days) + ' 天有效' if args.days else ''}）"
            )
        elif args.cmd == "admin":
            if store.promote_admin(args.email):
                print(f"[auth] 已提权：{args.email} → admin")
            else:
                print(f"[auth] 无此邮箱（{args.email}）——请先让其注册再提权")
                return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
