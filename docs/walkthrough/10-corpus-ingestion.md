# 10 · 语料从哪来：md 之前的解决方案与实测

> 回答「源数据是 Word/PDF/Excel/图片时怎么办」。现状先亮明：gewu 的语料是手工
> 规整的 Markdown，格式解析层被刻意划在仓库边界外——本篇讲清楚这个边界决策的
> 理由、接真实源数据时的标准方案，以及一组在本机真跑的微型实验：解析质量到底
> 丢在哪里、丢多少。

## 现状与边界：为什么仓库里没有解析层

`data/corpus/` 下十来篇语料是手工编写、人工 review 过的 Markdown，frontmatter
的 `title / source / updated` 手写（源标教务处/校长办公室）。从「原始格式 →
规整 md」这一步是**语料工程，发生在仓库之外**，不进代码。

这不是遗漏，是与检索层设计同一条线上的 scope 决策：

1. **清洗/解析的厚度应匹配语料的脏度**。十几篇可控语料，重解析层是负资产；
   工程预算花在检索层（父子块、混检、RRF、精排）才产生演示价值。
2. **frontmatter 契约为真实源数据预留了位置**：`updated` 是 roadmap 里「时效
   过滤」缺口要用的字段，`source` 管溯源——接入多源语料时这两个字段才真正
   开始干活。
3. 代价也说清楚：`ParseDoc`（`internal/rag/ingest.go:32`）只认 md，任何格式
   问题都得在进 `corpus/` 之前解决。

面试口径：不说「没做」，说「边界划在哪、为什么、要接怎么接」——主动讲 scope
是加分项，被动承认缺失才是减分项。

## 总方案：异构格式 → 统一中间表示

核心一句话：**所有格式都转成「带 frontmatter 的 Markdown」这一个契约**，下游
`ParseDoc` / `HierarchicalChunks` / `rag_tokenize` 全部零改动。解析层的任何
变化（换工具、加格式）被隔离在上游，这是「统一中间表示」（normalize to a
canonical IR）的标准收益。

上下游之间的**接口是标题树**：`ParseMarkdownTree`（`internal/rag/hierarchical.go:38`）
按 `#{1,6}` 切 section、维护 breadcrumb，父块聚合完全依赖标题层级。所以上游
解析的验收指标不是「文字抽出来了没」，而是「**结构（标题层级）还原了没**」——
这决定了父子块切分的质量。下面的实验会反复回到这一点。

```
Word(docx) ──pandoc/mammoth──┐
PDF(文本层) ──pdftotext/PyMuPDF──┤          ┌─ frontmatter(title/source/updated)
PDF(扫描件) ──OCR(PaddleOCR)──┼──→ 清洗 ──→┤     + Markdown 正文(标题树)
Excel(xlsx) ──行记录化/表头拼接──┤   (去噪/归一) └─→ corpus/*.md → 现有 Ingest 管线
图片 ──OCR / VLM 描述文本──┘
```

## 分格式打法

**Word（docx）——最容易，但有暗坑**。docx 本质是 OOXML（zip 包里的 XML），
结构信息存在段落样式名（`Heading1/2`）里。`pandoc` / `mammoth` 走样式映射转
md。暗坑：**不是所有 docx 都有语义样式**——转换器/WPS 产的文件常把标题编码成
直接格式（大字号+加粗），样式映射直接失效（实验 A 实测）。兜底是格式启发式
（字号聚类），或上游统一用 Word 原生样式模板。

**PDF——最难，难得有道理**。先分两类：

- **文本层 PDF**（能选中复制）：`pdftotext` / PyMuPDF 抽文本，看似简单；
- **扫描件**：必须 OCR（中文 PaddleOCR 是主流，Tesseract 中文一般）。

真正的难点不是抽字，是**版面理解**：阅读顺序、页眉页脚去噪、表格线还原。
工业界现行做法是版面分析模型（unstructured、Marker、MinerU、LayoutLM 系），
输出带标题层级的 md。注意标题层级判定质量直接决定下游父子块质量——上游解析
和下游切分不是两个孤立环节，标题树是它们之间的接口。

**Excel——不能按段落切**。表格走段落聚合/滑切会把一行拦腰截断（实验 C2 实测
断碎率）。三种序列化按表性子选：

