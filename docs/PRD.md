> ARCHIVED v0.1 design: descriptions below are historical intentions, not the v0.2 support contract. See README.md, GUIDE.md and PILOT.md for implemented behavior.

Current product direction: quality-constrained optimization for repeatable Agent
workloads, starting with data extraction. The implemented experiment, policy
selection and explicit fallback workflow is documented in [OPTIMIZATION.md](OPTIMIZATION.md).
Tracing and budget control below remain its execution foundation. Learned routing,
model compression and hardware configuration search are future integrations.

# Reins — 产品需求文档 (PRD)

> **Reins: Take Control of Your AI Agents**
> The runtime platform that makes AI agents debuggable, affordable, and reliable.

| 字段 | 值 |
|------|-----|
| 版本 | v0.1 |
| 日期 | 2026-04-03 |
| 状态 | Draft |

---

## 1. 产品愿景

**一句话：** Reins 是 AI Agent 的运行时操作系统——像 Grafana 之于微服务，Reins 之于 AI Agent。

**核心价值主张：** 现有工具让你看到 Agent 发生了什么。Reins 让你**控制** Agent 该怎么做。

```python
pip install reins

@reins.trace(budget="$0.50", on_exceed="degrade")
async def my_agent(task: str):
    ...
```

一行代码，同时获得：成本控制、调用追踪、调试诊断、可靠性监控。

---

## 2. 目标用户

### 2.1 Persona A：独立开发者 / AI Hacker

| 属性 | 描述 |
|------|------|
| 画像 | 用 OpenAI/Anthropic API 构建 Agent 项目的个人开发者 |
| 痛点 | Agent 调试全靠 print；一个 bug 导致 Agent 循环，一觉醒来账单 $50+ |
| 期望 | `pip install` 一下就能用，不要让我部署任何服务 |
| 付费意愿 | 低（需要开源免费方案） |
| 关键场景 | 开发阶段调试、个人项目成本控制 |

### 2.2 Persona B：AI 工程团队（3-20 人）

| 属性 | 描述 |
|------|------|
| 画像 | 初创公司或中型企业的 AI 团队，维护多个 Agent 系统 |
| 痛点 | 不知道哪个 Agent 花了多少钱；Agent 上线后质量退化但没有告警；Claude Code 等工具的成本完全不可控 |
| 期望 | 团队级别的成本归因、预算分配、质量监控 |
| 付费意愿 | 中（愿意为团队管理功能付费） |
| 关键场景 | 生产环境监控、团队预算管理、CI/CD 集成 |

### 2.3 Persona C：企业 AI 平台团队

| 属性 | 描述 |
|------|------|
| 画像 | 大型企业的平台团队，支撑数十个内部 AI 应用 |
| 痛点 | 跨供应商的统一成本视图；合规审计；多团队的预算治理 |
| 期望 | 组织级预算层级、SSO/RBAC、审计日志、ClickHouse 级别的可扩展性 |
| 付费意愿 | 高 |
| 关键场景 | 组织级成本治理、合规审计、高可用部署 |

**MVP 聚焦：Persona A + B。** Persona C 是 Phase 3+ 目标。

---

## 3. 用户旅程

### 3.1 五分钟上手（Persona A）

```
1. pip install reins[budget]
2. 在 Agent 函数上加 @trace(budget="$0.50")
3. 运行 Agent
4. reins report  ← 看到成本报表
5. "哦，原来这个工具调用花了 80% 的钱"
```

### 3.2 团队部署（Persona B）

```
1. pip install reins[all]
2. 创建 reins.yaml，配置团队预算
3. 各 Agent 加 @trace() 装饰器
4. reins proxy 启动，管控 Claude Code 等 CLI 工具
5. reins dashboard  ← 团队成本仪表板
6. 设置告警：成本异常 → Slack 通知
7. 接入 CI/CD：Agent 变更前跑回归测试
```

### 3.3 Claude Code 用户旅程

```
1. pip install reins[budget]
2. reins proxy --port 8082 --budget "$5/day"
3. export ANTHROPIC_BASE_URL=http://localhost:8082
4. claude "帮我重构代码"  ← 正常使用，Reins 透明拦截
5. reins report  ← 看到 Claude Code 的成本明细
6. 预算快用完时，自动降级 sonnet → haiku（用户无感）
```

---

## 4. 功能需求

### 4.1 Core（核心层）— 始终安装

