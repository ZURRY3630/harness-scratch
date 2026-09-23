import os
from typing import List, Optional
from openai import AsyncOpenAI
import uuid
from models.base import BaseModelProvider, ModelResponse
from core.message import ToolCall
import json

class OpenAIProvider(BaseModelProvider):
    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
    ):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.temperature = temperature
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL"),
        )

    async def chat(self, messages: List[dict], tools: Optional[list] = None) -> ModelResponse:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # 调试用
        import json as _json
        print("=== 实际发送的 messages ===")
        print(_json.dumps(messages, ensure_ascii=False, indent=2))
        print("===========================")

        resp = await self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls = None
        if msg.tool_calls:
            tool_calls = []
            for tc in msg.tool_calls:
                # call_id 兜底：确保不为空，否则 tool 消息就没法配对
                call_id = tc.id or f"call_{uuid.uuid4().hex[:8]}"
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    tool_name=tc.function.name,
                    arguments=args,
                    call_id=call_id,
                ))

        return ModelResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
        )