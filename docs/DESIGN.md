> ARCHIVED v0.1 design: descriptions below are historical intentions, not the v0.2 support contract. See README.md, GUIDE.md and PILOT.md for implemented behavior.

Current product direction: quality-constrained optimization for repeatable Agent
workloads, starting with data extraction. The implemented experiment, policy
selection and explicit fallback workflow is documented in [OPTIMIZATION.md](OPTIMIZATION.md).
Tracing and budget control below remain its execution foundation. Learned routing,
model compression and hardware configuration search are future integrations.

# Reins — 技术设计文档

> **版本：** v0.1 | **日期：** 2026-04-03 | **状态：** Draft

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户代码 / CLI 工具                        │
│  ┌────────────────────┐          ┌────────────────────┐          │
│  │  Python Agent 代码  │          │  Claude Code / CLI  │          │
│  │  import reins       │          │  ANTHROPIC_BASE_URL │          │
│  └─────────┬──────────┘          └─────────┬──────────┘          │
│            │ Library 模式                   │ Proxy 模式           │
└────────────┼───────────────────────────────┼────────────────────┘
             ▼                               ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 1: Harness SDK                                          │
│  ┌─────────────────────┐    ┌──────────────────────┐           │
│  │ Instrumentor        │    │ Proxy Server         │           │
│  │ (monkey-patch SDK)  │    │ (aiohttp reverse     │           │
│  │                     │    │  proxy)               │           │
│  └─────────┬───────────┘    └──────────┬───────────┘           │
│            └────────────┬───────────────┘                      │
│                         ▼                                      │
│              ┌──────────────────┐                              │
│              │   Span Collector  │  ← 统一采集点                │
│              │   + Event Bus     │  ← 模块间通信                │
│              └────────┬─────────┘                              │
└───────────────────────┼────────────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 2: Storage                                              │
│              ┌──────────────────┐                              │
│              │     DuckDB       │  ← 嵌入式，零配置              │
│              │  ~/.reins/data/  │                              │
│              └────────┬─────────┘                              │
└───────────────────────┼────────────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 3: Modules (插件化，按需加载)                              │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │  Budget   │  │  Trace   │  │   Lens   │  │  Pulse   │       │
│  │  成本治理  │  │  深度追踪 │  │  调试诊断 │  │  可靠性   │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                │
│  每个模块通过 entry_points 注册，通过 Event Bus 通信              │
│  模块间无直接 import 依赖                                        │
└────────────────────────────────────────────────────────────────┘
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  Layer 4: CLI                                                  │
│  reins report | reins query | reins dashboard | reins proxy    │
│  reins trace  | reins replay | reins pulse    | reins diagnose │
└────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心数据模型

### 2.1 数据库 Schema（DuckDB）

```sql
-- Agent 运行记录
CREATE TABLE runs (
    run_id          VARCHAR PRIMARY KEY,  -- UUID
    session_id      VARCHAR,              -- 跨多次 run 的 session
    agent_name      VARCHAR NOT NULL,     -- Agent 标识
    status          VARCHAR NOT NULL,     -- running | completed | failed | budget_exceeded
    started_at      TIMESTAMP NOT NULL,
    ended_at        TIMESTAMP,
    total_cost      DECIMAL(10, 6),       -- 美元
    total_tokens_in  BIGINT,
    total_tokens_out BIGINT,
    budget_limit    DECIMAL(10, 6),       -- 配置的预算上限
    degraded_count  INTEGER DEFAULT 0,    -- 降级次数
    metadata        JSON                  -- 用户自定义标签
);

-- Span：每次 LLM 调用或工具调用
CREATE TABLE spans (
    span_id         VARCHAR PRIMARY KEY,
    run_id          VARCHAR NOT NULL REFERENCES runs(run_id),
    parent_span_id  VARCHAR,              -- 树形结构
    span_type       VARCHAR NOT NULL,     -- llm | tool | retrieval | custom
    name            VARCHAR NOT NULL,     -- e.g. "anthropic.messages.create"
    started_at      TIMESTAMP NOT NULL,
    ended_at        TIMESTAMP,
    duration_ms     DOUBLE,

    -- LLM 特有字段
    model           VARCHAR,              -- e.g. "claude-sonnet-4-20250514"
    model_requested VARCHAR,              -- 降级前请求的模型
    provider        VARCHAR,              -- anthropic | openai | google
    tokens_in       BIGINT,
    tokens_out      BIGINT,
    cost            DECIMAL(10, 6),
    degraded        BOOLEAN DEFAULT false,

    -- 工具调用特有字段
    tool_name       VARCHAR,
    tool_status     VARCHAR,              -- success | error | timeout
    tool_error      VARCHAR,

    -- 上下文健康（Lens 模块填充）
    context_tokens  BIGINT,               -- 当前上下文窗口大小
    context_health  DOUBLE,               -- 0.0 - 1.0

    -- 评估分数（Pulse 模块填充）
    eval_scores     JSON,                 -- {"coherence": 0.85, "instruction_following": 0.92}

    -- 安全标记（Pulse 模块填充）
    safety_flags    JSON,                 -- ["pii_detected", "sql_injection"]

    -- 通用
    status          VARCHAR NOT NULL,     -- ok | error
    error_message   VARCHAR,
    metadata        JSON
);

-- 预算账本（Budget 模块）
CREATE TABLE budget_ledger (
    id              VARCHAR PRIMARY KEY,
    timestamp       TIMESTAMP NOT NULL,
    agent_name      VARCHAR NOT NULL,
    event_type      VARCHAR NOT NULL,     -- reserve | commit | release | degrade | reject
    amount          DECIMAL(10, 6),       -- 美元
    balance_after   DECIMAL(10, 6),
    run_id          VARCHAR,
    span_id         VARCHAR,
    details         JSON                  -- 降级详情等
);

-- 预算配置快照
CREATE TABLE budget_configs (
    id              VARCHAR PRIMARY KEY,
    loaded_at       TIMESTAMP NOT NULL,
    config_hash     VARCHAR NOT NULL,     -- 配置变更检测
    config          JSON NOT NULL         -- 完整的 YAML 配置 JSON 化
);

-- 可靠性事件（Pulse 模块）
CREATE TABLE reliability_events (
    id              VARCHAR PRIMARY KEY,
    timestamp       TIMESTAMP NOT NULL,
    agent_name      VARCHAR NOT NULL,
    event_type      VARCHAR NOT NULL,     -- context_rot | guardrail_hit | quality_drop | recovery
    severity        VARCHAR,              -- info | warning | critical
    details         JSON,
    run_id          VARCHAR,
    span_id         VARCHAR
);
```

