# DOC: docs/16-skills.md
"""技能包解析：`SKILL.md`（YAML front-matter）+ `_meta.json` -> `SkillManifest`。

技能包目录结构（与官方技能包一致）：

    <slug>/
    ├── _meta.json        可选：{"ownerId","slug","version","publishedAt"}
    ├── SKILL.md          必需：front-matter(name/description/...) + 正文
    └── scripts/*.py      可选：argparse CLI 脚本，stdout 输出 JSON

本模块只做"读 + 校验"，不碰文件系统写操作（安装见 installer.py，执行见 runner.py）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

SKILL_FILE = "SKILL.md"
META_FILE = "_meta.json"
INSTALL_RECORD = ".install.json"
SCRIPTS_DIR = "scripts"

# slug 用于目录名与工具参数：允许 Unicode 词字符（便于中文项目），但禁止分隔符与 ..
_SLUG_RE = re.compile(r"^[^\W.][\w.\-]{0,63}$", re.UNICODE)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

# 脚本里读取环境变量的常见写法（用于自动推断需要注入的凭证）
_ENV_PATTERNS = (
    re.compile(r"""os\.environ(?:\.get)?\s*[\(\[]\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""os\.getenv\s*\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
)

# 带默认值的读取视为"可选配置"（`os.environ.get("X", "default")`）
_ENV_OPTIONAL_PATTERNS = (
    re.compile(r"""os\.environ\.get\s*\(\s*["']([A-Z][A-Z0-9_]*)["']\s*,"""),
    re.compile(r"""os\.getenv\s*\(\s*["']([A-Z][A-Z0-9_]*)["']\s*,"""),
)


class SkillError(Exception):
    """技能包不合法（解析、校验、安装阶段统一抛这个）。"""


@dataclass
class SkillManifest:
    """一个技能的元数据 + 正文 + 运行期推断信息。"""

    slug: str
    name: str
    description: str
    version: str = ""
    owner_id: str = ""
    published_at: int = 0
    dependency: dict[str, Any] = field(default_factory=dict)
    credentials: list[dict[str, str]] = field(default_factory=list)   # [{name, env, description}]
    body: str = ""
    path: Optional[Path] = None
    scripts: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)                 # 需要注入子进程的环境变量（含可选）
    required_env: list[str] = field(default_factory=list)             # 缺失即拒绝执行的必需凭证
    interactive_hint: bool = False                                    # 脚本里有 getpass 之类的交互回退
    install: dict[str, Any] = field(default_factory=dict)             # {source, installed_at}

    # ----- 供工具与 API 使用 -----
    def missing_env(self) -> list[str]:
        """必需但当前环境里没有的凭证（调用方据此给出可行动提示）。"""
        return [name for name in self.required_env if not (os.environ.get(name) or "").strip()]

    def missing_optional_env(self) -> list[str]:
        """可选配置项里当前没配的（不影响执行，仅提示）。"""
        return [name for name in self.env_vars
                if name not in self.required_env and not (os.environ.get(name) or "").strip()]

    def dependencies_declared(self) -> list[str]:
        """front-matter 里声明的 Python 第三方依赖（用于提示"可能装不上"，不做硬校验）。"""
        python_deps = self.dependency.get("python") if isinstance(self.dependency, dict) else None
        if not python_deps:
            return []
        if isinstance(python_deps, str):
            return [] if "无" in python_deps or "standard library" in python_deps.lower() else [python_deps]
        if isinstance(python_deps, list):
            return [str(d) for d in python_deps if "无" not in str(d)]
        return []

    def to_dict(self, *, with_body: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "owner_id": self.owner_id,
            "published_at": self.published_at,
            "dependency": self.dependency,
            "credentials": self.credentials,
            "env_vars": self.env_vars,
            "required_env": self.required_env,
            "missing_env": self.missing_env(),
            "missing_optional_env": self.missing_optional_env(),
            "interactive_hint": self.interactive_hint,
            "dependencies_declared": self.dependencies_declared(),
            "scripts": self.scripts,
            "path": str(self.path) if self.path else "",
            "install": self.install,
        }
        if with_body:
            data["body"] = self.body
        return data


def compose_skill_md(
    *,
    name: str,
    description: str,
    slug: str = "",
    version: str = "",
    credentials: Optional[list[dict[str, Any]]] = None,
    body: str = "",
) -> str:
    """按 front-matter 约定拼一份 SKILL.md（供界面"自定义创建"使用）。"""
    meta: dict[str, Any] = {"name": name, "description": description}
    if slug:
        meta["slug"] = slug
    if version:
        meta["version"] = version
    if credentials:
        meta["credentials"] = credentials
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    content = body.strip() or f"# {name}\n\n{description}\n"
    return f"---\n{front}\n---\n\n{content}\n"


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """拆出 front-matter（字典）与正文。缺 front-matter 直接报错。"""
    match = _FRONT_MATTER_RE.match(text.lstrip("\ufeff"))
    if not match:
        raise SkillError(f"{SKILL_FILE} 缺少 YAML front-matter（需以 --- 包裹）")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"{SKILL_FILE} front-matter 不是合法 YAML: {e}") from e
    if not isinstance(meta, dict):
        raise SkillError(f"{SKILL_FILE} front-matter 必须是映射")
    return meta, match.group(2).strip()


def detect_env_vars(text: str) -> list[str]:
    """从脚本源码里推断需要注入的环境变量。"""
    found: list[str] = []
    for pattern in _ENV_PATTERNS:
        for name in pattern.findall(text):
            if name not in found:
                found.append(name)
    return sorted(found)


