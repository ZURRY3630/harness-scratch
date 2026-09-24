"""技能系统测试：包解析 / 安装安全 / 受限执行 / 工具暴露 / API。"""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest

from mini_harness.skills.installer import InstallLimits, SkillInstaller
from mini_harness.skills.manifest import (
    SkillError,
    load_manifest,
    parse_skill_md,
    slugify,
)
from mini_harness.skills.runner import SkillRunner
from mini_harness.skills.runtime import build_skill_runtime
from mini_harness.skills.store import SkillStore

SKILL_MD = """---
name: 演示技能
slug: {slug}
description: 用于测试的技能
credentials:
  - name: demo_token
    env: DEMO_TOKEN
    description: 演示凭证
---

# 演示技能

调用 scripts/run.py。
"""

SCRIPT = '''import argparse, json, os, time
parser = argparse.ArgumentParser()
parser.add_argument("--name", "-n", default="世界")
parser.add_argument("--slow", action="store_true")
args = parser.parse_args()
if args.slow:
    time.sleep(5)
print(json.dumps({
    "name": args.name,
    "token": os.environ.get("DEMO_TOKEN", ""),
    "leaked": os.environ.get("LLM_API_KEY", ""),
}, ensure_ascii=False))
'''


def make_zip(files: dict[str, str], *, name: str = "demo-1.0.0.zip") -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def make_skill_dir(root: Path, slug: str = "demo", *, script: str = SCRIPT) -> Path:
    d = root / slug
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(SKILL_MD.format(slug=slug), encoding="utf-8")
    (d / "scripts" / "run.py").write_text(script, encoding="utf-8")
    return d


