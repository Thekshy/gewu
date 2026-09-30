# 10 · 评测体系

评测是这个仓库的方法论核心：「讲到哪必须是真有代码+测试+对比报告」。评测资产
与被测服务**跨语言隔离**（纯标准库 HTTP 客户端，契约靠 SSE 而非共享代码）。

## 数据集（eval/）

| 数据集 | 规模 | 断言方式 |
| --- | --- | --- |
| `dataset.jsonl` | 28 题：factual 7 / multi_hop 8 / refusal 3 / transaction 7 / hybrid 1（多轮 8 条） | gold_keywords + expected_docs 引用交集；transaction 断言**业务库真实状态** |
| `dataset-agent.jsonl` | 8 题 | expect 结构化断言（route/success/booking/ticket/answer_contains）；P14 起以 `--mode react` 跑 |
| `search-queries.jsonl` | 41 条 | 检索层 doc 级命中序列（对照实验用） |

交易型用例驱动完整多轮对话后查 `/api/business/overview` 断言预约单/请假单/
审批层级——比「答案里含 XX 字样」可信得多；权限拦截（学生调辅导员工具）与
冲突恢复（约满时段给可选项重问）都有专项用例。

## 运行口径

```bash
RATE_LIMIT_PER_MINUTE=600 make run          # 服务端
python3 eval/run_eval.py --tag <标签>        # 主集 28 题
python3 eval/run_eval.py --dataset eval/dataset-agent.jsonl --mode react --tag agent
MEMORY_CONSOLIDATE=off …                     # 全量评测隔离记忆固化（见 07）
```

报告（Markdown 指标表 + 逐题明细）落 `eval/reports/`，P 系列基线与 A/B 对照
全部留档；业务库断言前先 reset（残留预约会占「每人每天 2 时段」配额）。

## flaky 判定（无回归 ≠ 满分）

GLM 温度 0 仍非确定（flash 尤甚），评测失败集会漂移。仓库判据：**无回归 =
失败集不扩大**（相对已知 flaky 基线集），失败用例重跑确认——pass^k 思想。
已知 flaky：mtfact-002 / ag-know-002 / ag-tx-001~002（flash 路由漂移 + ReAct
偶发不落工具，见 eval/reports/P8-retire-baseline.md §4）。

## 方差基线归因法（P14-1 定案）

跨实现对照（如迁移前后）遇到指标漂移时，不能直接归因移植偏差——先测**自身
run-to-run 方差**：同实现跑两遍对照。判据：**跨实现差异 ≤ 自身方差 → 等价**。
实证（P14-1 检索对照）：Go↔Go 自身 15/41 ≈ Go↔Python 15/41（序列一致率），
叠加改写 5 连测实验（同查询 2 种改写输出）——漂移由 GLM 改写非确定性主导，
非移植偏差。存储函数级正确性由真库单测保障（同库同函数，理论逐条相等）。

## 评测覆盖的已知边界

- 检索层 golden set（recall@k 标注）未建——search-queries 只有 query/k；
- LLM-as-judge（忠实度打分）在 roadmap（M3）未落地；
- 重启续办（G3）为手动场景验证，未进自动评测。

## 相关文件

`eval/run_eval.py`（评测客户端：SSE 解析/断言/报告）、`eval/run_search_parity.py`
（检索对照与方差基线）、`eval/k6-chat.js`（压测）；门禁历史见
[P14 任务书 §6](../P14-langgraph-migration.md) 与 eval/reports/。
