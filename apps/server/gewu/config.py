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
    rerank_threshold: float = 2.0  # LLM 精排模型分阈值（0~10）；全滤空自动退化
    rerank_mode: str = "on"  # on（默认，LLM 精排）| off
    react_mode: str = "off"  # off（默认，纯 workflow）| on（路径不定的办理问题转 ReAct）
    query_rewrite: str = "on"  # on（默认，多轮指代消解补全）| off（路由/检索只见裸问题）
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
            rerank_mode=env.get("RERANK_MODE", "on"),
            react_mode=env.get("REACT_MODE", "off"),
            query_rewrite=env.get("QUERY_REWRITE", "on"),
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
        )