| ID | 功能 | 用户故事 | 验收标准 | 优先级 |
|----|------|---------|---------|--------|
| C-1 | 自动 Instrumentation | 作为开发者，我希望加一个装饰器就能自动捕获所有 LLM 调用 | `@trace()` 装饰器自动记录 Anthropic SDK 的每次调用（model, tokens_in, tokens_out, latency, cost） | P0 |
| C-2 | Client Wrapper | 作为开发者，我希望用 `wrap()` 包装现有 client 实现同样效果 | `wrap(anthropic.Anthropic())` 返回的 client 自动记录所有调用 | P0 |
| C-3 | DuckDB 本地存储 | 作为开发者，我希望 trace 数据存在本地，不需要外部服务 | 数据自动写入 `~/.reins/traces.duckdb`，零配置 | P0 |
| C-4 | SQL 查询 | 作为开发者，我想用 SQL 查询历史 trace | `reins query "SELECT * FROM spans WHERE cost > 0.1"` 返回结果 | P0 |
| C-5 | OpenAI SDK 支持 | 作为使用 OpenAI 的开发者，我也希望被支持 | `@trace()` 同样捕获 OpenAI SDK 调用 | P1 |
| C-6 | Google GenAI 支持 | 作为使用 Gemini 的开发者，我也希望被支持 | `@trace()` 同样捕获 Google GenAI SDK 调用 | P2 |

### 4.2 Budget 模块 — `pip install reins[budget]`

| ID | 功能 | 用户故事 | 验收标准 | 优先级 |
|----|------|---------|---------|--------|
| B-1 | Per-Run 预算 | 作为开发者，我想给单次 Agent 运行设置成本上限 | `@trace(budget="$0.50")` 在总成本接近 $0.50 时触发 on_exceed 策略 | P0 |
| B-2 | 自动降级 | 作为开发者，我希望超预算时自动切换到便宜模型而不是直接失败 | `on_exceed="degrade"` 自动将 claude-sonnet → claude-haiku，请求正常完成 | P0 |
| B-3 | 预算策略 | 作为开发者，我想选择超预算时的行为 | 支持 4 种策略：`degrade`（降级）、`pause`（暂停等审批）、`alert`（告警不阻止）、`reject`（拒绝） | P0 |
| B-4 | YAML 预算配置 | 作为团队负责人，我想用配置文件管理多个 Agent 的预算 | `reins.yaml` 支持 per-agent、daily、monthly 预算配置 | P0 |
| B-5 | 成本报表 | 作为开发者，我想看到成本花在哪了 | `reins report` 展示 per-agent、per-model 成本分解，含降级次数统计 | P0 |
| B-6 | TUI 仪表板 | 作为开发者，我想在终端实时看到成本 | `reins dashboard` 打开终端 TUI，实时展示预算消耗和 Agent 活动 | P1 |
| B-7 | 成本异常检测 | 作为开发者，我希望单次运行成本异常高时被提醒 | 当某次 run 成本超过该 Agent 历史均值的 3 倍时，输出告警 | P1 |
| B-8 | 熔断器 | 作为开发者，我想防止 Agent 陷入无限循环导致成本爆炸 | 检测到 Agent 在短时间内重复调用相同 API（>N 次/分钟）时自动熔断 | P1 |
| B-9 | 团队预算层级 | 作为团队负责人，我想分配团队总预算到各个 Agent | YAML 支持 organization → team → agent 的预算层级，各级独立计量 | P2 |
| B-10 | 模型路由建议 | 作为开发者，我想知道哪些调用可以用更便宜的模型 | `reins suggest` 分析历史数据，建议可降级的调用模式 | P2 |

### 4.3 Trace 模块 — `pip install reins[trace]`

| ID | 功能 | 用户故事 | 验收标准 | 优先级 |
|----|------|---------|---------|--------|
| T-1 | Trace 列表 | 作为开发者，我想查看最近的 Agent 运行记录 | `reins trace list` 展示最近 N 条 run（时间、agent、cost、status） | P0 |
| T-2 | 调用树可视化 | 作为开发者，我想看到一次运行的完整调用链 | `reins trace show <run_id>` 在终端展示树形结构（LLM 调用 + 工具调用） | P0 |
| T-3 | OTel 导出 | 作为团队，我们已有 Grafana/Datadog，希望 trace 数据能导入 | `reins.yaml` 配置 `export: otel`，数据通过 OTLP 导出到外部后端 | P1 |
| T-4 | 跨 Agent 关联 | 作为开发者，我的 Agent A 会调用 Agent B，我想看到完整链路 | 自动传播 trace_id，跨 Agent 调用链在同一个 trace 下展示 | P1 |
| T-5 | Context Timeline | 作为开发者，我想看到上下文窗口在整个运行过程中的变化 | `reins trace context <run_id>` 展示每步的上下文 token 数和内容摘要 | P2 |
| T-6 | TraceQL 查询 | 作为高级用户，我想用专门的查询语言筛选 trace | `reins query --traceql "{agent.cost > 1.0 && span.status = 'error'}"` 返回匹配结果 | P2 |

### 4.4 Lens 模块 — `pip install reins[lens]`

