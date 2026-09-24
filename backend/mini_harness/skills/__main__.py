"""技能管理 CLI：`python -m mini_harness.skills <命令>`

    python -m mini_harness.skills install examples/skills/hello-world      # 安装本地目录（自己创建）
    python -m mini_harness.skills install dist/skill-1.0.0.zip             # 安装本地 zip
    python -m mini_harness.skills install --url https://host/skill.zip    # 从网上直链安装
    python -m mini_harness.skills list                                    # 已安装 + 是否在本项目启用
    python -m mini_harness.skills remove <slug>                           # 卸载

技能安装根目录取项目配置的 `skills.dir`（`--config` 指定项目配置，`--dir` 直接覆盖）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.config import REPO_ROOT, load_project_config
from .installer import SkillInstaller
from .manifest import SkillError
from .store import SkillStore

_DEFAULT_CONFIG = REPO_ROOT / "projects" / "default" / "config.yaml"


def _resolve(args: argparse.Namespace) -> tuple[SkillStore, list[str]]:
    """返回 (技能仓库, 该项目启用的 slug 列表)。"""
    if args.dir:
        return SkillStore(Path(args.dir)), []
    cfg = load_project_config(args.config or str(_DEFAULT_CONFIG))
    return SkillStore(Path(cfg.skills["dir"])), list(cfg.skills["enabled"])


def _cmd_install(args: argparse.Namespace) -> int:
    store, _ = _resolve(args)
    installer = SkillInstaller(store)
    if args.url:
        manifest = installer.install_url(args.url)
    else:
        target = Path(args.source or "")
        if target.is_dir():
            manifest = installer.install_dir(target)
        elif target.is_file():
            manifest = installer.install_zip_file(target)
        else:
            print(f"来源不存在（既不是目录也不是文件）: {target}", file=sys.stderr)
            return 1
    print(f"已安装: {manifest.slug} v{manifest.version or '-'} -> {manifest.path}")
    if manifest.scripts:
        print(f"  脚本: {', '.join(manifest.scripts)}")
    if manifest.missing_env():
        print(f"  待配置凭证: {', '.join(manifest.missing_env())}（写入仓库根 .env 后即可执行）")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store, enabled = _resolve(args)
    manifests = store.list()
    if not manifests:
        print(f"未安装任何技能（安装根目录: {store.root}）")
        return 0
    for m in manifests:
        flag = "已启用" if m.slug in enabled else "未启用"
        missing = f" | 缺凭证: {', '.join(m.missing_env())}" if m.missing_env() else ""
        print(f"- {m.slug} v{m.version or '-'} [{flag}] {m.name}{missing}")
        if m.scripts:
            print(f"    脚本: {', '.join(m.scripts)}")
    print(f"\n安装根目录: {store.root}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    store, _ = _resolve(args)
    if not store.remove(args.slug):
        print(f"技能不存在: {args.slug}", file=sys.stderr)
        return 1
    print(f"已卸载: {args.slug}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m mini_harness.skills", description="技能管理")
    parser.add_argument("--config", help=f"项目配置路径（默认 {_DEFAULT_CONFIG}）")
    parser.add_argument("--dir", help="直接指定技能安装根目录（覆盖配置）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="安装技能（目录 / zip / URL）")
    p_install.add_argument("source", nargs="?", help="本地目录或 zip 文件路径")
    p_install.add_argument("--url", help="http(s) 直链 zip")
    p_install.set_defaults(func=_cmd_install)

    p_list = sub.add_parser("list", help="列出已安装技能")
    p_list.set_defaults(func=_cmd_list)

    p_remove = sub.add_parser("remove", help="卸载技能")
    p_remove.add_argument("slug")
    p_remove.set_defaults(func=_cmd_remove)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SkillError as e:
        print(f"技能操作失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
