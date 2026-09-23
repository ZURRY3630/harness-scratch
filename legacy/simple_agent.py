import asyncio
import os
from dotenv import load_dotenv

from models.openai_provider import OpenAIProvider
from runtime.engine import RuntimeEngine
from tools.registry import Tool, ToolRegistry
from memory.storage import SimpleMemory

load_dotenv()

def make_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool(
        name="echo",
        description="Echoes back the input text.",
        func=lambda text: f"Echo: {text}",
    ))
    registry.register(Tool(
        name="get_time",
        description="Return current UTC time in ISO format.",
        func=lambda: __import__("datetime").datetime.utcnow().isoformat(),
    ))
    return registry

async def main():
    provider = OpenAIProvider(model="deepseek-flash")
    engine = RuntimeEngine(
        model_provider=provider,
        tool_registry=make_tools(),
        memory=SimpleMemory(),
        max_turns=5,
    )

    async for event in engine.run("现在几点？"):
        print(event)

if __name__ == "__main__":
    asyncio.run(main())