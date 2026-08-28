"""全局配置：环境变量 / 仓库根 .env 双来源，模型无关的 provider 设置。"""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # 任意 OpenAI 兼容端点：智谱 / DeepSeek / OpenAI / 本地 vLLM 均可
    llm_api_key: str = ""
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    llm_model: str = "glm-5.3"
    embed_model: str = "embedding-3"
    # 推理型模型（如 glm-5.3）关闭思考直出答案：分类/抽取/作答更快更省。
    # thinking 为智谱私有参数，OpenAI 等端点不识别会报错，故默认关闭、按需开启。
    llm_disable_thinking: bool = False

    data_dir: Path = _REPO_ROOT / "data"
    corpus_dir: Path | None = None
    index_path: Path | None = None

    # 公开 demo 的两道防线
    rate_limit_per_minute: int = 20
    daily_token_budget: int = 2_000_000

    retrieval_k: int = 6
    max_question_chars: int = 500

    @model_validator(mode="after")
    def _derive_paths(self) -> "Settings":
        if self.corpus_dir is None:
            self.corpus_dir = self.data_dir / "corpus"
        if self.index_path is None:
            self.index_path = self.data_dir / "index.db"
        return self

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