### 2.2 数据关系

```
AgentSession (session_id)
├── Run (run_id)
│   ├── Span[] (span_id, 树形结构)
│   │   ├── LLM Span (model, tokens, cost, context_health)
│   │   └── Tool Span (tool_name, tool_status)
│   ├── BudgetLedger[] (reserve → commit/release events)
│   └── ReliabilityEvents[] (context_rot, guardrail_hit)
└── BudgetConfig (当前生效的预算配置)
```

---

## 3. 插件化架构

### 3.1 模块接口

每个模块实现统一的 `ReinsModule` 接口：

```python
# reins/core/module.py
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reins.core.events import EventBus
    from reins.core.storage import Storage
    from reins.core.config import ReinsConfig

class ReinsModule(ABC):
    """所有 Reins 模块的基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """模块名称，如 'budget', 'trace', 'lens', 'pulse'。"""
        ...

    @abstractmethod
    def init(self, event_bus: "EventBus", storage: "Storage", config: "ReinsConfig") -> None:
        """初始化模块。注册事件监听器，创建数据库表等。"""
        ...

    def on_span_start(self, span: "SpanData") -> "SpanData":
        """Span 开始前的 hook。可修改 span（如 Budget 修改 model）。"""
        return span

    def on_span_end(self, span: "SpanData") -> None:
        """Span 结束后的 hook。用于记录、评估等。"""
        pass

    def shutdown(self) -> None:
        """清理资源。"""
        pass
```

### 3.2 模块发现与加载

```python
# reins/core/loader.py
import importlib.metadata
from reins.core.module import ReinsModule

def discover_modules() -> list[type[ReinsModule]]:
    """通过 entry_points 发现所有已安装的模块。"""
    modules = []
    for ep in importlib.metadata.entry_points(group="reins.modules"):
        module_cls = ep.load()
        if issubclass(module_cls, ReinsModule):
            modules.append(module_cls)
    return modules

def load_modules(event_bus, storage, config) -> list[ReinsModule]:
    """加载并初始化所有启用的模块。"""
    instances = []
    for cls in discover_modules():
        module = cls()
        if config.is_module_enabled(module.name):
            module.init(event_bus, storage, config)
            instances.append(module)
    return instances
```

### 3.3 pyproject.toml 中的 entry_points 注册

```toml
[project.entry-points."reins.modules"]
budget = "reins.budget:BudgetModule"
trace  = "reins.trace:TraceModule"
lens   = "reins.lens:LensModule"
pulse  = "reins.pulse:PulseModule"
```

### 3.4 事件总线

模块间通信的唯一通道，零耦合：

