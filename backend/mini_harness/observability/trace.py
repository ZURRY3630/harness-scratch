# DOC: docs/12-observability.md
"""Trace 落盘接口（P1-4）：把引擎事件流持久化，供回放 / 评测 / 排障。

契约：
- `write(event)` 只做序列化与缓冲，不得阻塞主流程；
- `flush()` 把缓冲刷盘（引擎淘汰、进程退出前调用）；
- 实现不得抛异常影响业务；引擎侧也会再兜一层——观测链路故障不能拖垮 Agent。

启用方式：环境变量 `HARNESS_TRACE_PATH` 指向输出文件（见 api/routes.py 的 build_engine）。
未设置时不落盘，引擎侧 `trace_writer=None` 直接跳过。
"""

from __future__ import annotations

import abc
import json
from pathlib import Path

from ..core.events import Event


class TraceWriter(abc.ABC):
    """事件落盘接口。"""

    @abc.abstractmethod
    def write(self, event: Event) -> None:
        """写入一个事件（应快速返回，不阻塞主流程）。"""

    @abc.abstractmethod
    def flush(self) -> None:
        """把缓冲刷盘。"""


class NullTraceWriter(TraceWriter):
    """空实现：用于显式关闭追踪或测试注入。"""

    def write(self, event: Event) -> None:
        return None

    def flush(self) -> None:
        return None


class JsonlTraceWriter(TraceWriter):
    """最小可用实现：一行一个事件（JSONL），便于 tail / grep / 后续离线加载。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, event: Event) -> None:
        self._fh.write(json.dumps(event.to_trace_dict(), ensure_ascii=False) + "\n")

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self.flush()
        self._fh.close()
