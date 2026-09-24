# DOC: docs/16-skills.md
"""技能运行时：把「安装根目录 + 项目启用白名单 + 执行器」打包成一个对象交给工具层。

工具层（`tools/builtin/skills.py`）只认这个对象，不关心技能从哪来、装在哪。
**未启用的技能对模型完全不可见**（列表、正文、脚本执行三条路都拦），这是安全默认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .manifest import SkillError, SkillManifest
from .runner import SkillRunResult, SkillRunner
from .store import SkillStore


@dataclass
class SkillRuntime:
    """已安装技能的对外视图（受项目白名单 + 运行时覆盖过滤）。"""

    store: SkillStore
    runner: SkillRunner
    enabled: list[str] = field(default_factory=list)          # 最终生效的 slug
    config_enabled: list[str] = field(default_factory=list)   # config.yaml 里的白名单
    overrides: dict[str, bool] = field(default_factory=dict)  # 界面上的运行时覆盖

    # ----- 查询 -----
    def enabled_manifests(self) -> list[SkillManifest]:
        return self.store.enabled(self.enabled)

    def enabled_source(self, slug: str) -> str:
        """启用状态来自哪里：`override`（界面覆盖）/ `config`（配置文件）/ 空（未启用）。"""
        if slug in self.overrides:
            return "override" if self.overrides[slug] else ""
        return "config" if slug in self.config_enabled else ""

    def get(self, slug: str) -> Optional[SkillManifest]:
        """取已启用的技能；未启用/不存在一律返回 None（对外表现为"没有这个技能"）。"""
        if slug not in self.enabled:
            return None
        return self.store.get(slug)

    def index_lines(self) -> list[str]:
        """一行一个技能的索引（供系统提示词注入）。"""
        lines = []
        for m in self.enabled_manifests():
            scripts = f"；脚本：{', '.join(m.scripts)}" if m.scripts else "；无可执行脚本"
            lines.append(f"- `{m.slug}` — {m.name}：{m.description}{scripts}")
        return lines

    def index_block(self) -> str:
        """拼成可注入提示词的段落；没有启用技能时返回空串。"""
        lines = self.index_lines()
        if not lines:
            return ""
        return (
            "## 可用技能\n\n"
            "下列技能是打包好的外部能力（文档 + 脚本）。需要时先读全文再执行脚本：\n"
            f"{chr(10).join(lines)}\n\n"
            "用法：`read_skill(skill)` 读完整说明，`run_skill_script(skill, script, params)` 执行其中脚本。"
        )

    # ----- 读 / 执行 -----
    def read(self, slug: str) -> str:
        """返回 SKILL.md 正文（未启用则报错文案，避免模型反复试探）。"""
        manifest = self.get(slug)
        if manifest is None:
            return f"[NOT_ENABLED] 技能 '{slug}' 未安装或未在当前项目启用。可用：{self._slug_list()}"
        body = manifest.body
        # 正文自带标题时不重复加（多数技能包第一行就是 # 标题）
        head = "" if body.lstrip().startswith("#") else f"# {manifest.name}\n\n"
        scripts = "\n\n## 可执行脚本\n" + "\n".join(f"- {s}" for s in manifest.scripts) if manifest.scripts else ""
        missing = manifest.missing_env()
        warn = f"\n\n> 注意：缺少环境变量 {missing}，相关脚本暂不可执行。" if missing else ""
        return f"{head}{body}{scripts}{warn}"

    def run(self, slug: str, script: str, params: Optional[dict[str, Any]] = None) -> SkillRunResult:
        """执行技能脚本（未启用则直接拒绝）。"""
        manifest = self.get(slug)
        if manifest is None:
            return SkillRunResult(
                ok=False, exit_code=-1,
                stderr=f"[NOT_ENABLED] 技能 '{slug}' 未安装或未在当前项目启用。可用：{self._slug_list()}",
            )
        return self.runner.run(manifest, script, params)

    # ----- 内部 -----
    def _slug_list(self) -> str:
        slugs = [m.slug for m in self.enabled_manifests()]
        return ", ".join(slugs) if slugs else "(无)"


def build_skill_runtime(
    directory: str | Path,
    enabled: list[str],
    *,
    timeout: float,
    max_output_bytes: int = 20_000,
    overrides: Optional[dict[str, bool]] = None,
) -> SkillRuntime:
    """组装技能运行时。

    Args:
        directory: 安装根目录（不存在时也能安全构造，列表为空）
        enabled: `config.yaml` 的 `skills.enabled` 白名单
        overrides: 运行时覆盖（来自界面/API，持久化在 SQLite），优先级高于配置
    """
    store = SkillStore(Path(directory))
    runner = SkillRunner(timeout=timeout, max_output_bytes=max_output_bytes)
    config_enabled = list(enabled)
    resolved = dict(overrides or {})

    effective = [s for s in config_enabled if resolved.get(s, True)]
    effective += [s for s, on in resolved.items() if on and s not in effective]
    return SkillRuntime(store=store, runner=runner, enabled=effective,
                        config_enabled=config_enabled, overrides=resolved)


__all__ = ["SkillRuntime", "build_skill_runtime", "SkillError"]