| ID | 功能 | 用户故事 | 验收标准 | 优先级 |
|----|------|---------|---------|--------|
| L-1 | Agent Replay | 作为开发者，我想逐步回放 Agent 的执行过程 | `reins replay <run_id>` 在终端逐步展示每步的输入/输出/决策 | P1 |
| L-2 | Context Health Score | 作为开发者，我想知道 Agent 的上下文是否在退化 | 每个 Span 自动计算 `context_health_score`（0-1），基于 token 利用率、重复率、输出变化率 | P1 |
| L-3 | Context Rot 告警 | 作为开发者，我想在 Agent 开始"犯糊涂"时被提醒 | 当 context_health_score 下降到阈值以下时输出告警 | P1 |
| L-4 | Failure Root Cause | 作为开发者，Agent 失败时我想知道根本原因 | `reins diagnose <run_id>` 自动分析因果链，输出 "Step 12 的工具调用失败是因为 Step 10 的 LLM 输出格式错误" | P2 |
| L-5 | 断点调试 | 作为开发者，我想在 Agent 执行中暂停检查状态 | `@trace(breakpoints=["before_tool_call"])` 在工具调用前暂停，可检查上下文后恢复 | P2 |

### 4.5 Pulse 模块 — `pip install reins[pulse]`

| ID | 功能 | 用户故事 | 验收标准 | 优先级 |
|----|------|---------|---------|--------|
| P-1 | 运行时评估 | 作为开发者，我想在 Agent 运行时实时评估输出质量 | `@trace(evaluators=["coherence"])` 每步评分，分数记录到 DuckDB | P1 |
| P-2 | 守护栏 | 作为开发者，我想自动拦截危险操作 | 内置 PII 检测、SQL 注入检测；`@trace(guardrails=["no_pii"])` 自动拦截含 PII 的输出 | P1 |
| P-3 | 可靠性报表 | 作为团队负责人，我想看到各 Agent 的可靠性趋势 | `reins pulse report` 展示一致性、恢复力、鲁棒性指标的 7 天趋势 | P2 |
| P-4 | 自动回归测试 | 作为开发者，我希望失败的 run 自动变成测试用例 | `reins pulse test` 从历史失败 run 生成测试用例并执行 | P2 |
| P-5 | 自定义守护栏 | 作为开发者，我想用 Python 函数定义自己的守护栏规则 | 支持 `@guardrail` 装饰器注册自定义检查函数 | P2 |

### 4.6 Proxy 模式

| ID | 功能 | 用户故事 | 验收标准 | 优先级 |
|----|------|---------|---------|--------|
| X-1 | 本地透明代理 | 作为 Claude Code 用户，我想控制它的 API 成本 | `reins proxy --port 8082` 启动代理，设置 `ANTHROPIC_BASE_URL` 后 Claude Code 流量经过 Reins | P0 |
| X-2 | Proxy 预算执行 | 作为用户，我想在 Proxy 层面执行预算 | Proxy 支持 `--budget "$5/day"` 和 `--on-exceed degrade` 参数 | P0 |
| X-3 | 多 Provider 支持 | 作为用户，我同时用 Anthropic 和 OpenAI | Proxy 同时支持 Anthropic Messages API 和 OpenAI Chat Completions API 路由 | P1 |
| X-4 | Claude Code Hooks | 作为 Claude Code 用户，我想在工具调用层面集成 Reins | 提供 Claude Code PreToolUse hook 脚本，在工具调用前检查预算 | P2 |
| X-5 | Claude Code Plugin | 作为 Claude Code 用户，我想一键安装所有 Reins 集成 | `reins install-claude-plugin` 自动配置 proxy + hooks + MCP | P2 |

---

## 5. 非功能需求

### 5.1 性能

| 指标 | 要求 |
|------|------|
| Library 模式延迟开销 | < 1ms per LLM call |
| Proxy 模式延迟开销 | < 5ms per request（本地） |
| DuckDB 查询性能 | 100 万条 Span 的聚合查询 < 1s |
| 内存占用 | SDK 常驻内存 < 50MB |
| 磁盘占用 | DuckDB 每 10 万条 Span 约 50-100MB |

### 5.2 兼容性

| 维度 | 要求 |
|------|------|
| Python 版本 | >= 3.10 |
| LLM SDK | Anthropic SDK >= 0.30, OpenAI SDK >= 1.0 |
| OS | macOS, Linux, Windows (WSL) |
| 并发模型 | 支持 asyncio 和 sync 调用 |
| 与其他工具共存 | 不与 Langfuse / OpenLLMetry monkey-patch 冲突 |

### 5.3 安全

| 维度 | 要求 |
|------|------|
| API Key 处理 | SDK 不记录、不存储用户的 API Key |
| Prompt/Response 存储 | 默认不存储 prompt/response 内容；可通过配置开启（`store_content: true`） |
| Proxy 模式 | 不修改请求内容（降级除外）；TLS 转发到上游 API |
| 本地存储 | DuckDB 文件权限 600（仅当前用户可读） |

