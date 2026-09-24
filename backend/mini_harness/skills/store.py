# DOC: docs/16-skills.md
"""技能清单：扫描安装根目录，提供列表 / 查询 / 卸载 / 项目白名单过滤。

安装根目录由配置 `skills.dir` 决定（默认 `<仓库>/data/skills/`）。本模块只读 + 卸载，
安装写入见 installer.py。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

from ..observability.logging import get_logger
from .manifest import SKILL_FILE, SkillError, SkillManifest, load_manifest, normalize_slug

log = get_logger(__name__)


class SkillStore:
    """已安装技能的唯一读入口。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list(self) -> list[SkillManifest]:
        """列出全部可用技能（按 slug 排序）；单个技能损坏时跳过并告警，不影响其余。"""
        if not self.root.is_dir():
            return []
        manifests: list[SkillManifest] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / SKILL_FILE).is_file():
                continue
            try:
                manifests.append(load_manifest(child))
            except SkillError as e:
                log.warning("技能清单解析失败，已跳过", skill=child.name, error=str(e))
        return manifests

    def get(self, slug: str) -> Optional[SkillManifest]:
        """按 slug 取技能；不存在或损坏返回 None。"""
        try:
            path = self.path_of(slug)
        except SkillError:
            return None
        if not (path / SKILL_FILE).is_file():
            return None
        try:
            return load_manifest(path)
        except SkillError as e:
            log.warning("技能清单解析失败", skill=slug, error=str(e))
            return None

    def path_of(self, slug: str) -> Path:
        """slug -> 目录绝对路径（校验 slug，防目录穿越）。"""
        return (self.root / normalize_slug(slug)).resolve()

    def enabled(self, slugs: list[str]) -> list[SkillManifest]:
        """按项目白名单过滤出可用技能；白名单里不存在/损坏的会告警。"""
        out: list[SkillManifest] = []
        for slug in slugs:
            manifest = self.get(slug)
            if manifest is None:
                log.warning("项目启用的技能不存在，已忽略", skill=slug, root=str(self.root))
                continue
            out.append(manifest)
        return out

    def remove(self, slug: str) -> bool:
        """卸载：整目录删除。返回是否确实删掉了东西。"""
        path = self.path_of(slug)
        if not path.is_dir() or path == self.root.resolve() or self.root.resolve() not in path.parents:
            return False
        # 先改名再删，避免删到一半留下半成品目录
        trash = self.root / f".trash-{slug}-{int(time.time())}"
        try:
            path.rename(trash)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)
            return True
        shutil.rmtree(trash, ignore_errors=True)
        log.info("技能已卸载", skill=slug)
        return True

    def write_install_record(self, path: Path, source: str) -> None:
        """在技能目录里留一条安装记录（来源与时间），便于审计与排障。"""
        from .manifest import INSTALL_RECORD

        record = {"source": source, "installed_at": int(time.time()), "installed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S")}
        try:
            (path / INSTALL_RECORD).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:  # noqa: BLE001 —— 记录写失败不影响安装结果
            log.warning("安装记录写入失败", skill=path.name, error=str(e))