| 策略 | 适用 | 一句话 |
|---|---|---|
| 整表 Markdown | 小维表（一屏内） | 一个 chunk，上下文完整，但零行级判别 |
| 裸行记录 | 表头无语义价值 | 每行一个 chunk，"字段 值"自然语言化 |
| **表头拼接行**（默认） | 大表/宽表 | 每行 chunk 前拼表头，等价于父块 breadcrumb 的思路——小块自带定位上下文 |

**图片——按有没有字分两条路**。文字型（通知截图）→ OCR 走正常入库；语义型
（流程图/地图）→ VLM 生成描述文本入库，原图进对象存储，chunk 里放引用。
必须文本化的原因：doubao-embedding 是纯文本模型，嵌不了图。

**清洗层**（转换后、切分前）：去页眉页脚/水印/目录、重复空行、全半角归一、
**NFKC 归一化**（康熙部首→正字，实测教训）、CRLF→LF。`hierarchical.go:49`
对 `\r` 的处理就是在给非手工语料兜底——现有代码已预埋了一点对脏输入的容忍。

## 微型实验与效果分析

以下三个实验在本机真跑（2026-09-16，macOS）。**定位是机理验证，不是生产基准**：
样本是语料切片的迷你版，工具只用本机现成的（textutil / cupsfilter / pdftotext
+ 仓库自己的 Go 代码）。复现命令附在各节。三个反直觉发现先列出来：

1. docx 往返：文字 100% 保真，**结构 0% 保真**（样式缺失时）；
2. PDF 文本层：133 个汉字一个不少，默认模式下 **6 句里 5 句顺序损坏**——
   信息全在、顺序坏了；
3. 表格：口语问「多少钱」时，表头拼接**在关键词路下不加分**（字段名「金额」
   与口语不同词面），它的真实价值在语义路。

### 实验 A：docx 往返——文本全对，结构全丢，启发式救回

**方法**：取 0001 语料切片写成 HTML（h1/h2/p）→ `textutil -convert docx` 产
docx → 纯标准库 Python 解 `word/document.xml` 转回 md（脚本约 60 行，核心是
zipfile + ElementTree，**docx 就是个 zip 包里的 XML**，这是本实验最想展示的
认知）。两个解析路径：语义样式（`Heading1/2` 样式名）与字号启发式（加粗段按
字号降序映射 H1/H2）。

**结果**：

| 维度 | 结果 |
|---|---|
| 文本保真 | 6/6 句逐字完整往返 |
| 语义样式路径 | 标题全丢——textutil 产的 docx **连 styles.xml 都没有**，标题被编码为直接格式（`<w:sz w:val="48"/><w:b/>`，即 24pt 加粗） |
| 字号启发式路径 | 三级标题完整恢复（`# 转专业管理办法` / `## 申请条件` / `## 办理流程`） |

**结论**：docx 的结构信息在样式名里，但**样式是可选的**——「大号粗体」和
「二级标题」在直接格式层面不可区分，只能靠字号聚类猜。生产含义：转换器要
双路径（样式优先、格式启发式兜底），且转换后必须验标题节点数。用 Word 原生
样式 authored 的文档走 pandoc/mammoth 没有这个问题——坑集中在转换器产的
docx。

### 实验 B：PDF 文本层——字符全在，顺序会坏

**方法**：同一份文本 → `cupsfilter` 产 PDF（模拟转换器产的中文 PDF）→
`pdftotext` 默认模式与 `-raw` 模式各抽一次 → 对照原文做三层校验（汉字多重集、
句子完整率、结构节点数），再把抽取文本喂给仓库自己的 `HierarchicalChunks`
看切分退化。

**结果**：

| 维度 | 默认模式（按坐标重排） | `-raw` 模式（按内容流） |
|---|---|---|
| 汉字多重集 vs 原文 | **相等（133/133，一个不少）** | 相等 |
| 句子完整率 | **1/6**（如「学生」丢字漂到页尾成乱序单字「入/生/二/口/面」） | **6/6** |
| 标题结构 | 丢失（无 `#` 标记） | 丢失 |
| `ParseMarkdownTree` 节点数 | 1（原文 3） | 1 |
| `HierarchicalChunks` 父块 | 1 个、**breadcrumb 路径为空**（原文父块带「转专业管理办法」路径） | 同左 |

**结论**：

