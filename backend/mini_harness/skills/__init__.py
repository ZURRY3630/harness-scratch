"""技能子系统：安装（zip / URL / 本地目录）、清单、脚本执行。

对外入口（详见 docs/16-skills.md）：

    SkillStore        已安装技能的读入口（列表 / 查询 / 卸载）
    SkillInstaller    安装（upload / url / dir）
    SkillRunner       在受限环境里执行 scripts/*.py
    SkillRuntime      项目视角的运行时（按白名单过滤后交给工具层）
    SkillManifest     SKILL.md + _meta.json 解析结果
"""

from __future__ import annotations

from .installer import InstallLimits, SkillInstaller
from .manifest import SkillError, SkillManifest, load_manifest, parse_skill_md, slugify, slug_from_zip_name
from .runner import SkillRunResult, SkillRunner
from .runtime import SkillRuntime, build_skill_runtime
from .store import SkillStore

__all__ = [
    "SkillError",
    "SkillManifest",
    "SkillStore",
    "SkillInstaller",
    "SkillRunner",
    "SkillRunResult",
    "SkillRuntime",
    "InstallLimits",
    "load_manifest",
    "parse_skill_md",
    "slugify",
    "slug_from_zip_name",
    "build_skill_runtime",
]
