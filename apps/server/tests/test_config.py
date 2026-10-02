"""config 层测试：.env 加载语义与 Settings 缺省值（对齐 Go config.Load 行为）。"""

from __future__ import annotations

from pathlib import Path

from gewu.config import (
    DEFAULT_DAILY_TOKEN_BUDGET,
    DEFAULT_PG_DSN,
    Settings,
    anchor_dir,
    find_dotenv,
    load_dotenv,
)


def test_settings_defaults():
    s = Settings.load(env={})
    assert s.llm_api_key == ""
    assert s.embed_api_key == ""
    assert s.pg_dsn == DEFAULT_PG_DSN
    assert s.daily_token_budget == DEFAULT_DAILY_TOKEN_BUDGET
    assert s.iqs_api_key == ""  # P26：key 空=联网整链关闭
    assert s.web_search_daily_limit == 200


def test_settings_reads_iqs_env_mapping():
    s = Settings.load(env={"IQS_API_KEY": "k3", "WEB_SEARCH_DAILY_LIMIT": "50"})
    assert s.iqs_api_key == "k3"
    assert s.web_search_daily_limit == 50


def test_settings_reads_env_mapping(tmp_path: Path):
    s = Settings.load(
        env={
            "LLM_API_KEY": "k1",
            "EMBED_API_KEY": "k2",
            "PG_DSN": "postgres://x/y",
            "DATA_DIR": str(tmp_path),
            "DAILY_TOKEN_BUDGET": "123",
        }
    )
    assert s.llm_api_key == "k1"
    assert s.embed_api_key == "k2"
    assert s.pg_dsn == "postgres://x/y"
    assert s.data_dir == tmp_path
    assert s.daily_token_budget == 123


def test_load_dotenv_skips_comments_and_respects_existing(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# 注释\n\nLLM_API_KEY=from-file\nPG_DSN='quoted-dsn'\nBAD_LINE\n",
        encoding="utf-8",
    )
    env: dict[str, str] = {"LLM_API_KEY": "already-set"}
    load_dotenv(env_file, env)
    assert env["LLM_API_KEY"] == "already-set"  # 已有变量不覆盖
    assert env["PG_DSN"] == "quoted-dsn"  # 引号剥离
    assert len(env) == 2  # BAD_LINE 无 = 被跳过


def test_find_dotenv_walks_up(tmp_path: Path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_dotenv(nested) is None  # 链上尚无 .env
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    assert find_dotenv(nested) == tmp_path / ".env"  # 从子目录向上找到


def test_anchor_dir_prefers_dotenv_parent(tmp_path: Path):
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    assert anchor_dir(tmp_path / "sub") == tmp_path
