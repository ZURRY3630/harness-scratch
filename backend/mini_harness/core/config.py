# DOC: docs/06-configure.md
"""统一配置，两层分离：

- 环境变量层 `Config` / `get_config()`：部署相关信息（密钥、端点、路径、运维参数）；
- 领域层 `ProjectConfig` / `load_project_config()`：从 YAML 加载的项目/角色配置。

`load_project_config` 是两层的唯一交汇点：YAML 里写 `null` 或省略的字段会在加载时
回填环境变量层的值，因此产物 `ProjectConfig` 始终是具体值，组装层不再做任何取值判断。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

# backend/.env 优先，其次项目根 .env
_HERE = Path(__file__).resolve().parent
load_dotenv(_HERE.parent.parent / ".env")
load_dotenv(_HERE.parent.parent.parent / ".env")

# 仓库根目录（backend/mini_harness/core/config.py -> parents[3]）
REPO_ROOT = Path(__file__).resolve().parents[3]
# 全局默认值文件；所有项目配置都在它之上做覆盖
DEFAULTS_PATH = REPO_ROOT / "configs" / "default.yaml"


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


# ======================================================================
# 领域层：ProjectConfig（YAML 驱动，与上面的环境变量层分离）
# ======================================================================
@dataclass
class ProjectConfig:
    """一个项目的完整配置。

    字段取值规则：
    - YAML 写了具体值 -> 用它；
    - YAML 写 `null` 或省略 -> 回填环境变量层（`Config`）的值；
    - 路径类字段在加载时统一解析为绝对路径（相对路径按**配置文件所在目录**解析）。

    因此本对象对外始终是具体值，组装层（build_engine）只做构造与注入，不做取值判断。
    """

    name: str
    agent_name: str
    language: str
    system_prompt_path: Optional[str]          # 项目级 System Prompt（md）；为空则只用框架级
    provider: dict[str, Any]                   # {"type": "openai", "model": ..., ...}
    memory: dict[str, Any]                     # {"type": "sqlite", "path": ..., "longterm_top_k": ...}
    budget: dict[str, Any]
    compressor: dict[str, Any]
    assembler: dict[str, Any]
    tools: dict[str, Any]                      # {"builtin": [...], "plugins_dir": "..."}
    permission: dict[str, Any]                 # {"allowed_paths": [...], "approval_store": bool}
    hooks: list[str]                           # Hook 类的导入路径（P0-3）
    max_turns: int
    tool_timeout: float


def _read_yaml(path: Path, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"配置文件不存在: {path}")
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是映射: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并：override 覆盖 base；同为 dict 时向下合并，其余类型直接替换。"""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _pick(value: Any, fallback: Any) -> Any:
    """YAML 值为空（null / 空串）时回退环境变量层。"""
    return fallback if value in (None, "") else value


def _resolve_path(value: Any, base_dir: Path) -> Optional[str]:
    """相对路径按配置文件所在目录解析为绝对路径；空值返回 None。"""
    if value in (None, ""):
        return None
    p = Path(str(value))
    return str(p if p.is_absolute() else (base_dir / p).resolve())


def _resolve_or_env(value: Any, env_value: str, base_dir: Path) -> str:
    """有环境变量兜底的路径字段。

    YAML 写了路径 -> 按配置文件所在目录解析；YAML 留空 -> 原样沿用环境变量层的值
    （保持历史语义：`MEMORY_DB_PATH=data/harness.db` 相对**进程工作目录**）。
    """
    if value in (None, ""):
        return env_value
    return str(_resolve_path(value, base_dir))


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"配置项 {key!r} 必须是映射，实际是 {type(value).__name__}")
    return value


def load_project_config(path: str, env: Optional[Config] = None) -> ProjectConfig:
    """加载项目配置：`configs/default.yaml` 打底 + 目标文件覆盖 + 环境变量层回填补齐。

    Args:
        path: 项目配置 YAML 路径（相对路径按当前工作目录解析）。
        env:  环境变量层；默认取 `get_config()`（测试可注入）。
    """
    env = env or get_config()

    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"项目配置不存在: {config_path}")

    raw = _deep_merge(_read_yaml(DEFAULTS_PATH, required=True), _read_yaml(config_path, required=True))
    base_dir = config_path.parent

    provider_raw = _section(raw, "provider")
    memory_raw = _section(raw, "memory")
    budget_raw = _section(raw, "budget")
    compressor_raw = _section(raw, "compressor")
    tools_raw = _section(raw, "tools")
    permission_raw = _section(raw, "permission")

    return ProjectConfig(
        name=str(raw.get("name") or config_path.stem),
        agent_name=str(raw.get("agent_name") or "Agent"),
        language=str(raw.get("language") or "中文"),
        system_prompt_path=_resolve_path(raw.get("system_prompt_path"), base_dir),
        provider={
            "type": str(provider_raw.get("type") or "openai"),
            "model": _pick(provider_raw.get("model"), env.model),
            "api_key": _pick(provider_raw.get("api_key"), env.api_key),
            "base_url": _pick(provider_raw.get("base_url"), env.base_url),
            "temperature": float(_pick(provider_raw.get("temperature"), 0.0)),
            "max_retries": int(_pick(provider_raw.get("max_retries"), 2)),
            "request_timeout": float(_pick(provider_raw.get("request_timeout"), 120.0)),
        },
        memory={
            "type": str(memory_raw.get("type") or "sqlite"),
            "path": _resolve_or_env(memory_raw.get("path"), env.memory_db_path, base_dir),
            "longterm_top_k": int(_pick(memory_raw.get("longterm_top_k"), env.longterm_top_k)),
        },
        budget={
            "context_token_budget": int(_pick(budget_raw.get("context_token_budget"), env.context_token_budget)),
            "reserve_output_tokens": int(_pick(budget_raw.get("reserve_output_tokens"), env.reserve_output_tokens)),
            "compress_threshold": float(_pick(budget_raw.get("compress_threshold"), env.compress_threshold)),
        },
        compressor={"keep_recent": int(_pick(compressor_raw.get("keep_recent"), env.keep_recent_messages))},
        assembler=_section(raw, "assembler"),
        tools={
            "builtin": [str(n) for n in (tools_raw.get("builtin") or [])],
            "plugins_dir": _resolve_path(tools_raw.get("plugins_dir"), base_dir),
        },
        permission={
            "allowed_paths": [_resolve_path(p, base_dir) for p in (permission_raw.get("allowed_paths") or [])],
            "approval_store": bool(_pick(permission_raw.get("approval_store"), True)),
        },
        hooks=[str(h) for h in (raw.get("hooks") or [])],
        max_turns=int(_pick(raw.get("max_turns"), env.max_turns)),
        tool_timeout=float(_pick(raw.get("tool_timeout"), env.tool_timeout_seconds)),
    )
