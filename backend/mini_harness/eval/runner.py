# DOC: docs/13-evaluation.md
"""评估框架占位（P1-3）：接口先固定，实现留给后续迭代。

约定：
- `TestCase` = 一段输入 + 期望（期望工具调用序列 / 期望关键词 / 是否允许报错）；
- `EvalResult` = 逐条断言的结论 + 实际产物（事件序列、最终回答、耗时）；
- 未来实现必须**离线可跑**：用 mock provider 脚本化模型响应，不依赖真实网络调用，
  否则评测结果不可复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TestCase:
    """一条评测用例。"""

    name: str
    input: str
    expect_tools: list[str] = field(default_factory=list)      # 期望按序调用的工具名
    expect_contains: list[str] = field(default_factory=list)   # 最终回答应包含的片段
    expect_no_error: bool = True                               # 是否禁止 error 事件
    tags: list[str] = field(default_factory=list)
    timeout_seconds: float = 60.0


@dataclass
class EvalResult:
    """一条用例的执行结论。"""

    case: TestCase
    passed: bool
    asserts: list[dict] = field(default_factory=list)   # [{"name":..., "passed":..., "detail":...}]
    events: list[dict] = field(default_factory=list)    # Event.to_trace_dict() 序列
    answer: str = ""
    error: str = ""
    elapsed_ms: int = 0


class EvalRunner:
    """评测执行器（占位：签名固定，实现待补）。

    实现要点（留给后续迭代）：
    - 用 `load_project_config` + `build_engine` 组装被测引擎，provider 换成脚本化 mock；
    - 消费 `engine.run(case.input)` 的事件流，落成 `EvalResult`；
    - 断言维度：工具调用序列、最终回答片段、审批挂起行为、error 事件数、token 预算用量。
    """

    def __init__(self, project_config_path: str, provider_script_path: str | None = None) -> None:
        self.project_config_path = project_config_path
        self.provider_script_path = provider_script_path

    def load_cases(self, cases_path: str) -> list[TestCase]:
        """从 YAML/JSON 加载用例集。"""
        raise NotImplementedError("P1-3 占位：用例加载待实现")

    async def run_case(self, case: TestCase) -> EvalResult:
        """执行单条用例。"""
        raise NotImplementedError("P1-3 占位：单用例执行待实现")

    async def run_suite(self, cases: list[TestCase]) -> list[EvalResult]:
        """批量执行并聚合。"""
        raise NotImplementedError("P1-3 占位：批量执行待实现")
