# DOC: docs/03-architecture.md
"""RuntimeEngine：Agent Loop，唯一执行入口。

循环（每轮）：
    1. pending 对账 -> Schema/路径校验 -> 权限裁决 ->（ASK 挂起 / 执行回填）
    2. 预算检查 -> 触发上下文压缩（如需要）
    3. 组装上下文（缓存友好）-> 流式调模型
    4. 有 tool_calls -> 登记，回 1；无 -> 输出最终回答

设计不变量：
- 工具执行只有一个入口：_process_tool_call（校验+权限+沙箱+审计全在这条链上）
- 状态全在 SessionStore（SQLite），挂起/恢复/崩溃恢复都靠它
- 对外只发类型化 Event；ASK 时立即挂起，绝不带着未决审批继续调模型
- system_prompt 由组装层注入（context.prompt_loader），引擎内不含任何领域提示词
- 横切关注（输入过滤 / 结果清洗 / 埋点）走 HookChain 的五个点位，引擎自身不含策略判断
- 观测收口在 run()：trace_id / span 树 / 指标 / 日志全部在这里起止，_run_impl 只负责业务循环
"""

from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from ..context.assembler import ContextAssembler
from ..context.budget import Budget, estimate_tokens
from ..core.events import EventType, Event, ev
from ..core.hooks import HookChain
from ..core.message import Role, ToolCall
from ..memory.compressor import ContextCompressor
from ..memory.longterm import LongTermMemory
from ..memory.session_store import SessionStore
from ..models.provider import BaseModelProvider
from ..observability.logging import get_logger, reset_trace_context, set_trace_context
from ..observability.metrics import METRICS, MetricsRegistry
from ..observability.spans import Tracer
from ..observability.trace import TraceWriter
from ..tools.permission import Decision
from ..tools.registry import ToolRegistry

log = get_logger(__name__)


