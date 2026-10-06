"""游客清理工具（P39）：过期游客影子用户及其下游数据回收（CLI）。

用法（Makefile 薄封装）：
  make guest-prune            # 按 .env 的 GUEST_SESSION_TTL_DAYS 作为清理年龄
  make guest-prune DAYS=30    # 显式指定保留天数

线上节奏：展示站流量级手动即可（答辩前/每周一次）；要自动化可挂 systemd timer。
"""

from __future__ import annotations

import argparse
import sys

from gewu.config import Settings, load_dotenv
from gewu.maintenance import prune_guests


def main() -> int:
    load_dotenv()
    settings = Settings.load()
    parser = argparse.ArgumentParser(description="gewu 游客数据清理")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="清理创建早于 N 天的游客（缺省取 GUEST_SESSION_TTL_DAYS 配置）",
    )
    args = parser.parse_args()
    days = args.days or settings.guest_session_ttl_days
    counts = prune_guests(settings.pg_dsn, days=days)
    if not counts.get("users"):
        print(f"[prune] 无早于 {days} 天的游客，未动数据")
        return 0
    detail = "、".join(f"{k}={v}" for k, v in counts.items())
    print(f"[prune] 已清理 {counts['users']} 个游客（{detail}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
