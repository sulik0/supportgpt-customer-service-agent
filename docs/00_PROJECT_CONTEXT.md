# SupportGPT 智能客服 Agent 项目上下文

> 本文档是本项目唯一的项目概览，也是 Codex、GPT、Claude Code、Cursor 等 AI 参与开发前的必读入口。
> 当本文档与代码不一致时，以当前代码和测试为准，并在同一次改动中更新本文档。

## 项目背景

SupportGPT 智能客服 Agent 面向售后服务场景。项目基于开源客服问答系统改造，将原有 FAQ / RAG 问答扩展为可以理解工单、补充业务信息、拦截高风险请求、检查回复并转人工审批的客服平台。工单也会记录从提交到处理完成的状态变化。

核心业务场景包括退款、保修、物流异常、订单取消、账户问题和技术支持。目标用户是客服坐席、客服组长和运营管理人员。

本项目可在本地运行，用于演示客服 Agent 的工程实现；它尚未接入真实客户数据，也不是已经上线的生产客服系统。CRM、OMS、历史工单和退款初筛均使用本地 Mock Adapter；它们用于验证 Tool Calling 协议、权限和审计设计，不代表已接入真实企业系统。

退款默认仍使用 Mock Adapter；另提供可独立启动的 PostgreSQL OMS 参考服务。它用数据库保存幂等回执，拒绝同一幂等键的参数冲突，并按幂等键查询已提交结果。`OMS_PROVIDER=reference_http` 可让现有 Outbox Worker 经 HTTP 使用该服务。参考服务只保存虚构订单的退款申请，不执行资金退款，也不是已接入的企业 OMS。

用户页提供 6 个虚构演示客户，其中中文客户为张晓雨 `cust_201`（物流延迟）、李明 `cust_202`（设备保修）和星河科技联系人陈晨 `cust_203`（API 与发票）。每个客户有独立的订单、账务、权益和历史工单；新增订单金额使用 CNY。这些记录随代码加载，不是客服后台登录账号，也不代表真实用户资料。

## 项目目标

### 业务目标

- 将客户的自然语言问题转换为可路由、可审批、可追踪的工单处理流程。
- 在生成回复前同时引入知识库依据和结构化业务上下文。
- 通过独立 Risk Engine 统一评估安全、业务、分类置信度和回复质量，对高风险或临界风险引入 Human-in-the-Loop。
- 通过 citation、QA 结果、工具审计和 Trace 为客服坐席提供可核查依据。

### 工程目标

- 使用 LangGraph 将 Agent 流程拆成若干节点。每个节点处理一类工作，并能单独查看执行情况。
- 使用 Skill Registry 将统一 Intent 映射到有版本的能力配置，并用各 Skill 的 Tool Allowlist 限制可用工具。
- 使用 LangGraph Checkpoint 持久化 Graph State，让高风险回复可在人工审批前暂停，并在进程重启后从原 Thread 恢复。
- 通过 ToolRegistry 统一工具协议，实现 Schema 校验、RBAC、超时和调用审计。
- 使用 Hybrid RAG 兼顾语义检索与政策编号、产品名、时间窗口等精确词匹配。
- 保持默认 Mock LLM、可选 Redis 和 SQLite 本地配置，使项目在无企业凭据、无外部 LLM 和无 Redis 时仍可复现。
- 通过 Prometheus、OpenTelemetry、分层依赖、Python 3.11 CI 和 Agent Evaluation Quality Gate 提供可观测性、可维护性与发布质量保障。

## 核心能力

1. **工单分析**：识别 `sentiment`、`priority`、`department` 和 `intent`。
2. **Skill 选择**：将 8 个统一 Intent 确定性映射到 6 个版本化 Skill，固定 Tool Allowlist、RAG 类别和必需槽位快照。
2. **多层安全短路**：先执行 Unicode 规范化、中英文特征、组合启发式、角色提权与 Base64 载荷扫描，规则未命中时再使用 Qwen3Guard-Gen-0.6B 扫描客户输入、Tool 返回和 RAG 文档。
3. **PII 脱敏**：正常请求进入 LLM 前对主题和描述中的敏感信息进行匿名化。
4. **业务工具上下文**：通过 9 个注册 Tool 查询客户画像、近期订单、历史工单、物流、保修权益、支付发票和服务状态，并把结构化结果注入 Resolver。查询数据都是本地演示数据，未知客户返回无记录，不读取其他客户的数据。
5. **工具治理**：除 ToolRegistry 的 Schema、RBAC 和审计外，高风险写操作具备业务幂等键、Transactional Outbox、异步 Worker、`unknown` 自动对账、Retry/DLQ、补偿和版本化 Policy 回放。
6. **Hybrid RAG**：融合 ChromaDB 向量召回、进程内 BM25 风格词法打分和轻量 rerank，返回带版本的 citation。
7. **回复生成与 QA**：使用知识库 citation 和 Tool Context 生成草稿，再评估 QA 分数、幻觉风险和输出泄露。
8. **Risk Engine 与 Human-in-the-Loop**：统一输出 `risk_level`、`risk_score`、`risk_reasons`、是否人工处理及是否阻断自动化，并为高风险草稿创建审批记录。
9. **工单状态机**：统一约束 `open`、`in_progress`、`pending_approval`、`resolved` 和 `closed` 的合法流转。
10. **Memory V1**：以结构化 Conversation/Message/Snapshot 作为 SQL 事实源，Redis 缓存最近 12 条 final 消息；有界历史、确定性摘要和业务实体已注入 Analyzer、Retriever、Resolver 与 QA。
11. **可观测性**：使用 OpenTelemetry 统一采集 Trace 与 Metrics；Collector 将 Trace 转发 LangSmith，并通过 Prometheus exporter 提供指标，由 Grafana 展示。
12. **离线评测适配**：提供 RAGAS、DeepEval 和本地确定性指标的统一评测入口。
13. **Durable Execution**：使用 SQLite / PostgreSQL Checkpointer、稳定 `thread_id`、`AgentExecution` 业务元数据和数据库恢复租约，支持 `interrupt` / `Command(resume=...)`、重启恢复和幂等重试。
14. **DecisionProvider**：以可替换接口封装 Jev System One；启用后复核 Analyzer 规则候选，并承担 QA 正向 Grounding 与非确定评判，规则/LLM 作为分层回退。

