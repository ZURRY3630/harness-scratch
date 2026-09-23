"""场景 2 验证用领域工具：证明项目工具目录自动装载。"""

from __future__ import annotations

from mini_harness.sdk.decorator import tool
from mini_harness.tools.levels import PermissionLevel


@tool(
    name="lint_rule",
    description="查询某条代码规范的说明（演示项目专属工具）。",
    permission=PermissionLevel.FULL_TRUST,
)
def lint_rule(rule_id: str) -> str:
    return f"规则 {rule_id}：禁止在循环内做重复 IO，建议提前批量读取。"
