# P41 检索精排头切换：百炼 qwen3.7-text-rerank（任务书）

> **背景（2026-10-06 五引擎对照实测，报告
> `gewu-laya/eval/reports/rerank-engines-20261006-full.md`）**：用 135 题冻结候选池
> （BM25+向量 RRF 并集，25~40 chunk/池，消费逻辑逐行镜像 `Retriever.search` +
> `_rerank_or_keep`）对照五种精排方案，qwen3.7-text-rerank 全场最优：
>
> | 引擎 | Recall@6 | MRR | NDCG@6 | p50 | p95 | 降级率 | 空命中 |
> |---|---|---|---|---|---|---|---|
> | RRF 原序（no-rerank） | 0.8568 | 0.7605 | 0.7543 | 0 | 0 | — | 0 |
> | flash（现生产 LLMReranker） | 0.8938 | 0.7823 | 0.7917 | 2682ms | 8022ms | **12.2%** | 0 |
> | flash_hard（工程加固版） | 0.8963 | 0.8007 | 0.8028 | 2810ms | 9736ms | 4.1% | 0 |
> | **bailian（本票目标）** | **0.9012** | 0.7994 | 0.7948 | **251ms** | **414ms** | **0** | 0 |
> | decision（决策模型） | 0.8728 | 0.6944 | 0.7203 | 5462ms† | 6563ms† | 0 | 0 |
>
> flash 的 12.2% 降级是系统性的：30+ 编号候选下 LLM 稳定多数一个分（33/32、
> 35/34 型），`json_object` 只保 JSON 语法合法、锁不住数组长度，数量约束只活在
> 提示词文字里；工程加固（max_tokens 放宽+重试）天花板低且把 p95 推到 9.7s。
> bailian 按 index 结构化返回故零对齐问题，延迟为 flash 的 1/11，计价按 token
> 目录价 ¥0.8/M 量级、全量 135 池一轮 ~¥1。§4 判据（质量 ≥ flash−2pp 且
> p95 ≤ flash 的 1/3）双双超标（+0.74pp / 1/19）。
> 编号说明：P38 预留评测体系四票（未立项），P39 游客、P40 语料已闭，本票取 P41。

## 0. 拍板结论

| # | 问题 | 拍板 | 依据 |
|---|------|------|------|
| Q1 | 精排头选型 | **qwen3.7-text-rerank（百炼 rerank API）替换 LLMReranker(flash) 作生产精排头**，失败降级链语义不变（退 RRF 原序） | 五引擎对照全场最优（R@6 第一/零降级/快 11 倍）；专用 reranker 是文本相关性判别的正解，通用 LLM 的价值在改写与综合不在打分 |
| Q2 | 开关形态 | `RERANK_MODE` 从 `on\|off` 扩为 **`flash\|bailian\|off`**，**`on` 保留为 flash 的历史别名**（存量 .env/文档不断链）；代码缺省 flash = 零行为变化，bailian 经服务器 .env 显式开启 | 行为开关+灰度+可回退是本仓库既定机制（AGENTS.md：引入开源/外部方案经行为开关接入，缺省不改现有路径）；线上 `RERANK_MODE=on` 一行回退 |
| Q3 | 降级链 | **bailian 失败直落 RRF 原序，不中间垫 flash**；`BailianReranker.rerank` 只负责抛异常，降级由 `_rerank_or_keep` 现有 try/except 兜底，不新增降级代码 | 实测降级率 0；垫 flash 会引入意外 LLM 成本与 8s 级 p95；现有 `print(f"[rag] rerank 失败…")` 惯例即观测面 |
| Q4 | 凭证来源 | **只读环境变量 `DASHSCOPE_API_KEY`**；gewu-laya 参考实现里回落 `~/.bailian/config.json` 的逻辑**不搬进生产代码**（服务器无此文件；对齐 `IQS_API_KEY` 空值关断惯例） | key 严禁入库/入日志；本地 .env 手工补 key 即可调试 |
| Q5 | HTTP 客户端 | **httpx + 可注入 client（`httpx.MockTransport` 测试）**，不用参考实现的 `urllib` | 对齐 `gewu/websearch.py` 既有惯例（`client or httpx` 注入缝，test_websearch.py 同款测法） |
| Q6 | 阈值与复合分 | **`RERANK_THRESHOLD=2.0`、复合分 0.7×(分/10)+0.3×融合分、×0.7 重试、保底 top1 全部不动**；分数映射 = `relevance_score × 10` | 实测 135 池过阈值 2.0 后空命中 0；消费逻辑零改动 = 对照结论直接可迁移 |
| Q7 | 候选池 | **保持 RRF 并集不截断**（top_n=候选全量 n，禁传 k） | 模型上限 500 候选，实测池 25~40 无压力；截断会丢低分候选使 index 映射缺项 |
| Q8 | 请求 instruct | **携带（文案见 §2.1，与 RERANK_SYSTEM 的 10/5/0 锚点语义对齐），勿省略** | 评测口径即含 instruct 的结果，省略会分数分布漂移、阈值语义失效 |
| Q9 | 精排 passage 口径 | **缺省 body（=评测已验证口径，零变化），新增 `RERANK_PASSAGE=body\|titled` 实验开关**；titled 臂验收留档，增益 ≥+1pp 再翻缺省 | WeKnora 对照发现（§2.6）：gewu 嵌入拼 title+面包屑（`ingest.embed_content`）而精排只喂裸 body，向量靠标题召回的块会被精排降分滤掉——WeKnora ModelPassage 的原教训 |

