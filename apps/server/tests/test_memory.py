"""P32 记忆层测试：软删/禁抽守卫/抽取窗口/分轨装配/陈旧标注（store 级）。

API 面板契约测试在 test_memory_api.py；本文件直接打 MemoryStore 与
consolidate/memory_block，抽取 LLM 用脚本替身（不依赖真实 flash）。
"""

from __future__ import annotations

import json

from gewu.memory import (
    MAX_FACTS_IN_CONTEXT,
    MEM_BLOCK_HEADER,
    Fact,
    MemoryStore,
    consolidate,
    memory_block,
)


class _ExtractLLM:
    """consolidate 抽取替身：记录调用，按脚本回 JSON（脚本耗尽后复用末条）。"""

    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads
        self.calls: list[dict] = []

    def has_key(self) -> bool:
        return True

    def chat(self, messages, *, small=False, json_mode=False, temperature=0.0, max_tokens=2048):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        return self._payloads[min(len(self.calls), len(self._payloads)) - 1]


def _backdate(mem: MemoryStore, user: str, key: str, days: int, hours: int = 0) -> None:
    """回拨 updated_at（陈旧标注测试用；直接 SQL，绕过 upsert 的时间戳）。"""
    with mem._pool.connection() as conn:
        conn.execute(
            "UPDATE memory_fact SET updated_at = now() - make_interval(days => %s, hours => %s)"
            " WHERE user_id = %s AND key = %s",
            (days, hours, user, key),
        )


# ---------- 软删（P32-1）----------


def test_soft_delete_filters_all_reads(mem: MemoryStore):
    user = "u1@example.com"
    mem.upsert_facts(
        user, [Fact("profile", "major", "物理"), Fact("preference", "sport", "羽毛球")]
    )
    assert mem.delete_fact(user, "profile", "major") is True
    assert mem.delete_fact(user, "profile", "major") is False  # 已删不重复置位
    assert [f.key for f in mem.recent_facts(user)] == ["sport"]
    assert [f.key for f in mem.all_facts(user)] == ["sport"]
    assert [(f.kind, f.key) for f in mem.deleted_facts(user)] == [("profile", "major")]


def test_upsert_revives_soft_deleted(mem: MemoryStore):
    user = "u1@example.com"
    mem.upsert_facts(user, [Fact("profile", "major", "物理")])
    mem.delete_fact(user, "profile", "major")
    mem.upsert_facts(user, [Fact("profile", "major", "物理")])  # 面板重新加回=恢复
    assert [f.key for f in mem.recent_facts(user)] == ["major"]
    assert mem.deleted_facts(user) == []


def test_soft_delete_keys_only_hits_existing(mem: MemoryStore):
    user = "u1@example.com"
    mem.upsert_facts(user, [Fact("profile", "major", "物理"), Fact("profile", "gpa", "3.5")])
    assert mem.soft_delete_keys(user, ["major", "ghost", ""]) == 1  # 未命中/空串忽略
    assert [f.key for f in mem.recent_facts(user)] == ["gpa"]
    assert mem.soft_delete_keys(user, ["major"]) == 0  # 幂等：已删不重复计数


# ---------- consolidate 开眼 + forget 守卫（P32-1）----------


def test_consolidate_forget_guard(mem: MemoryStore):
    user, sess = "u1@example.com", "s1"
    mem.upsert_facts(user, [Fact("profile", "major", "计算机科学"), Fact("profile", "gpa", "3.5")])
    llm = _ExtractLLM(
        [
            json.dumps(
                {
                    "facts": [
                        {"kind": "profile", "key": "major", "value": "研究生"},
                        {"kind": "preference", "key": "sport", "value": "羽毛球"},
                    ],
                    "forget": ["gpa", "ghost"],  # ghost 不在现有清单→守卫忽略
                },
                ensure_ascii=False,
            )
        ]
    )
    consolidate(mem, llm, user, sess, "其实我是研究生，绩点那条别记了", "好的")
    keys = {f.key: f.value for f in mem.recent_facts(user)}
    assert keys == {"major": "研究生", "sport": "羽毛球"}
    assert [f.key for f in mem.deleted_facts(user)] == ["gpa"]
    system = llm.calls[0]["messages"][0][1]
    assert "该用户现有记忆" in system
    assert "profile/major：计算机科学" in system and "profile/gpa：3.5" in system
    assert llm.calls[0]["max_tokens"] == 300


def test_consolidate_forget_accepts_kind_prefixed_key(mem: MemoryStore):
    """flash 偶发把 forget 给成「kind/key」复合串（模仿清单行格式）——归一化后生效；
    同轮既 forget 又给新 value 时纠正语义获胜（先软删、upsert 恢复并写新值）。"""
    user, sess = "u1@example.com", "s1"
    mem.upsert_facts(user, [Fact("profile", "grade", "大二")])
    llm = _ExtractLLM(
        [
            json.dumps(
                {
                    "facts": [{"kind": "profile", "key": "grade", "value": "马上大三了"}],
                    "forget": ["profile/grade"],
                },
                ensure_ascii=False,
            )
        ]
    )
    consolidate(mem, llm, user, sess, "我记错了，我马上大三了", "好的")
    keys = {f.key: f.value for f in mem.recent_facts(user)}
    assert keys == {"grade": "马上大三了"}
    assert mem.deleted_facts(user) == []


