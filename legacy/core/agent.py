from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, Optional

class AgentState(str, Enum):
    """智能体状态"""
    IDLE = "idle"   # 空闲状态
    INITIALIZING = "initializing"  # 初始化状态
    EXECUTING = "executing"  # 执行状态
    PAUSED = "paused"  # 暂停状态
    COMPLETED = "completed"  # 完成状态
    FAILED = "failed"  # 失败状态

@dataclass
class ExecuteResult:
    """执行结果"""
    status: str  # "success", "error", "timeout"   # 执行状态
    output: Optional[str] = None  # 执行输出
    error: Optional[str] = None  # 错误信息
    metadata: Optional[Dict[str, Any]] = None  # 元数据

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

@dataclass
class Agent:
    """智能体定义"""
    agent_id: str  # 智能体ID
    name: str  # 智能体名称
    description: str  # 智能体描述
    system_prompt: str  # 系统提示
    model_name: str = "deepseek-flash"  # 模型名称
    max_steps: int = 10  # 最大执行步数
    metadata: Optional[Dict[str, Any]] = None  # 元数据

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
