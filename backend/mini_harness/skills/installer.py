# DOC: docs/16-skills.md
"""技能安装：上传的 zip / URL 直链 / 本地目录（自己创建）。

安全策略（技能包 = 可执行代码，安装即引入风险，故"约束优先"）：
- 解压前逐条校验：拒绝绝对路径、`..`、符号链接、非白名单后缀、超限体积；
- 校验与解压都在 staging 目录完成，最后原子替换目标目录，失败不留半成品；
- URL 安装只允许 http/https，限制大小与超时，并校验 zip 魔数。

安装根目录由 `SkillStore.root` 决定（配置 `skills.dir`）。
"""

from __future__ import annotations

import io
import shutil
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import urlsplit

from ..observability.logging import get_logger
from .manifest import SKILL_FILE, SkillError, SkillManifest, load_manifest, slug_from_zip_name
from .store import SkillStore

log = get_logger(__name__)

# 只允许这些后缀进技能目录：挡住 .exe/.dll/.so 之类的可执行二进制
_ALLOWED_SUFFIXES = frozenset({
    ".md", ".json", ".yaml", ".yml", ".py", ".txt", ".toml", ".csv",
    ".png", ".jpg", ".jpeg", ".svg", ".gif", ".html", ".css",
})
_META_FILE = "_meta.json"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


@dataclass
class InstallLimits:
    """安装体积上限（防 zip 炸弹与超大包）。"""

    max_files: int = 300
    max_file_bytes: int = 5 * 1024 * 1024
    max_total_bytes: int = 20 * 1024 * 1024


