"""自适应切片策略链（P15：参考 WeKnora chunker 的画像→选型→校验→降级框架）。

heading 档切分算法移植自 Go hierarchical（ParseMarkdownTree/aggregateParents/
父子双层），recursive 档为段落聚合滑切兜底（WeKnora legacy 语义）。auto 档由
profiler 画像选型：标题结构良好走 heading；任一档校验不过自动降到下一档
（WeKnora ValidateChunks 降级语义）。所有长度按 rune 计（Python len 语义），
遍历顺序固定，输出确定可复现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 标题行：行首 1~6 个 # + 空白 + 标题文本。
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)

# 选型阈值（WeKnora profiler 同款量级）：标题总数 ≥3 且密度（标题/字符）达标。
MIN_HEADINGS = 3
MIN_DENSITY = 0.005


@dataclass(frozen=True)
class ChunkConfig:
    """切片策略与尺寸参数（Settings CHUNK_* 注入）。"""

    strategy: str = "auto"  # auto | heading | recursive
    parent_limit: int = 800  # heading 档父块上限（rune）
    child_limit: int = 200  # 子块上限（rune）
    overlap: int = 40  # 超长滑切重叠（rune）

    def __post_init__(self) -> None:
        if self.strategy not in ("auto", "heading", "recursive"):
            raise ValueError(f"未知切片策略 {self.strategy}")
        if self.child_limit <= 0 or self.parent_limit < self.child_limit:
            raise ValueError("要求 0 < child_limit <= parent_limit")
        if not 0 <= self.overlap < self.child_limit:
            raise ValueError("要求 0 <= overlap < child_limit")


@dataclass(frozen=True)
class Node:
    """标题切分出的 section 节点。"""

    level: int  # 标题层级 1~6；0 = 无标题文档的整篇
    title: str
    path: str  # breadcrumb，如 "转专业管理办法 > 申请条件"；无标题为空
    body: str


@dataclass(frozen=True)
class Chunk:
    """切片输出单元（入库前中间形态，parent_idx 契约同 rag_upsert_doc 载荷）。"""

    text: str
    section_path: str
    is_parent: bool
    parent_idx: int  # 指向输出列表中父块下标；父块自身与 flat 块为 -1


@dataclass(frozen=True)
class DocProfile:
    """文档画像（profiler 一次扫描的统计量，选型依据）。"""

    headings: int  # 标题行总数
    chars: int  # 正文 rune 数
    density: float  # 标题数 / 正文字符数
    main_level: int  # 最浅标题层级；无标题为 0


@dataclass(frozen=True)
class ChunkResult:
    """chunk_document 输出：块序列 + 实际生效档位 + 降级轨迹。"""

    chunks: list[Chunk]
    tier: str
    fallbacks: list[str] = field(default_factory=list)


def profile(text: str) -> DocProfile:
    """一次扫描统计标题结构（WeKnora profiler 的精简版：标题数/密度/主层级）。"""
    levels = [len(m.group(1)) for m in _HEADING_RE.finditer(text)]
    chars = len(text)
    return DocProfile(
        headings=len(levels),
        chars=chars,
        density=(len(levels) / chars if chars else 0.0),
        main_level=(min(levels) if levels else 0),
    )


def parse_markdown_tree(text: str) -> list[Node]:
    """按标题切成带 breadcrumb 的 section 节点（移植 Go ParseMarkdownTree）。

    body 从标题行结束、吃掉行尾换行之后开始——不含 "# 标题" 行本身，
    breadcrumb 只由 path 承担一次。无任何标题时返回单个 Level=0 节点（整篇）。
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [Node(level=0, title="", path="", body=text.strip())]
    nodes: list[Node] = []
    stack: list[str] = []
    for i, m in enumerate(matches):
        lvl = len(m.group(1))
        title = m.group(2).strip()
        body_start = m.end()
        if body_start < len(text) and text[body_start] == "\r":
            body_start += 1
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        stack = stack[: lvl - 1]  # 裁掉同级及更深标题，维持面包屑栈（Go 语义）
        stack.append(title)
        nodes.append(Node(level=lvl, title=title, path=" > ".join(stack), body=body))
    return nodes


