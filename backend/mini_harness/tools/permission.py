"""权限门控：5 级权限语义落实 + 审批持久化 + 渐进信任。

级别 -> 决策：
    FULL_TRUST               -> ALLOW（审计）
    AUTO_WITH_NOTIFICATION   -> ALLOW + 通知事件（不挂起）
    ASK_FIRST                -> 首次 ASK；批准后本 call_id 放行
    APPROVE_ALWAYS           -> 首次 ASK；批准后按 (tool, 参数形状) 记忆，同类后续自动放行
    MANUAL_ONLY              -> HANDOFF（转人工，不执行不拒绝）
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
from typing import TYPE_CHECKING

from .levels import Decision, PermissionLevel

if TYPE_CHECKING:
    from .registry import ToolRegistry


class PermissionGate:
    def __init__(self, registry: "ToolRegistry", approval_store=None):
        """approval_store: persistence.Database，可注入以持久化批准记录（重启生效）。"""
        self.registry = registry
        self.approval_store = approval_store
        self._approved_calls: set[str] = set()          # call_id 粒度（内存，run 内有效）
        self._approved_patterns: set[str] = set()       # APPROVE_ALWAYS 记忆（持久化）

    # ---------- 指纹 ----------
    @staticmethod
    def _pattern(tool_name: str, arguments: dict) -> str:
        """参数形状指纹：键集合 + 值类型（不含具体值，避免过拟合单次调用）。"""
        shape = {k: type(v).__name__ for k, v in sorted(arguments.items())}
        raw = tool_name + ":" + json.dumps(shape, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _args_hash(tool_name: str, arguments: dict) -> str:
        raw = tool_name + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode()).hexdigest()

    # ---------- 决策 ----------
    def effective_level(self, tool_name: str) -> PermissionLevel:
        """解析生效级别：DB 运行时覆盖 > 工具声明默认。"""
        if self.approval_store is not None:
            override = self.approval_store.get_tool_permission(tool_name)
            if override:
                try:
                    return PermissionLevel(override)
                except ValueError:
                    pass
        tool = self.registry.get(tool_name)
        return tool.permission if tool else PermissionLevel.ASK_FIRST

    def decide(self, tool_name: str, call_id: str, arguments: dict) -> Decision:
        if not self.registry.get(tool_name):
            return Decision.DENY
        level = self.effective_level(tool_name)

        if level == PermissionLevel.FULL_TRUST:
            return Decision.ALLOW
        if level == PermissionLevel.AUTO_WITH_NOTIFICATION:
            return Decision.ALLOW_NOTIFY
        if level == PermissionLevel.MANUAL_ONLY:
            return Decision.HANDOFF

        # ASK_FIRST / APPROVE_ALWAYS
        if call_id in self._approved_calls:
            return Decision.ALLOW
        if self.approval_store is not None:
            row = self.approval_store.get_approval(call_id)
            if row and row["decision"] == "approved" and row["args_hash"] == self._args_hash(tool_name, arguments):
                self._approved_calls.add(call_id)
                return Decision.ALLOW
        if level == PermissionLevel.APPROVE_ALWAYS:
            if self._pattern(tool_name, arguments) in self._approved_patterns:
                return Decision.ALLOW
        return Decision.ASK

    # ---------- 审批 ----------
    def approve(self, call_id: str, tool_name: str = "", arguments: dict | None = None, remember: bool = False) -> None:
        self._approved_calls.add(call_id)
        if remember and tool_name:
            self._approved_patterns.add(self._pattern(tool_name, arguments or {}))
        if self.approval_store is not None:
            self.approval_store.record_approval(
                call_id, session_id="", tool_name=tool_name,
                args_hash=self._args_hash(tool_name, arguments or {}), decision="approved",
            )

    def deny(self, call_id: str, tool_name: str = "", arguments: dict | None = None) -> None:
        if self.approval_store is not None:
            self.approval_store.record_approval(
                call_id, session_id="", tool_name=tool_name,
                args_hash=self._args_hash(tool_name, arguments or {}), decision="denied",
            )

    # ---------- 唯一执行入口（收口）----------
    def execute(self, tool_name: str, arguments: dict, timeout_seconds: float = 30.0) -> tuple[bool, str, float]:
        """同步工具在线程池中执行 + 超时保护。返回 (ok, result, elapsed)。"""
        tool = self.registry.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' not found.", 0.0
        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.func, **arguments)
            try:
                result = future.result(timeout=timeout_seconds)
                return True, str(result), time.perf_counter() - start
            except concurrent.futures.TimeoutError:
                future.cancel()
                return False, f"[TIMEOUT] 工具 '{tool_name}' 执行超过 {timeout_seconds:.0f}s 被终止", time.perf_counter() - start
            except Exception as e:  # noqa: BLE001
                return False, f"[ERROR] {tool_name}: {e}", time.perf_counter() - start
