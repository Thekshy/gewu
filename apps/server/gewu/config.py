"""环境配置：键名与缺省值对齐 Go internal/config。

.env 自动发现（从 cwd 向上找），进程环境变量优先——与 Go config.Load 同语义。
相对路径（DATA_DIR）锚定 .env 所在目录（=仓库根），保证从任意目录启动行为一致。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"

DEFAULT_PG_DSN = "postgres://gewu:gewu@127.0.0.1:5433/gewu?sslmode=disable"
DEFAULT_DAILY_TOKEN_BUDGET = 2_000_000
DEFAULT_DAILY_USER_BUDGET = 200_000  # P23：per-user 限额缺省（users.daily_token_limit NULL 时用）

# P41 精排引擎：flash（LLM 精排）| bailian（百炼专用 rerank）| off；
# on 为 flash 的历史别名（P41 前的取值，存量 .env 不断链）。
RERANK_MODES = ("flash", "bailian", "off")
# 百炼工作空间域名（冒烟验证值；公网 https://dashscope.aliyuncs.com 同路径可替换）
DEFAULT_BAILIAN_RERANK_ENDPOINT = "https://llm-wu8666v3ftxa4dsf.cn-beijing.maas.aliyuncs.com"


def _parse_choice(
    raw: str, choices: tuple[str, ...], what: str, *, aliases: dict[str, str] | None = None
) -> str:
    """枚举解析：别名归一 + 非法值 fail-fast（拼错值静默当缺省是配置事故温床）。"""
    val = raw.strip().lower()
    val = (aliases or {}).get(val, val)
    if val not in choices:
        legal = "|".join(choices) + (f"（{'/'.join(aliases)} 为别名）" if aliases else "")
        raise ValueError(f"{what} 非法值 {raw!r}，合法：{legal}")
    return val


def find_dotenv(start: Path | None = None) -> Path | None:
    """从 start（缺省 cwd）向上逐级找 .env，到文件系统顶为止。"""
    cur = (start or Path.cwd()).resolve()
    for p in (cur, *cur.parents):
        candidate = p / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(path: Path | None = None, env: dict[str, str] | None = None) -> None:
    """极简 .env 加载：KEY=VALUE 逐行，# 注释与空行跳过，已有环境变量不覆盖。"""
    path = path or find_dotenv()
    if path is None:
        return
    target = os.environ if env is None else env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in target:
            target[key] = value


def anchor_dir(start: Path | None = None) -> Path:
    """相对路径锚点：.env 所在目录（=仓库根），找不到 .env 时退回 cwd。"""
    dotenv = find_dotenv(start)
    return dotenv.parent if dotenv else Path.cwd()


