# P24 · 线上观测补盲与检索链路提速（任务书）

> **背景**：2026-10-01 用线上日志（`journalctl -u gewu-api`，session
> `8fb8b028`）取证「你知道学校食堂在哪买」一轮（14837ms，route=factual
> auto 路径）时发现三类**非语料**问题；语料缺口（15 篇无食堂/生活服务文档）
> 同轮确认，用户拍板**语料线暂缓**（量大优先级低），本票不含。三类问题：
>
> 1. **[llm] 埋点盲区**：agent 主循环（create_agent 内部 `model.invoke`）不
>    经过 `LLMService.chat_stream/chat_with_tools` 封装（service.py:116/130
>    只覆盖直答与 classic 路径），538b9cf 号称「直答与 agent 两入口」的
>    agent 侧实际漏了主循环——食堂轮日志只有 [rag]+[chat] 两行，无 [llm]。
> 2. **agent 自拟检索词丢原话 + 双重改写**：auto 路径下
>    `search_knowledge(query)` 的 query 由 LLM 自拟（agenttools.py:75），
>    用户原话措辞不进检索串；该关键词串再过 `Rewriter.expand()`
>    （retrieve.py:244）构成**双重改写**——线上实证：检索串
>    `食堂位置 就餐指南 食堂位置 用餐安排 就餐管理规定`，「食堂位置」×2
>    就是 rewriter 对已是关键词串的输入再改写产的重复词。
> 3. **延迟无归因**：14837ms 无 per-call 耗时数据。按代码推演的调用链：
>    guard flash（guardrails.py:69，GREETING_RE 之外每轮一次）→ agent 主
>    模型#1（工具决策）→ rewriter flash + embed + BM25/RRF + rerank flash
>    （POOL_N=20 候选 ×600 字 ≈12KB prompt）→ agent 主模型#2（终答）。
>    你好轮 GREETING_RE 快路径零工具、单次主模型调用 4479ms，标定了单次
>    主模型调用 ≈4.5s——**大头是两次主模型串行（≈9~10s），检索链 ≈2.5s，
>    guard ≈1.5s**。
>
> 认知修正（写进票防止误优化）：**auto 模式不走 L1 级联**——
> `mode_dispatch`（agent/graph.py:141）auto/react 直入 agent 子图，L1/L2
> 级联判定只在 classic 路径 route 节点（graph.py:107）。auto 的路由语义是
> guard 两段式 + `RouteEventMiddleware` 按工具轨迹合成 effective route。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 语料缺口 | **暂缓**（不进本票） | 用户 2026-10-01：语料量大、优先级低；log-report.sh 空命中清单持续挖着，攒够一批再补 |
| Q2 | auto+factual 走直答快路砍延迟 | **明确不做** | 推翻 P17 agent-first 核心设计（auto 全走 agent 单循环是论文对照实验自变量）；trade-off 用 P24-1 的 per-call 数据量化后写进对照报告更有价值 |
| Q3 | L1 级联提速 / guard 同问缓存 | **不做** | auto 不走 L1（本票认知修正）；classic 是对照组不动；guard 会话感知（"对话已在进行中"条款），同文本缓存有跨上下文误判风险，省 1.5s 不值得 |
| Q4 | rerank 候选池缩减 | **数据驱动、带评测门禁**（P24-4 可选段） | POOL_N 20→10 省约 0.5~1s，但可能掉检索分——必须 `make retrieval-eval` 复跑对比，掉分超阈即回退 |
| Q5 | 检索词丢原话的守卫形态 | **docstring 引导 + wrap_tool_call 代码闸**（deep_research 限次同款组合拳） | GLM 无视否定指令是已知坑（工具行为必须代码级兜底）；代码闸只在「完全丢词」时干预，避免口语原话摊薄 BM25 权重 |

## 1. 目标 / 非目标

**目标**