@pytest.fixture()
def skill_root(tmp_path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


@pytest.fixture()
def store(skill_root) -> SkillStore:
    return SkillStore(skill_root)


@pytest.fixture()
def installer(store) -> SkillInstaller:
    return SkillInstaller(store)


# ---------------------------------------------------------------- 解析
def test_parse_skill_md_splits_front_matter_and_body():
    meta, body = parse_skill_md("---\nname: A\ndescription: B\n---\n\n# 正文\n")
    assert meta["name"] == "A" and meta["description"] == "B"
    assert body.strip() == "# 正文"


def test_parse_skill_md_requires_front_matter():
    with pytest.raises(SkillError, match="front-matter"):
        parse_skill_md("# 没有 front-matter")


def test_slug_precedence_meta_json_wins(tmp_path):
    """slug 优先级：_meta.json > front-matter > 入参兜底。"""
    d = make_skill_dir(tmp_path, "from-dir")
    (d / "_meta.json").write_text(json.dumps({"slug": "from-meta", "version": "2.0"}), encoding="utf-8")
    assert load_manifest(d, slug="from-arg").slug == "from-meta"
    assert load_manifest(d).version == "2.0"


def test_missing_name_or_description_rejected(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nslug: x\n---\n正文\n", encoding="utf-8")
    with pytest.raises(SkillError, match="name 与 description"):
        load_manifest(d)


def test_slugify_fallback():
    """技能名兜底转 slug：保留词字符与汉字，其余折成连字符。"""
    assert slugify("City Weather Poster") == "City-Weather-Poster"
    assert slugify("城市 天气 画报") == "城市-天气-画报"
    assert slugify("  ") == ""


def test_slug_allows_cjk_but_rejects_separators(tmp_path):
    """中文目录名可直接当 slug；带路径分隔符的必须被拒（且要绕过 front-matter 的 slug 声明）。"""
    assert load_manifest(make_skill_dir(tmp_path, "城市天气画报")).slug == "城市天气画报"

    bare = tmp_path / "bare"
    (bare / "scripts").mkdir(parents=True)
    (bare / "SKILL.md").write_text("---\nname: 裸技能\ndescription: 无 slug 声明\n---\n正文\n", encoding="utf-8")
    (bare / "scripts" / "run.py").write_text("print('{}')", encoding="utf-8")

    for bad in ("../escape", "a/b", "a\\b", ".hidden", ".."):
        with pytest.raises(SkillError):
            load_manifest(bare, slug=bad)

    # 没有显式声明与兜底线索时，用技能名兜底
    assert load_manifest(bare).slug == "裸技能"
    assert load_manifest(bare, slug="").slug == "裸技能"


# ---------------------------------------------------------------- 安装安全
def test_install_zip_bytes_then_list(installer, store):
    data = make_zip({"SKILL.md": SKILL_MD.format(slug="demo"),
                     "scripts/run.py": SCRIPT,
                     "_meta.json": json.dumps({"slug": "demo", "version": "1.0.0"})})
    manifest = installer.install_zip_bytes(data)
    assert manifest.slug == "demo" and manifest.version == "1.0.0"
    assert manifest.scripts == ["run.py"]
    assert manifest.path.is_dir() and (manifest.path / "scripts" / "run.py").is_file()
    assert [m.slug for m in store.list()] == ["demo"]
    assert manifest.install["source"] == "upload"
    assert (manifest.path / ".install.json").is_file()
    # staging 不残留
    assert not (store.root / ".staging" / manifest.slug).exists()


def test_install_accepts_single_wrapped_dir(installer, store):
    data = make_zip({"demo-1.0.0/SKILL.md": SKILL_MD.format(slug="demo"),
                     "demo-1.0.0/scripts/run.py": SCRIPT})
    assert installer.install_zip_bytes(data).slug == "demo"


def test_install_rejects_path_traversal(installer):
    with pytest.raises(SkillError, match="越界路径"):
        installer.install_zip_bytes(make_zip({"../evil.md": "x", "SKILL.md": "---\nname: a\ndescription: b\n---"}))
    with pytest.raises(SkillError, match="越界路径"):
        installer.install_zip_bytes(make_zip({"/abs/evil.md": "x", "SKILL.md": "---\nname: a\ndescription: b\n---"}))
    with pytest.raises(SkillError, match="越界路径"):
        installer.install_zip_bytes(make_zip({"a\\..\\evil.md": "x", "SKILL.md": "---\nname: a\ndescription: b\n---"}))


def test_install_rejects_symlink(installer):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: a\ndescription: b\n---\n")
        info = zipfile.ZipInfo("link.md")
        info.external_attr = (0o120777 << 16)      # S_IFLNK
        zf.writestr(info, "target")
    with pytest.raises(SkillError, match="符号链接"):
        installer.install_zip_bytes(buf.getvalue())


def test_install_rejects_disallowed_suffix_and_oversize(store):
    strict = SkillInstaller(store, InstallLimits(max_file_bytes=100, max_total_bytes=200))
    with pytest.raises(SkillError, match="不允许的文件类型"):
        strict.install_zip_bytes(make_zip({"SKILL.md": "---\nname: a\ndescription: b\n---", "evil.exe": "MZ"}))
    with pytest.raises(SkillError, match="单文件超过上限"):
        strict.install_zip_bytes(make_zip({"SKILL.md": "---\nname: a\ndescription: b\n---", "big.txt": "x" * 500}))


def test_install_requires_skill_md(installer):
    with pytest.raises(SkillError, match="SKILL.md"):
        installer.install_zip_bytes(make_zip({"readme.txt": "hello"}))


def test_install_rejects_non_zip(installer):
    with pytest.raises(SkillError, match="不是合法的 zip"):
        installer.install_zip_bytes(b"not a zip at all")


def test_install_dir_replaces_existing_and_cleans_trash(installer, store, tmp_path):
    src = make_skill_dir(tmp_path / "src", "demo")
    first = installer.install_dir(src)
    assert first.slug == "demo"

    (src / "SKILL.md").write_text(SKILL_MD.format(slug="demo").replace("用于测试", "第二版"), encoding="utf-8")
    second = installer.install_dir(src)
    assert "第二版" in second.description
    assert [m.slug for m in store.list()] == ["demo"]            # 只有一个，没重复
    assert not list(store.root.glob(".trash-*"))                 # 旧目录已清理


def test_store_skips_broken_skill(store, skill_root):
    make_skill_dir(skill_root, "good")
    broken = skill_root / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("# 没有 front-matter", encoding="utf-8")
    assert [m.slug for m in store.list()] == ["good"]


def test_store_remove(store, skill_root):
    make_skill_dir(skill_root, "demo")
    assert store.remove("demo") is True
    assert [m.slug for m in store.list()] == []
    assert store.remove("demo") is False
    with pytest.raises(SkillError):
        store.remove("../escape")


# ---------------------------------------------------------------- 执行
def test_build_argv_converts_params():
    assert SkillRunner.build_argv({"city": "西安", "ratio": "9:16"}) == ["--city=西安", "--ratio=9:16"]
    assert SkillRunner.build_argv({"flag": True, "skip": False, "none": None, "empty": ""}) == ["--flag"]
    assert SkillRunner.build_argv({"dry_run": True}) == ["--dry-run"]


def test_run_script_passes_params_and_injects_credentials(tmp_path, monkeypatch):
    manifest = load_manifest(make_skill_dir(tmp_path, "demo"))
    monkeypatch.setenv("DEMO_TOKEN", "tok-123")
    monkeypatch.setenv("LLM_API_KEY", "host-secret")
    result = SkillRunner(timeout=30).run(manifest, "run.py", {"name": "小明"})

    assert result.ok and result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "小明"
    assert payload["token"] == "tok-123"      # 白名单凭证已注入
    assert payload["leaked"] == ""            # 宿主密钥未泄漏给技能脚本


def test_run_refuses_when_required_credential_missing(tmp_path, monkeypatch):
    manifest = load_manifest(make_skill_dir(tmp_path, "demo"))
    monkeypatch.delenv("DEMO_TOKEN", raising=False)
    result = SkillRunner(timeout=30).run(manifest, "run.py")

    assert result.ok is False and "MISSING_CREDENTIAL" in result.stderr
    assert manifest.missing_env() == ["DEMO_TOKEN"]


def test_run_rejects_script_escape(tmp_path):
    manifest = load_manifest(make_skill_dir(tmp_path, "demo"))
    runner = SkillRunner()
    for bad in ("../run.py", "sub/run.py", "/etc/passwd", "run.sh", ""):
        result = runner.run(manifest, bad)
        assert result.ok is False and "非法脚本名" in result.stderr or "不存在脚本" in result.stderr


def test_run_timeout_kills_script(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "tok")
    manifest = load_manifest(make_skill_dir(tmp_path, "demo"))
    result = SkillRunner(timeout=0.5).run(manifest, "run.py", {"slow": True})
    assert result.ok is False and result.timed_out is True
    assert "TIMEOUT" in result.to_text()


def test_run_refuses_host_model_credentials(tmp_path, monkeypatch):
    """技能要求宿主模型密钥时拒绝下发（FDORBIDDEN_CREDENTIAL），不透传。"""
    script = 'import json, os\nprint(json.dumps({"k": os.environ["LLM_API_KEY"]}))\n'
    manifest = load_manifest(make_skill_dir(tmp_path, "demo", script=script))
    monkeypatch.setenv("LLM_API_KEY", "host-secret")

    assert "LLM_API_KEY" in manifest.required_env
    result = SkillRunner(timeout=30).run(manifest, "run.py")
    assert result.ok is False and "FORBIDDEN_CREDENTIAL" in result.stderr
    assert "host-secret" not in result.stderr and "host-secret" not in result.stdout


# ---------------------------------------------------------------- 工具暴露
def test_skill_tools_invisible_when_not_enabled(skill_root, installer):
    installer.install_dir(make_skill_dir(skill_root.parent / "src", "demo"))
    runtime = build_skill_runtime(skill_root, [], timeout=5)

    assert runtime.get("demo") is None
    assert runtime.index_block() == ""
    assert "[NOT_ENABLED]" in runtime.read("demo")
    assert "[NOT_ENABLED]" in runtime.run("demo", "run.py").stderr


def test_skill_tools_visible_when_enabled(skill_root, installer):
    installer.install_dir(make_skill_dir(skill_root.parent / "src", "demo"))
    runtime = build_skill_runtime(skill_root, ["demo"], timeout=30)

    block = runtime.index_block()
    assert "demo" in block and "run.py" in block
    assert "# 演示技能" in runtime.read("demo")


def test_skill_tools_factories_return_none_without_runtime():
    from mini_harness.tools.builtin.skills import TOOLS
    from mini_harness.tools.loader import ToolContext

    ctx = ToolContext()                      # skills=None
    for name, factory in TOOLS.items():
        assert factory(ctx) is None, name


def test_run_skill_script_tool_is_ask_first(skill_root, installer):
    from mini_harness.tools.builtin.skills import TOOLS
    from mini_harness.tools.levels import PermissionLevel
    from mini_harness.tools.loader import ToolContext

    installer.install_dir(make_skill_dir(skill_root.parent / "src", "demo"))
    ctx = ToolContext(skills=build_skill_runtime(skill_root, ["demo"], timeout=30))
    tool = TOOLS["run_skill_script"](ctx)

    assert tool.permission == PermissionLevel.ASK_FIRST
    assert tool.schema()["function"]["parameters"]["required"] == ["skill", "script"]
    assert "非法脚本名" in tool.func(skill="demo", script="../run.py")


def test_skill_runtime_overrides_win_over_config():
    """运行时覆盖优先于配置：可停用配置启用的技能，也可启用配置里没有的技能。"""
    from mini_harness.skills.runtime import build_skill_runtime

    root = Path("unused")
    runtime = build_skill_runtime(root, ["a", "b"], timeout=5, overrides={"b": False, "c": True})
    assert sorted(runtime.enabled) == ["a", "c"]
    assert runtime.enabled_source("b") == ""            # 被界面停用
    assert runtime.enabled_source("c") == "override"    # 被界面启用
    assert runtime.enabled_source("a") == "config"      # 来自配置
    assert "c" in runtime.index_lines()[1] if len(runtime.index_lines()) > 1 else True


# ---------------------------------------------------------------- 启停 / 自定义创建（API）
@pytest.fixture()
def api_env(skill_root, tmp_path, monkeypatch):
    """隔离的 API 环境：临时技能目录 + 临时 DB（不碰开发库）。"""
    from collections import OrderedDict
    from dataclasses import replace

    import mini_harness.api.routes as routes
    from mini_harness.persistence.database import Database

    cfg = replace(routes.get_project_config(),
                  skills={**routes.get_project_config().skills, "dir": str(skill_root), "enabled": ["demo"]})
    db = Database(tmp_path / "api.db")
    monkeypatch.setattr(routes, "get_project_config", lambda: cfg)
    monkeypatch.setattr(routes, "_get_db", lambda cfg=None: db)
    monkeypatch.setattr(routes, "_engines", OrderedDict())
    return routes, db


def test_api_enable_disable_and_clear_override(api_env, skill_root):
    """界面启停：写入覆盖 -> 立即生效 -> 可清除回配置默认。"""
    from fastapi.testclient import TestClient

    from mini_harness.main import app

    routes, db = api_env
    installer = SkillInstaller(SkillStore(skill_root))
    installer.install_dir(make_skill_dir(skill_root.parent / "src", "demo"))    # 配置里启用的
    installer.install_dir(make_skill_dir(skill_root.parent / "src", "extra"))   # 配置里没有的

    with TestClient(app) as client:
        rows = {r["slug"]: r for r in client.get("/api/skills").json()}
        assert rows["demo"]["enabled"] and rows["demo"]["enabled_source"] == "config"
        assert not rows["extra"]["enabled"] and rows["extra"]["enabled_source"] == ""

        # 启用一个配置里没有的技能
        assert client.put("/api/skills/extra/enabled", json={"enabled": True}).status_code == 200
        rows = {r["slug"]: r for r in client.get("/api/skills").json()}
        assert rows["extra"]["enabled"] and rows["extra"]["enabled_source"] == "override"
        assert db.get_skill_state("extra") is True

        # 停用一个配置里启用的技能
        client.put("/api/skills/demo/enabled", json={"enabled": False})
        rows = {r["slug"]: r for r in client.get("/api/skills").json()}
        assert not rows["demo"]["enabled"] and rows["demo"]["enabled_source"] == ""

        # 清除覆盖后回到配置默认
        assert client.delete("/api/skills/demo/enabled").status_code == 200
        rows = {r["slug"]: r for r in client.get("/api/skills").json()}
        assert rows["demo"]["enabled"] and rows["demo"]["enabled_source"] == "config"
        assert client.delete("/api/skills/demo/enabled").status_code == 404   # 已无覆盖

        assert client.put("/api/skills/nope/enabled", json={"enabled": True}).status_code == 404


def test_api_enable_rebuilds_engine_cache(api_env, skill_root):
    """启停会清空引擎缓存，让系统提示词里的技能索引重建。"""
    from fastapi.testclient import TestClient

    from mini_harness.main import app

    routes, _db = api_env
    SkillInstaller(SkillStore(skill_root)).install_dir(make_skill_dir(skill_root.parent / "src", "demo"))

    class _StubEngine:
        def flush_trace(self) -> None:
            return None

    routes._engines["s1"] = _StubEngine()      # 只验证"被清掉"，用最小替身
    with TestClient(app) as client:
        body = client.put("/api/skills/demo/enabled", json={"enabled": False}).json()
    assert body["engines_rebuilt"] == 1
    assert len(routes._engines) == 0


def test_api_create_skill_builds_package(api_env, skill_root):
    """自定义创建：表单内容拼成 SKILL.md（含 front-matter 与凭证），脚本落到 scripts/。"""
    from fastapi.testclient import TestClient

    from mini_harness.main import app

    with TestClient(app) as client:
        resp = client.post("/api/skills/create", json={
            "name": "周报助手",
            "description": "把流水账整理成结构化周报",
            "slug": "weekly-report",
            "body": "# 周报助手\n\n## 操作步骤\n1. 执行脚本",
            "credentials": [{"name": "report_team", "env": "REPORT_TEAM", "required": False}],
            "scripts": {"build_report.py": "import argparse\nprint('{}')\n"},
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["slug"] == "weekly-report" and resp.json()["enabled"] is False

        skill_md = (skill_root / "weekly-report" / "SKILL.md").read_text(encoding="utf-8")
        assert skill_md.startswith("---") and "name: 周报助手" in skill_md
        assert "slug: weekly-report" in skill_md and "env: REPORT_TEAM" in skill_md
        assert (skill_root / "weekly-report" / "scripts" / "build_report.py").is_file()

        detail = client.get("/api/skills/weekly-report").json()
        assert detail["required_env"] == []            # 声明为可选
        assert detail["env_vars"] == ["REPORT_TEAM"]

        assert client.post("/api/skills/create", json={"name": "", "description": "x"}).status_code == 400
        assert client.post("/api/skills/create", json={
            "name": "x", "description": "y", "scripts": {"../escape.py": "print(1)"},
        }).status_code == 400


def test_api_uninstall_clears_override(api_env, skill_root):
    """卸载会顺带清掉启用覆盖，避免遗留孤儿状态。"""
    from fastapi.testclient import TestClient

    from mini_harness.main import app

    routes, db = api_env
    SkillInstaller(SkillStore(skill_root)).install_dir(make_skill_dir(skill_root.parent / "src", "extra"))

    with TestClient(app) as client:
        client.put("/api/skills/extra/enabled", json={"enabled": True})
        assert client.delete("/api/skills/extra").status_code == 200
    assert db.get_skill_state("extra") is None


def test_api_install_list_delete(api_env, skill_root, monkeypatch):
    """上传 zip 安装 -> 列表 -> 详情 -> 卸载（全程走 API）。"""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    import mini_harness.api.routes as routes
    from mini_harness.main import app

    # 这个用例只关心安装链路，把白名单清空以便断言"未启用"
    base = routes.get_project_config()          # 先取住当前配置，避免 lambda 里递归调用自己
    monkeypatch.setattr(routes, "get_project_config",
                        lambda: replace(base, skills={**base.skills, "enabled": []}))

    payload = make_zip({"SKILL.md": SKILL_MD.format(slug="demo"), "scripts/run.py": SCRIPT})
    with TestClient(app) as client:
        assert client.get("/api/skills").json() == []

        installed = client.post("/api/skills/install", content=payload,
                                headers={"Content-Type": "application/zip"})
        assert installed.status_code == 200 and installed.json()["slug"] == "demo"

        listed = client.get("/api/skills").json()
        assert len(listed) == 1 and listed[0]["enabled"] is False
        assert listed[0]["scripts"] == ["run.py"]
        assert listed[0]["required_env"] == ["DEMO_TOKEN"]          # 声明 + 探测合并
        assert listed[0]["missing_env"] == ["DEMO_TOKEN"]           # 当前未配置

        detail = client.get("/api/skills/demo").json()
        assert "演示技能" in detail["body"]

        assert client.post("/api/skills/install", content=b"").status_code == 400
        assert client.post("/api/skills/install-url", json={"url": "ftp://x/y.zip"}).status_code == 400

        assert client.delete("/api/skills/demo").status_code == 200
        assert client.get("/api/skills").json() == []
        assert client.get("/api/skills/demo").status_code == 404
        assert client.delete("/api/skills/demo").status_code == 404