## 技术栈

| 层级 | 技术 | 当前用途 |
|---|---|---|
| API | Python、FastAPI、Pydantic | 异步 API、请求/响应 Schema与健康检查 |
| Agent 编排 | LangGraph、LangGraph Checkpoint | 编排 Analyzer、Skill Selector、Tooling、Retriever、Resolver、QA、Escalation 和 Approval Gate，持久化暂停/恢复状态 |
| Skill Framework | SkillDefinition、SkillRegistry | 管理各版本 Skill，根据 Intent 选择能力，并记录运行时用到的配置 |
| LLM | Mock LLM、OpenAI、Azure OpenAI | 默认 Mock 保证离线可复现；通过 `BaseLLMProvider` 适配外部模型 |
| 决策模型 | DecisionProvider、Jev System One | 可选封闭选项语义分类与 QA 评判，不授予 Tool 执行权 |
| 数据库 | SQLAlchemy Async、SQLite、PostgreSQL | 本地默认 SQLite；Docker Compose 使用 PostgreSQL |
| Memory | SQLAlchemy Async、Redis | 追加为主的会话消息、摘要/实体 Snapshot、revision 缓存校验与多轮 Context Assembly |
| RAG | ChromaDB、Embedding、Hybrid RAG | 知识库分块、版本/类别过滤、向量与词法混合召回、rerank |
| 安全 | JWT、RBAC、Prompt Guardrails、Qwen3Guard-Gen-0.6B、Risk Engine | API 鉴权、工具权限、PII 脱敏、规则 + 语义的直接/间接 Prompt Injection 检测、Jailbreak、输出过滤和统一风险分级 |
| 评测 | RAGAS、DeepEval、确定性 Security Evaluator、本地启发式指标 | RAG 质量、Agent 行为、安全检测混淆矩阵与阻断处置质量 |
| 可观测 | LangSmith、OpenTelemetry、Prometheus、Grafana | OTel 统一采集 Trace 与 Metrics；Collector 转发 Trace 并导出 Prometheus 指标 |
| 交付 | Docker、Docker Compose、Kubernetes manifests | 本地组件编排和部署模板 |
| 质量保障 | pytest、GitHub Actions | Python 3.11 编译检查与定向后端测试 |

## 系统整体架构

```text
客服端 / API Client
        |
        v
FastAPI
  |-- JWT / RBAC
  |-- Chat / Ticket / Approval / Evaluation API
  |-- OpenTelemetry Trace + Metrics
  |
  +--> LangGraph Agent Workflow
  |      |-- Analyzer + Guardrails --> 规则 / DecisionProvider / LLM Fallback
  |      |-- Skill Selector --> SkillRegistry
  |      |-- Tooling --> ToolRegistry --> Mock CRM / OMS / Ticketing
  |      |-- Retriever --> ChromaDB Hybrid RAG
  |      |-- Resolver --> BaseLLMProvider --> Mock / OpenAI / Azure OpenAI
  |      |-- QA --> 规则 / DecisionProvider / LLM Fallback + Response Filter
  |      `-- Risk Engine --> Escalation --> Approval Gate
  |                                      |-- 普通请求 --> END
  |                                      `-- 高风险 --> Checkpoint / interrupt --> Human-in-the-Loop --> resume
  |
  +--> SQLAlchemy Async --> SQLite / PostgreSQL
  |      |-- User
  |      |-- Ticket
  |      |-- ConversationSession / ConversationMessage / MemorySnapshot
  |      |-- SessionMemory（旧数据兼容迁移）
  |      |-- KnowledgeDoc
  |      |-- ResponseApproval
  |      `-- AgentExecution + LangGraph Checkpoint
  |
  `--> Redis（可选短期记忆）

Observability
  |-- OpenTelemetry Collector
  |     |-- Trace --> LangSmith
  |     `-- Metrics --> Prometheus exporter
  `-- Prometheus --> Grafana
```

本地默认使用 SQLite、Mock LLM 和本地持久化 ChromaDB，Redis 不是强依赖。Docker Compose 编排 backend、PostgreSQL、Redis、OpenTelemetry Collector、Prometheus 和 Grafana。应用通过 OTLP/HTTP 将 Trace 与 Metrics 发送到 Collector；后端不直接暴露 Prometheus `/metrics`。

## Agent 工作流

前端提供公开的 `/#workflow` 架构演示页，以及后台导航入口。页面按照当前 `graph.py` 展示 7 个主 Graph 节点，并明确 Tooling 与 Retriever 是 `context_enrichment` 内部并行分支。可查看各节点的输入、输出和设计说明，并逐步演示普通查询、退款待审批、输入安全拦截与上下文安全拦截。演示只在前端运行，不执行 Agent，不读取后台数据。

### AgentState

LangGraph 使用 `AgentState` 作为节点间共享状态。关键字段分为：