- **P24-1 观测**：`UsageRecordMiddleware.wrap_model_call`（mw.py:150）加
  per-call 一行打印——`[llm] agent主循环 ms=… <ctx_profile>`，复用
  service.py:29 的 `ctx_profile`（已兼容 BaseMessage 列表）；格式含
  `chars=` 使 log-report.sh 的 ctx_chars 聚合自动吃到 agent 主循环数据。
- **P24-2 检索链三改**：
  - `Retriever.search(query, k=0, *, expand=True)` 加参（retrieve.py:94）；
    `search_knowledge`（agenttools.py:88）与 `deep_research` 两处
    （agenttools.py:127、research.py:56）传 `expand=False`——工具路径
    query 已是 LLM 提炼的关键词串，跳过 rewriter 二次改写；classic/direct
    路径（graph.py:610）保持默认 `expand=True`（原话需要「最多→上限」类
    术语归一）。预期省一次 flash ≈1~1.5s，并消灭重复词。
  - `Rewriter.expand()` 输出去重：空格 split + `dict.fromkeys` 保序重组
    （对 expand=True 路径同样生效，纯清理无语义损失）。
  - `search_knowledge` docstring 加引导：「query 必须保留用户原话中的关键
    实体与数字，再补充政策术语」。
- **P24-3 检索词丢原话代码闸**：新增 `SearchQueryGuardMiddleware`
  （wrap_tool_call，挂 mw.py、装配进 agent.py）拦 `search_knowledge`：
  - 从 `request.state["messages"]` 取最后一条 HumanMessage 为原问题
    （request.state 在 ResearchLimitMiddleware mw.py:238 已有先例）；
  - CJK bigram 交集判定：原问题与工具 query 的二字滑窗 bigram 交集为空
    = 完全丢词 → `request.override(tool_call={**call, "args": {**args,
    "query": f"{question} {query}"}})` 拼原问题（override 改参是官方
    文档示范用法，langchain types.py:704）；交集非空（本例「食堂」保住）
    → 不干预；
  - deep_research 不拦（sub 是 plan 拆解产物，本来就不是原话）；
  - 判定用纯 bigram，不引分词器。
- **P24-4 延迟归因真跑**（数据段，不写新逻辑）：P24-1 落地后真跑三轮
  （直答/agent 检索/办理），journalctl 抓 per-call ms 表留档进 §5；
  **可选加菜**：若确认 rerank 是显著大头，POOL_N 20→10 + `make
  retrieval-eval` 复跑对比（门禁：主要指标掉 >2pp 即回退 20）。

**非目标**

- 不补语料（Q1）；不动 auto 的 agent-first 结构（Q2）；不动 classic 链路
  与 L1 级联（Q3）。
- 不做 [chat] 汇总的 llm_calls/llm_ms 扩展——wrap_model_call 无 state 写
  通道，需要 after_model/contextvars 桥，per-call print + log-report.sh
  聚合已满足排障需求，复杂度不值当（若后续需要再立票）。
- 不改 SSE 事件形状、检索分数标尺、rerank 阈值语义。

## 2. 设计与实现

### 2.1 P24-1 观测（纯 print，对齐 538b9cf 零逻辑改动原则）

```python
# mw.py UsageRecordMiddleware（改后全文）
def wrap_model_call(self, request, handler):
    t0 = time.monotonic()
    resp = handler(request)
    print(
        f"[llm] agent主循环 ms={int((time.monotonic() - t0) * 1000)} "
        f"{ctx_profile(request.messages)}",
        flush=True,
    )
    for m in resp.result:
        usage = getattr(m, "usage_metadata", None)
        if usage:
            self._llm.record_usage(int(usage.get("total_tokens", 0) or 0))
    return resp
```

- `ctx_profile` 从 llm/service.py 导入（mw.py 已 import 他域符号的先例：
  prompts/tx；llm 域对 agent 域无反向依赖，方向合规，lint-arch 过）。
- 四层埋点补全后覆盖面：[routing]（classic 路径）/[rag]/[llm]（直答 +
  classic 工具 + agent 主循环）/[chat] 汇总——食堂类 agent 轮从此四层齐。