## 1. 目标 / 非目标

**目标**

- `BailianReranker` 落地 `apps/server/gewu/rag/retrieve.py`，接口与 `LLMReranker`
  同形（`rerank(query, texts) -> list[float]`，0-10 标尺），生产与评测共用一个
  构造收口 `build_reranker()`。
- `RERANK_MODE` 三态 + 别名，非法值启动即报错（fail-fast，对齐 P6 强制 key 惯例；
  现状 `!= "off"` 会把拼错值静默当 on，顺手加固）。
- 评测脚本可一键切换引擎并量化延迟：`run_retrieval_eval.py` 加 `--rerank-mode`，
  报告新增 `rerank_engine` / rerank 跳延迟 p50/p95 / fallback 计数字段。
- `RERANK_PASSAGE` 实验开关落地（Q9/§2.6，缺省 body 零变化），为 titled 口径
  对照铺路。
- PARITY 留档：新增 §0.12 契约节 + 配置表更新（行为语义变更必须进冻结契约）。

**非目标**

- 不动改写层（P40 撞坑 #1 的 rewriter fail-open 缺陷另挂 roadmap，本票不夹带）。
- 不做 flash 质量加固（flash_hard 的 max_tokens+重试不落地——flash 降级为后备
  开关，若百炼长期稳定可评估直接退役）。
- 不动阈值/复合分/父子扩展/召回池参数（Q6/Q7）。
- 不做 rerank 调用的 span 级埋点与 token 记账（百炼计费走阿里云控制台，不进
  `DAILY_TOKEN_BUDGET`；P27 trace 覆盖面不扩）。
- 不截断候选池、不引入 gte-rerank-v2 等第二引擎对照（`BAILIAN_RERANK_MODEL`
  留了换型口子，真要换再评）。

## 2. 设计与实现

### 2.1 `BailianReranker`（retrieve.py 新增，与 LLMReranker 并排）

```python
BAILIAN_RERANK_TIMEOUT = 5.0  # 实测 p95 414ms，>10x 余量；超时即异常走降级

BAILIAN_INSTRUCT = (
    "为校园制度查询评估候选段落相关性：直接包含回答查询所需核心条款/数字/流程"
    "的段落给最高分，主题相关但仅为背景信息的居中，与查询无关的给零分。"
)

class BailianReranker:
    def __init__(self, api_key: str, *, endpoint: str, model: str = "qwen3.7-text-rerank",
                 client=None, timeout: float = BAILIAN_RERANK_TIMEOUT) -> None: ...
    def rerank(self, query: str, candidates: list[str]) -> list[float]: ...
```

要点（每条都有出处，勿凭感觉改）：

1. **请求走嵌套契约**（qwen3.7-text-rerank / gte-rerank-v2；`qwen3-rerank` 才是
   compatible 平铺契约，别用混）：
   `POST {endpoint}/api/v1/services/rerank/text-rerank/text-rerank`，
   `Authorization: Bearer $DASHSCOPE_API_KEY`，
   body = `{"model": …, "input": {"query": query, "documents": docs},
   "parameters": {"top_n": len(docs), "return_documents": false, "instruct": BAILIAN_INSTRUCT}}`。