- 请求标识：`ticket_id`、`customer_id`、`subject`、`description`、`kb_version`、`checkpoint_thread_id`。
- 分析结果：`sentiment`、`priority`、`intent`、`department`、`analyzer_confidence`。`intent` 必须来自统一 `IntentType`：`billing_dispute`、`outage_report`、`order_cancellation`、`order_status`、`account_support`、`warranty_claim`、`feedback`、`information_request`；最后一项是唯一兜底值。
- Skill 快照：`skill_name`、`skill_version`、`selection_strategy`、`skill_registry_id`、必需/缺失槽位、Tool Allowlist/Forbidden List 和 RAG 类别。
- 权限与工具：`operator_role`、`tool_context`、`tool_calls`。
- RAG 与回复：`context_citations`、`resolution_evidence`、`suggested_response`。`resolution_evidence` 保存本次生成回复实际使用的精简证据，QA 原样复用，不重新挑选上下文。
- 能力范围：`request_scope`、`scope_reason`、`scope_strategy` 和 `response_kind`。无害但超出客服能力的问题可正常结束，不因缺少这类问题的知识或 Tool 而自动转人工。
- 安全与风险：`security_threat_detected`、`security_risk_score`、`security_findings`、`semantic_guard_label`、`semantic_guard_categories`、`semantic_guard_checks`、`semantic_guard_degraded`、`risk_level`、`risk_score`、`risk_reasons`、`risk_requires_human`、`risk_block_automation`。
- 质量结果：`qa_score`、`hallucination_detected`、`citation_verified`、`errors`。
- 性能策略：`analyzer_strategy`、`qa_strategy`，用于区分规则短路、Jev 决策与 LLM 评估；`decision_records` 保存问题集版本、模型、类型化结果、置信度和回退原因。
- 人工处理判断：`escalation_recommended`、`escalation_reason`、`approval_required`。
- 持久执行：`checkpoint_namespace`、`durable_execution_enabled`、`execution_status`、`approval_status`、`human_decision`。
- 成本与延迟：`tokens_input`、`tokens_output`、`cost_usd`、`latency_seconds`。

当前项目没有另外定义 `TaskState`，仍由单一 `AgentState` 承载固定 Workflow 的上下文；但 Graph State 已通过 LangGraph Checkpointer 持久化。本地默认使用独立 SQLite Saver，PostgreSQL 环境默认使用 AsyncPostgresSaver。

### 路由

```text
analyzer
  |-- 命中 Prompt Injection / Jailbreak
  |      `--> escalation --> approval_gate
  |
  |-- 无害但超出客服能力
  |      `--> resolver（说明能力范围）--> qa --> escalation --> approval_gate
  |
  `-- 客服业务请求
         `--> skill_selector --> context_enrichment
                |-- tooling（并行）
                `-- retriever（并行）
                      |-- 任一上下文命中注入 --> escalation --> approval_gate
                      `-- 合并安全结果与上下文 --> resolver --> qa --> escalation --> approval_gate

approval_gate
  |-- 无需审批 --> END
  `-- 需审批 --> interrupt + Checkpoint --> 人工决策 --> Command(resume) --> END