```python
# reins/core/events.py
from collections import defaultdict
from typing import Any, Callable
import asyncio

class EventBus:
    """进程内事件总线。模块间通信的唯一通道。"""

    def __init__(self):
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event_name: str, callback: Callable) -> None:
        """注册事件监听器。"""
        self._listeners[event_name].append(callback)

    def emit(self, event_name: str, **kwargs: Any) -> None:
        """触发事件。同步执行所有监听器（fail-safe）。"""
        for callback in self._listeners.get(event_name, []):
            try:
                result = callback(**kwargs)
                # 支持 async callback
                if asyncio.iscoroutine(result):
                    asyncio.get_event_loop().create_task(result)
            except Exception:
                # 模块错误不能影响主流程
                pass  # TODO: 记录到内部日志

# 预定义事件名
class Events:
    # Core 事件
    SPAN_START = "core.span_start"
    SPAN_END = "core.span_end"
    RUN_START = "core.run_start"
    RUN_END = "core.run_end"

    # Budget 事件
    BUDGET_THRESHOLD = "budget.threshold_reached"
    BUDGET_EXCEEDED = "budget.exceeded"
    BUDGET_DEGRADED = "budget.degraded"
    BUDGET_CIRCUIT_BREAK = "budget.circuit_break"

    # Lens 事件
    CONTEXT_ROT = "lens.context_rot_detected"
    HEALTH_DROP = "lens.health_score_drop"

    # Pulse 事件
    QUALITY_DROP = "pulse.quality_drop"
    GUARDRAIL_HIT = "pulse.guardrail_hit"
```

### 3.5 模块依赖关系

```
Core（始终加载）
  ├── Budget（依赖 Core）
  ├── Trace（依赖 Core）
  │     └── Lens（依赖 Core + Trace）
  └── Pulse（依赖 Core）
```

依赖在 `pyproject.toml` 的 `extras` 中声明：

```toml
[project.optional-dependencies]
budget = []                     # 无额外依赖
trace  = []                     # 无额外依赖
lens   = ["reins[trace]"]       # 依赖 Trace 模块
pulse  = []                     # 无额外依赖
all    = ["reins[budget]", "reins[trace]", "reins[lens]", "reins[pulse]"]
```

---

## 4. SDK Instrumentation 引擎

### 4.1 Monkey-patch 策略

```python
# reins/core/instrumentor.py
from reins.core.events import EventBus
from reins.core.models import SpanData

class Instrumentor:
    """自动 instrumentation 引擎。Monkey-patch LLM SDK 的调用方法。"""

    def __init__(self, event_bus: EventBus, modules: list):
        self._event_bus = event_bus
        self._modules = modules
        self._original_methods = {}  # 保存原始方法，用于卸载

    def instrument_anthropic(self):
        """Patch Anthropic SDK 的 messages.create 方法。"""
        try:
            import anthropic
        except ImportError:
            return

        original_create = anthropic.resources.Messages.create
        original_acreate = anthropic.resources.AsyncMessages.create
        self._original_methods["anthropic.messages.create"] = original_create
        self._original_methods["anthropic.messages.acreate"] = original_acreate

        instrumentor = self

        def patched_create(self_inner, *args, **kwargs):
            return instrumentor._wrap_sync_call(
                original_create, self_inner, "anthropic", *args, **kwargs
            )

        async def patched_acreate(self_inner, *args, **kwargs):
            return await instrumentor._wrap_async_call(
                original_acreate, self_inner, "anthropic", *args, **kwargs
            )

        anthropic.resources.Messages.create = patched_create
        anthropic.resources.AsyncMessages.create = patched_acreate

    def _wrap_sync_call(self, original_fn, client, provider, *args, **kwargs):
        """包装同步 LLM 调用：pre-hook → call → post-hook。"""
        span = SpanData.from_llm_call(provider, kwargs)

        # Pre-hooks: 让各模块修改 span（如 Budget 改 model）
        for module in self._modules:
            span = module.on_span_start(span)

        # 应用模块修改（如降级后的 model）
        kwargs["model"] = span.model

        try:
            response = original_fn(client, *args, **kwargs)
            span.complete_from_response(response)
        except Exception as e:
            span.mark_error(e)
            raise
        finally:
            # Post-hooks
            for module in self._modules:
                module.on_span_end(span)
            self._event_bus.emit("core.span_end", span=span)

        return response

    def uninstrument(self):
        """恢复所有原始方法。用于测试和清理。"""
        for key, original in self._original_methods.items():
            # 恢复原始方法
            ...
```

### 4.2 Span 数据模型

```python
# reins/core/models.py
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
import uuid

@dataclass
class SpanData:
    span_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    parent_span_id: str | None = None
    span_type: str = "llm"           # llm | tool | retrieval | custom
    name: str = ""
    provider: str = ""               # anthropic | openai | google
    model: str = ""
    model_requested: str = ""        # 降级前的原始模型
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    duration_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Decimal = Decimal("0")
    degraded: bool = False
    status: str = "ok"
    error_message: str | None = None
    context_tokens: int | None = None
    context_health: float | None = None
    eval_scores: dict[str, float] | None = None
    safety_flags: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_llm_call(cls, provider: str, kwargs: dict) -> "SpanData":
        """从 LLM 调用参数创建 Span。"""
        return cls(
            span_type="llm",
            provider=provider,
            model=kwargs.get("model", ""),
            model_requested=kwargs.get("model", ""),
            name=f"{provider}.messages.create",
        )

    def complete_from_response(self, response) -> None:
        """从 LLM 响应补充 Span 数据。"""
        self.ended_at = datetime.utcnow()
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        # 从 response.usage 提取 token 数
        if hasattr(response, "usage"):
            self.tokens_in = getattr(response.usage, "input_tokens", 0)
            self.tokens_out = getattr(response.usage, "output_tokens", 0)
        self.cost = self._calculate_cost()

    def _calculate_cost(self) -> Decimal:
        """根据 model + tokens 计算成本。"""
        from reins.core.pricing import get_price
        return get_price(self.provider, self.model, self.tokens_in, self.tokens_out)
```