2. **响应**：`output.results[].{index, relevance_score}`；分数 ∈ [0,1] 且是
   **请求内相对分**（官方声明不可跨请求比较），映射 `分数 = relevance_score × 10`。
   注意 DashScope **部分失败以 HTTP 200 + 顶层 `code`/`message` 返回**（WeKnora
   适配器同款注释）——解析时先查 `code` 非空即抛 `ValueError(code, message)`，
   再看 results，否则降级日志只剩「数量不符」看不出真实原因。
3. **空串候选换空格**（`t or " "`）：`_rerank_or_keep` 对缺失行喂的是 `""`，API 拒空串。
4. **空候选返 `[]`**（对齐 LLMReranker；`len(fused) <= k` 时根本不会进精排，双保险）。
5. **响应校验从严，非法即抛 ValueError 交现有降级链**——对齐 `parse_scores` 的
   严格惯例，不静默补 0（补 0 会让缺失候选被阈值过滤，掩盖契约漂移）：
   - `results` 数量 ≠ n（top_n=n 下应全量返回）；
   - `index` 越界或重复；
   - `relevance_score` 缺失 / 非数值 / 超 [0,1]（超界 ×10 会打破复合分标尺）；
   - 顶层既无 `output.results` 也无 `results`。
6. **单次调用不重试**：超时/HTTP 非 2xx/校验失败一律抛异常，由
   `_rerank_or_keep` 的 `except` 打印 `[rag] rerank 失败，退回 RRF 粗排顺序`
   后回退 `fused[:k]`。重试只会拉长关键路径（flash_hard 教训：重试把 p95 从
   8s 推到 9.7s）。
7. HTTP 用 `httpx`（`client or httpx` 注入缝，同 websearch.py），**不要 urllib**
   （无连接复用、测试要真网或 monkey-patch）。
8. key 只从构造参数进，不读文件不读全局；异常消息里不得含 key 或完整请求体。

### 2.2 配置（config.py + .env.example）

`Settings` 新增三字段（load 映射同步）：

| 字段 | env | 缺省 | 语义 |
|---|---|---|---|
| `dashscope_api_key` | `DASHSCOPE_API_KEY` | `""` | 百炼凭证；空 = bailian 模式不可用（§2.3 关断语义） |
| `bailian_rerank_endpoint` | `BAILIAN_RERANK_ENDPOINT` | `https://llm-wu8666v3ftxa4dsf.cn-beijing.maas.aliyuncs.com` | 冒烟验证过的工作空间域名；公网 `https://dashscope.aliyuncs.com` 同路径可作备选，切换只改 env |
| `bailian_rerank_model` | `BAILIAN_RERANK_MODEL` | `qwen3.7-text-rerank` | 换引擎（如 gte-rerank-v2）不动代码 |
| `rerank_passage` | `RERANK_PASSAGE` | `body` | 精排候选拼装口径：body（裸正文，已验证缺省）\| titled（title+面包屑+正文，对齐 `embed_content`，WeKnora 对照实验，§2.6） |

`rerank_mode` 解析改为：合法值 `flash | bailian | off | on`（`on` 归一为 flash，
dataclass 内只存归一后值）；**其余值 `Settings.load` 即抛错**，报错文案列出合法枚举。
`.env.example` 检索参数节更新（RERANK_MODE 注释改三态说明 + 上表四个新变量，key 行
只留空占位不写值）。

### 2.3 构造收口 `build_reranker()`（retrieve.py）

```python
def build_reranker(mode, llm=None, *, api_key="", endpoint=None, model=None):
    """RERANK_MODE → 精排器实例；off/缺凭证返 None。app 与评测共用，别各写一份。"""
```

- `flash`：`LLMReranker(llm)`（llm 缺失/无 key 返 None——现状语义）；
- `bailian`：`api_key` 为空 → **print 告警后返 None（RRF 直跑）**，不崩不偷换
  flash（缺凭证关断，对齐 `IQS_API_KEY` 空值整链关闭惯例）；
- `off` / 未知值：返 None / 抛 ValueError。

接线改动两处：`api/app.py::_build_retriever` 与 `eval/run_retrieval_eval.py` 都改调
`build_reranker`（app 传 settings 四值；eval 由 `--rerank-mode` 覆盖 settings 值）。
`Retriever.__init__` 的 `reranker` 参数类型注解放宽为 `LLMReranker | BailianReranker | None`
（两者已同构，无接口改动）。

