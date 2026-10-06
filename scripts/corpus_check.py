#!/usr/bin/env python3
"""P40 语料自检：命名/编号/frontmatter/金额白名单/近邻重复度/交叉引用可解析性。

对照 docs/corpus-bible.md（第 2/4/5/6/7 节）的机器侧执行。三类结论：
  FAIL——格式/命名/编号/部门枚举问题，必须修；
  WARN ——金额不在白名单（人工裁决：补 bible 或改文本）、近邻重复率高（>0.55，
          防互抄）、《》引用在库内与外部白名单均不可解析；
  PASS 。
用法：python3 scripts/corpus_check.py（全量跑，纯标准库，零依赖）。
白名单与 bible 同步维护：新增合法金额先改 bible 第 4/5/6 节再同步本文件。
"""

from __future__ import annotations

import re
import sys
from itertools import combinations
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"

# 部门枚举（bible 第 2 节；存量 15 篇的部门全在其中）
SOURCES = {
    "教务处", "学生工作部", "研究生院", "招生就业处", "体育工作部", "图书馆",
    "信息化建设办公室", "国际交流合作处", "校医院（医保办）", "校长办公室",
    "财务处", "保卫处", "后勤保障处", "实验室与设备管理处", "校团委",
    "心理健康教育中心", "学生资助管理中心",
}

# 金额白名单（元）——bible 第 4/5/6 节 + 存量台账的并集
ALLOWED_AMOUNTS = {
    # 奖助（0004 + bible 第 5 节）
    "8000", "3000", "1500", "800", "5000", "4500", "3500", "2800", "1000", "640",
    # 勤工/贷款/补助/兵役
    "20", "16000", "12000",
    # 学费住宿（bible 第 6 节）
    "4800", "5500", "10000", "28000", "1200",
    # 校园卡/图书/证明（0007/0009/bible 第 6 节）
    "0.10", "15", "500", "2000", "10", "30", "380",
    # 学分费（0012/bible）
    "100",
    # 交换奖学金（0011 区间 5000-20000）
    "20000",
    # 派生金额（辅修全程学费例：26 学分 × 100 元）
    "2600",
    # 学生证补办工本费
    "5",
    # 派生：论文重做 2 学分 × 100 元；2024 版勤工 80 元/天
    "200", "80",
    # 后勤生活域（bible 未列的合理口径）：食堂限价/打包盒/水电单价/空调
    # 年租/游泳票/保险保额与医疗上限/暑期资助上限/文献传递自费/复印
    "12", "0.5", "0.55", "3.2", "420", "8", "200000", "50000", "15000",
    "2", "0.2",
}

# 简称对照——FAQ/通知中的惯用简称 → 库内正式 title（去"钱塘大学"前缀）
SHORTCUTS = {
    "推荐免试研究生（保研）实施细则": "推荐优秀应届本科毕业生免试攻读研究生实施细则",
    "推荐免试研究生实施细则": "推荐优秀应届本科毕业生免试攻读研究生实施细则",
    "保研实施细则": "推荐优秀应届本科毕业生免试攻读研究生实施细则",
    "学生医疗保障与就诊须知": "学生医疗保障与校医院就诊须知",
    "优秀毕业论文评选办法": "优秀本科毕业论文（设计）评选办法",
}

# 表单/证书/课程名——形如《》但不是制度文件
KNOWN_NON_DOC = {
    "考场情况记录表",
    "国家学生体质健康标准登记卡",
    "家庭经济困难学生认定申请表",
    "转专业申请表",
    "学籍异动申请表",
    "复学申请表",
    "受理证明",
    "安全责任承诺书",
    "交换课程预审表",
    "学分认定申请表",
    "创业基础",
    "文献检索与利用",
    "职业生涯规划",
    "就业指导",
}

# 外部国家/部委文件前缀——可引用但不在库内（《普通高等学校学生管理规定》等）
EXTERNAL_REF_PREFIXES = (
    "国家", "普通高等学校", "中华人民共和国", "教育部", "国务院", "浙江省",
    "钱塘市", "居民", "学生伤害事故",
)

NAME_RE = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
REF_RE = re.compile(r"《([^》]{2,40})》")

fails: list[str] = []
warns: list[str] = []

files = sorted(CORPUS.glob("*.md"))
titles: dict[str, str] = {}  # 文件名 -> 去前缀标题
bodies: dict[str, str] = {}