### 4.3 定价表

```python
# reins/core/pricing.py
from decimal import Decimal

# 每百万 token 的美元价格（定期更新）
PRICING: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
    # provider -> model -> (input_per_m, output_per_m)
    "anthropic": {
        "claude-opus-4-20250514":     (Decimal("15.0"),  Decimal("75.0")),
        "claude-sonnet-4-20250514":   (Decimal("3.0"),   Decimal("15.0")),
        "claude-haiku-4-20250514":    (Decimal("0.80"),  Decimal("4.0")),
    },
    "openai": {
        "gpt-4o":                     (Decimal("2.50"),  Decimal("10.0")),
        "gpt-4o-mini":                (Decimal("0.15"),  Decimal("0.60")),
        "o3":                         (Decimal("10.0"),  Decimal("40.0")),
    },
}

def get_price(provider: str, model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """计算单次调用成本。"""
    prices = PRICING.get(provider, {}).get(model)
    if prices is None:
        return Decimal("0")  # 未知模型不计费（fail-open）
    input_cost = prices[0] * tokens_in / 1_000_000
    output_cost = prices[1] * tokens_out / 1_000_000
    return input_cost + output_cost
```

---

## 5. Budget 引擎

### 5.1 核心流程

```
LLM 调用请求
      │
      ▼
┌─────────────────┐
│ 1. 估算成本      │  ← 基于 model + 预估 token 数
│    (pre-call)    │
└────────┬────────┘
         ▼
┌─────────────────┐
│ 2. 原子预留      │  ← budget -= estimated_cost (atomic)
│    (reserve)     │
└────────┬────────┘
         │
    ┌────┴────┐
    │ 余额够? │
    └────┬────┘
     Yes │        No
         │    ┌──────────────────┐
         │    │ 3. 执行 on_exceed │
         │    │   策略             │
         │    └────────┬─────────┘
         │             │
         │    ┌────────┴────────┐
         │    │ degrade: 换模型  │
         │    │ pause: 等待审批  │
         │    │ alert: 继续执行  │
         │    │ reject: 抛异常   │
         │    └────────┬────────┘
         │             │
         ▼             ▼
┌─────────────────────────┐
│ 4. 执行 LLM 调用         │
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│ 5. 成本校正              │  ← 实际成本 vs 预估成本
│    commit 或 release 差额│
└─────────────────────────┘
```

### 5.2 原子预留实现

