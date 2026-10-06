"""config 层测试：.env 加载语义与 Settings 缺省值（对齐 Go config.Load 行为）。"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ---------- P42：内容审查兜底开关 ----------


def test_content_filter_fallback_default_on_and_off():
    assert Settings.load(env={}).content_filter_fallback is True  # 缺省开
    for v in ("0", "false", "no", "off"):
        assert Settings.load(env={"CONTENT_FILTER_FALLBACK": v}).content_filter_fallback is False


def test_settings_reads_iqs_env_mapping():
    s = Settings.load(env={"IQS_API_KEY": "k3", "WEB_SEARCH_DAILY_LIMIT": "50"})
    assert s.iqs_api_key == "k3"
    assert s.web_search_daily_limit == 50


# ---------- P41：精排引擎配置 ----------


def test_rerank_mode_on_alias_and_fail_fast():
    assert Settings.load(env={"RERANK_MODE": "on"}).rerank_mode == "flash"  # 历史别名
    assert Settings.load(env={"RERANK_MODE": "Bailian"}).rerank_mode == "bailian"  # 大小写归一
    assert Settings.load(env={}).rerank_mode == "flash"  # 缺省零行为变化
    with pytest.raises(ValueError, match="flash"):
        Settings.load(env={"RERANK_MODE": "weird"})  # fail-fast：拼错不静默当缺省


def test_bailian_settings_defaults_and_env_mapping():
    s = Settings.load(env={})
    assert s.dashscope_api_key == ""  # 空 = bailian 模式关断
    assert s.bailian_rerank_endpoint.startswith("https://")
    assert s.bailian_rerank_model == "qwen3.7-text-rerank"
    assert s.rerank_passage == "body"
    s2 = Settings.load(
        env={
            "DASHSCOPE_API_KEY": "bk",
            "BAILIAN_RERANK_ENDPOINT": "https://x.example",
            "BAILIAN_RERANK_MODEL": "gte-rerank-v2",
            "RERANK_PASSAGE": "titled",
        }
    )
    assert s2.dashscope_api_key == "bk"
    assert s2.bailian_rerank_endpoint == "https://x.example"
    assert s2.bailian_rerank_model == "gte-rerank-v2"
    assert s2.rerank_passage == "titled"
    with pytest.raises(ValueError, match="RERANK_PASSAGE"):
        Settings.load(env={"RERANK_PASSAGE": "nope"})


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
