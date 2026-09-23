"""统一配置：.env 全量生效，分层覆盖（默认值 -> 环境变量）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# backend/.env 优先，其次项目根 .env
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE.parent.parent / ".env")
load_dotenv(_HERE.parent.parent.parent / ".env")


def _env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    return v if v not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)) or default)
    except ValueError:
        return default


@dataclass
class Config:
    """全局配置（单例：get_config）。

    每个 LLM_* 项都有 OPENAI_* 兼容回退，保持与旧版 .env 的兼容。
    """

    # --- LLM ---
    api_key: str = field(default_factory=lambda: _env("LLM_API_KEY") or _env("OPENAI_API_KEY") or "")
    base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL") or _env("OPENAI_BASE_URL") or "")
    model: str = field(default_factory=lambda: _env("LLM_MODEL") or _env("OPENAI_MODEL") or "gpt-4o-mini")

    # --- Agent 行为 ---
    max_turns: int = field(default_factory=lambda: _env_int("MAX_TURNS", 10))
    max_steps: int = field(default_factory=lambda: _env_int("MAX_STEPS", 10))
    tool_timeout_seconds: float = field(default_factory=lambda: float(_env_int("EXECUTION_TIMEOUT_SECONDS", 300)))

    # --- Token 预算 / 上下文 ---
    context_token_budget: int = field(default_factory=lambda: _env_int("CONTEXT_TOKEN_BUDGET", 24000))
    reserve_output_tokens: int = field(default_factory=lambda: _env_int("RESERVE_OUTPUT_TOKENS", 4096))
    compress_threshold: float = field(default_factory=lambda: float(_env("COMPRESS_THRESHOLD", "0.8")))
    keep_recent_messages: int = field(default_factory=lambda: _env_int("KEEP_RECENT_MESSAGES", 8))

    # --- 长期记忆 ---
    memory_db_path: str = field(default_factory=lambda: _env("MEMORY_DB_PATH", "data/harness.db"))
    longterm_top_k: int = field(default_factory=lambda: _env_int("LONGTERM_TOP_K", 3))

    # --- 运行 ---
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000"))

    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