```python
# reins/budget/engine.py
import threading
from decimal import Decimal
from reins.core.module import ReinsModule
from reins.core.models import SpanData

class BudgetEngine(ReinsModule):
    name = "budget"

    def __init__(self):
        self._lock = threading.Lock()     # 进程内原子操作
        self._balances: dict[str, Decimal] = {}  # agent_name -> remaining
        self._reservations: dict[str, Decimal] = {}  # span_id -> reserved

    def init(self, event_bus, storage, config):
        self._storage = storage
        self._config = config
        self._event_bus = event_bus
        self._load_balances()
        # 监听事件
        event_bus.on("lens.context_rot_detected", self._on_context_rot)

    def on_span_start(self, span: SpanData) -> SpanData:
        """Pre-call hook: 预留预算，必要时降级。"""
        agent = span.metadata.get("agent_name", "default")
        budget_config = self._config.get_agent_budget(agent)
        if not budget_config:
            return span  # 无预算限制

        estimated_cost = self._estimate_cost(span)

        with self._lock:
            balance = self._balances.get(agent, budget_config.limit)

            if balance >= estimated_cost:
                # 正常预留
                self._balances[agent] = balance - estimated_cost
                self._reservations[span.span_id] = estimated_cost
                return span

            # 预算不足 → 执行策略
            return self._handle_exceed(span, agent, budget_config, balance, estimated_cost)

    def on_span_end(self, span: SpanData) -> None:
        """Post-call hook: 校正预算（实际 vs 预估）。"""
        reserved = self._reservations.pop(span.span_id, None)
        if reserved is None:
            return

        actual_cost = span.cost
        delta = reserved - actual_cost

        agent = span.metadata.get("agent_name", "default")
        with self._lock:
            self._balances[agent] = self._balances.get(agent, Decimal("0")) + delta

        # 记录账本
        self._storage.insert_budget_event(
            agent_name=agent,
            event_type="commit",
            amount=actual_cost,
            balance_after=self._balances[agent],
            run_id=span.run_id,
            span_id=span.span_id,
        )

    def _handle_exceed(self, span, agent, config, balance, estimated):
        """处理预算超支。"""
        strategy = config.on_exceed

        if strategy == "degrade":
            degraded_model = self._get_cheaper_model(span.provider, span.model)
            if degraded_model:
                span.model = degraded_model
                span.degraded = True
                new_cost = self._estimate_cost(span)
                self._balances[agent] = balance - new_cost
                self._reservations[span.span_id] = new_cost
                self._event_bus.emit("budget.degraded",
                    agent=agent, original=span.model_requested, degraded=degraded_model)
                return span
            # 没有更便宜的模型，fallthrough to reject
            strategy = "reject"

        if strategy == "alert":
            self._event_bus.emit("budget.exceeded", agent=agent, balance=balance)
            self._balances[agent] = balance - estimated
            self._reservations[span.span_id] = estimated
            return span

        if strategy == "pause":
            self._event_bus.emit("budget.exceeded", agent=agent, balance=balance)
            raise BudgetPausedError(agent=agent, balance=balance)

        # reject
        raise BudgetExceededError(agent=agent, balance=balance)

    def _get_cheaper_model(self, provider: str, model: str) -> str | None:
        """获取同 provider 更便宜的模型。"""
        DEGRADATION_MAP = {
            "anthropic": {
                "claude-opus-4-20250514": "claude-sonnet-4-20250514",
                "claude-sonnet-4-20250514": "claude-haiku-4-20250514",
            },
            "openai": {
                "o3": "gpt-4o",
                "gpt-4o": "gpt-4o-mini",
            },
        }
        return DEGRADATION_MAP.get(provider, {}).get(model)
```

### 5.3 熔断器

```python
# reins/budget/circuit_breaker.py
from collections import deque
from datetime import datetime, timedelta

class CircuitBreaker:
    """检测 Agent 循环调用，自动熔断。"""

    def __init__(self, max_calls_per_minute: int = 20, window: timedelta = timedelta(minutes=1)):
        self._max = max_calls_per_minute
        self._window = window
        self._call_history: dict[str, deque[datetime]] = {}

    def check(self, agent_name: str) -> bool:
        """返回 True 如果应该熔断。"""
        now = datetime.utcnow()
        history = self._call_history.setdefault(agent_name, deque())

        # 清理过期记录
        while history and (now - history[0]) > self._window:
            history.popleft()

        history.append(now)
        return len(history) > self._max
```

---

## 6. Proxy 服务器设计

### 6.1 架构

```
Claude Code                    Reins Proxy                    Anthropic API
    │                              │                              │
    │  POST /v1/messages           │                              │
    │  ANTHROPIC_BASE_URL ────────►│                              │
    │                              │  1. 解析请求                  │
    │                              │  2. Budget 检查/降级          │
    │                              │  3. 创建 Span                 │
    │                              │                              │
    │                              │  POST /v1/messages ─────────►│
    │                              │  (可能换了 model)              │
    │                              │                              │
    │                              │  ◄───────── Response ────────│
    │                              │  4. 提取 usage               │
    │                              │  5. 计算成本                  │
    │                              │  6. 更新预算                  │
    │                              │  7. 写入 DuckDB              │
    │  ◄───────── Response ────────│                              │
    │                              │                              │
```

### 6.2 实现

```python
# reins/proxy/server.py
from aiohttp import web, ClientSession

class ReinsProxy:
    """本地透明代理服务器。"""

    def __init__(self, config, budget_engine, storage):
        self._config = config
        self._budget = budget_engine
        self._storage = storage
        self._app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        # Anthropic API 路由
        self._app.router.add_route("*", "/v1/{path:.*}", self._handle_anthropic)
        # OpenAI API 路由（Phase 1.5）
        self._app.router.add_route("*", "/openai/v1/{path:.*}", self._handle_openai)
        # 健康检查
        self._app.router.add_get("/health", self._health)

    async def _handle_anthropic(self, request: web.Request) -> web.Response:
        """处理 Anthropic Messages API 请求。"""
        body = await request.json()
        path = request.match_info["path"]

        # 只拦截 messages 调用
        if path == "messages" and request.method == "POST":
            return await self._handle_messages(request, body, provider="anthropic")

        # 其他请求直接透传
        return await self._forward(request, "https://api.anthropic.com")

    async def _handle_messages(self, request, body, provider):
        """拦截 LLM 调用：预算检查 → 可能降级 → 转发 → 记录。"""
        span = SpanData.from_llm_call(provider, body)
        span.metadata["agent_name"] = "proxy"  # 或从 header 提取

        # Budget pre-hook
        span = self._budget.on_span_start(span)

        # 如果降级了，修改请求体中的 model
        if span.degraded:
            body["model"] = span.model

        # 转发到真实 API
        upstream_url = f"https://api.anthropic.com/v1/messages"
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "content-length")}

        async with ClientSession() as session:
            async with session.post(upstream_url, json=body, headers=headers) as resp:
                response_body = await resp.json()

                # 提取 usage，完成 span
                span.complete_from_response_dict(response_body)

                # Budget post-hook
                self._budget.on_span_end(span)

                # 写入存储
                self._storage.insert_span(span)

                return web.json_response(response_body, status=resp.status)

    async def start(self, port: int = 8082):
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", port)
        await site.start()
```