### 2.4 评测脚本扩展（run_retrieval_eval.py）

- 新增 `--rerank-mode {flash,bailian}`（缺省跟随 settings，覆盖 `--no-rerank` 之外
  的引擎选择；`--no-rerank` 保留为 off 快捷方式）。
- rerank 跳延迟：harness 侧包一层计时（只计 `reranker.rerank` 调用，不含召回/改写），
  报告 JSON 与控制台加 `rerank_latency_ms: {p50, p95}`。
- fallback 计数：同层包装统计 rerank 抛异常次数，进报告 `rerank_fallbacks`。
- 报告加 `rerank_engine` 字段（flash/bailian/off），留档可辨。

### 2.5 PARITY.md 留档

- 新增 **§0.12 P41 精排引擎切换契约**：RERANK_MODE 枚举与 on 别名、bailian 分数
  映射（relevance_score×10，请求内相对分声明）、降级链（异常→RRF 原序，无中间垫）、
  key 空关断语义、instruct 常量、延迟/降级率观测口径（[rag] 日志三指标）。
- §12 配置表 `RERANK_MODE` 行更新：`flash / 2.0`，注释列写明三态+别名。

### 2.6 WeKnora 对照吸收（2026-10-06 增补，读码 `~/Project/WeKnora`）

WeKnora 的 rerank 分两层：`internal/reranking`（消费层：阈值退化/top1 保底/复合分）
+ `internal/models/rerank`（协议层：多厂商适配）。逐点对照结论：

**已印证本任务书既有拍板**（不重复改）：

- `top_n = len(documents)` 全量返回：WeKnora 注释原话「top_n is not optional in
  this shape: leaving it at zero would ask for no documents at all」——与 Q7 同结论；
- index 越界即整单报错（协议层 `dashscoperank/client.go` 与 `protocol.go` 双处
  硬检查）——与 §2.1 要点 5 的从严立场一致；
- 阈值 ×0.7 退化 + 保底 top1 需过下限：gewu P15 本就镜像此语义（WeKnora
  `DefaultThreshold=0.2 / degradeFactor=0.7 / FallbackMinScore=0.15`，换到 0-10
  标尺即 gewu 的 2.0 / ×0.7 / 1.5），本票不动（Q6）；
- 「搞错分数标尺，阈值就失去意义」（WeKnora `ScoreScale` 注册表 + NIM logit→
  sigmoid 归一的存在理由）——与 relevance_score×10 映射 + 相对分警示同课。

**本票吸收（新增）**：

1. **200+code 失败语义**（已进 §2.1 要点 2）：body 层 `code` 优先于 results 检查。
2. **golden test 用官方文档响应原样**：WeKnora 把 DashScope 文档示例（含
   `document.text` 回显、乱序 index、4 位小数分数）钉成解码器测试。本票 §3
   正常态 mock 响应直接采用官方示例形状，防解析器漂移。
3. **`RERANK_PASSAGE=body|titled` 实验开关（Q9，缺省 body）**：WeKnora
   `ModelPassage = 文档标题 + 标题面包屑 + 降噪正文`，注释给出实锤案例——同一
   段落无标题打 0.002、带标题 0.45，且理由与 gewu 完全同构：**标题面包屑当初
   就是跟着块一起嵌入的**（gewu `ingest.embed_content` 拼装 title+section_path+
   body），向量能靠标题召回，裸 body 打分的精排却认不出来，等于精排在系统性
   撤销向量侧的标题匹配。titled 模式下 `_rerank_or_keep` 按同款形状拼候选
   （复用 `embed_content`；若 retrieve→ingest 出现循环依赖则把函数下沉到共享
   位置，title 经 `doc_meta_map` 批量取，`_expand_to_parents` 已有同款取法）。
   开关引擎无关（flash 同样受益），缺省 body = 五引擎对照的已验证口径。

**明确不吸收 + 理由**：

- **分批发送 + 并发合并**（`SplitBatches`/errgroup）：gewu 池 25~40 « 500 上限
  用不上；且 DashScope 分数是**请求内相对分**，跨批合并可比性存疑（WeKnora 假设
  同查询各批可比——对 gte 类绝对分成立，对声明相对分的模型是隐患）。gewu 单批
  结构性避开此坑，永远不要为省延迟引入分批。