def chunk_text(text: str, limit: int, overlap: int) -> list[str]:
    """段落聚合切块；超长单段滑动硬切保留 overlap 衔接（移植 Go ChunkText）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        r = para
        while len(r) > limit:
            chunks.append(r[:limit])
            r = r[limit - overlap :]
        buf = r
    if buf:
        chunks.append(buf)
    return chunks


def _with_path(path: str, body: str) -> str:
    """父块文本前缀 breadcrumb，使回取进上下文时自带标题定位（Go withPath 同款）。"""
    if not path:
        return body
    return f"【{path}】\n{body}"


def _aggregate_parents(nodes: list[Node], parent_level: int) -> list[tuple[str, str]]:
    """按父边界层级两级聚合：Level<=parent_level（或整篇）开启新父块，
    更深层级小节归并进来（小节标题以纯文本行保留）；聚合后为空的不产出。
    """
    groups: list[tuple[str, list[str]]] = []
    for n in nodes:
        if n.level == 0 or n.level <= parent_level:
            groups.append((n.path, [n.body] if n.body else []))
            continue
        if not groups:
            groups.append(("", []))  # 文档以小节标题开头：先开一个匿名父块
        section = f"{n.title}\n{n.body}" if n.title else n.body
        groups[-1][1].append(section)
    out: list[tuple[str, str]] = []
    for path, parts in groups:
        body = "\n\n".join(p for p in parts if p)
        if body:
            out.append((path, body))
    return out


def _heading_chunks(text: str, cfg: ChunkConfig, main_level: int) -> list[Chunk]:
    """heading 档：父子双层——父=按主标题级聚合（超限滑切，带 breadcrumb 前缀），
    子=父块文本内段落聚合到 child_limit；输出顺序父-子-子…（parent_idx 契约）。
    """
    parent_level = main_level if main_level > 0 else 1
    out: list[Chunk] = []
    for path, body in _aggregate_parents(parse_markdown_tree(text), parent_level):
        for p in chunk_text(body, cfg.parent_limit, cfg.overlap):
            parent_idx = len(out)
            out.append(
                Chunk(
                    text=_with_path(path, p),
                    section_path=path,
                    is_parent=True,
                    parent_idx=-1,
                )
            )
            for c in chunk_text(p, cfg.child_limit, cfg.overlap):
                out.append(Chunk(text=c, section_path=path, is_parent=False, parent_idx=parent_idx))
    return out


def _recursive_chunks(text: str, cfg: ChunkConfig) -> list[Chunk]:
    """recursive 兜底档：扁平段落聚合滑切，无父子结构（检索层走 flat 模式）。"""
    return [
        Chunk(text=c, section_path="", is_parent=False, parent_idx=-1)
        for c in chunk_text(text, cfg.child_limit, cfg.overlap)
    ]


def validate_chunks(chunks: list[Chunk], cfg: ChunkConfig) -> str:
    """校验切分结果，返回拒绝原因（空串 = 通过）。WeKnora 降级判据：
    非空、无空白块、子块不超限、父块引用完整且 heading 结构确有父子。
    """
    if not chunks:
        return "切分结果为空"
    if any(not c.text.strip() for c in chunks):
        return "存在空白块"
    if any(not c.is_parent and len(c.text) > cfg.child_limit for c in chunks):
        return f"存在超过 child_limit={cfg.child_limit} 的子块"
    parents = {i for i, c in enumerate(chunks) if c.is_parent}
    children = [c for c in chunks if not c.is_parent]
    if not children:
        return "没有可索引的子块"
    for c in children:
        if c.parent_idx not in parents:
            return f"子块引用的父块下标 {c.parent_idx} 无效"
    return ""


def _select_tier(cfg: ChunkConfig, prof: DocProfile) -> str:
    """auto 档画像选型：标题结构良好（数量、密度、主层级齐备）→ heading。"""
    if cfg.strategy == "heading":
        return "heading"
    if cfg.strategy == "recursive":
        return "recursive"
    if prof.headings >= MIN_HEADINGS and prof.density > MIN_DENSITY and prof.main_level > 0:
        return "heading"
    return "recursive"


_TIERS = ("heading", "recursive")  # 降级链：heading 不过 → recursive 兜底


def chunk_document(text: str, cfg: ChunkConfig) -> ChunkResult:
    """策略链入口：选档 → 切分 → 校验 → 失败降级到下一档（记轨迹）。"""
    prof = profile(text)
    tier = _select_tier(cfg, prof)
    fallbacks: list[str] = []
    while True:
        if tier == "heading":
            chunks = _heading_chunks(text, cfg, prof.main_level)
        else:
            chunks = _recursive_chunks(text, cfg)
        reason = validate_chunks(chunks, cfg)
        if not reason:
            return ChunkResult(chunks=chunks, tier=tier, fallbacks=fallbacks)
        fallbacks.append(f"{tier} 档被拒（{reason}）")
        nxt = _TIERS[_TIERS.index(tier) + 1] if tier in _TIERS[:-1] else ""
        if not nxt:
            return ChunkResult(chunks=chunks, tier=tier, fallbacks=fallbacks)
        tier = nxt