### 6.3 Streaming 支持

```python
async def _handle_messages_stream(self, request, body, provider):
    """处理 SSE streaming 响应。"""
    span = SpanData.from_llm_call(provider, body)
    span = self._budget.on_span_start(span)

    if span.degraded:
        body["model"] = span.model

    upstream_url = f"https://api.anthropic.com/v1/messages"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length")}

    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream"}
    )
    await response.prepare(request)

    async with ClientSession() as session:
        async with session.post(upstream_url, json=body, headers=headers) as resp:
            accumulated_usage = {}
            async for chunk in resp.content:
                await response.write(chunk)
                # 解析 SSE 事件，累积 usage
                self._accumulate_usage(chunk, accumulated_usage)

    # Stream 结束后记录
    span.complete_from_usage(accumulated_usage)
    self._budget.on_span_end(span)
    self._storage.insert_span(span)
    return response
```

---

## 7. 存储层

### 7.1 DuckDB 管理

```python
# reins/core/storage.py
import duckdb
from pathlib import Path

class Storage:
    """DuckDB 存储层。"""

    DEFAULT_PATH = Path.home() / ".reins" / "data" / "traces.duckdb"

    def __init__(self, db_path: Path | None = None):
        self._path = db_path or self.DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self._path))
        self._migrate()

    def _migrate(self):
        """创建/升级数据库 schema。"""
        version = self._get_schema_version()
        migrations = self._get_pending_migrations(version)
        for migration in migrations:
            self._conn.execute(migration.sql)
            self._set_schema_version(migration.version)

    def insert_span(self, span: "SpanData") -> None:
        """写入一条 Span 记录。"""
        self._conn.execute("""
            INSERT INTO spans (span_id, run_id, parent_span_id, span_type, name,
                started_at, ended_at, duration_ms, model, model_requested,
                provider, tokens_in, tokens_out, cost, degraded, status,
                error_message, context_tokens, context_health, eval_scores,
                safety_flags, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, span.to_row())

    def query(self, sql: str) -> list[dict]:
        """执行 SQL 查询。"""
        result = self._conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]
```

### 7.2 数据保留策略

```python
def cleanup(self, retention_days: int = 30) -> int:
    """清理过期数据。"""
    result = self._conn.execute("""
        DELETE FROM spans
        WHERE started_at < CURRENT_TIMESTAMP - INTERVAL ? DAY
    """, [retention_days])
    # 同步清理 runs、budget_ledger 等
    self._conn.execute("VACUUM")
    return result.fetchone()[0]
```

### 7.3 ClickHouse 升级路径（Phase 3+）

```python
# reins/core/storage.py
class StorageFactory:
    @staticmethod
    def create(config) -> Storage:
        backend = config.get("storage", "duckdb")
        if backend == "duckdb":
            return DuckDBStorage(config.get("duckdb_path"))
        elif backend == "clickhouse":
            return ClickHouseStorage(
                host=config["clickhouse_host"],
                database=config.get("clickhouse_db", "reins"),
            )
        raise ValueError(f"Unknown storage backend: {backend}")
```

---

## 8. CLI 工具设计

### 8.1 命令结构

```
reins
├── report                  # 成本报表（默认：今天）
│   ├── --period today|week|month|<date-range>
│   ├── --agent <name>      # 过滤特定 agent
│   └── --format table|json|csv
├── query <sql>             # SQL 查询
├── dashboard               # TUI 实时仪表板
├── proxy                   # 启动本地代理
│   ├── --port <port>       # 默认 8082
│   ├── --budget <amount>   # 如 "$5/day"
│   └── --on-exceed <strategy>
├── trace                   # Trace 相关（Trace 模块）
│   ├── list                # 列出最近 runs
│   ├── show <run_id>       # 查看调用树
│   └── export --otel       # 导出
├── replay <run_id>         # Agent 回放（Lens 模块）
├── health <run_id>         # Context health（Lens 模块）
├── diagnose <run_id>       # 根因分析（Lens 模块）
├── pulse                   # 可靠性（Pulse 模块）
│   ├── report              # 可靠性报表
│   └── test                # 运行回归测试
├── config                  # 配置管理
│   ├── show                # 显示当前配置
│   └── validate            # 校验 reins.yaml
└── version                 # 版本信息
```

