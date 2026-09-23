from typing import AsyncIterator

from memory.storage import SimpleMemory
from models.base import BaseModelProvider
from tools.registry import ToolRegistry
from tools.permission import PermissionGate, PermissionLevel, Decision
import json



class RuntimeEngine:
    def __init__(
        self,
        model_provider: BaseModelProvider,
        tool_registry: ToolRegistry,
        memory: SimpleMemory,
        max_turns: int = 10,
        permission_gate: PermissionGate | None = None,
    ):
        self.model = model_provider
        self.tools = tool_registry
        self.memory = memory
        self.max_turns = max_turns
        self.permission_gate = permission_gate or PermissionGate(tool_registry)

    async def run(self, user_input: str | None = None) -> AsyncIterator[str]:
        if user_input:
            self.memory.add_user_message(user_input)

        for _ in range(self.max_turns):
            # ---------- 1. 处理 pending 工具调用（唯一的执行入口）----------
            pending = self.memory.get_pending_tool_calls()
            for tc in pending:
                decision = self.permission_gate.decide(tc.tool_name, tc.call_id)

                if decision == Decision.ASK:
                    payload = json.dumps({
                        "call_id": tc.call_id,
                        "tool_name": tc.tool_name,
                        "arguments": tc.arguments,
                    }, ensure_ascii=False)
                    yield f"[APPROVAL_REQUIRED] {payload}"
                    return

                if decision == Decision.DENY:
                    self.memory.add_tool_result(
                        tc.call_id, f"[DENIED] '{tc.tool_name}' 需要人工执行"
                    )
                    yield f"[Tool {tc.tool_name}] [DENIED]"
                    continue

                # ALLOW
                ok, result = self.permission_gate.execute(tc.tool_name, tc.arguments)
                self.memory.add_tool_result(tc.call_id, result)
                yield f"[Tool {tc.tool_name}] {result[:200]}"

            # ---------- 2. 调用模型 ----------
            messages = self.memory.get_context()
            tool_schemas = self.tools.get_tool_schemas()
            response = await self.model.chat(messages, tools=tool_schemas or None)

            if response.tool_calls:
                # 关键：只登记，不执行；回到循环开头由 pending 处理
                self.memory.add_assistant_with_tool_calls(
                    response.content or "", response.tool_calls
                )
                continue                      # ← 就是这个 continue，取代了原来的执行块
            else:
                self.memory.add_assistant_message(response.content or "")
                yield response.content or ""
                return

        yield "[Max turns reached without final answer]"