class RuntimeEngine:
    def __init__(
        self,
        provider: BaseModelProvider,
        registry: ToolRegistry,
        store: SessionStore,
        gate,  # PermissionGate（避免循环 import 用 duck typing）
        budget: Budget,
        compressor: ContextCompressor,
        assembler: ContextAssembler,
        system_prompt: str,  # 必填：由组装层（context.prompt_loader）注入，引擎不自带领域提示词
        longterm: LongTermMemory | None = None,
        max_turns: int = 10,
        tool_timeout: float = 30.0,
        hook_chain: HookChain | None = None,  # 为空即全 no-op，等价于"没有钩子"
        trace_writer: TraceWriter | None = None,  # 为空则不落 trace（P1-4）
        tracer: Tracer | None = None,  # 为空则不产 span（调用链）
        metrics: MetricsRegistry | None = None,  # 为空则写进程级默认注册表
    ):
        self.provider = provider
        self.tools = registry
        self.store = store
        self.gate = gate
        self.budget = budget
        self.compressor = compressor
        self.assembler = assembler
        self.longterm = longterm
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.tool_timeout = tool_timeout
        self.hooks = hook_chain or HookChain()
        self.trace_writer = trace_writer
        self.tracer = tracer or Tracer()
        self.metrics = metrics or METRICS
        self._approval_requested_at: dict[str, float] = {}  # call_id -> 下发审批的时刻

    # ------------------------------------------------------------------
    async def run(self, user_input: str | None = None) -> AsyncIterator[Event]:
        """对外唯一入口：产出 `_run_impl` 的全部事件，并收口观测（trace / span / 指标 / 日志）。"""
        sid = self.store.session_id
        trace_id = uuid.uuid4().hex
        tokens = set_trace_context(trace_id, sid)
        span = self.tracer.start("run", session_id=sid, trace_id=trace_id, max_turns=self.max_turns)
        attach = self.tracer.attach(span)
        outcome, finished = "ok", False
        try:
            async for event in self._run_impl(user_input):
                if event.type == EventType.APPROVAL_REQUIRED:
                    outcome = "suspended"
                elif event.type == EventType.ERROR:
                    outcome = "error"
                elif event.type == EventType.BUDGET_EXCEEDED:
                    outcome = "budget_exceeded"
                elif event.type == EventType.RUN_FINISHED:
                    finished = True
                self._trace(event)
                yield event
        except Exception:  # noqa: BLE001 —— 观测后原样抛出，由 SSE 层兜底转 error 事件
            outcome = "error"
            self.metrics.incr("errors_total", where="engine")
            log.exception("引擎运行异常", session_id=sid, trace_id=trace_id)
            raise
        finally:
            # 客户端中途断开（生成器被 close）时也要收口，避免 span 悬挂
            if not finished and outcome == "ok":
                outcome = "aborted"
            self.tracer.detach(attach)
            self.tracer.finish(span, status="error" if outcome == "error" else "ok", outcome=outcome)
            self.metrics.incr("agent_runs_total", status=outcome)
            log.info("运行结束", outcome=outcome, llm_calls=self.budget.usage.llm_calls,
                     tool_calls=self.budget.usage.tool_calls)
            reset_trace_context(tokens)
            self.flush_trace()

    def flush_trace(self) -> None:
        """刷观测缓冲：每次运行收尾刷一次，崩溃时最多丢当轮而不是丢整个缓冲区。"""
        try:
            self.tracer.flush()
            if self.trace_writer is not None:
                self.trace_writer.flush()
        except Exception:  # noqa: BLE001 —— 观测链路故障不影响主流程
            pass

    def _trace(self, event: Event) -> None:
        """观测链路故障不得影响业务：写失败即忽略。"""
        if self.trace_writer is None:
            return
        try:
            self.trace_writer.write(event)
        except Exception:  # noqa: BLE001
            pass

    async def _run_impl(self, user_input: str | None = None) -> AsyncIterator[Event]:
        sid = self.store.session_id
        yield ev(EventType.RUN_STARTED, sid)

        if user_input:
            # 新输入到达时，若有未批准的挂起调用：先取消它们（保持 tool 消息配对合法），
            # 否则 [assistant(tool_calls), user] 序列会被 API 拒绝（400 insufficient tool messages）
            for tc in self._unapproved_pending(self.store.get_pending_tool_calls()):
                self.store.add_tool_result(tc.call_id, "[SUPERSEDED] 用户新输入到达，本调用未获批准已取消。")
                yield ev(EventType.TOOL_EXECUTED, sid, tool=tc.tool_name, call_id=tc.call_id,
                         ok=False, result="superseded", superseded=True)
            self.store.add_user(user_input, tokens=estimate_tokens(user_input))

        for turn in range(1, self.max_turns + 1):
            # ---- 0. 若有未决审批（挂起点），本轮不调模型，只等审批 ----
            pending = self.store.get_pending_tool_calls()
            unapproved = self._unapproved_pending(pending)
            if unapproved:
                for tc in unapproved:
                    self._approval_requested_at[tc.call_id] = time.time()
                    self.metrics.incr("approvals_requested_total", tool=tc.tool_name)
                    log.info("等待人工审批", tool=tc.tool_name, call_id=tc.call_id)
                    yield ev(EventType.APPROVAL_REQUIRED, sid, call_id=tc.call_id,
                             tool_name=tc.tool_name, arguments=tc.arguments)
                return  # 挂起：状态在账本里，审批后 resume() 续跑

            yield ev(EventType.TURN_STARTED, sid, turn=turn)
            self.metrics.incr("agent_turns_total")

            # ---- 1. 已批准的 pending 工具调用：执行回填 ----
            for tc in pending:
                async for e in self._execute_pending(tc, sid):
                    yield e

            # ---- 2. 预算检查 + 压缩 ----
            info = await self.compressor.compress(
                self.store, self.budget.context_token_budget, self.budget.compress_threshold
            )
            if info.get("compressed"):
                self.budget.account_compression()
                self.metrics.incr("compressions_total")
                log.info("上下文已压缩", summarized_messages=info.get("summarized_messages"),
                         kept_messages=info.get("kept_messages"), summary_tokens=info.get("summary_tokens"))
                yield ev(EventType.CONTEXT_COMPRESSED, sid, **info)

            # ---- 3. 组装 + 钩子改写 + 流式调用 ----
            ctx = self.assembler.assemble(
                self.store, self.budget, system_prompt=self.system_prompt,
                recall_query=self._last_user_text(),
            )
            messages, tool_schemas = await self.hooks.before_llm_call(
                ctx.messages, self.tools.schemas() or None
            )
            async for he in self._emit_hook_errors(sid):
                yield he

            final = None
            llm_span = self.tracer.start("llm_call", session_id=sid,
                                        model=str(getattr(self.provider, "model", "")))
            llm_start = time.perf_counter()
            try:
                async for chunk in self.provider.chat_stream(messages, tools=tool_schemas):
                    if "delta" in chunk:
                        yield ev(EventType.DELTA, sid, text=chunk["delta"])
                    else:
                        final = chunk.get("response")
            except Exception as e:  # noqa: BLE001 —— 模型异常转 Error 事件，不崩引擎
                self.metrics.incr("llm_calls_total", status="error")
                self.metrics.observe("llm_latency_ms", (time.perf_counter() - llm_start) * 1000)
                self.tracer.finish(llm_span, status="error", error=type(e).__name__)
                log.error("模型调用失败", error=str(e), messages=len(messages))
                await self.hooks.on_error(e)
                async for he in self._emit_hook_errors(sid):
                    yield he
                yield ev(EventType.ERROR, sid, message=f"模型调用失败: {e}")
                yield ev(EventType.RUN_FINISHED, sid, usage=self.budget.snapshot())
                return

            llm_ms = int((time.perf_counter() - llm_start) * 1000)
            self.metrics.observe("llm_latency_ms", llm_ms)
            self.tracer.finish(llm_span, status="ok" if final is not None else "error",
                               elapsed_ms=llm_ms, messages=len(messages), tools=len(tool_schemas or []))
            if final is not None:
                self._account_llm_usage(final, llm_ms, len(messages), len(tool_schemas or []))

            if final is None:
                err = RuntimeError("模型流式响应未返回最终结果")
                self.metrics.incr("llm_calls_total", status="empty")
                log.error("模型流式响应未返回最终结果", elapsed_ms=llm_ms)
                await self.hooks.on_error(err)
                async for he in self._emit_hook_errors(sid):
                    yield he
                yield ev(EventType.ERROR, sid, message=str(err))
                break

            final = await self.hooks.after_llm_call(final)
            async for he in self._emit_hook_errors(sid):
                yield he

            if final.tool_calls:
                self.store.add_assistant_with_tool_calls(
                    final.content or "", final.tool_calls, tokens=estimate_tokens(final.content or "")
                )
                continue  # 回到循环开头处理 pending

            # ---- 4. 最终回答 ----
            self.store.add_assistant(final.content or "", tokens=estimate_tokens(final.content or ""))
            log.debug("产出最终回答", chars=len(final.content or ""))
            yield ev(EventType.TURN_FINISHED, sid, content=final.content or "", usage=self.budget.snapshot())
            yield ev(EventType.RUN_FINISHED, sid, usage=self.budget.snapshot())
            return

        # max_turns 耗尽：给遗留 pending 回填合法结果，保证消息序列可续跑
        for tc in self.store.get_pending_tool_calls():
            self.store.add_tool_result(tc.call_id, "[BUDGET] 轮次预算耗尽，本调用未执行。")
        log.warning("轮次预算耗尽", max_turns=self.max_turns)
        yield ev(EventType.BUDGET_EXCEEDED, sid, max_turns=self.max_turns, usage=self.budget.snapshot())
        yield ev(EventType.RUN_FINISHED, sid, usage=self.budget.snapshot())

    # ------------------------------------------------------------------
    def _unapproved_pending(self, pending: list[ToolCall]) -> list[ToolCall]:
        """需要审批（ASK）的 pending：挂起条件。"""
        out = []
        for tc in pending:
            if self.tools.get(tc.tool_name) is None:
                continue  # 幻觉工具：交由 _execute_pending 回填错误
            if self.gate.decide(tc.tool_name, tc.call_id, tc.arguments) == Decision.ASK:
                out.append(tc)
        return out

    async def _execute_pending(self, tc: ToolCall, sid: str) -> AsyncIterator[Event]:
        """已批准 / 无需审批的 pending：校验 -> 钩子 -> 权限 -> 执行 -> 清洗 -> 回填。唯一执行入口。"""
        # 1) Schema + 路径校验（执行链路强制第一步）
        ok, msg = self.tools.validate_and_check(tc.tool_name, tc.arguments)
        if not ok:
            self.store.add_tool_result(tc.call_id, f"[INVALID] {msg}")
            self.metrics.incr("tool_calls_total", tool=tc.tool_name, status="invalid")
            log.warning("工具调用未通过校验", tool=tc.tool_name, reason=msg)
            yield ev(EventType.TOOL_EXECUTED, sid, tool=tc.tool_name, call_id=tc.call_id,
                     ok=False, result=msg[:500], invalid=True)
            return

        # 2) 钩子前置：放在权限裁决之前，危险调用可被拦在审批弹窗之外
        allowed, reason = await self.hooks.before_tool_execute(tc.tool_name, tc.arguments)
        async for he in self._emit_hook_errors(sid):
            yield he
        if not allowed:
            blocked = reason or "被钩子拦截"
            self.store.add_tool_result(tc.call_id, f"[HOOK_BLOCKED] {blocked}")
            self.metrics.incr("tool_calls_total", tool=tc.tool_name, status="hook_blocked")
            log.warning("工具调用被钩子拦截", tool=tc.tool_name, reason=blocked)
            yield ev(EventType.TOOL_EXECUTED, sid, tool=tc.tool_name, call_id=tc.call_id,
                     ok=False, result="hook_blocked", reason=blocked, hook_blocked=True)
            return

        decision = self.gate.decide(tc.tool_name, tc.call_id, tc.arguments)

        if decision == Decision.DENY:
            self.store.add_tool_result(tc.call_id, f"[DENIED] '{tc.tool_name}' 被拒绝")
            self.metrics.incr("tool_calls_total", tool=tc.tool_name, status="denied")
            log.warning("工具调用被拒绝", tool=tc.tool_name)
            yield ev(EventType.TOOL_EXECUTED, sid, tool=tc.tool_name, call_id=tc.call_id, ok=False, result="denied")
            return

        if decision == Decision.HANDOFF:
            self.store.add_tool_result(
                tc.call_id, f"[HANDOFF] '{tc.tool_name}' 需要人工执行。请用户手动完成后告知结果。"
            )
            self.metrics.incr("tool_calls_total", tool=tc.tool_name, status="handoff")
            log.info("工具调用转人工", tool=tc.tool_name)
            yield ev(EventType.TOOL_EXECUTED, sid, tool=tc.tool_name, call_id=tc.call_id,
                     ok=False, result="handoff", handoff=True)
            return

        if decision == Decision.ALLOW_NOTIFY:
            yield ev(EventType.DELTA, sid, text=f"[自动执行] {tc.tool_name}\n")

        # 3) 沙箱执行（线程池 + 超时）+ 钩子后置（结果清洗，清洗后的结果才进账本）
        requested_at = self._approval_requested_at.pop(tc.call_id, None)
        approval_wait_ms = None
        if requested_at is not None:
            approval_wait_ms = int((time.time() - requested_at) * 1000)
            self.metrics.observe("approval_wait_ms", approval_wait_ms, tool=tc.tool_name)

        start = time.perf_counter()
        tool_span = self.tracer.start("tool_execute", session_id=sid, tool=tc.tool_name)
        ok, result, elapsed = self.gate.execute(tc.tool_name, tc.arguments, timeout_seconds=self.tool_timeout)
        result = str(await self.hooks.after_tool_execute(tc.tool_name, tc.arguments, result))
        async for he in self._emit_hook_errors(sid):
            yield he
        self.budget.account_tool()
        self.store.add_tool_result(tc.call_id, result)

        elapsed_ms = int(elapsed * 1000)
        self.metrics.incr("tool_calls_total", tool=tc.tool_name, status="ok" if ok else "error")
        self.metrics.observe("tool_latency_ms", elapsed_ms, tool=tc.tool_name)
        self.tracer.finish(tool_span, status="ok" if ok else "error", elapsed_ms=elapsed_ms,
                           decision=decision.value, ok=ok, approval_wait_ms=approval_wait_ms)
        log.info("工具执行完成", tool=tc.tool_name, ok=ok, elapsed_ms=elapsed_ms,
                 decision=decision.value, approval_wait_ms=approval_wait_ms)
        yield ev(EventType.TOOL_EXECUTED, sid, tool=tc.tool_name, call_id=tc.call_id,
                 ok=ok, result=result[:500], elapsed_ms=elapsed_ms)

    # ------------------------------------------------------------------
    def _account_llm_usage(self, final, llm_ms: int, message_count: int, tool_count: int) -> None:
        """把模型回报的 usage 计入指标并打日志（usage 缺失时只计一次成功）。"""
        usage = getattr(final, "usage", None) or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cache_hits = int(usage.get("cache_hit_tokens") or 0)
        self.metrics.incr("llm_calls_total", status="ok")
        if prompt_tokens:
            self.metrics.incr("llm_tokens_total", value=float(prompt_tokens), kind="prompt")
        if completion_tokens:
            self.metrics.incr("llm_tokens_total", value=float(completion_tokens), kind="completion")
        if cache_hits:
            self.metrics.incr("llm_cache_hits_total")
        log.info("模型调用完成", elapsed_ms=llm_ms, messages=message_count, tools=tool_count,
                 prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                 cache_hit_tokens=cache_hits,
                 tool_calls=len(getattr(final, "tool_calls", None) or []))

    # ------------------------------------------------------------------
    async def _emit_hook_errors(self, sid: str) -> AsyncIterator[Event]:
        """钩子自身执行失败：转成 error 事件下发，主流程继续。"""
        for msg in self.hooks.drain_errors():
            self.metrics.incr("hook_errors_total")
            log.warning("钩子执行失败", detail=msg)
            yield ev(EventType.ERROR, sid, message=f"钩子执行失败: {msg}", hook_error=True)

    # ------------------------------------------------------------------
    def _last_user_text(self) -> str:
        for m in reversed(self.store.messages):
            if m.role == Role.USER and not m.is_summary:
                return m.content
        return ""

    # ---- 恢复入口（审批后 / 人工执行后）----
    async def resume(self) -> AsyncIterator[Event]:
        """不注入新输入，继续处理 pending。"""
        async for e in self.run(None):
            yield e

    def inject_tool_result_and_prepare(self, call_id: str, result: str) -> None:
        """人工路径：用户手动执行完工具后，把结果写回账本。"""
        self.store.add_tool_result(call_id, result)