### 8.2 CLI 实现

```python
# reins/cli/main.py
import click
from reins.core.storage import Storage

@click.group()
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)
    ctx.obj["storage"] = Storage()

@cli.command()
@click.option("--period", default="today")
@click.option("--agent", default=None)
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "csv"]))
@click.pass_context
def report(ctx, period, agent, fmt):
    """展示成本报表。"""
    storage = ctx.obj["storage"]
    # ... 查询并格式化输出

@cli.command()
@click.argument("sql")
@click.pass_context
def query(ctx, sql):
    """执行 SQL 查询。"""
    results = ctx.obj["storage"].query(sql)
    # ... 格式化输出

@cli.command()
@click.option("--port", default=8082)
@click.option("--budget", default=None)
@click.option("--on-exceed", default="alert")
def proxy(port, budget, on_exceed):
    """启动本地透明代理。"""
    import asyncio
    from reins.proxy.server import ReinsProxy
    # ... 初始化并启动

def main():
    cli()
```

---

## 9. Agent 适配层

### 9.1 框架适配器接口

```python
# reins/adapters/base.py
from abc import ABC, abstractmethod

class FrameworkAdapter(ABC):
    """框架适配器基类。"""

    @abstractmethod
    def attach(self, **kwargs) -> Any:
        """返回框架需要的 callback/hook 对象。"""
        ...
```

### 9.2 OpenAI Agents SDK 适配器

```python
# reins/adapters/openai_agents.py
from reins.adapters.base import FrameworkAdapter

class OpenAIAgentsAdapter(FrameworkAdapter):
    """OpenAI Agents SDK 适配器。利用其原生 tracing API。"""

    def attach(self, **kwargs):
        from agents import add_trace_processor
        processor = ReinsTraceProcessor(self._modules, self._storage)
        add_trace_processor(processor)
        return processor

class ReinsTraceProcessor:
    """实现 OpenAI Agents SDK 的 TraceProcessor 接口。"""

    def on_trace_start(self, trace):
        ...

    def on_span_start(self, span):
        ...

    def on_span_end(self, span):
        # 转换为 Reins SpanData，走统一流程
        reins_span = self._convert(span)
        for module in self._modules:
            module.on_span_end(reins_span)
        self._storage.insert_span(reins_span)
```

### 9.3 LangChain 适配器

```python
# reins/adapters/langchain.py
from langchain_core.callbacks import BaseCallbackHandler

class ReinsCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler。与 Langfuse 的集成方式完全相同。"""

    def on_llm_start(self, serialized, prompts, **kwargs):
        span = SpanData(span_type="llm", name=serialized.get("name", "llm"))
        # Budget pre-hook
        for module in self._modules:
            span = module.on_span_start(span)
        self._active_spans[kwargs["run_id"]] = span

    def on_llm_end(self, response, **kwargs):
        span = self._active_spans.pop(kwargs["run_id"])
        span.complete_from_langchain_response(response)
        for module in self._modules:
            module.on_span_end(span)
        self._storage.insert_span(span)

    def on_tool_start(self, serialized, input_str, **kwargs):
        ...

    def on_tool_end(self, output, **kwargs):
        ...
```

---

## 10. 项目结构