def cjk_bigrams(text: str) -> set[str]:
    han = re.findall(r"[\u4e00-\u9fff]", text)
    return {a + b for a, b in zip(han, han[1:])}


# ---------- 1. 命名 / 编号 / frontmatter ----------
nums = []
for f in files:
    if not NAME_RE.match(f.name):
        fails.append(f"命名不符 NNNN-slug.md：{f.name}")
        continue
    nums.append(int(f.stem[:4]))
    raw = f.read_text(encoding="utf-8")
    m = FM_RE.match(raw)
    if not m:
        fails.append(f"缺 frontmatter：{f.name}")
        continue
    fm = m.group(1)
    t = re.search(r"^title:\s*(.+)$", fm, re.M)
    s = re.search(r"^source:\s*(.+)$", fm, re.M)
    u = re.search(r"^updated:\s*(.+)$", fm, re.M)
    if not (t and s and u):
        fails.append(f"frontmatter 缺键：{f.name}")
        continue
    title, src, upd = t.group(1).strip(), s.group(1).strip(), u.group(1).strip()
    if not title.startswith("钱塘大学"):
        fails.append(f"title 未以「钱塘大学」开头：{f.name} -> {title}")
    if src not in SOURCES:
        fails.append(f"source 不在部门枚举：{f.name} -> {src}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", upd):
        fails.append(f"updated 非法：{f.name} -> {upd}")
    titles[f.name] = title.replace("钱塘大学", "")
    bodies[f.name] = raw

if nums:
    expect = list(range(nums[0], nums[0] + len(nums)))
    if nums != expect:
        gaps = sorted(set(expect) - set(nums))
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        fails.append(f"编号不连续：缺口 {gaps} 重复 {dupes}")

# ---------- 2. 金额白名单 ----------
for name, raw in bodies.items():
    found = {a for a in AMOUNT_RE.findall(raw)}
    unknown = found - ALLOWED_AMOUNTS
    if unknown:
        warns.append(f"金额不在白名单（人工裁决）：{name} -> {sorted(unknown)} 元")

# ---------- 3. 近邻重复度（CJK bigram Jaccard，版本对豁免） ----------
def _norm(s: str) -> str:
    """去空白（含全角空格）与 blockquote 前缀，标题匹配用。"""
    return re.sub(r"[\s>]+", "", s)


def _is_version_pair(a: str, b: str) -> bool:
    """一方是"历史版本说明"文档且其替代声明指向另一方。"""
    for x, y in ((a, b), (b, a)):
        if "历史版本说明" in bodies[x]:
            m = re.search(r"已被《([^》]+)》", bodies[x])
            if m:
                target = _norm(m.group(1).replace("钱塘大学", ""))
                ty = _norm(titles[y])
                if target in ty or (ty and ty in target):
                    return True
    return False


grams = {n: cjk_bigrams(r) for n, r in bodies.items()}
for a, b in combinations(sorted(grams), 2):
    ga, gb = grams[a], grams[b]
    if not ga or not gb:
        continue
    if _is_version_pair(a, b):
        continue
    j = len(ga & gb) / len(ga | gb)
    if j > 0.55:
        warns.append(f"近邻重复度高（{j:.2f} > 0.55，疑互抄）：{a} vs {b}")

# ---------- 4. 交叉引用可解析性（跨行《》合并后匹配） ----------
def _resolve(ref: str) -> bool:
    ref = _norm(ref.replace("钱塘大学", ""))
    ref = { _norm(k): _norm(v) for k, v in SHORTCUTS.items() }.get(ref, ref)
    if ref in {_norm(x) for x in KNOWN_NON_DOC}:
        return True
    return any(ref in _norm(t) for t in titles.values()) or any(
        t and _norm(t) in ref for t in titles.values()
    )


for name, raw in sorted(bodies.items()):
    flat = re.sub(r"[\s>]+", "", raw)  # 《》内跨行折行与引用前缀合并后再抽
    for ref in REF_RE.findall(flat):
        if ref.startswith(EXTERNAL_REF_PREFIXES):
            continue
        if not _resolve(ref):
            warns.append(f"《{ref}》库内不可解析（交叉点缺失或笔误）：{name}")

# ---------- 汇总 ----------
print(f"语料 {len(files)} 篇；FAIL {len(fails)}，WARN {len(warns)}")
for x in fails:
    print(f"FAIL  {x}")
for x in warns:
    print(f"WARN  {x}")
sys.exit(1 if fails else 0)