- **超长文档尾部截断**（`fitPassages`/`MaxPassageRunes`）：防「一个大 OCR 块
  拖死整单」的教训值得记，但 gewu 子块 ≤200~800 rune 远低于文档上限，现状无险；
  若日后语料带表格/长附件再启用。
- **markdown 降噪**（`CleanPassage` 十二步正则）：gewu 语料是条款体纯文本几乎
  无 markdown 噪声，收益不成比例。
- **MMR 多样性选择**（TopK 用 MMR 挑、EnrichedPassage 刻意去标题防同文档聚簇）：
  gewu 父子扩展去重已覆盖主要重复面；同文档父块挤占 top-6 真成为指标瓶颈时再议
  （挂账 §6）。
- **逐跑诊断日志**（outcome 枚举 + 计数进每跑 log）：观测价值真实，但 gewu 是
  print 惯例、单进程单用户量级，fallback 日志 + eval harness 字段已覆盖验收所需；
  P27 trace 若后续扩 rerank span 再顺势落。
- **查询长度闸 / base URL SSRF 校验 / 厂商目录**：单厂商单运维面 env，不构成
  攻击面与配置面，按「少造轮子」不引入。

## 3. 测试（apps/server/tests/）

`test_rag_pipeline.py` 新增 BailianReranker 组（`httpx.MockTransport` 回放，不打真网，
测法对齐 test_websearch.py）：

1. **正常**：n 候选 → 请求体断言（嵌套契约形状、instruct 在 parameters 内、
   top_n=n、空串候选已换空格、Bearer 头）→ 返回分数 = relevance_score×10、
   index 对齐。**mock 响应采用官方文档示例原样**（含 `document.text` 回显与
   乱序 index，WeKnora golden test 做法，钉住解析器不漂移）；
2. **超时**：handler 抛 `httpx.TimeoutException` → rerank 抛出（不吞），配
   Retriever 后整链退 RRF 原序（复用现有 fallback 测法）；
3. **坏响应**：非 2xx / **HTTP 200 但顶层 `code` 非空（断言异常消息含
   code+message）** / 缺 output.results / results 短一个 / index 越界 / 分数
   1.5 → 均 ValueError；
4. **空候选**：返 `[]` 且不发请求。

`test_config.py` 新增：`on` 别名归一 flash、非法值报错文案含枚举、三新变量缺省值。
接线测试（test_api.py 或 test_rag_pipeline.py）：mode=bailian 且有 key 时
`_build_retriever` 产出 BailianReranker；无 key 时产出 None 且有告警。

## 4. 验收

1. **冒烟（真网）**：进程内调 `BailianReranker.rerank("图书馆借阅上限", [...])` 一次，
   返回 results 且 index 对齐、分数 ∈ [0,10]；key 不出现在任何输出。
2. **引擎对照**（`make retrieval-eval EXTRA=1`，同配置同池；flash 对照 2 轮取均值
   ——GLM 温度 0 仍非确定，bailian 确定性单轮即可）：
   - Recall@6（bailian）≥ Recall@6（flash）− 2pp；参考锚：冻结池对照 0.9012 vs
     0.8938，P40 四象限全开 flash 基线 0.8932；
   - rerank 跳延迟 p95 < 500ms（参考 414ms）；
   - rerank_fallbacks = 0。
3. **既有测试全绿**：`make test`（pg-up 前置），新增单测如 §3 全过。
4. **titled passage 实验臂**（Q9，记录留档不设翻转门）：`RERANK_PASSAGE=titled`
   + bailian 跑一轮同口径对照，报告留档；Recall@6 增益 ≥ +1pp 则翻缺省
   （本票内翻转须重跑第 2 条全部对照，否则另立小票）。
5. **PARITY/.env.example/docs 同步**：§2.5 两处落档，缺一不可。
6. 走本仓库既有 PR/评测流程（报告留 `eval/reports/`，commit 分步 pathspec 限定）。

## 5. 上线与回退

1. 服务器 `.env` 追加 `DASHSCOPE_API_KEY=<key>`（本地取自 `~/.bailian/config.json`
   的 `api_key` 字段，人工搬运；.env 不入库）+ `RERANK_MODE=bailian`。
2. `systemctl restart gewu-api`（生产是 systemd 非 pm2）；health 检查。
3. 上线观察（对齐既有 [rag] 日志面）：rerank-fallback 率应为 ~0、空命中率、
   每跳延迟分布（可临时 `make trace-query` 看检索 span 时间占比）。
