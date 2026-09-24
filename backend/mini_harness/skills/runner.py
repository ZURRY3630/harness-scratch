# DOC: docs/16-skills.md
"""技能脚本执行器：在受限环境里跑 `scripts/*.py`。

三条硬约束（对应"技能 = 可执行代码"的风险）：
1. **脚本锁定在技能目录内**：只接受纯文件名，resolve 后必须仍在 `scripts/` 下，防目录穿越；
2. **环境变量白名单注入**：子进程**不继承**宿主环境，只注入技能声明/脚本里探测到的凭证，
   避免把 `LLM_API_KEY` 之类的宿主密钥泄给技能脚本；
3. **超时 + 输出截断**：超时即杀，输出超过上限截断，避免拖垮服务或撑爆上下文。

缺凭证时**不启动子进程**：直接返回可行动的失败结果。第三方技能脚本常用 `getpass` 兜底，
在服务端会一直阻塞到超时，不如提前拒绝。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..observability.logging import get_logger
from .manifest import SCRIPTS_DIR, SkillError, SkillManifest

log = get_logger(__name__)

# 子进程保留的最小宿主环境（Windows 下 Python 需要 SYSTEMROOT 等才能启动）
_ENV_KEEP = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")

# 永不下发的宿主凭证：即便技能脚本里写了要读它们，也不注入（脚本能自己读 .env 是另一回事，
# 这里保证的是"框架不会主动把模型密钥递给技能进程"）
_NEVER_INJECT = frozenset({
    "LLM_API_KEY", "LLM_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
})

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_MAX_OUTPUT = 20_000


@dataclass
class SkillRunResult:
    """一次技能脚本执行的结果。"""

    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    command: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """给模型看的结果文本：失败时把原因放在最前面（可行动优先）。"""
        if self.timed_out:
            return f"[TIMEOUT] 技能脚本执行超时（{self.duration_ms}ms）已终止。stderr: {self.stderr[:500]}"
        if not self.ok:
            detail = (self.stderr or self.stdout).strip()
            return f"[ERROR] 技能脚本以退出码 {self.exit_code} 结束。{detail[:1500]}"
        return self.stdout or "(脚本无标准输出)"


class SkillRunner:
    """执行技能脚本。"""

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT,
        python: Optional[str] = None,
    ) -> None:
        self.timeout = float(timeout)
        self.max_output_bytes = int(max_output_bytes)
        self.python = python or sys.executable

    # ----- 目标解析 -----
    def resolve_script(self, manifest: SkillManifest, script: str) -> Path:
        """把 `script` 解析成技能目录内的真实路径；任何越界都抛 SkillError。"""
        if manifest.path is None:
            raise SkillError(f"技能 {manifest.slug} 没有安装路径")
        name = (script or "").strip()
        if not name or Path(name).name != name or not name.endswith(".py"):
            raise SkillError(f"非法脚本名: {script!r}（只接受 scripts 下的 .py 文件名）")
        base = manifest.path.resolve()
        target = (base / SCRIPTS_DIR / name).resolve()
        if base not in target.parents or not target.is_file():
            raise SkillError(f"技能 {manifest.slug} 中不存在脚本: {name}")
        return target

    @staticmethod
    def build_argv(params: Optional[dict[str, Any]]) -> list[str]:
        """params -> argparse 命令行：`{"city":"西安","ratio":"9:16"}` -> `--city=西安 --ratio=9:16`。

        值为 `True` 时输出裸开关 `--flag`；`None` / `False` / 空串跳过。
        """
        argv: list[str] = []
        for key, value in (params or {}).items():
            flag = "--" + str(key).strip().replace("_", "-")
            if value is None or value is False or value == "":
                continue
            if value is True:
                argv.append(flag)
                continue
            argv.append(f"{flag}={value}")
        return argv

    # ----- 执行 -----
    def run(self, manifest: SkillManifest, script: str, params: Optional[dict[str, Any]] = None) -> SkillRunResult:
        """执行脚本；不抛异常（把失败编码进返回值，便于回灌给模型）。"""
        try:
            script_path = self.resolve_script(manifest, script)
        except SkillError as e:
            return SkillRunResult(ok=False, exit_code=-1, stderr=str(e))

        missing = manifest.missing_env()
        blocked = [name for name in manifest.required_env if name in _NEVER_INJECT]
        if blocked:
            return SkillRunResult(
                ok=False, exit_code=-1,
                stderr=(f"[FORBIDDEN_CREDENTIAL] 技能 {manifest.slug} 要求宿主密钥 "
                        f"{'、'.join('`' + n + '`' for n in blocked)}，框架按策略拒绝下发。"
                        "模型密钥不会传给技能脚本，请改为在技能内使用自有凭证。"),
            )
        if missing:
            hint = "、".join(f"`{name}`" for name in missing)
            return SkillRunResult(
                ok=False, exit_code=-1,
                stderr=(f"[MISSING_CREDENTIAL] 技能 {manifest.slug} 需要环境变量 {hint}，"
                        f"当前未配置。请在仓库根的 .env 中配置后重试"
                        + ("（该技能脚本还会尝试交互式输入，服务端不可用，故直接拒绝执行）"
                           if manifest.interactive_hint else "")),
            )

        argv = [self.python, str(script_path), *self.build_argv(params)]
        env = self._build_env(manifest)
        start = time.perf_counter()
        try:
            proc = subprocess.run(  # noqa: S603 —— 固定解释器 + 不可变参数列表，未走 shell
                argv, cwd=str(manifest.path), env=env, capture_output=True,
                timeout=self.timeout, text=True, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log.warning("技能脚本超时", skill=manifest.slug, script=script, timeout=self.timeout)
            return SkillRunResult(ok=False, exit_code=-1, timed_out=True, duration_ms=elapsed_ms,
                                  stdout=self._clip(_to_text(e.stdout)), stderr=self._clip(_to_text(e.stderr)),
                                  command=argv)
        except OSError as e:
            log.warning("技能脚本启动失败", skill=manifest.slug, script=script, error=str(e))
            return SkillRunResult(ok=False, exit_code=-1, stderr=f"脚本启动失败: {e}", command=argv)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        result = SkillRunResult(
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            stdout=self._clip(proc.stdout),
            stderr=self._clip(proc.stderr),
            duration_ms=elapsed_ms,
            command=argv,
        )
        log.info("技能脚本执行完成", skill=manifest.slug, script=script, ok=result.ok,
                 exit_code=result.exit_code, elapsed_ms=elapsed_ms, params=params or {})
        return result

    # ----- 内部 -----
    def _build_env(self, manifest: SkillManifest) -> dict[str, str]:
        """最小宿主环境 + 该技能的白名单凭证。"""
        env = {key: os.environ[key] for key in _ENV_KEEP if os.environ.get(key)}
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        for name in manifest.env_vars:
            if name in _NEVER_INJECT:
                continue
            value = os.environ.get(name)
            if value:
                env[name] = value
        return env

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_output_bytes:
            return text
        return text[: self.max_output_bytes] + f"\n…[已截断，原始长度 {len(text)} 字符]"


def _to_text(value: Any) -> str:
    """TimeoutExpired 携带的输出可能是 str 或 bytes，统一成 str。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