- log-report.sh 零改动兼容：`chars=(\d+)` 正则直接吃到新行。

### 2.2 P24-2 检索链

- `search()` 签名与 expand 分支（retrieve.py:94-97）：

```python
def search(self, query: str, k: int = 0, *, expand: bool = True) -> list[Hit]:
    if k <= 0:
        k = self.k
    if expand:
        query = self.rewriter.expand(query)  # 口语 → 政策术语（无 key 时原样返回）
```

- 调用方三处改 `expand=False`：agenttools.py:88、agenttools.py:127、
  research.py:56；graph.py:610 不动。deep_research 的 plan() 产物与
  search_knowledge 的 LLM 自拟串同性质（已是提炼关键词）。
- `Rewriter.expand()` 去重（result 组装后）：

```python
result = " ".join(dict.fromkeys(result.split())) if result else result
```

- docstring 引导加在 search_knowledge 参数描述后（B2 代码闸兜底，docstring
  是软引导不是防线）。

### 2.3 P24-3 SearchQueryGuardMiddleware

```python
def _cjk_bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}  # 二字滑窗，够用免分词

class SearchQueryGuardMiddleware(AgentMiddleware):
    """search_knowledge 完全丢原词时代码兜底拼回原问题（GLM 无视指令坑，
    docstring 引导是软防线，本件是硬防线；deep_research 不拦：sub 是拆解
    产物本非原话。只在 bigram 交集为空时干预，避免口语摊薄 BM25 权重。）"""

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if call.get("name") != "search_knowledge":
            return handler(request)
        args = call.get("args") or {}
        query = str(args.get("query", "") or "")
        question = ""
        for m in reversed(request.state.get("messages") or []):
            if isinstance(m, HumanMessage):
                question = str(m.content)
                break
        if query and question and not (_cjk_bigrams(question) & _cjk_bigrams(query)):
            call = {**call, "args": {**args, "query": f"{question} {query}"}}
            print(f"[rag] 检索词与原问题零重合，已拼回原问题：q={query!r}")
            return handler(request.override(tool_call=call))
        return handler(request)
```

- 装配位：agent.py middleware 列表 `ResearchLimitMiddleware()` 之后、
  `RouteEventMiddleware()` 之前（wrap_tool_call 链与其他件无交互，顺序
  不敏感，就近检索相关件放）。
- 行为示例：原话「体育挂科了怎么办」+ query「补考安排」→ bigram 交集空 →
  拼接；原话「你知道学校食堂在哪买」+ query「食堂位置 就餐指南」→「食堂」
  bigram 命中 → 不动（线上实例属于后者，闸不触发，靠 docstring 演进）。

### 2.4 P24-4 真跑与可选加菜

- 真跑剧本（线上或本地一致）：①直答：图书馆借书上限（direct 模式）；
  ②agent 检索：食堂轮同题复放；③办理：VE-0271 全链。各抓
  journalctl 四层行留档，重点 per-call ms 表与检索串重复词消失确认。
- 可选加菜（POOL_N 缩减）独立成段执行：改 retrieve.py:17 常量 → 全量
  `make retrieval-eval` 对比上一基线 → 主要指标（recall@5 / nDCG）掉
  >2pp 回退，否则留档通过。**此段可整体跳过**，不影响前三段验收。

## 3. 验收门禁

1. `make lint`（ruff）+ `make test`（174 基线 + 新增）+ `make lint-arch`
   全绿；新增测试至少覆盖：expand=False 跳过 rewriter（mock rewriter 断言
   未调用）/ expand 输出去重 / 代码闸两分支（零重合拼接、有重合放行）/
   deep_research 不被拦。
2. 真跑：§2.4 剧本三轮，[llm] agent主循环行出现且 ms 合理、食堂轮检索串
   无重复词、classic 直答轮行为不变（expand=True 路径回归）。