def test_consolidate_drops_forbidden_facts(mem: MemoryStore):
    """面板删除后，抽取不得复活（禁抽清单进 prompt + facts 守卫丢弃双保险）。"""
    user, sess = "u1@example.com", "s1"
    mem.upsert_facts(user, [Fact("profile", "gpa", "3.5")])
    mem.delete_fact(user, "profile", "gpa")
    llm = _ExtractLLM(
        [json.dumps({"facts": [{"kind": "profile", "key": "gpa", "value": "3.8"}], "forget": []})]
    )
    consolidate(mem, llm, user, sess, "我绩点 3.8", "不错")
    assert mem.recent_facts(user) == []  # 不复活
    assert [f.key for f in mem.deleted_facts(user)] == ["gpa"]
    system = llm.calls[0]["messages"][0][1]
    assert "用户已删除的记忆" in system and "profile/gpa" in system


def test_consolidate_window_covers_recent_turns(mem: MemoryStore):
    user, sess = "u1@example.com", "s1"
    for i in range(1, 9):
        mem.append_episode(sess, user, "user", f"旧问{i}")
        mem.append_episode(sess, user, "assistant", f"旧答{i}")
    llm = _ExtractLLM(['{"facts":[],"forget":[]}'])
    consolidate(mem, llm, user, sess, "新问九", "新答九")
    user_msg = llm.calls[0]["messages"][1][1]
    # 窗口=最近 6 条 episodic（含本轮共 18 条中的 e13..e18=旧问7..新答九）
    assert "旧问7" in user_msg and "新答九" in user_msg
    assert "旧答6" not in user_msg and "旧问1" not in user_msg


def test_consolidate_malformed_json_silent(mem: MemoryStore):
    user, sess = "u1@example.com", "s1"
    llm = _ExtractLLM(["这不是 JSON"])
    consolidate(mem, llm, user, sess, "问", "答")
    assert mem.recent_facts(user) == []
    assert len(mem.recent_episodes(sess, 10)) == 2  # episodic 原文仍留存


# ---------- memory_block 装配（P32-2）----------


def test_memory_block_empty(mem: MemoryStore):
    assert memory_block(mem, "nobody@example.com", "s") == ""
    assert memory_block(None, "u", "s") == ""


def test_memory_block_header_and_age(mem: MemoryStore):
    user, sess = "u1@example.com", "s1"
    mem.upsert_facts(user, [Fact("profile", "major", "物理"), Fact("profile", "grade", "大三")])
    _backdate(mem, user, "major", days=2, hours=23)  # 2.96 天 → 标 2 天，避开毫秒竞态
    block = memory_block(mem, user, sess, question="我按哪个培养方案")
    lines = block.split("\n")
    assert lines[0] == MEM_BLOCK_HEADER.split("\n")[0]
    assert lines[1] == MEM_BLOCK_HEADER.split("\n")[1]
    assert "- profile/major：物理（2天前更新）" in lines
    assert "- profile/grade：大三" in lines
    assert "（0天前更新）" not in block  # 当天不标


def test_memory_block_under_quota_full_injection(mem: MemoryStore):
    user, sess = "u1@example.com", "s1"
    mem.upsert_facts(
        user, [Fact("profile", "major", "物理"), Fact("preference", "sport", "羽毛球")]
    )
    block = memory_block(mem, user, sess, question="无关问题")
    assert "profile/major：物理" in block and "preference/sport：羽毛球" in block
    assert "天前更新" not in block


def test_memory_block_episodes_still_injected(mem: MemoryStore):
    user, sess = "u1@example.com", "s1"
    mem.append_episode(sess, user, "user", "我想转专业")
    block = memory_block(mem, user, sess)
    assert "近期对话要点" in block and "用户：我想转专业" in block


def test_memory_block_tiered_beyond_quota(mem: MemoryStore):
    """>20 条触发分轨：profile 全量常驻 + 非核心词面命中优先 + 零重叠 recency 兜底。"""
    user, sess = "u1@example.com", "s1"
    facts = [Fact("profile", f"p{i}", f"画像{i}") for i in range(5)]
    facts += [Fact("preference", f"t{i:02d}", f"偏好{i:02d}") for i in range(20)]
    facts.append(Fact("preference", "astro", "天文社活动"))  # 共 26 条
    mem.upsert_facts(user, facts)
    block = memory_block(mem, user, sess, question="帮我看看天文社活动怎么报名")
    fact_lines = [ln for ln in block.split("\n") if ln.startswith("- ")]
    assert len(fact_lines) == MAX_FACTS_IN_CONTEXT
    joined = "\n".join(fact_lines)
    for i in range(5):
        assert f"profile/p{i}：画像{i}" in joined  # profile 常驻
    assert "preference/astro：天文社活动" in fact_lines[5]  # 命中项排非核心首位
    assert "preference/t00：偏好00" in joined  # 零重叠按 recency 兜底
    assert "t14" not in joined and "t19" not in joined  # recency 尾部被挤出
