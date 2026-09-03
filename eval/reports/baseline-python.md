# Python 基线报告（重构对照基线 · 冻结版）

> 由 `tag: python-final`(commit `01dea6a`)的 Python(FastAPI) 实现进程内直跑产出。
> 本文件是 Go 重构版的行为对照基线；Go 版同数据集结果见
> [rewrite-go-vs-python.md](./rewrite-go-vs-python.md)。
> 运行环境:macOS arm64,glm-5.3(主答案)+ glm-5.3-flash(辅助调用)。
> 索引:data/index.db 为 **仅 BM25**(15 篇 / 22 chunk,2026-08-28 以 no-embed 方式入库,
> 向量表为空)——检索条件与 2026-08-28 首次 26/26 基线完全一致;Go 对照跑使用同一索引文件,
> 检索侧变量受控。

- 时间：2026-09-03 09:14
- 模型：glm-5.3（LLM 启用）
- 数据集：26 题（factual 7，multi_hop 8，refusal 3，transaction 7，hybrid 1）

| 类型 | 通过率 | 关键词命中 | 引用召回 | 平均延迟 |
| --- | --- | --- | --- | --- |
| factual | 7/7 | 7/7 | 7/7 | 5944ms |
| multi_hop | 8/8 | 8/8 | 8/8 | 34200ms* |
| refusal | 3/3 | - | - | 1383ms |
| transaction（办理） | 7/7 | - | - | 5576ms |
| hybrid（问答+办理） | 1/1 | - | - | 14347ms |

\* multi-006 单题 201.7s(模型端点该次请求长时间无响应,偶发抖动);
剔除该题后 multi_hop 平均 ≈ 10.3s。对照报告的延迟对比将同时给出含/不含该样本的数字。

## 明细

| ID | 类型 | 多轮 | 通过 | 延迟 | 说明 |
| --- | --- | --- | --- | --- | --- |
| fact-001 | factual | - | ✓ | 5232ms |  |
| fact-002 | factual | - | ✓ | 6123ms |  |
| fact-003 | factual | - | ✓ | 6450ms |  |
| fact-004 | factual | - | ✓ | 7233ms |  |
| fact-005 | factual | - | ✓ | 5944ms |  |
| fact-006 | factual | - | ✓ | 5369ms |  |
| fact-007 | factual | - | ✓ | 5256ms |  |
| multi-001 | multi_hop | - | ✓ | 6511ms |  |
| multi-002 | multi_hop | - | ✓ | 12425ms |  |
| multi-003 | multi_hop | - | ✓ | 15632ms |  |
| multi-004 | multi_hop | - | ✓ | 11930ms |  |
| multi-005 | multi_hop | - | ✓ | 7746ms |  |
| multi-006 | multi_hop | - | ✓ | 201722ms | 端点抖动,见上注 |
| multi-007 | multi_hop | - | ✓ | 6704ms |  |
| multi-008 | multi_hop | - | ✓ | 10929ms |  |
| refu-001 | refusal | - | ✓ | 1636ms |  |
| refu-002 | refusal | - | ✓ | 1378ms |  |
| refu-003 | refusal | - | ✓ | 1135ms |  |
| tx-001 | transaction | ✓ | ✓ | 4202ms | routes=transaction |
| tx-002 | transaction | ✓ | ✓ | 7608ms | routes=transaction |
| tx-003 | transaction | ✓ | ✓ | 10455ms | routes=transaction |
| tx-004 | transaction | ✓ | ✓ | 9028ms | routes=transaction |
| tx-005 | transaction | ✓ | ✓ | 5378ms | routes=transaction |
| tx-006 | transaction | ✓ | ✓ | 1256ms | routes=transaction |
| tx-007 | hybrid | ✓ | ✓ | 14347ms | routes=hybrid,transaction |
| tx-008 | transaction | ✓ | ✓ | 1105ms | routes=transaction |

分链路延迟(供 P50/P95 对照):直答(factual)合计 41.6s,P50 5.9s;
深研(multi_hop)合计 273.6s(剔除 multi-006 后 71.9s,P50 10.9s);
办理(transaction+hybrid)合计 53.4s,transaction P50 5.4s。