def normalize_slug(raw: str) -> str:
    """校验并规范化 slug；非法直接报错（slug 会变成目录名）。"""
    slug = (raw or "").strip()
    if not _SLUG_RE.match(slug) or ".." in slug or "/" in slug or "\\" in slug:
        raise SkillError(f"非法技能标识 slug: {raw!r}（需以字母/数字/汉字开头，可含 . _ -，长度 1~64，不得含路径分隔符）")
    return slug


def slug_from_zip_name(filename: str) -> str:
    """从 `city-weather-poster-generator-showapi-1.0.3.zip` 推出 slug。"""
    stem = Path(filename).stem
    return re.sub(r"[-_]v?\d+(\.\d+)*$", "", stem) or stem


def slugify(name: str) -> str:
    """把技能名兜底转成合法 slug（无显式声明时用）；保留汉字与词字符。"""
    slug = re.sub(r"[^\w.\-]+", "-", (name or "").strip(), flags=re.UNICODE).strip("-.")
    return slug[:64]


def load_manifest(skill_dir: Path, *, slug: Optional[str] = None) -> SkillManifest:
    """读取一个已落盘的技能目录。

    slug 取值优先级：`_meta.json.slug` > front-matter 的 `slug` > 入参 `slug` > 技能名兜底。
    显式声明的永远优先，避免安装来源（文件名/目录名）覆盖作者的意图。

    Args:
        skill_dir: 技能目录（需含 SKILL.md）
        slug: 兜底标识（通常是 zip 文件名或源目录名）
    """
    skill_md = skill_dir / SKILL_FILE
    if not skill_md.is_file():
        raise SkillError(f"技能缺少 {SKILL_FILE}: {skill_dir}")
    meta, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))

    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description:
        raise SkillError(f"{SKILL_FILE} 必须包含 name 与 description")

    file_meta: dict[str, Any] = {}
    meta_path = skill_dir / META_FILE
    if meta_path.is_file():
        try:
            file_meta = json.loads(meta_path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError as e:
            raise SkillError(f"{META_FILE} 不是合法 JSON: {e}") from e

    raw_slug = (str(file_meta.get("slug") or "").strip()
                or str(meta.get("slug") or "").strip()
                or (slug or "").strip()
                or slugify(name))
    if not raw_slug:
        raise SkillError("无法推断技能标识：请在 SKILL.md front-matter 或 _meta.json 里声明 slug")
    resolved_slug = normalize_slug(raw_slug)

    scripts = _collect_scripts(skill_dir)
    credentials = _normalize_credentials(meta.get("credentials"))
    declared_all = {c["env"] for c in credentials if c.get("env")}
    declared_required = {c["env"] for c in credentials if c.get("env") and c.get("required", True)}
    detected, detected_required = _detect_from_scripts(skill_dir, scripts)
    # 显式声明优先：声明为可选的变量不再因为"脚本里没给默认值"而被判为必需
    required_env = declared_required | {n for n in detected_required if n not in declared_all}

    install: dict[str, Any] = {}
    record = skill_dir / INSTALL_RECORD
    if record.is_file():
        try:
            install = json.loads(record.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            install = {}

    return SkillManifest(
        slug=resolved_slug,
        name=name,
        description=description,
        version=str(meta.get("version") or file_meta.get("version") or ""),
        owner_id=str(file_meta.get("ownerId") or ""),
        published_at=int(file_meta.get("publishedAt") or 0),
        dependency=meta.get("dependency") if isinstance(meta.get("dependency"), dict) else {},
        credentials=credentials,
        body=body,
        path=skill_dir,
        scripts=scripts,
        env_vars=sorted(set(declared_all) | set(detected)),
        required_env=sorted(required_env),
        interactive_hint=_has_interactive_fallback(skill_dir, scripts),
        install=install,
    )


def _collect_scripts(skill_dir: Path) -> list[str]:
    scripts_dir = skill_dir / SCRIPTS_DIR
    if not scripts_dir.is_dir():
        return []
    return sorted(p.name for p in scripts_dir.glob("*.py") if p.is_file())


def _detect_from_scripts(skill_dir: Path, scripts: list[str]) -> tuple[list[str], list[str]]:
    """返回 (全部环境变量, 必需环境变量)。带默认值的读取算可选。"""
    found: list[str] = []
    required: list[str] = []
    for name in scripts:
        try:
            text = (skill_dir / SCRIPTS_DIR / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        optional = {m for pattern in _ENV_OPTIONAL_PATTERNS for m in pattern.findall(text)}
        for env_name in detect_env_vars(text):
            if env_name not in found:
                found.append(env_name)
            if env_name not in optional and env_name not in required:
                required.append(env_name)
    return found, required


def _has_interactive_fallback(skill_dir: Path, scripts: list[str]) -> bool:
    for name in scripts:
        try:
            text = (skill_dir / SCRIPTS_DIR / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "getpass" in text or "input(" in text:
            return True
    return False


def _normalize_credentials(raw: Any) -> list[dict[str, str]]:
    """把 front-matter 的 credentials 统一成 [{name, env, description}]。"""
    if not raw:
        return []
    out: list[dict[str, str]] = []
    if isinstance(raw, dict):
        raw = [{"name": k, **({"env": v} if isinstance(v, str) else v)} for k, v in raw.items()]
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item, "env": item.upper(), "description": "", "required": True})
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            env = str(item.get("env") or name.upper()).strip()
            if name or env:
                out.append({
                    "name": name or env,
                    "env": env,
                    "description": str(item.get("description") or ""),
                    "required": bool(item.get("required", True)),
                })
    return out