4. **回退 = .env 改回 `RERANK_MODE=flash`（或 on）+ restart**，一行一级，无代码回滚。
   chat 关键路径延迟应显著下降（flash p50 2.7s → bailian 0.25s）。

## 6. 注意与挂账

- **instruct 勿省略**（Q8）；文案改动 = 分数分布漂移 = 阈值语义失效，改前必须重跑对照。
- relevance_score 是请求内相对分：跨请求比分数、按分数做绝对统计（如「平均相关性
  趋势」看板）都是误用；绝对阈值 2.0 在本语料实测成立，换语料/换模型需复验空命中率。
- 百炼工作空间域名与 key 同生命周期：换工作空间 = endpoint + key 一起换。
- rerank 计费不进 `DAILY_TOKEN_BUDGET`（只记 LLM 接缝），成本监控靠百炼控制台；
  目录价量级下 219 篇语料场景可忽略。
- 挂账（不本票）：~~rewriter fail-open 缺陷（P40 撞坑 #1）~~（**P42 已闭线**：
  `llm/safety.py` 200 文案形态拦截 + `ContentFilterError` 统一异常，见
  [P42 任务书](P42-provider-filter-fallback.md) §2.2）；flash 降级为后备开关后的
  去留评估；rerank span 级埋点；`qwen3-rerank` 平铺契约支持（真要用再解）；
  MMR 多样性选择（WeKnora SelectMMR——同文档父块挤占 top-6 成为指标瓶颈时再议，
  参见 §2.6 不吸收清单）。

## 7. 执行实况（2026-10-06）

**实现**：`BailianReranker`（嵌套契约/instruct/空串换空格/200+code 检查/从严
校验不补 0）+ `build_reranker` 收口（app 与 eval 共用）+ `RERANK_PASSAGE`
body|titled 双口径（titled 与 `ingest.embed_content` 同形状）+ `RERANK_MODE`
三态（on 归一 flash，非法值 `Settings.load` 即抛错）+ eval 脚本
`--rerank-mode` / rerank 跳延迟 p50/p95 / fallback 计数 / `rerank_engine`
字段。PARITY 新增 §0.12 + §12 配置表 + architecture/04 两处旧口径同步。

**测试**：新增 17 例（BailianReranker 四态×golden 官方响应形状/200+code/
index 越界重复/分数超界/超时整链退 RRF、build_reranker 五模式、passage 双
口径、config 别名与 fail-fast、`_build_retriever` 接线选择），`make test`
327 passed 全绿。

**真网冒烟**：3 候选（借阅条款/食堂/空串）得 [9.92, 0.01, 3.04]——index
对齐、0-10 标尺、top1 正确，key 零泄漏。

**135 题生产管线对照（EXTRA=1 口语变体并入，k=6，rewrite on）**：

| 配置 | Recall@6 | MRR | NDCG@6 | rerank p50/p95 | fallbacks |
|---|---|---|---|---|---|
| no-rerank | 0.8654 | 0.7562 | 0.7622 | — | — |
| flash ×2 轮均值 | 0.8938 | 0.8068 | 0.8021 | 2585 / 6760ms | **36/270 = 13.3%** |
| **bailian** | **0.9086** | 0.7926 | 0.7954 | **236 / 351ms** | **0** |
| bailian + titled | 0.9012 | 0.8130 | 0.8037 | 258 / 388ms | 0 |

**验收判定**：①R@6(bailian)=0.9086 ≥ flash−2pp=0.8738 ✓（实际反超
+1.48pp）；②rerank 跳延迟 p95=351ms < 500ms ✓；③fallbacks=0 ✓。flash
生产管线复测降级率 13.3% 与冻结池研究 12.2% 互证——系统性缺陷坐实。
MRR/NDCG flash 两轮均值略高 0.7~1.4pp（GLM 两轮方差内），bailian 胜在
R@6、零降级与 1/11 延迟。

**titled 臂（§4.4 门槛判定）**：R@6 = body −0.74pp，**未达 +1pp 翻缺省
门槛 → 缺省保持 body**；但 MRR +2.04pp / 挑战 NDCG +2.08pp / 黄金 R
+3.34pp——排序质量收益真实，若日后指标口径转向 NDCG 可复评翻案。

**部署实况**：见 §5（服务器 .env 加 DASHSCOPE_API_KEY + RERANK_MODE=bailian，
systemd 重启后 health/日志观察）。