class SkillInstaller:
    """把技能包装进 `SkillStore.root`。"""

    def __init__(self, store: SkillStore, limits: Optional[InstallLimits] = None) -> None:
        self.store = store
        self.limits = limits or InstallLimits()

    # ----- 三种来源 -----
    def install_zip_bytes(self, data: bytes, *, source: str = "upload", slug_hint: str = "") -> SkillManifest:
        """从 zip 字节流安装（前端上传走这里）。"""
        staging = self._new_staging()
        try:
            self._extract_zip(data, staging)
            return self._finalize(staging, source=source, slug_hint=slug_hint)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def install_zip_file(self, path: str | Path, *, source: str = "") -> SkillManifest:
        """从本地 zip 文件安装。"""
        zip_path = Path(path)
        if not zip_path.is_file():
            raise SkillError(f"技能包不存在: {zip_path}")
        data = zip_path.read_bytes()
        return self.install_zip_bytes(data, source=source or f"file:{zip_path.name}",
                                     slug_hint=slug_from_zip_name(zip_path.name))

    def install_dir(self, src: str | Path, *, source: str = "local") -> SkillManifest:
        """从本地目录安装（自己创建技能的推荐方式）。"""
        source_dir = Path(src).resolve()
        if not (source_dir / SKILL_FILE).is_file():
            raise SkillError(f"目录里没有 {SKILL_FILE}: {source_dir}")
        staging = self._new_staging()
        try:
            shutil.copytree(source_dir, staging, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".install.json", "__pycache__", "*.pyc"))
            return self._finalize(staging, source=source or f"dir:{source_dir.name}",
                                 slug_hint=source_dir.name)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def install_url(self, url: str, *, timeout: float = 30.0) -> SkillManifest:
        """从 http(s) 直链下载 zip 后安装。"""
        parsed = urlsplit((url or "").strip())
        if parsed.scheme not in ("http", "https"):
            raise SkillError(f"只支持 http/https 直链，收到: {parsed.scheme or '(空)'}")

        limit = self.limits.max_total_bytes
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 —— 已限制协议
                data = resp.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise SkillError(f"下载失败: HTTP {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise SkillError(f"下载失败: {e.reason}") from e
        except OSError as e:
            raise SkillError(f"下载失败: {e}") from e

        if len(data) > limit:
            raise SkillError(f"技能包超过上限 {limit} 字节，已中止下载")
        if not data.startswith(_ZIP_MAGIC):
            raise SkillError("下载内容不是 zip 技能包（魔数校验失败）")

        filename = PurePosixPath(parsed.path).name
        return self.install_zip_bytes(data, source=f"url:{url}",
                                     slug_hint=slug_from_zip_name(filename) if filename.endswith(".zip") else "")

    # ----- 内部：staging 与落盘 -----
    def _new_staging(self) -> Path:
        staging_root = self.store.root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / uuid.uuid4().hex[:12]
        staging.mkdir()
        return staging

    def _finalize(self, staging: Path, *, source: str, slug_hint: str) -> SkillManifest:
        """校验技能结构 -> 解析 manifest -> 原子替换到 `root/<slug>`。"""
        root = self._resolve_skill_root(staging)
        probe = load_manifest(root, slug=slug_hint or None)   # 非法包在这里被拒

        target = self.store.path_of(probe.slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            trash = self.store.root / f".trash-{probe.slug}-{int(time.time())}"
            target.rename(trash)
            shutil.rmtree(trash, ignore_errors=True)
        shutil.move(str(root), str(target))
        shutil.rmtree(staging, ignore_errors=True)            # 清掉 staging 残留（含被套的那层目录）

        self.store.write_install_record(target, source)
        manifest = load_manifest(target)
        log.info("技能安装完成", skill=manifest.slug, version=manifest.version,
                 source=source, scripts=len(manifest.scripts))
        return manifest

    def _resolve_skill_root(self, staging: Path) -> Path:
        """兼容两种打包方式：SKILL.md 在根，或整体被套了一层目录。"""
        if (staging / SKILL_FILE).is_file():
            return staging
        children = [p for p in staging.iterdir() if p.is_dir() and not p.name.startswith(".")]
        if len(children) == 1 and (children[0] / SKILL_FILE).is_file():
            return children[0]
        raise SkillError(f"技能包缺少根级 {SKILL_FILE}（允许整体套一层目录）")

    # ----- 内部：zip 校验与解压 -----
    def _extract_zip(self, data: bytes, dest: Path) -> None:
        if not data.startswith(_ZIP_MAGIC):
            raise SkillError("不是合法的 zip 文件")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as e:
            raise SkillError(f"zip 解析失败: {e}") from e

        with archive:
            infos = archive.infolist()
            if len(infos) > self.limits.max_files:
                raise SkillError(f"zip 条目数 {len(infos)} 超过上限 {self.limits.max_files}")

            total = 0
            for info in infos:
                self._check_entry(info)
                if info.is_dir():
                    continue
                total += info.file_size
                if total > self.limits.max_total_bytes:
                    raise SkillError(f"解压后体积超过上限 {self.limits.max_total_bytes} 字节")
            archive.extractall(dest)

        # 复核实际落盘体积：header 里的 file_size 不可全信
        actual = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
        if actual > self.limits.max_total_bytes:
            raise SkillError(f"解压后实际体积 {actual} 字节超过上限 {self.limits.max_total_bytes}")

    def _check_entry(self, info: zipfile.ZipInfo) -> None:
        """单条 zip 条目的安全校验（路径穿越 / 符号链接 / 体积 / 后缀）。"""
        name = info.filename.replace("\\", "/")
        parts = [p for p in name.split("/") if p]

        if info.is_dir():
            return
        if not parts or name.startswith("/") or ":" in parts[0] or ".." in parts:
            raise SkillError(f"zip 内存在越界路径: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:      # S_IFLNK
            raise SkillError(f"zip 内含符号链接，已拒绝: {info.filename}")
        if info.file_size > self.limits.max_file_bytes:
            raise SkillError(f"单文件超过上限 {self.limits.max_file_bytes} 字节: {info.filename}")

        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise SkillError(f"不允许的文件类型 {suffix or '(无后缀)'}: {info.filename}")
