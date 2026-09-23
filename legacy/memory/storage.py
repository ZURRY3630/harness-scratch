import json
from typing import List

from core.message import Message, MessageRole, ToolCall



class SimpleMemory:
    def __init__(self):
        self.messages: List[Message] = []

    def add_user_message(self, text: str) -> None:
        self.messages.append(Message(role=MessageRole.USER, content=text))

    def add_assistant_message(self, text: str) -> None:
        self.messages.append(Message(role=MessageRole.ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, tool_calls: List[ToolCall]) -> None:
        self.messages.append(
            Message(role=MessageRole.ASSISTANT, content=text or "", tool_calls=tool_calls)
        )

    def add_tool_result(self, call_id: str, result: str) -> None:
        self.messages.append(
            Message(role=MessageRole.TOOL, content=result, tool_call_id=call_id)
        )

    def get_context(self) -> List[dict]:
        result = []
        for m in self.messages:
            if m.role == MessageRole.ASSISTANT and m.tool_calls:
                msg = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
                # 关键：content 为空则完全省略这个键（不要传 None！）
                if m.content:
                    msg["content"] = m.content
                result.append(msg)

            elif m.role == MessageRole.TOOL:
                # tool 消息必须带 tool_call_id，否则丢弃（避免孤儿 tool）
                if m.tool_call_id:
                    result.append({
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    })

            else:
                result.append({"role": m.role.value, "content": m.content})

        return result

    def get_pending_tool_calls(self) -> List[ToolCall]:
        """返回已声明但还没拿到结果的工具调用。"""
        completed = {
            m.tool_call_id for m in self.messages
            if m.role == MessageRole.TOOL and m.tool_call_id
        }
        pending = []
        for m in self.messages:
            if m.role == MessageRole.ASSISTANT and m.tool_calls:
                for tc in m.tool_calls:
                    if tc.call_id not in completed:
                        pending.append(tc)
        return pending