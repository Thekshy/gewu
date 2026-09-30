#!/usr/bin/env python3
"""P14-1 门禁：模型冒烟——主/小模型各一轮 chat + 一次查询向量化（真跑真 key）。

用法：cd apps/server && uv run python scripts/smoke_chat.py
输出 finish_reason / usage / 回答头部 / 向量维度，任一通道失败即非零退出。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gewu.config import Settings, load_dotenv  # noqa: E402
from gewu.llm.chat import parse_finish_reason, parse_usage  # noqa: E402
from gewu.llm.service import LLMService  # noqa: E402

MESSAGES = [
    ("system", "你是校园问答助手，用一句话回答。"),
    ("user", "图书馆周末开门吗？如果不确定请说不确定。"),
]


def main() -> int:
    load_dotenv()
    settings = Settings.load()
    if not settings.llm_api_key:
        print("[smoke] LLM_API_KEY 未配置，无法冒烟")
        return 1
    svc = LLMService(settings)

    for small in (False, True):
        label = "small" if small else "main"
        model = settings.llm_small_model if small else settings.llm_model
        try:
            msg = svc.chat_full(MESSAGES, small=small, max_tokens=256)
        except Exception as e:  # noqa: BLE001
            print(f"[chat:{label}] 失败：{e}")
            return 1
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        print(
            f"[chat:{label}] model={model} "
            f"finish_reason={parse_finish_reason(msg)!r} usage={parse_usage(msg)}"
        )
        print(f"[chat:{label}] content={content[:80]!r}")

    try:
        emb = svc.embed(["图书馆 开放时间"])
    except Exception as e:  # noqa: BLE001
        print(f"[embed] 失败：{e}")
        return 1
    print(f"[embed] mode={settings.embed_mode} model={settings.embed_model} dim={len(emb[0])}")
    print("[smoke] 全通道 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
