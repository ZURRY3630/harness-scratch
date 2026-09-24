"""技能工具：列出 / 读取 / 执行技能包里的脚本。

技能是"文档 + CLI 脚本"形态的外部能力，不是 `@tool` 函数，因此按渐进披露分三层暴露：

    list_skills()                        只看索引（极低上下文成本）
    read_skill(skill)                    需要时读 SKILL.md 全文
    run_skill_script(skill, script, ...) 真正执行脚本（默认逐次审批）

未启用的技能三条路全部不可见（`SkillRuntime` 负责拦截）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...sdk.decorator import build_tool, tool
from ..levels import PermissionLevel
from ..registry import Tool

if TYPE_CHECKING:
    from ...skills.runtime import SkillRuntime
    from ..loader import BuiltinFactory, ToolContext

# 脚本参数是自由键值对，无法从函数签名推导，故手写 schema
_RUN_PARAMETERS = {
    "type": "object",
    "properties": {
        "skill": {"type": "string", "description": "技能标识 slug（见 list_skills 的输出）"},
        "script": {"type": "string", "description": "scripts 目录下的脚本文件名，例如 execute_task.py"},
        "params": {
            "type": "object",
            "description": '脚本命令行参数，键名不加 -- 前缀，例如 {"city": "西安", "ratio": "9:16"}',
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["skill", "script"],
}


def _list_skills(ctx: "ToolContext") -> Optional[Tool]:
    runtime = ctx.skills
    if runtime is None:
        return None

    @tool(
        name="list_skills",
        description="列出当前项目已启用的技能（标识、名称、说明、可用脚本）。需要外部能力时先看这里。",
        permission=PermissionLevel.FULL_TRUST,
    )
    def list_skills() -> str:
        manifests = runtime.enabled_manifests()
        if not manifests:
            return "当前项目没有启用任何技能。"
        lines: list[str] = []
        for m in manifests:
            lines.append(f"- {m.slug}（{m.name}）：{m.description}")
            if m.scripts:
                lines.append(f"  脚本：{', '.join(m.scripts)}")
            missing = m.missing_env()
            if missing:
                lines.append(f"  缺少凭证：{', '.join(missing)}（需要先配置才能执行相关脚本）")
        return "\n".join(lines)

    return build_tool(list_skills)


def _read_skill(ctx: "ToolContext") -> Optional[Tool]:
    runtime = ctx.skills
    if runtime is None:
        return None

    @tool(
        name="read_skill",
        description="读取某个技能的完整说明（SKILL.md 正文），确认它是否适用于当前任务以及该如何调用。",
        permission=PermissionLevel.FULL_TRUST,
    )
    def read_skill(skill: str) -> str:
        return runtime.read(skill)

    return build_tool(read_skill)


def _run_skill_script(ctx: "ToolContext") -> Optional[Tool]:
    runtime = ctx.skills
    if runtime is None:
        return None

    @tool(
        name="run_skill_script",
        description="执行技能包里的脚本（argparse CLI），返回脚本的标准输出。每次执行都需要人工审批。",
        permission=PermissionLevel.ASK_FIRST,   # 技能脚本是外部可执行代码，默认逐次审批
        parameters=_RUN_PARAMETERS,
    )
    def run_skill_script(skill: str, script: str, params: Optional[dict] = None) -> str:
        return runtime.run(skill, script, params or {}).to_text()

    return build_tool(run_skill_script)


# 工具名 -> 工厂（依赖缺失即不注册）
TOOLS: dict[str, "BuiltinFactory"] = {
    "list_skills": _list_skills,
    "read_skill": _read_skill,
    "run_skill_script": _run_skill_script,
}