1. **抽取质量 = 生产者 × 抽取器参数的组合**。同一个 PDF，默认模式 1/6 句
   完整、`-raw` 6/6；换一个生产者（Word 直接导出）结论可能反转。不能盲抽，
   **必须带验收门禁**。
2. 便宜的验收门禁就有了：**汉字多重集对比**（本实验用的招）——字符少了是
   数据丢失（不可接受），字符全但句子碎了是顺序问题（换参数/换工具可救），
   两种坏法处理路径完全不同。
3. 文本层 PDF 丢的是**结构元数据**（标题层级、breadcrumb），文字本身还在。
   对 gewu 单 H1 语料，主要损失是父块 breadcrumb 和段落边界可靠性；对多级
   标题语料，父块边界整个消失，退化为任意 800 rune 滑切。
4. 诚实边界：cupsfilter 产的 PDF 是字体定位特殊的**下界样本**；真实 Word
   导出 PDF 的 ToUnicode 映射通常正常。本实验证明的是下界存在与验收必要性，
   不是「pdftotext 不可用」。

### 实验 C：表格序列化——关键词路的能与不能

**方法**：奖学金等级表（4 行真实数据，字段：等级/覆盖范围/GPA 要求/金额）
构造三种序列化；对三个口语 query，用仓库的 `rag.Tokenize`（`internal/rag/tokenize.go`，
与 SQL 侧 `rag_tokenize` 集合等价）算 query token 与各块 token 的交集命中，
看「正确的行能否被判别为最佳块」。另用 `ChunkText(200, 40)` 切一张 42 行
宽表数断碎率。

**结果一（命中判别）**：

| query | 整表 Markdown | 裸行 / 表头拼接行 |
|---|---|---|
| 「国家奖学金多少钱」 | 单块命中 4 | 最佳行正确（4/2/2/2），两种序列化**同分** |
| 「专业前 10% 有什么奖学金」 | 单块命中 5 | 最佳行正确（5/4/4/4），同分 |
| 「3000 元是几等奖学金」 | 单块命中 4 | 最佳行正确（一等奖学金行，靠 token `3000` 精确命中） |

**结果二（宽表断碎）**：42 行表按 `childLimit=200` 滑切 → 23 块，**完整存活
行 34/42——8 行（19%）被拦腰截断**。表格不能走段落切分，实锤。

**结论**：

1. **精确值查询关键词路就能正确判别行**：「3000」「10%」这类 token 被
   `rag_tokenize` 的拉丁词小写路径稳稳兜住——混检里的 BM25 路不是陪跑，
   就是为这类 query 准备的。
2. **口语-字段名错配关键词路无解**：「多少钱」和「金额」词面不同，零命中。
   表头拼接行在这个实验里**不加分**——它的真实价值在语义路：embedding 看到
   「金额=3000 元/年」才能桥接口语与字段名。这解释了为什么表格场景混检的
   两路缺一不可，也提醒评测要分路归因（这次若只看总命中就会错怪关键词路）。
3. 整表 Markdown 单块零行级判别：小表可接受（上下文完整、一次召回全拿到），
   大表既超限断碎又无法判别——按规模换策略。

### 复现命令

```bash
# A: docx 往返（textutil 产 docx；docx2md.py 为 60 行标准库解析器，双路径）
textutil -convert docx -output exp-a.docx exp-a.html && python3 docx2md.py exp-a.docx [--heuristic]
# B: PDF 文本层（cupsfilter 产 PDF；对比默认与 -raw）
cupsfilter exp-b.txt > exp-b.pdf && pdftotext exp-b.pdf out.txt && pdftotext -raw exp-b.pdf raw.txt
# B/C 落到仓库代码：临时 _test.go 调 HierarchicalChunks/Tokenize 后即删（本文数字即其输出）
# 工具实测：uv venv + 国内镜像；docling 首跑自动拉模型（HF_ENDPOINT=hf-mirror.com）
uv pip install "markitdown[all]" python-docx openpyxl reportlab docling
markitdown styled.docx > out.md          # office 路
python -c "from docling.document_converter import DocumentConverter as C; print(C().convert('good.pdf').document.export_to_markdown())"
```

## 成熟方案盘点：轮子可以白嫖，验收不能外包

上面四条路径**都不该手写**——生态已经成熟，自研解析器是负资产。问「微软那个
转 md 的项目」指的是 **MarkItDown**，它只是这个赛道的轻量级选手。按重量级
排（License 与能力为 2026-09 官方仓库核实）：