```

### 每个节点做什么

1. **Analyzer**
   - 先执行多层 Prompt Injection 和 Jailbreak 检测。
   - 命中安全风险时写入 `errors`，设置紧急优先级和拒绝回复，不执行后续 Tooling、RAG、Resolver 和 QA。
   - 正常请求先对 PII 脱敏并生成规则候选；Jev 启用时优先执行封闭 Intent 决策，低置信度或故障时，有候选则回退规则，无候选才回退 Analyzer LLM。
   - 安全检查通过后，明确的天气、创作和常识等无关请求先按规则标记为 `out_of_scope`；其余请求可在同一次 Jev 分类中判断 `support_scope`，只有普通信息请求且判断置信度达到原有门槛时才采用。业务操作、混合意图和不确定请求保留业务路径。
   - `out_of_scope` 请求只隔离本轮无关 Memory，不删除会话历史；直接进入 Resolver，不选择 Skill 或查询 Tool / RAG。
2. **Skill Selector**
   - 基于归一化 `IntentType` 使用确定性规则选择 Skill，V1 不调用 LLM。
   - 固定本次请求的 Skill 版本、Registry Hash、Tool 边界和缺失槽位，并写入 State、Trace 与 Metrics。
3. **Tooling**
   - 始终查询客户画像和历史工单。
   - 只在 billing、shipping 或相关意图下查询订单历史。
   - 所有调用必须经过 ToolRegistry，Agent 不能直接调用 Mock Adapter。
   - Skill `v1.1` 新增物流、保修权益、支付发票和服务状态的只读查询，按 Intent 选择并与原有工具并行执行；不增加自动退款或取消订单的权限。
   - 工具返回在写入 Tool Context 前扫描间接 Prompt Injection；命中后保留调用审计，但清空工具上下文并短路。
4. **Retriever**
   - 用工单主题和描述构造 Query，默认返回 Top 3 citation。
   - 强制带 `kb_version`，并优先按 `department` 过滤类别。
   - 类别过滤无结果时，保留版本过滤并放宽类别再检索一次。
   - citation 在交给 Resolver 前扫描间接 Prompt Injection；命中后清空 citation 并短路。
   - 与 Tooling 并行执行，由 Context Enrichment 统一合并结果；风险信号只升不降。
5. **Resolver**
   - 对已确认的无害范围外请求直接生成能力说明，设置 `response_kind=capability_boundary`，不调用业务生成模型、不声称已转人工。
   - 只选取最高相关的 Top-2 citation 与必要 Tool 字段，优先保留当前业务查询的状态、异常和下一步；Tool JSON 按完整字段裁剪，不截成半份数据。
   - 将 Tool、KB 和有界会话内容保存为 `resolution_evidence`，并把这份证据交给生成模型。KB 保留 `[S1]` 等编号、来源和版本。
   - 回复先说明当前状态、异常和下一步，只有问题需要时才补充政策。默认输出上限为 480 tokens；遇到 `finish_reason=length` 时，使用同一证据重新生成一次更短的完整回复。再次截断则转入已有人工降级流程，不返回半句话。
6. **QA**
   - 验证纯能力说明时不要求天气等外部知识依据；仅接受可验证的能力说明，附加“今天晴、25 度”等事实仍进入正常 QA，不能靠范围外标签免检。
   - 规则校验和 Jev / LLM Judge 都读取 Resolver 保存的 `resolution_evidence`，不再次裁剪或从原始 State 拼装另一份证据。旧 Checkpoint 没有该字段时才使用共同构造器补齐。
   - 空回复、输出泄露或完全缺少依据等确定性失败优先使用规则判断，不调用 LLM。
   - 安全硬失败、澄清和安全限制回复仍由规则短路；正向 Grounding 候选和其余非确定请求在 Jev 启用时合并评判 Grounding、完成度、citation 与未授权承诺。Jev 不可用时，前者回退规则，后者回退轻量 LLM Judge。
   - 通过 Response Filter 删除内部指令或工作流泄露；命中时将 QA 分数降为 `0.5` 并标记幻觉。
7. **Escalation**
   - 按优先级计算 SLA：urgent `2h`、high `12h`、medium `24h`、low `48h`。
   - 调用独立 Risk Engine 综合安全威胁、优先级、情绪、业务意图、Analyzer 置信度、QA、幻觉和 Workflow 错误。
   - 默认风险等级阈值为 `medium >= 0.4`、`high >= 0.7`、`critical >= 0.9`；`high` / `critical` 要求人工处理。
   - 建议升级、`qa_score < 0.8` 或 `risk_requires_human = true` 任一命中，就设置 `approval_required = true`。
8. **Approval Gate**
   - 无需审批时直接结束；需审批时调用 LangGraph `interrupt()` 暂停并保存 Checkpoint。
   - 人工通过、修改或拒绝后，API 使用原 `thread_id` 和 `Command(resume=...)` 续跑，不重跑 Analyzer、Tool、RAG、Resolver 和 QA。

### 工单如何从提交处理到结束

```text
open --start_work--> in_progress
open / in_progress --request_approval--> pending_approval
pending_approval --approve_response / modify_response--> resolved
pending_approval --reject_response--> in_progress
resolved --close--> closed
resolved / closed --reopen--> in_progress
```

状态流转必须通过 `TicketStateMachine.transition()`。非法流转返回 `409 Conflict`，不允许在业务代码中直接修改 `Ticket.status`。

## 关键模块

| 模块 | 路径 | 模块会做什么 |
|---|---|---|
| API 入口 | `src/main.py` | FastAPI 应用、鉴权、聊天、工单、审批、评测、Metrics 与 HTTP Trace |
| Agent Graph | `src/agents/graph.py` | `AgentState`、节点编排、安全条件路由、token/成本/延迟汇总 |
| Skill Framework | `src/skills/`、`src/agents/skill_selector.py` | Skill 协议、Registry、Intent 选择、Tool Allowlist 与版本快照 |
| Checkpoint | `src/agents/checkpointing.py` | 根据环境管理 Memory / SQLite / PostgreSQL Saver 及其连接生命周期 |
| Durable Execution | `src/agents/durable_execution.py` | 管理 Thread 业务关联、执行状态、恢复租约、重启扫描和幂等续跑 |
| Agent 节点 | `src/agents/` | Analyzer、Tooling、Retriever、Resolver、QA、Escalation |
| Tool Registry | `src/tools/registry.py` | 工具注册、Schema、RBAC、风险策略、执行和 Trace |
| Tool Governance | `src/tools/governance.py`、`outbox.py`、`policy.py` | 加密保存高风险写操作的提议，交给不同的人审批，再使用幂等 Outbox 异步执行。结果不明时自动对账，并支持补偿、Retry/DLQ 和 Policy 回放 |
| Mock Adapter | `src/tools/crm.py`、`order_mgmt.py`、`ticketing.py` | 模拟 CRM、OMS 和历史工单系统 |
| LLM Provider | `src/llm/provider.py` | 定义分析、生成、QA 和通用 Chat 接口；选择 Mock / OpenAI / Azure，并支持 Analyzer/QA 独立 Fast Model 路由 |
| DecisionProvider | `src/decision/` | 版本化封闭问题集、Jev System One Adapter、置信度门禁和 LLM Fallback 映射 |
| Guardrails | `src/guardrails/` | PII 脱敏、Prompt Injection、Jailbreak 和 Response Filter |
| Risk Engine | `src/risk/engine.py` | 统一综合安全、业务、置信度、QA 和异常信号，输出风险等级与处置建议 |
| RAG | `src/rag/` | 文档解析、分块、Embedding、版本管理、Hybrid Retrieval 和 citation |
| 记忆 | `src/memory/service.py`、`redis_memory.py` | 会话归属、有界 Context Assembly、实体/Snapshot、HITL 回写与 Redis 缓存 |
| 审批 | `src/approval/workflows.py` | 创建待审批记录，处理通过、修改、拒绝和审批延迟 |
| 工单状态机 | `src/tickets/state_machine.py` | 工单合法状态与动作约束 |
| 数据模型 | `src/models/` | User、Ticket、Conversation/Memory、KnowledgeDoc、ResponseApproval、AgentRun/AgentSkillSelection、AgentExecution、Feedback 与 Tool Action/Audit |
| 评测 | `src/evaluation/` | RAGAS / DeepEval Adapter、本地指标与统一评测入口 |
| 可观测 | `src/observability/` | Prometheus Metrics、token/成本估算和 OpenTelemetry Trace |
| 部署 | `deployment/`、`monitoring/` | Docker、Docker Compose、Kubernetes、Prometheus 和 Grafana 模板 |

## 数据流

### 用户咨询与客服后台主链路

1. 终端用户通过用户咨询页提交问题，前端调用 `POST /support/requests`。
2. API 创建唯一业务工单并执行 LangGraph Workflow，随后持久化 `AgentRun`，包含回复、citation、Tool Call、QA、版本、Token、延迟和 Trace ID。
3. 普通且通过质量门的请求只向用户返回安全的最终回复，不暴露 Tool、QA、风险或 Trace 内部字段。
4. 高风险、低置信度、低 QA、幻觉或异常请求创建 `ResponseApproval`；用户页持续显示安全的处理回复，并以 `risk_review`、`quality_review`、`processing_exception` 或 `manual_review` 告知人工介入类别，但不暴露内部规则和未经审批的草稿。
5. 客服员工后台通过受 RBAC 保护的 `GET /staff/review-queue` 仅加载 `pending_approval` 工单，不展示普通自动处理工单。
6. 员工打开详情时调用 `GET /tickets/{ticket_id}/agent-result`，只读取最新持久化结果，不重新执行 Workflow；审批完成后工单自动移出人工队列。

### `/chat` 主链路

1. Client 提交 `session_id`、`customer_id`、`message` 和 `kb_version`。
2. API 在创建工单前校验 `session_id -> customer_id` 归属，然后以 Append-only 方式写入当前 User 消息。
3. MemoryService 使用 SQL revision 校验 Redis Cache；Cache Miss/过期/不可用时回退 SQL，并组装最近消息、摘要、实体和上一轮 Intent。
4. API 将有界 Memory Context 与当前工单共同写入 `AgentState`并调用 `run_agent_workflow()`。
5. Analyzer 进行输入多层安全检测、PII 脱敏、工单分类和初始风险评估。
6. Context Enrichment 并行执行 Tooling 与 Retriever：前者补充客户、订单和历史工单上下文，后者按知识库版本和部门检索 citation；两条分支分别扫描间接注入。
7. 系统以风险只升不降的方式合并两条分支；任一分支命中安全威胁就清空受污染上下文并直接进入 Escalation。
8. Resolver 合并 Knowledge Base Context、Structured Tool Context 和带信任边界的 Conversation Context 生成回复草稿。
9. QA 校验草稿，Risk Engine 更新输出风险，Escalation 计算 SLA 并决定是否升级。
10. API 回写工单的情绪、优先级、部门和 SLA。
11. Approval Gate 对普通请求直接结束；如果 `approval_required = true`，则在持久化 Checkpoint 后 `interrupt`。
12. API 创建 `ResponseApproval(status="pending")` 和 `AgentExecution(status="interrupted")`，并通过状态机将工单转为 `pending_approval`。
13. 普通 Assistant 回复直接写为 final；待审草稿只写为 pending，不进入后续 Prompt，直到人工通过/修改后再幂等回写，拒绝则标记 rejected。

**当前限制**：Memory V1 是有界短期对话记忆，不是向量化长期记忆；实体提取仅覆盖明确订单号/运单号，用户端身份仍是 Demo 客户选择器，生产仍需真实登录与租户边界。

### 人工审批链路

1. 客服通过 `GET /approvals/pending` 查询待审批草稿。
2. 客服通过 `POST /approvals/{approval_id}` 提交 `approved`、`modified` 或 `rejected`。
3. 系统记录审批人、最终回复和从草稿创建到人工处理的延迟，再原子抢占数据库恢复租约。
4. 续跑使用原 Checkpoint Thread，只重新进入 Approval Gate；成功后更新 AgentRun 的 Workflow Path 和 AgentExecution。AgentRun 保留原始 AI 草稿，人工最终回复保存在 ResponseApproval / Feedback，供 DPO 数据正确区分 rejected 与 chosen。
5. 状态机将通过/修改的工单转为 `resolved`，将拒绝的工单转回 `in_progress`。恢复失败时状态保留为 `resume_pending`，可在重启扫描或主管 API 中重试。
6. 只有 `resolved` 工单可以通过关闭动作转为 `closed`。

### 知识库链路

1. 文档解析支持 PDF、DOCX、HTML、TXT、Markdown 和结构化 FAQ JSON。
2. `RecursiveTextSplitter` 默认使用 `600` 字符 chunk 和 `120` 字符 overlap。
3. `KBVersioningService` 将原文和元数据写入 SQL，并将分块与 Embedding 写入 ChromaDB。
4. 检索时强制 `version`，可选 `category`，扩展候选集后融合向量分数和 BM25 风格词法分数。
5. 最终返回 `source`、`text`、`score` 和 `version` 组成的 citation。

## Prompt 策略

### 总体原则

- PromptOps V1 将 Analyzer / Resolver / QA 模板集中为 `src/promptops/defaults.py` 内置 Bundle 与文件型 Prompt Registry。内容 Hash 同时覆盖版本、系统/用户模板及冻结的 Intent 说明；Workflow 和整次评测固定 Bundle，AgentRun 的 `prompt_version` 保存实际 Bundle Hash。
- `BaseLLMProvider` 定义 `analyze_ticket`、`generate_resolution`、`evaluate_qa` 和 `run_chat` 四类统一接口。
- 默认 `LLM_PROVIDER=mock`，保证无 API Key 的本地开发、测试和演示可复现。
- OpenAI 和 Azure OpenAI 使用 `temperature=0.0`；Analyzer 和 QA 要求 JSON Mode，Resolver 返回自然语言。
- OpenAI-compatible Provider 支持通过 `LLM_FAST_*` 将 Analyzer 与 QA 路由到独立小模型服务（例如 Qwen Turbo），Resolver 继续使用 `LLM_MODEL_NAME`；未配置 Fast Model 时安全回退主模型。
- 输入在到达 Prompt 前先经过安全检测与 PII 脱敏，输出在返回客户前经过 QA 和 Response Filter。

### Analyzer Prompt

- 角色是客服工单分析器。
- 输入是脱敏后的主题与描述。
- 输出必须包含情绪、优先级、部门、意图、情绪标签和置信分数。
- 允许的部门为 billing、technical、shipping 和 general；优先级为 low、medium、high 和 urgent。

### Resolver Prompt

- System Prompt 要求只使用提供的上下文回答。
- 上下文同时包含 Knowledge Base citation 和 Structured Tool Context。
- 当上下文不足时，应说明需要升级，而不是自行编造政策或业务结果。
- 对外回复应保持专业、直接、可执行，并在适当位置保留来源依据。

### QA Prompt

- 输入包含 Query、citation 文本和 Resolver 回复。
- 输出必须包含 `qa_score`、`hallucination_detected`、`reasons`、`faithfulness`、`context_precision` 和 `citation_verified`。
- QA 结果不直接修改业务事实，而是为 Escalation 和 Approval 提供决策信号。

### Prompt 修改约束

- 不得在 Prompt 中宣称 Mock Adapter 可以执行真实退款、取消订单或修改 CRM 数据。
- 不得删除“上下文不足时升级”的核心约束。
- 新增 Prompt 字段时必须同步更新 Provider 接口、Mock Provider、外部 Provider 和相关测试。
- 已实现内容快照、成对评测、环境指针、门禁晋级、显式回滚和发布审计；未实现按用户分流的 A/B 灰度、自动回滚、人工校准的语义发布门禁和反馈自动纳入评测集。旧 `PROMPT_VERSION` 仅为无 Bundle 历史记录的兼容标签。

## 评测体系

项目包含“在线 QA”和“离线评测 Adapter”两层质量保障，两者不可混为同一概念。

### 在线 QA

- QA Agent 对每次 Agent 草稿返回 `qa_score` 和 `hallucination_detected`。
- `qa_score < 0.8` 或检测到幻觉时触发 Escalation 与人工审批。
- Response Filter 命中指令泄露时强制将分数降为 `0.5` 并标记风险。
- Mock LLM 在无 citation 时返回低 QA 分数和幻觉标记，用于可复现测试。

### 离线评测

- 在线单条评测入口是 `run_deeval_evaluation()` 和 `POST /evaluate-response`。
- 离线统一入口是 Dataset + Workflow Replay Pipeline，RAG 采用 RAGAS，Agent 行为采用 DeepEval。
- 第一版真实 Baseline 入口是 `scripts/run_baseline_eval.py`：固定读取 100 条 Baseline，逐 Case 构造完整 Ticket State 并回放当前 LangGraph Workflow。Case Pass 仅使用 intent、department、Required/Forbidden Tool、HITL 和 Approval 六类确定性比较；暂不读取 reference answer、priority、expected nodes 和安全标签参与判定。
- Baseline V1 在同一次 OTel Trace 中采集端到端与 Analyzer / Tool / RAG / Resolver / QA 节点耗时、Token、模型、Analyzer 策略和 LLM 调用明细，报告汇总 Average、P50、P95、平均 Token 与 Rule Hit Rate，并关联实际 Agent Trace ID。
- 每次 Baseline V1 正式运行同时保存带本地时间戳的不可变 JSON / Markdown 快照，`baseline_v1_latest.*` 以普通文件副本保留最新内容，兼容 Typora 等不打开符号链接的桌面工具；报告固定记录 Dataset SHA256、Evaluator 范围、Workflow/Prompt 版本、模型、生成限制、Risk 阈值与 Observability 配置。旧单条评测隔离到 `single_response/` 并最多保留 20 份，所有运行报告默认不提交 Git。
- Baseline JSON 写入后会纯离线、确定性生成 `error_analysis_<run_id>.md` 与 `error_analysis_latest.md`，只分析 FAIL Case 的 Failure Breakdown、Intent Confusion Matrix、HITL/Approval mismatch、Tool 问题和逐 Case Expected/Actual/Trace，不重放 Workflow、不调用 LLM、不修改 Dataset 或 Agent。
- PR Gate 使用 Mock Provider 在隔离 SQLite/Chroma 目录中回放同一固定 100 条 Workflow，对 Dataset Hash、六项行为指标和新增失败 Case 执行免费、确定性门禁。Release Gate 必须显式确认付费调用，对真实 LLM 报告额外检查 P95、Token、LLM Calls 和 Analyzer Rule Hit Rate。
- GitHub Actions `CI` 执行全量后端测试、前端构建、PR Gate 和镜像构建；仅当 `Release Quality Gate` 通过时，`CD` 才会将完全相同 Git SHA 的镜像发布到 GHCR。当前交付目标是镜像仓库，不代表已自动部署生产集群。
- 正式离线报告同时输出 Faithfulness、Answer Relevancy、Context Precision、Context Recall、Agent 行为指标、Security Precision / Recall / F1 / 误报率、安全处置正确率、citation hit rate、Workflow Path 和 Trace ID。
- 没有可用 API Key 时可显式选择 `local` 确定性指标进行 CI 烟测；正式 RAGAS / DeepEval 模式缺少依赖或密钥会直接失败，不会自动伪装为正式结果。
- 报告写入 `evaluation/reports/evaluation_latest.json` 和 `evaluation_latest.md`。

### 评测边界

- 当前有 13 条 Synthetic Golden Dataset，另有 100 条 Workflow Replay Baseline；Golden Dataset 本身仍需继续扩充和人工复核。
- Synthetic 参考答案不是经真实客服专家审核的生产标准答案。
- 本地启发式指标适合验证评测链路和做基础回归，不能代表生产环境真实准确率。
- 在完成人工标注、稳定基线和真实环境校准前，不得将本项目表述为已有生产质量结论。

## 当前完成情况

### 已完成

- FastAPI 后端 API、JWT 鉴权和基础 RBAC。
- LangGraph 六个业务节点 + Skill Selector + Approval Gate 工作流和安全条件路由。
- Skill Framework V1：6 个版本化 Skill 覆盖 8 个统一 Intent；选择结果进入 AgentState、Checkpoint、OpenTelemetry、AgentRun 独立关联表和 Baseline Report，ToolRegistry 在 Handler 前执行 Skill Allowlist 兜底。
- LangGraph Checkpoint + Durable Execution：本地 SQLite / 生产 PostgreSQL Saver、`interrupt` / `Command(resume)`、`AgentExecution` 状态、数据库恢复租约、启动恢复和主管手动重试。
- Prompt Injection、Jailbreak、PII 脱敏和 Response Filter。
- ToolRegistry、4 个读 Tool 与 1 个高风险 Mock 写 Tool，具备 Schema、RBAC、风险策略和超时边界。
- Tool Governance V2.2：高风险写操作必须经过 `proposed -> pending_approval -> approved/rejected -> queued -> executing -> succeeded/failed/unknown` 状态机；提议人不能自批，Agent Workflow 不会自动路由该写 Tool。
- 写 Action 具备唯一业务幂等键；`queued + Outbox` 在同一数据库事务提交，Worker 用数据库租约和乐观版本竞争消费。超时只进入 `unknown` 并自动对账；对账 Retry 耗尽进入 DLQ，主管可显式重放；已成功动作可进入独立幂等补偿状态机。
- Tool Policy 在 Action 创建时保存版本化快照与 HMAC，支持不调用外部系统的 deterministic 审计回放。
- Tool 调用已持久化到 `tool_invocation_audits`，只保存 HMAC、字段名、脱敏结果、执行状态、身份与 Request/Trace 关联；Action 迁移以 Append-only Event 保存。
- ChromaDB、知识库版本/类别过滤、Hybrid Retrieval、轻量 rerank 和 citation。
- Mock / OpenAI / Azure OpenAI LLM Provider 适配。
- Memory V1：结构化会话表、追加为主的消息、确定性摘要/实体、会话归属、Redis revision Cache、多节点上下文注入和 HITL 最终回写。
- Human-in-the-Loop 审批与工单状态机。
- OpenTelemetry 统一 Trace / Metrics 采集、OTLP Collector、LangSmith Trace 后端和 Prometheus / Grafana 指标展示。
- Trace 问题修复：Resolver / QA 共享生成证据；截断回复最多重写一次；Tool Span 显示具体工具名和重试/降级状态；脱敏保留合法时间戳和系统观测 ID，对外观测/决策内容使用稳定业务 ID 别名，不影响内部 Memory 的订单号提取。
- 无害范围外请求：天气等明确主题按规则处理，规则外主题可由同一次 Jev 分类识别；生成能力说明后正常完成，不查无关业务数据，不自动创建审批。安全、业务证据不足和真实依赖故障继续执行原有风险规则。
- Docker Compose、Kubernetes manifests、分层 requirements、Python 3.11 GitHub Actions 全量 CI、两级 Evaluation Quality Gate 与 GHCR CD。
- RAGAS / DeepEval Adapter、本地评测降级和 JSON 报告输出。
- Dataset + Workflow Replay 离线评测，统一输出 RAG / Agent / Security 指标并关联 Trace ID。
- Baseline Workflow Replay V1：固定 100 条 Dataset、完整 Ticket State、六项确定性行为指标、逐 Case 执行结果及 OTel Trace 同源性能报告。
- 真实 LLM Regression 专用入口，支持 12 条 smoke 和 100 条 full 套件，能拒绝 Mock、要求明确确认并控制调用预算；同时记录使用的模型、Token 数和成本。
- Feedback Pipeline 第一阶段：Agent Run 快照、用户评价、人工修正、评测结果关联，以及脱敏后的 SFT / DPO 候选导出。
- LangSmith 前端入口：主管/管理员可分页或按工单编号精确查询 Agent Run、Trace ID、Workflow Path 和执行快照；列表时间统一展示为北京时间，并可跳转至配置的 LangSmith Project 下钻。
- Prompt Injection 多层检测已覆盖用户输入、Tool 返回和 RAG 文档，命中时从当前信任边界短路到 Escalation。
- Qwen3Guard-Gen-0.6B 已作为独立 OpenAI-compatible 语义安全 Adapter 接入三类信任边界；默认关闭外部服务，启用后将 `Safe / Controversial / Unsafe` 交给 Risk Engine。
- 独立 Risk Engine 已接入 Analyzer、QA、Escalation、AgentState、API、Trace、Metrics 和结构化日志。
- 第一版 Resilience 已覆盖 LLM、Hybrid RAG 和 Tool：统一故障分类、超时、有界 Retry、进程内 Circuit Breaker、可选备用模型/单路 RAG Fallback，并将降级事件关联 AgentState、Risk Engine、Trace 和 Metrics。
- MVP 主链路已实测通过：FastAPI `/health` -> LangGraph Workflow -> Ticket / AgentRun 持久化 -> 用户评价 -> FeedbackEvent 持久化。
- 覆盖 Agent、API、Auth、Guardrails、RAG、Evaluation、Observability、Tool Registry 和工单状态机的 pytest 测试模块。

### 部分完成

- **多轮记忆**：V1 已完成有界历史、实体续接、Prompt/Retrieval Context 和审批回写；尚无向量长期 Memory、语义摘要模型、多租户身份接入和专项多轮 Evaluation Gate。
- **Tool Governance**：V2.2 的审批、执行、对账和补偿流程已实现，但目前退款、对账和补偿还在使用 Mock OMS 账本。项目尚未接入真实 OMS、验证跨服务调用约定，也未用生产 Alembic Migration 管理数据库变更。
- **Trace**：核心 Span 与 OTLP Collector 已接入，当前 Collector 将 Trace 转发 LangSmith；尚未接入 Jaeger / Tempo。
- **评测**：已具备 Golden Dataset、100 条 Workflow Replay Baseline、真实 LLM 运行入口、统一报告与两级 Quality Gate。2026-08-30 同一固定 Dataset 的 DeepSeek + Qwen 真实复测将 Case Pass Rate 从 `0.54` 提升到 `0.99`，平均耗时约 `1.62s`、P95 约 `3.24s`、平均总 Token `453.29`、LLM Calls `87`。PR Gate 要求 Mock 确定性回放 100% 通过，Release Gate 固化当前真实模型质量和性能阈值；语义回答质量与人工标注仍是后续评测范围。
- **Feedback Pipeline**：第一阶段采集和候选导出已实现，尚未接入标注平台、训练任务、Dataset Registry 和模型发布门禁。
- **部署**：本地 Docker Compose 和 Kubernetes 模板已存在，但不代表已在真实生产环境部署。
- **前端**：React 已拆分连续会话式用户咨询页与客服员工后台；用户窗口使用浏览器保存的最多 50 个不可预测 Session ID 作为能力凭据，从 SQL 加载这些 Session 最近 7 天的安全历史。正常回复、风险核验、质量复核和处理异常都会返回可见状态，待审/拒绝草稿只显示安全状态。自动回复后可提交关联 AgentRun / Trace 的一次性评价。员工后台仅处理待审批异常工单，支持原样批准、人工修改和拒绝，并保留 Agent Run / LangSmith 可观测入口。尚未接入真实终端用户身份、Prometheus 真实趋势指标、内嵌 Span 时间轴和异步消息通知。
- **安全治理**：已有确定性多层检测、Qwen3Guard 语义 Adapter 与可配置 Risk Engine，但 Guard 服务默认未启用，且尚无策略版本、持久化安全事件和真实数据阈值校准。
- **故障治理**：LLM/RAG/读 Tool 仍是单进程 Resilience；受治理写 Tool 已有数据库 Outbox、租约、Retry/DLQ、业务幂等和结果对账，但尚无分布式 Circuit Breaker、通用消息平台与故障注入压测。
- **Durable Execution**：已覆盖人工审批等待与重启续跑；尚无 Checkpoint TTL/归档清理、多 Workflow 版本兼容执行器、通用后台任务队列和全节点失败的自动续跑策略。

### 已知环境限制

- 项目推荐 Python 3.11。
- 旧的本机 `.venv` 是混装 Evaluation 依赖的 Python 3.13 环境，其 pytest `exit code 139` 与 LangGraph 版本冲突不代表业务断言失败。
- 核心运行时已固定经验证的 LangChain / LangGraph / ChromaDB 版本组合；2026-09-08 当前环境全量测试 231 passed，并通过固定 100 条 Baseline 的 PR Agent Quality Gate；CI / Docker 继续使用 Python 3.11。
- 本地 ChromaDB 使用版本化目录 `.runtime/chromadb-0.5`；其他 ChromaDB 大版本写入的旧 SQLite schema 不应直接复用。

## 下一步规划

按当前优先级推进，未在代码中完成前不得将以下项目表述为已有能力。

### P0：Feedback Dataset 治理与训练准备

- 增加 Dataset Registry、数据版本、Review 状态和训练集快照。
- 建立训练样本人工复核、数据删除、数据保留周期和来源授权策略。
- 增加 SFT / DPO 数据分层、Train / Validation / Test 划分和数据漂移检查。

### P1：扩充 Golden Set 与回归基线

- 继续扩充并人工复核 Synthetic Golden Dataset，将保修、物流和订单取消等尚未入库政策与现有 100 条 Baseline 分开治理。
- 每条样本包含 Query、Expected Answer Points、Expected Sources、Risk Level 和 Category。
- 输出 citation hit rate、Context Recall、Answer Relevance、Faithfulness Proxy 和 Hallucination Risk。
- 生成稳定的 JSON + Markdown 报告，用于知识库和 Prompt 变更的回归比较。

### P1：多租户知识库隔离

- 为 KnowledgeDoc 和 ChromaDB metadata 增加 `tenant_id`。
- RAG Query 强制使用 `tenant_id + kb_version` Filter。
- 从鉴权上下文解析 Tenant，并增加跨租户越权检索测试。

### P1：检索后端抽象

- 定义 `SearchBackend` 接口。
- 保留当前 `ChromaHybridBackend`。
- 设计 `OpenSearchHybridBackend`，支持 Vector Search、BM25、Metadata Filter、rerank 和 citation。

### P1：审计与可观测增强

- 新增 `ticket_status_events` 持久化状态流转历史。
- 将当前 Mock OMS 的幂等、结果查询和补偿契约接入真实 OMS，并补充跨服务故障演练。
- 根据部署需要为 Collector 增加 Jaeger、Tempo 或其他 APM exporter，并完善采样与告警策略。

### P2：用户通知与异步处理

- 将同步 `POST /support/requests` 演进为提交后立即返回 `ticket_id` 的后台任务。
- 增加用户身份、工单归属校验以及人工审批完成后的站内通知或推送。

### P2：PromptOps 后续优化

- V1 已为三个节点保存 Prompt 版本，并能查到每次运行使用的版本。候选版可先评测，通过后才能晋级或回滚。
- 后续建立独立留出集、反馈人工复核、Dataset Registry 与人工校准的语义质量指标。
- 在有真实流量后引入 A/B、按会话灰度和自动回滚。

## 项目不变约束

后续 AI 和开发者必须遵守以下约束：

1. 不得将 Mock CRM、OMS、Ticketing、Refund 或默认 Mock LLM 写成真实企业接入。
2. 所有业务工具必须经过 ToolRegistry，不得在 Agent 中直接调用 Adapter。
3. 工单状态只能通过 TicketStateMachine 流转，不得直接赋值 `ticket.status`。
4. 不得移除 Analyzer、Tooling 和 Retriever 后的安全短路路由。
5. Redis 必须保持可选，本地 Demo 不得因 Redis 未启动而失败。
6. 默认 LLM Provider 必须保持 Mock，以便无 API Key 运行和测试。
7. API 中面向 Demo 和审计的 `tool_context`、`tool_calls`、`citations`、`approval_required`、`approval_id` 和 `cost_metadata` 不得无理由删除。
8. `docs/` 下文档默认使用中文，代码标识符、API 路径和通用技术名词可保留英文。
9. 新能力必须同步更新本文档、相关架构文档、测试和变更日志。
10. 不得根据路线图、接口占位或文档设想宣称能力已实现；是否完成必须以当前代码和测试为准。

## AI 参与开发时的使用方式

1. 首先阅读本文档，确认当前能力、Mock 边界、已知限制和下一步优先级。
2. 在修改前阅读相关实现与测试，不能只依赖旧文档推断现状。
3. 将改动限制在当前任务范围，不重构无关模块，不删除未理解的用户文件。
4. 实现后使用 Python 3.11 环境执行与风险相匹配的编译、定向测试或 CI 验证。
5. 完成新能力时更新本文档中的“当前完成情况”和“下一步规划”，避免上下文失真。
