#!/usr/bin/env python3
"""生成检索评测的口语化/同义改写变体（P15：roadmap M1「同义改写、口语化提问」扩充）。

对 dataset.jsonl 的 factual/multi_hop 题各生成 N 个学生口吻的变体查询，gold
（expected_docs）继承原题——零人工标注的难例扩充：变体更贴近真实提问方式，
检索难度真实上升。产物 eval/retrieval-queries.jsonl：{query, gold, src_id}。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "server"))

from gewu.config import Settings, load_dotenv  # noqa: E402
from gewu.llm.service import LLMService  # noqa: E402

EVAL_TYPES = ("factual", "multi_hop")
VARIANTS_PER_Q = 3

SYSTEM = """你是校园制度问答系统的检索评测数据扩充器。给定一道学生问题，生成 3 个语义等价的变体查询，用于测试检索的鲁棒性：
1. 口语化改写（学生日常口吻，如「挂科了咋办」「卡里没钱了去哪充」）；
2. 同义词替换（如 奖学金→补助金、寝室→宿舍、绩点→GPA）；
3. 缩略问法（省略主语宾语，只留核心词）。
要求：语义与原题严格等价（答案文档不变）；不引入新实体；每个变体 8~30 字。
只输出 JSON：{"variants": ["v1", "v2", "v3"]}"""


def main() -> int:
    load_dotenv()
    settings = Settings.load()
    if not settings.llm_api_key:
        print("需要 LLM_API_KEY（flash 小模型生成变体）")
        return 1
    llm = LLMService(settings)

    cases = [
        json.loads(line)
        for line in (REPO_ROOT / "eval" / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    out_lines: list[str] = []
    for row in cases:
        if row.get("type") not in EVAL_TYPES or not row.get("expected_docs"):
            continue
        try:
            raw = llm.chat(
                [("system", SYSTEM), ("user", row["question"])],
                small=True,
                json_mode=True,
                temperature=0.3,
                max_tokens=300,
            )
            variants = json.loads(raw[raw.find("{") : raw.rfind("}") + 1]).get("variants", [])
        except Exception as e:  # noqa: BLE001 - 单题失败不阻断
            print(f"[variants] {row['id']} 生成失败，跳过：{e}")
            continue
        for v in variants[:VARIANTS_PER_Q]:
            v = str(v).strip()
            if v:
                out_lines.append(
                    json.dumps(
                        {"query": v, "gold": row["expected_docs"], "src_id": row["id"]},
                        ensure_ascii=False,
                    )
                )
        print(f"  ✓ {row['id']} → {len(variants[:VARIANTS_PER_Q])} 个变体")

    out = REPO_ROOT / "eval" / "retrieval-queries.jsonl"
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"[variants] 共 {len(out_lines)} 条变体 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