| 工具 | 出品方 | License | 定位与强项 | 短板 |
|---|---|---|---|---|
| **MarkItDown** | Microsoft | MIT | 轻量格式适配器：office 全家桶 + 图片（EXIF/OCR）+ 音频转写 + HTML/EPUB/邮件，CPU 秒级，pip 即用 | PDF 仅文本层（pdfminer 底座）无版面模型；docx 依赖语义样式——实验 A 的坑它同样会踩 |
| **Docling** | IBM Research | MIT | 深度理解：版面模型 + TableFormer 表格结构 + OCR + 公式识别，PDF/office/HTML → md/JSON | 首次要拉模型权重；重文档的「认真之选」 |
| **MinerU** | 上海 AI 实验室（OpenDataLab） | **AGPL-3.0，官方确认无商业双许可** | 中文场景强：公式、表格、pipeline 与 VLM 双后端 | AGPL 网络服务传染——做 SaaS 须开源或进程隔离 |
| **Marker** | Datalab | **代码 GPL-3.0 + 模型权重收入门槛**，商业 API 另售 | 快，Surya OCR 底座 | License 摩擦最大，商用最贵 |
| PaddleOCR PP-Structure | 百度 | Apache-2.0 | 中文 OCR + 版面 + 表格还原 | 偏 OCR 栈，非 md-first |
| olmOCR | Allen AI | Apache-2.0 | VLM 逐页 OCR，质量强 | 自托管要 GPU |

选型按文档画像分层：**规整 office 文档 → MarkItDown；文本层 PDF 认真做 →
Docling；扫描件/学术/复杂中文版面 → MinerU 或 PP-Structure**。License 敏感
（要商用/闭源）就锁死 MIT/Apache 阵营。对 gewu 的校园规整文档，MarkItDown
（office）+ Docling（PDF）双工具组合是合理起点。

但本篇实验恰好说明了「用成熟工具」的完整含义：**成熟工具替换的是转换器，
不是验收**。MarkItDown 的 docx 路径与实验 A 同源（mammoth 走样式映射，无语义
样式的文件同样退化为平文本）；实验 B 的字符顺序风险是所有文本层抽取器的共性
参数敏感性问题。所以 `cmd/convert` 的设计不变——「格式 → md」交给工具白嫖，
**验收门禁（字符多重集、标题节点数、表格行存活率）依然自己写**。不重复造的
是轮子，不是质检。

### 实测可行性：office→MarkItDown + PDF→Docling 成立

固定方案前先真跑验证（2026-09-16 本机）：同一份内容（6 句 + 三级标题）构造
五类输入——styled docx（真实 Heading 样式，python-docx 产）/ 无样式 docx
（实验 A 的 textutil 样本）/ xlsx（奖学金表，openpyxl 产）/ 良品 PDF
（reportlab CID 字体，ToUnicode 正常）/ 劣质 PDF（实验 B 的 cupsfilter 样本）。
安装：uv venv + 清华镜像装 `markitdown[all]`；docling 连带 torch 与 HF 模型
（`HF_ENDPOINT=hf-mirror.com`）首次全自动无障碍，单页 CPU 推理秒级。

| 输入 | 工具 | 句子完整 | 汉字多重集 | 标题恢复 | 判定 |
|---|---|---|---|---|---|
| styled.docx | MarkItDown | 6/6 | 相等 | 3/3（`#`/`##`/`##`） | ✅ 完美 |
| 无样式 docx | MarkItDown | 6/6 | 相等 | 0/3（退化为**粗体**） | ⚠️ 文本对、结构丢 |
| xlsx | MarkItDown | 表头+4 行逐格完整 | — | sheet 名 → `##` | ✅ 完美 |
| 良品 PDF | MarkItDown | 6/6 | 相等 | 0/3（平文本） | ⚠️ 无结构 |
| 劣质 PDF | MarkItDown | 顺序对 | **121/133，含 11 个康熙部首码位** | 0/3 | ❌ 码位污染 |
| 良品 PDF | Docling | 6/6 | 相等 | 3/3 | ✅ 完美 |
| 劣质 PDF | Docling | 6/6 | 相等 | 3/3（节内 2/3 句序微乱） | ✅ 全指标通过 |

三个新发现，全部是验收门禁抓的：

