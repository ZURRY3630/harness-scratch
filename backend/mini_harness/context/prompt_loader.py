# DOC: docs/06-configure.md
"""System Prompt 加载器：框架级 base_system.md + 项目级 system.md 拼接 + 模板变量替换。

分层约定（与 Prompt 缓存的前缀稳定原则一致）：
- 框架层 `mini_harness/prompts/base_system.md`：领域无关的行为准则，所有项目共用；
- 项目层 `<project>/prompts/system.md`：角色与领域规则，由配置指定路径；
- 两层都为静态文本（不含时间戳等易变信息），拼接顺序固定，保证 system 前缀逐字稳定。

模板变量：`{{AGENT_NAME}}`、`{{LANGUAGE}}`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Protocol, runtime_checkable

# 框架级提示词目录（随包分发的静态资源）
_FRAMEWORK_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
BASE_PROMPT_FILENAME = "base_system.md"

_DEFAULT_AGENT_NAME = "Agent"
_DEFAULT_LANGUAGE = "中文"


@runtime_checkable
class PromptConfig(Protocol):
    """`load_system_prompt` 需要的最小配置契约（结构化类型）。

    `core.config.ProjectConfig`（P0-1）天然满足该契约；本模块因此不依赖 core.config，
    避免 context 层反向依赖配置层。
    """

    system_prompt_path: Optional[str]
    agent_name: str
    language: str


def base_prompt_path() -> Path:
    """框架级提示词的绝对路径（便于排障与测试断言）。"""
    return _FRAMEWORK_PROMPTS_DIR / BASE_PROMPT_FILENAME


def load_base_system_prompt() -> str:
    """读取框架级基础提示词。文件缺失属安装错误，直接抛出以便暴露。"""
    path = base_prompt_path()
    if not path.exists():
        raise FileNotFoundError(f"框架级 System Prompt 缺失: {path}")
    return path.read_text(encoding="utf-8").strip()


def render_template(template: str, variables: Mapping[str, str]) -> str:
    """替换 `{{KEY}}` 占位符。

    variables 未声明的占位符原样保留（便于发现遗漏，而不是静默变成空串）。
    """
    out = template
    for key, value in variables.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def load_system_prompt(cfg: PromptConfig) -> str:
    """加载 base_system.md + 项目 system.md，替换模板变量。

    - `cfg.system_prompt_path` 为空时只用框架级提示词；
    - 相对路径按当前工作目录解析；
    - 项目提示词文件不存在时抛 FileNotFoundError（配置错误要显式暴露，不静默降级）。
    """
    parts: list[str] = [load_base_system_prompt()]

    raw_path = getattr(cfg, "system_prompt_path", None)
    if raw_path:
        project_path = Path(raw_path)
        if not project_path.is_absolute():
            project_path = (Path.cwd() / project_path).resolve()
        if not project_path.exists():
            raise FileNotFoundError(f"项目级 System Prompt 缺失: {project_path}")
        content = project_path.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)

    variables = {
        "AGENT_NAME": getattr(cfg, "agent_name", "") or _DEFAULT_AGENT_NAME,
        "LANGUAGE": getattr(cfg, "language", "") or _DEFAULT_LANGUAGE,
    }
    return render_template("\n\n".join(parts), variables)