@dataclass(frozen=True)
class Settings:
    """启动配置（键名与缺省值对齐 Go internal/config）。"""

    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "glm-5.3"
    llm_small_model: str = "glm-5.3-flash"
    llm_disable_thinking: bool = False
    embed_api_key: str = ""
    embed_base_url: str = ""
    embed_model: str = "embedding-3"
    embed_mode: str = "text"  # text（标准 /embeddings）| ark_multimodal（火山多模态，不支持批量）
    pg_dsn: str = DEFAULT_PG_DSN
    data_dir: Path = Path("data")
    daily_token_budget: int = DEFAULT_DAILY_TOKEN_BUDGET
    daily_user_budget: int = DEFAULT_DAILY_USER_BUDGET
    retrieval_k: int = 6
    # 检索融合（P15：WeKnora 默认口径；加权 RRF + 精排阈值容错）
    retrieval_pool_n: int = 20  # 双路召回池
    rrf_k: int = 60
    rrf_vector_weight: float = 0.7  # 向量路权重（关键词路 = 1 - 向量权重比照配置）
    rrf_keyword_weight: float = 0.3
    rerank_threshold: float = 2.0  # 精排模型分阈值（0~10）；全滤空自动退化
    rerank_mode: str = "flash"  # flash（LLM 精排）| bailian（百炼 rerank）| off（on=flash 别名）
    # P41 百炼精排：key 空 = bailian 模式关断（告警后退化为不精排，RRF 直跑）
    dashscope_api_key: str = ""
    bailian_rerank_endpoint: str = DEFAULT_BAILIAN_RERANK_ENDPOINT
    bailian_rerank_model: str = "qwen3.7-text-rerank"
    rerank_passage: str = "body"  # body（裸正文）| titled（title+面包屑+正文，对齐嵌入拼装）
    rate_limit_per_minute: int = 600
    # 切片策略链（P15：入库侧，make ingest 生效；换策略/参数后须 REBUILD=1 重建）
    chunk_strategy: str = "auto"  # auto（画像选型）| heading（父子双层）| recursive（扁平兜底）
    chunk_parent_limit: int = 800
    chunk_child_limit: int = 200
    chunk_overlap: int = 40
    # P21 认证：cookie Secure（M4 https 部署后开）+ CORS 白名单（空=仅同源，
    # 前端经 next rewrite 同源代理访问，无跨域 cookie 依赖）
    cookie_secure: bool = False
    cors_origins: tuple[str, ...] = ()
    # 联网检索（IQS）：key 空=整链关闭（工具不注册、提示词不拼准则，
    # 默认行为零变化）；按次计费故配每日调用上限闸。
    iqs_api_key: str = ""
    web_search_daily_limit: int = 200
    # P30 agent 主循环答案流式（STREAM_ANSWER=0 紧急回退：单帧全文=改前行为）
    stream_answer: bool = True
    # P36 框架文档面（/docs /redoc /openapi.json）：缺省关闭收敛公网侦察面，
    # 本地调试 .env 加 API_DOCS=1 打开
    api_docs: bool = False
    # P39 游客开放通道：免登录影子用户（短 TTL + 低配额 + 学生同集工具面）；
    # 缺省关=现状（未登录一律 401），线上 .env 加 GUEST_MODE=1 开放
    guest_mode: bool = False
    guest_daily_token_limit: int = 50_000  # 游客日 token 限额（≈ 全局 per-user 的 1/4）
    guest_session_ttl_days: int = 7  # 游客会话硬过期（不滑动续期）
    # 开放注册（P39 二段）：=1 时注册免邀请码（纯邮箱+密码）；缺省关=邀请码内测制
    # （P21 语义不动，随时可收回）。开放态防滥用靠 register 端点 IP 限速。
    open_registration: bool = False
    # P42 provider 内容审查拒绝兜底（缺省开；=0 紧急回退：拒绝一律走通用
    # 错误路径=改前行为）。覆盖：分类器 / LLMService 200 形态拦截 / 主循环
    # 优雅拒答 / finish 收口四处。
    content_filter_fallback: bool = True

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        data_dir = Path(env.get("DATA_DIR", "data"))
        if not data_dir.is_absolute():
            data_dir = anchor_dir() / data_dir
        return cls(
            llm_api_key=env.get("LLM_API_KEY", ""),
            llm_base_url=env.get("LLM_BASE_URL", ""),
            llm_model=env.get("LLM_MODEL", "glm-5.3"),
            llm_small_model=env.get("LLM_SMALL_MODEL", "glm-5.3-flash"),
            llm_disable_thinking=env.get("LLM_DISABLE_THINKING", "").lower()
            in ("1", "true", "yes", "on"),
            embed_api_key=env.get("EMBED_API_KEY", ""),
            embed_base_url=env.get("EMBED_BASE_URL", ""),
            embed_model=env.get("EMBED_MODEL", "embedding-3"),
            embed_mode=env.get("EMBED_MODE", "text"),
            pg_dsn=env.get("PG_DSN", DEFAULT_PG_DSN),
            data_dir=data_dir,
            daily_token_budget=int(env.get("DAILY_TOKEN_BUDGET", str(DEFAULT_DAILY_TOKEN_BUDGET))),
            daily_user_budget=int(env.get("DAILY_USER_BUDGET", str(DEFAULT_DAILY_USER_BUDGET))),
            retrieval_k=int(env.get("RETRIEVAL_K", "6")),
            retrieval_pool_n=int(env.get("RETRIEVAL_POOL_N", "20")),
            rrf_k=int(env.get("RRF_K", "60")),
            rrf_vector_weight=float(env.get("RRF_VECTOR_WEIGHT", "0.7")),
            rrf_keyword_weight=float(env.get("RRF_KEYWORD_WEIGHT", "0.3")),
            rerank_threshold=float(env.get("RERANK_THRESHOLD", "2.0")),
            rerank_mode=_parse_choice(
                env.get("RERANK_MODE", "flash"),
                RERANK_MODES,
                "RERANK_MODE",
                aliases={"on": "flash"},
            ),
            dashscope_api_key=env.get("DASHSCOPE_API_KEY", ""),
            bailian_rerank_endpoint=env.get(
                "BAILIAN_RERANK_ENDPOINT", DEFAULT_BAILIAN_RERANK_ENDPOINT
            ),
            bailian_rerank_model=env.get("BAILIAN_RERANK_MODEL", "qwen3.7-text-rerank"),
            rerank_passage=_parse_choice(
                env.get("RERANK_PASSAGE", "body"), ("body", "titled"), "RERANK_PASSAGE"
            ),
            rate_limit_per_minute=int(env.get("RATE_LIMIT_PER_MINUTE", "600")),
            chunk_strategy=env.get("CHUNK_STRATEGY", "auto"),
            chunk_parent_limit=int(env.get("CHUNK_PARENT_LIMIT", "800")),
            chunk_child_limit=int(env.get("CHUNK_CHILD_LIMIT", "200")),
            chunk_overlap=int(env.get("CHUNK_OVERLAP", "40")),
            cookie_secure=env.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes", "on"),
            cors_origins=tuple(
                x.strip() for x in env.get("CORS_ORIGINS", "").split(",") if x.strip()
            ),
            iqs_api_key=env.get("IQS_API_KEY", ""),
            web_search_daily_limit=int(env.get("WEB_SEARCH_DAILY_LIMIT", "200")),
            stream_answer=env.get("STREAM_ANSWER", "1").lower() not in ("0", "false", "no", "off"),
            api_docs=env.get("API_DOCS", "").lower() in ("1", "true", "yes", "on"),
            guest_mode=env.get("GUEST_MODE", "").lower() in ("1", "true", "yes", "on"),
            guest_daily_token_limit=int(env.get("GUEST_DAILY_TOKEN_LIMIT", "50000")),
            guest_session_ttl_days=int(env.get("GUEST_SESSION_TTL_DAYS", "7")),
            open_registration=env.get("OPEN_REGISTRATION", "").lower()
            in ("1", "true", "yes", "on"),
            content_filter_fallback=env.get("CONTENT_FILTER_FALLBACK", "1").lower()
            not in ("0", "false", "no", "off"),
        )