```
reins/
├── pyproject.toml
├── README.md
├── LICENSE                          # MIT
├── docs/
│   ├── PRD.md
│   └── DESIGN.md
├── src/
│   └── reins/
│       ├── __init__.py              # 公开 API: trace, wrap, configure
│       ├── py.typed
│       │
│       ├── core/                    # 核心层（始终安装）
│       │   ├── __init__.py
│       │   ├── module.py            # ReinsModule 基类
│       │   ├── loader.py            # 模块发现与加载
│       │   ├── events.py            # 事件总线
│       │   ├── models.py            # SpanData, RunData 数据模型
│       │   ├── storage.py           # DuckDB 存储
│       │   ├── pricing.py           # 模型定价表
│       │   ├── config.py            # YAML 配置加载
│       │   ├── instrumentor.py      # Monkey-patch 引擎
│       │   ├── decorators.py        # @trace(), wrap()
│       │   └── context.py           # 运行上下文管理 (run_id 传播)
│       │
│       ├── budget/                  # Budget 模块
│       │   ├── __init__.py          # BudgetModule (entry_point)
│       │   ├── engine.py            # 预算引擎 (预留/校正)
│       │   ├── circuit_breaker.py   # 熔断器
│       │   ├── anomaly.py           # 成本异常检测
│       │   └── degradation.py       # 降级策略 + 模型映射
│       │
│       ├── trace/                   # Trace 模块
│       │   ├── __init__.py          # TraceModule (entry_point)
│       │   ├── visualizer.py        # 调用树终端可视化
│       │   ├── exporter.py          # OTel OTLP 导出
│       │   └── traceql.py           # TraceQL 解析器 (Phase 2)
│       │
│       ├── lens/                    # Lens 模块
│       │   ├── __init__.py          # LensModule (entry_point)
│       │   ├── replay.py            # Agent 回放
│       │   ├── context_health.py    # Context Rot 检测
│       │   ├── root_cause.py        # 根因分析
│       │   └── breakpoint.py        # 断点调试 (实验性)
│       │
│       ├── pulse/                   # Pulse 模块
│       │   ├── __init__.py          # PulseModule (entry_point)
│       │   ├── evaluators.py        # 运行时评估器
│       │   ├── guardrails.py        # 守护栏引擎
│       │   ├── reliability.py       # 可靠性指标
│       │   └── regression.py        # 回归测试生成
│       │
│       ├── proxy/                   # Proxy 服务器
│       │   ├── __init__.py
│       │   ├── server.py            # aiohttp 反向代理
│       │   ├── anthropic.py         # Anthropic API 处理
│       │   ├── openai.py            # OpenAI API 处理 (Phase 1.5)
│       │   └── stream.py            # SSE streaming 处理
│       │
│       ├── adapters/                # 框架适配器
│       │   ├── __init__.py
│       │   ├── base.py              # FrameworkAdapter 基类
│       │   ├── openai_agents.py     # OpenAI Agents SDK
│       │   ├── langchain.py         # LangChain / LangGraph
│       │   ├── crewai.py            # CrewAI
│       │   └── autogen.py           # AutoGen
│       │
│       └── cli/                     # CLI 工具
│           ├── __init__.py
│           ├── main.py              # click 入口
│           ├── report.py            # reins report
│           ├── dashboard.py         # reins dashboard (TUI)
│           └── proxy_cmd.py         # reins proxy
│
├── tests/
│   ├── unit/
│   │   ├── test_budget_engine.py
│   │   ├── test_instrumentor.py
│   │   ├── test_storage.py
│   │   ├── test_pricing.py
│   │   └── test_circuit_breaker.py
│   ├── integration/
│   │   ├── test_anthropic_patch.py
│   │   ├── test_proxy_anthropic.py
│   │   └── test_end_to_end.py
│   └── conftest.py
│
└── examples/
    ├── basic_trace.py
    ├── budget_control.py
    ├── claude_code_proxy.py
    └── langchain_integration.py
```

---

## 11. 公开 Python API

```python
# reins/__init__.py — 用户看到的全部 API

# 核心装饰器
from reins.core.decorators import trace, wrap, configure

# 使用示例：
# @trace()
# @trace(budget="$0.50", on_exceed="degrade")
# client = wrap(anthropic.Anthropic())
# client = wrap(openai.OpenAI(), budget="$1.00")

# 手动 Span（高级用法）
from reins.core.models import SpanData
from reins.core.context import current_run

# 配置
from reins.core.config import ReinsConfig

# 版本
__version__ = "0.1.0"
```

---

## 12. 测试策略

### 12.1 测试金字塔

```
          ╱╲
         ╱  ╲         E2E Tests（3-5 个）
        ╱    ╲        完整流程：装饰器 → 预算检查 → 降级 → DuckDB
       ╱──────╲
      ╱        ╲      Integration Tests（10-15 个）
     ╱          ╲     SDK patch 正确性、Proxy 请求转发、DuckDB 读写
    ╱────────────╲
   ╱              ╲    Unit Tests（50+ 个）
  ╱                ╲   Budget 引擎、定价计算、熔断器、事件总线、配置解析
 ╱──────────────────╲
```

### 12.2 关键测试场景

| 场景 | 类型 | 描述 |
|------|------|------|
| 预算预留竞态 | Unit | 100 个并发 goroutine 同时预留，总预算不超支 |
| 模型降级正确性 | Unit | 预算不足时 sonnet → haiku，请求正常完成 |
| 成本计算精度 | Unit | 各模型的 token 计费与官方定价一致 |
| Anthropic SDK patch | Integration | patch 后所有调用被捕获，卸载后恢复原始行为 |
| Proxy 透明转发 | Integration | 非 messages 请求原样转发，不修改 |
| Proxy streaming | Integration | SSE 响应正确透传，usage 被正确累积 |
| SDK 故障隔离 | Integration | Reins 内部异常不导致用户 Agent 崩溃 |
| 完整流程 | E2E | `@trace(budget="$0.50")` → 调用 → 降级 → `reins report` 显示正确数据 |

### 12.3 CI 兼容性矩阵

```yaml
# .github/workflows/ci.yml
strategy:
  matrix:
    python: ["3.10", "3.11", "3.12", "3.13"]
    anthropic-sdk: ["0.30", "0.35", "latest"]
    openai-sdk: ["1.0", "1.50", "latest"]
```