### 5.4 可靠性

| 维度 | 要求 |
|------|------|
| SDK 故障隔离 | Reins SDK 内部错误不能导致用户 Agent 崩溃（fail-open） |
| Proxy 故障 | Proxy 崩溃时返回 502，不丢失用户请求（不缓存请求体） |
| 数据一致性 | 预算计量允许 ±5% 误差（异步刷盘）；Proxy 模式实时精确 |

---

## 6. MVP 范围定义

### 6.1 Phase 1 — In Scope（Week 1-6）

```
✅ Core: @trace() 装饰器 + Anthropic SDK auto-instrumentation
✅ Core: DuckDB 本地存储 + reins query
✅ Budget: per-run 预算 + 4 种策略（degrade/pause/alert/reject）
✅ Budget: YAML 配置
✅ Budget: reins report 成本报表
✅ Budget: 成本异常检测
✅ Proxy: reins proxy 命令（Anthropic API 透明代理）
✅ Proxy: 预算执行 + 自动降级
✅ CLI: reins report / reins query / reins dashboard (TUI)
```

### 6.2 Phase 1 — Out of Scope

```
❌ Web UI（用 CLI TUI 替代）
❌ OpenAI / Google SDK 支持（Phase 1.5）
❌ Trace 模块高级功能（TraceQL、跨 Agent 关联）
❌ Lens 模块（Context Health、Replay、断点调试）
❌ Pulse 模块（评估、守护栏、回归测试）
❌ 框架适配器（LangChain、CrewAI、AutoGen）
❌ ClickHouse 生产存储
❌ 多租户 / RBAC / SSO
❌ Claude Code Plugin 打包
```

### 6.3 渐进发布路线

| 阶段 | 时间 | 交付 | 用户可做 |
|------|------|------|---------|
| Phase 1 | Week 1-6 | Core + Budget + Proxy + CLI | 成本控制 + Claude Code 管控 |
| Phase 1.5 | Week 7-8 | + OpenAI SDK + Trace 基础 | 多 Provider + 调用树查看 |
| Phase 2 | Week 9-12 | + Lens（Replay, Context Health） | Agent 调试 |
| Phase 3 | Week 13-16 | + Pulse（评估, 守护栏） | 质量监控 |
| Phase 4 | Week 17-20 | 框架适配器 + Claude Code Plugin | 生态扩展 |

---

## 7. 成功指标

### 7.1 Phase 1 上线后 30 天

| 指标 | 目标 |
|------|------|
| PyPI 周下载量 | > 500 |
| GitHub Stars | > 200 |
| 活跃用户（周至少 1 次 `reins` 命令） | > 50 |
| 社区反馈（Issues + Discussions） | > 20 条 |

### 7.2 Phase 2 上线后 90 天

| 指标 | 目标 |
|------|------|
| PyPI 周下载量 | > 5,000 |
| GitHub Stars | > 1,000 |
| 至少 1 个框架的官方集成推荐 | LangChain 或 CrewAI |
| Paper 1 投稿 | AgentBudget 论文提交 |

---

## 8. 竞品对比

| 维度 | Reins | LiteLLM | Langfuse | Helicone | Portkey |
|------|-------|---------|----------|----------|---------|
| **集成方式** | SDK + Proxy | Proxy | SDK | Proxy | Proxy/SDK |
| **基础设施依赖** | 零（嵌入式 DuckDB） | Redis + PostgreSQL | PostgreSQL | 云服务 | 云服务 |
| **成本追踪** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **预算执行** | ✅ 智能降级 | ✅ 硬拒绝 | ❌ | 速率限制 | Virtual Key 限额 |
| **自动模型降级** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **熔断器** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Per-Agent 预算** | ✅ | Per-Key | ❌ | ❌ | 部分 |
| **Agent Replay** | ✅ (Phase 2) | ❌ | ❌ | ❌ | ❌ |
| **Context Health** | ✅ (Phase 2) | ❌ | ❌ | ❌ | ❌ |
| **运行时评估** | ✅ (Phase 3) | ❌ | ✅ (事后) | ❌ | ❌ |
| **守护栏** | ✅ (Phase 3) | ✅ | ❌ | ✅ | ✅ |
| **开源** | ✅ MIT | ✅ (企业付费) | ✅ | ✅ | ❌ |
| **部署命令** | `pip install reins` | `litellm --config` | `docker-compose up` | 云注册 | 云注册 |

**一句话差异：** LiteLLM 是 API Gateway（需要 Redis + Postgres，超限硬拒绝）。Reins 是 Agent Runtime（零依赖，超限智能降级）。
