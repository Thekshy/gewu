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
    retrieval_k: int = 6
    rerank_mode: str = "on"  # on（默认，LLM 精排）| off

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
            retrieval_k=int(env.get("RETRIEVAL_K", "6")),
            rerank_mode=env.get("RERANK_MODE", "on"),
        )