1. **康熙部首污染（第三种失效模式）**：MarkItDown/pdfminer 抽劣质 PDF 时，
   字「看着对」但码位落在 U+2F00 部首块（`⽇`≠`日`）——`rag_tokenize` 的汉字
   正则 `[\x{4e00}-\x{9fff}]` 不匹配部首块，**分词静默丢字**，FTS 召回无感
   劣化。多重集门禁能抓（码位不同即不等）；修法是清洗层加 NFKC 归一化。
2. **版面模型的代差是实测事实**：同一份劣质 PDF，pdftotext 默认模式乱序
   （实验 B）、MarkItDown 部首污染、Docling（版面模型 + RapidOCR 管线）全
   指标通过——「PDF 认真做 → Docling」从推断升级为实测结论。
3. **层级校准是 convert 层的活，不是工具的**：Docling 把三级标题全判 `##`
   （无 H1），喂给 `parentHeadingLevel=1` 会开匿名父块、breadcrumb 丢失
   （实测父块路径为空串）；加十行「首标题提升为 H1」归一化后，路径恢复
   「转专业管理办法」，与语料的单 H1 约定完全对齐。工具负责恢复结构存在性，
   **层级的文档级语义要对齐**——这步归 `cmd/convert`。

**结论：方案固定可行。** office→MarkItDown（styled docx/xlsx 完美；无样式
docx 有已知退化，门禁拦截后人工兜底）；PDF→Docling（良劣质通吃）。

## 接入 gewu 的改动面

真要接源数据，设计是**一个离线 `cmd/convert` 步骤**，产出契约就是现有
frontmatter 契约，下游零改动：

1. **输入**：`raw/` 目录（docx/pdf/xlsx/png 原件）+ 一个 manifest（每个文件
   的 source/updated/格式策略）；
2. **转换**：按格式路由——office→MarkItDown、PDF→Docling（上节实测固定），
   加**层级归一化**（如首标题提升为 H1，对齐单 H1 约定）；
3. **验收门禁**（不做完不许进 `corpus/`）：汉字多重集对比（B 的招）+ 标题
   节点数下限（A/B 的教训）+ 表格行存活率 100%（C2 的教训）；
4. **产出**：`corpus/*.md`，走现有 `Ingest` 管线。

验收门禁是这套设计里最值得强调的部分：解析层是**管线里唯一没有类型系统保护
的边界**（md 进了 corpus 之后一切都有测试兜着），所以要用数据校验补上。这与
P6「去静默降级」同一条价值观——失败要响亮，坏数据要拦在门外。

平台级对照（桌面研究，未实测）：Onyx 的 connector 层、Dify 的 dataset 上传 API
重是因为多租户+增量同步+几十种数据源。gewu 单租户+公开语料+离线全量导入，一个
单向转换脚本就是合理形态——**抄架构要连场景一起抄，只抄形态不抄场景就是过度
设计**。转换器本体选型见上节，不自研。

## 边界与已知短板

- 实验是**微型机理验证**：样本迷你、工具是本机现成的，数字用来理解失效模式
  （哪里坏、怎么验），不能外推为生产基准；
- OCR 与 VLM 两条路径**未实测**（本机无 tesseract/PaddleOCR）；扫描件、多栏
  版面、真实 Word 导出 PDF 都没覆盖；
- 表格实验只测了关键词路；「表头拼接在语义路加分」是推断（机理成立），要有
  数字得跑 embedding——留作接真数据时的第一个评测项；
- 工具实测覆盖 MarkItDown/Docling 五类迷你输入（机理级验证）；MinerU/Marker
  未实测；长文档、多栏版面、表格型 PDF 的行为未覆盖；
- 劣质 PDF 上 Docling 的节内句序仍有微乱（2/3 句互换）——句子级**顺序**校验
  是比本篇子串匹配更严的门禁，接真数据时应补上；
- 方案本身未立项：本篇是设计文档 + 实验记录，`cmd/convert` 没有实现计划
  （若立项，验收门禁先行）。

**一句话背稿**：上游解析是格式归一化问题——统一中间表示隔离变化；下游切分
是语义结构问题——靠标题树和父子块。两者以 Markdown 契约为接口，各自独立演进；
而解析层没有类型系统保护，要用数据校验（字符多重集、结构节点数）把坏数据
拦在门外。