3. 若执行 §2.4 可选段：retrieval-eval 对比表留档。
4. 留档：本文件 §5 执行记录 + roadmap 勾选。

## 4. 风险与回滚

| 风险 | 缓解 | 回滚 |
|------|------|------|
| expand=False 后 agent 关键词串缺术语归一（LLM 写「最多」语料是「上限」） | docstring 引导用政策术语；P24-3 闸在完全丢词时拼回原话；retrieval-eval 抽查 | 三处调用方恢复默认 expand=True（一行级） |
| 代码闸误拼（LLM 故意转写同义检索词被判零重合） | 拼接是「增补」不是「替换」，原 query 保留在串内，召回只增不减；[rag] 打点可见拼接行为 | mw 装配行移除即退场 |
| per-call print 在长会话多轮时刷屏 | 单轮主循环 ≤8 次调用（AGENT_MAX_TURNS），量级与 classic 的 [llm] 行同档 | — |
| POOL_N 缩减掉检索分 | 评测门禁 2pp 阈值 | 常量回 20 |

## 5. 执行记录（2026-10-01 执行完毕）

**改动落点**：mw.py（UsageRecordMiddleware per-call 埋点 + `_cjk_bigrams` +
SearchQueryGuardMiddleware）、agent.py（装配 + 件清单 docstring）、
agenttools.py（expand=False ×2 + docstring 引导）、research.py
（expand=False）、retrieve.py（`search(expand=)` 参数 + Rewriter 去重）。

**门禁**：ruff 全绿 + lint-arch 全绿 + pytest **212 passed**（新增 7 测：
expand 跳改写/去重/埋点打印/代码闸三分支）。执行中段全量曾 3 败——
test_auth.py 夹具属**并行 P22 会话半成品**（两轮运行间其失败集合在收敛，
memory_api/chat_api 先行转绿），收尾复跑 212 全绿，非 P24 债。

**真跑**（线上 117.72.163.14，rsync 5 文件 + restart；原文件备份
`/root/gewu-backup-p24`，回滚=cp 回+restart）：

| 轮 | 结果 | 关键断言 |
|---|---|---|
| 直答 图书馆借书上限 | 5265ms completed | [rag] q=`图书馆借书最多能借几本 图书馆 图书 外借 数量 上限`——expand=True 路径回归 ✓；[llm] 直答上下文 ✓ |
| agent 检索（食堂同题） | 14416ms completed | **[llm] agent主循环 ×2**：`ms=5026 msgs=1 chars=10`（工具决策）/ `ms=3112 msgs=3 chars=460`（终答）——盲区补上 ✓；[rag] q=`学校食堂位置 在哪`（改前 `食堂位置 就餐指南 食堂位置 用餐安排 就餐管理规定`）双重改写与重复词消失 ✓；代码闸有重合正确放行（零重合分支单测覆盖）✓ |
| 办理 预约→确认 | 5941ms pending_action（args 齐）→ 4431ms action_result **VE-0272** 落库 | 新中间件不干扰写工具/HITL resume 链 ✓ |

**数据结论**（per-call 归因首次落地）：食堂轮 14416ms 中两次主模型串行
调用 8138ms（56%）——agent-first 结构性成本坐实，Q2 拍板不动；rewriter
省掉的一次 flash 在主模型抖动噪声内（14837→14416 不可归因）。§2.4 可选
段 POOL_N 缩减**未执行**：检索链（guard+search≈6.3s）里 rerank 占比需
log-report.sh 聚合多日数据再判，不达标不加菜。

**坑与备忘**：①P22 会话并行执行中，test_api.py 被共编（我的 FakeRetriever
签名对齐 1 行 + 其 sess 夹具一片）——commit 严格 pathspec 且**剔除共编
文件**，1 行改动随 P22 提交顺路带走；②真跑管道里 SSE answer_delta 解析
显示空（事件流本身正常，前端不受影响），验证一律以 journalctl
[chat].answer_head 为准；③服务器部署是 rsync 直推（非 git），文件级备份
先行的流程本票留样。
