# AI 开发交接

> 本文档面向 Codex、GPT、Claude Code、Cursor 等后续 AI。参与开发前必须先读 `00_PROJECT_CONTEXT.md`，再按任务查阅本文档与对应专题文档。

## 阅读顺序

1. `00_PROJECT_CONTEXT.md`：唯一项目概览。
2. `03_INTERVIEW_CANON.md`：唯一可对外引用的事实口径。
3. `01_ARCHITECTURE.md`：工程架构、State 与模块边界。
4. `02_BUSINESS_LOGIC.md`：业务流程与结束条件。
5. `04_DECISIONS.md`：已确定的技术决策。
6. `05_ENGINEERING_GUIDE.md`：启动、API、测试、评测、可观测与部署。
7. `06_PROMPTS.md`：Prompt 策略与变更约束。
8. `08_TODO.md`：当前任务、技术债和风险。
9. `09_INTERVIEW_QA.md`：面试问题的统一回答。

## 当前系统快照

- OMS 参考实现：`src/oms/` 使用独立 PostgreSQL、API Key、Fernet Key；`src/tools/oms_gateway.py` 适配三个 HTTP 接口。默认 `OMS_PROVIDER=mock` 不变，只有显式配置 reference_http 才接入。服务不操作资金，写入结果不确定时仍使用原 unknown / Outbox 对账；不要把 submitted 改写为已到账，也不要通过重试写入解决查询不到的问题。

- 后端：FastAPI + SQLAlchemy Async + JWT/RBAC。
- Agent：LangGraph StateGraph，包含 Analyzer、Tooling、Retriever、Resolver、QA、Escalation 六个逻辑业务 Agent 节点，以及不调用 LLM 的 Skill Selector 和 Approval Gate。
- 执行：Analyzer 后先使用统一 Intent 确定性选择 Skill，再并行 Tooling/Retriever；安全强命中直接短路；之后 Resolver、QA、Escalation、Approval Gate。高风险路径 interrupt，人工决策后从原 Checkpoint Thread 恢复。
- Skill：`src/skills/` 管理 6 个版本化 Skill，选择快照进入 State/Checkpoint/Trace/AgentRun/Evaluation；ToolRegistry 在现有治理前再做 Skill Allowlist 校验。
- Durable Execution：本地 AsyncSqliteSaver、PostgreSQL AsyncPostgresSaver；AgentExecution 关联工单/审批/Run/Trace，数据库恢复租约防重复，启动扫描补偿已决策但未完成的续跑。
- LLM：默认 Mock，保留 `mock/openai/azure`；`openai` 为通用 OpenAI-compatible Provider。
- DecisionProvider：`src/decision/` 封装可选 Jev System One。启用后复核 Analyzer 规则候选并评判 QA 正向证据；低置信度/不可用时按规则或 LLM 分层回退，未接入离线 Judge。
- 优化：Analyzer 保留规则候选与回退，Analyzer/QA 可使用 Jev 或小模型，Resolver 裁剪 Context，QA 仅返回最小 JSON。
- RAG：ChromaDB + 关键词/向量 Hybrid Search + 轻量 rerank + version/category filter + citation。
- Tool：Mock CRM/OMS/Ticket Adapter 通过 ToolRegistry 暴露；V2.2 为高风险写 Action 增加业务幂等、Transactional Outbox、Worker、unknown 自动对账、Retry/DLQ、补偿和 Policy 回放。
- V2.3：同一客户/订单整单退款复用原 Action，参数变化拒绝新建；Worker 续租和领取版本保护全部状态写回；unknown/DLQ 关联独立的业务核实任务。主管确认只保存已核实的外部结果，绝不重新执行写 Tool 或修改回复审批。新表由 create_all 创建，旧 Action 通过兼容检查去重，启动时补齐旧核实任务。
- PostgreSQL 故障演练：`scripts/run_postgres_tool_drill.py` 只接受显式确认的专用测试库，使用随机 schema 和持久化模拟 OMS。CI 已配置并发创建、跨进程续租、强杀后对账和旧 Worker 保护；未看到实际报告时不得声称已跑通，真实 OMS 仍未接入。
- 故障治理：LLM/RAG/读 Tool 统一超时、有界 Retry、进程内 Circuit Breaker 与 Fallback；高风险写调用禁止重试，只 Retry 幂等对账查询。
- 安全：确定性多层规则 + 可选 Qwen3Guard-Gen-0.6B + Risk Engine + 输出过滤 + HITL。
- Memory V1：SQL 结构化会话是事实源，Redis 是可选 revision Cache；有界历史、摘要和实体进入 AgentState，待审草稿不进入 Prompt。
- 可观测：OpenTelemetry 唯一采集，OTLP 统一导出，Collector 分发 LangSmith Trace 和 Prometheus Metrics。
- 评测：Ragas + DeepEval + 确定性 Agent/Security Evaluator，固定 100 条 Baseline 支持真实 Workflow Replay。
- PromptOps / EvalOps V1：`src/promptops/` 管理模板快照、运行绑定和实验晋级；CLI 为 `scripts/promptops.py`。已有 Bundle 和发布指针不会自动替换；production 不接受 Mock、过时版本、未提交代码或新增失败 Case。操作流程见 `06_PROMPTS.md`。
- 反馈：AgentRun、FeedbackEvent 和 AgentRunLink 关联 Trace、用户评价、人工修正与 Evaluation。
- 前端：连续会话式用户咨询页 + 客服审批后台 + Agent 可观测页。用户端以浏览器已知 Session 列表加载最近 7 天安全历史，始终显示正常回复或安全的风险/异常处理状态，用户评分直接写入 Feedback Pipeline；审批明确区分原样批准、人工修改和拒绝。打开工单详情只读持久化结果，不重复调用 Agent。

## 最近修改：2026-10-06 线上 Trace 问题修复

- `src/agents/evidence.py` 只在生成前构造精简证据，Resolver 保存 `resolution_evidence`，QA 原样复用；不要恢复 QA 独立拼装上下文的旧方式。
- 新增 State 字段可随现有 Checkpoint 保存；旧 Checkpoint 缺少字段时走兼容构造器，没有改动暂停、恢复或审批语义。
- Resolver 默认 480 tokens，回答优先包含状态、异常、下一步。`finish_reason=length` 最多重写一次，仍截断则安全降级，不能把残缺草稿返回用户。
- Tool Span 显示具体工具及异常结果，Resilience 子 Span 保留；观测失败不能改变 Tool 执行结果。
- Trace / Jev 对外内容使用稳定业务 ID 别名。内部 Memory 的 `redact_text` 默认不改业务 ID，以免订单实体续接失效；时间戳和合法系统 ID 保留，Secret / PII 仍过滤。
- 回归覆盖完整物流 Workflow、冻结证据、旧 State、错误回答反例、Provider 截断恢复、实际导出 Tool Span、脱敏和 Memory 兼容。没有运行真实付费 Jev / LLM，也没有启动 Docker。

## 必须保持的设计

范围外请求修复：Analyzer 的安全检查仍首先执行。天气等明确无关主题按规则识别，其他普通请求可由同一次 Jev 分类的 `support_scope` 判断。只有高置信度的普通信息请求可采用范围外结果，业务关键词、实际操作和不确定结果保留原路径。确认后从 Analyzer 直接进入 Resolver，隔离本轮 Memory，生成 `capability_boundary`，再经 QA、Escalation 和 Approval Gate 正常结束。不能给范围外标签增加跳过安全或业务风险检查的特权。

1. 默认 Mock 模式必须能在无 API Key、无 Redis、无 Collector 时启动和测试。
2. CRM、OMS、Ticketing 与 Refund 仍是 Mock Adapter，不得写成已接入真实企业系统。
3. 所有业务 Tool 必须经过 ToolRegistry，不得从 Agent 直接调 Adapter。
4. 工单状态只能经 TicketStateMachine 流转。
5. Prompt Injection 或 Jailbreak 强命中必须短路 Tool/RAG/Resolver/QA，隔离不可信上下文并转人工。
6. 退款、投诉、越权写操作、低置信度、低 QA 或高风险结果必须保持 HITL 策略。
7. OpenTelemetry 是唯一 Trace/Metrics 采集路径，不得恢复 LangSmith SDK `traceable` 双轨采集。
8. Trace 上报前必须脱敏、密钥过滤和敏感业务字段过滤；遥测失败不影响主流程。
9. LLM 默认使用用户当前输入语言回复，除非用户明确要求切换。
10. Evaluation 必须真实 Replay 当前 Workflow，报告保留实验配置与 Trace ID；不得通过降低 Dataset 期望来伪造通过率。
11. 打开工单详情不得触发新 Workflow，只加载已保存 AgentRun 和 Approval。
12. 需要审批的 Workflow 必须在 Approval Gate 暂停并使用原 thread_id 恢复；不得通过从头重跑模拟恢复。
13. 高风险写 Tool 的 HTTP 执行接口只能原子写入 `queued + Outbox`；`unknown` 必须先对账，禁止将超时直接当作失败并重试写入。
14. DecisionProvider 的输出不是授权。Skill、Tool 权限、Risk Engine 和 Approval Gate 不得改为 Jev 或 LLM 自由决定。

## 代码定位

| 范围 | 目录/文件 |
|---|---|
| FastAPI 路由与启动 | `src/main.py` |
| 环境配置 | `src/config.py` |
| LangGraph Workflow / AgentState | `src/agents/graph.py` |
| Checkpoint / Durable Execution | `src/agents/checkpointing.py`、`src/agents/durable_execution.py` |
| 意图枚举 | `src/models/intents.py` |
| Skill Framework | `src/skills/`、`src/agents/skill_selector.py` |
| LLM Provider 与 Prompt | `src/llm/provider.py` |
| DecisionProvider / Jev | `src/decision/` |
| Tool Registry / Adapter | `src/tools/` |
| RAG | `src/rag/` |
| Guardrails / Risk | `src/guardrails/`、`src/risk/` |
| Resilience | `src/resilience/` |
| Memory | `src/memory/` |
| Approval | `src/approval/` |
| Trace / Metrics / 脱敏 | `src/observability/` |
| Evaluation | `src/evaluation/`、`evaluation/`、`scripts/run_*eval.py` |
| Feedback | `src/feedback/`、`scripts/export_training_candidates.py` |
| React 前端 | `frontend/src/` |
| 部署与监控 | `deployment/`、`monitoring/` |

## 修改流程

1. 先检查 `git status`，保留用户未相关改动。
2. 阅读相关实现和测试，不根据文档猜测当前代码。
3. 以最小范围实现，函数可加一两行精简中文注释，主要类开头用中文说明它做什么。
4. 对新行为增加确定性测试；若影响 Agent，评估 Baseline 兼容性。
5. 运行 `git diff --check`、相关 pytest，高风险改动运行全量 pytest。
6. 同步 `00_PROJECT_CONTEXT.md`、`03_INTERVIEW_CANON.md`、`08_TODO.md` 中受影响的完成状态与边界。
7. 提交时不得包含 `.env`、评测运行产物、API Key、训练数据或无关用户文件。

## 已知限制

- 真实 CRM/OMS/Ticketing 尚未接入。
- Memory V1 已注入 Analyzer、Retriever、Resolver 和 QA；尚无向量长期记忆、真实用户/租户身份接入和多轮专项评测门禁。
- Tool 调用已持久化脱敏审计；高风险写 Tool 必须经过 `ToolAction` 状态机和 Outbox Worker。提议人不能审批自己的操作，Agent Workflow 不会自动执行。
- Qwen3Guard 默认关闭，Risk Engine 阈值尚未基于真实运营数据校准。
- Jev DecisionProvider 默认关闭，尚无真实准确率/延迟校准，且按当前边界不接入离线 Evaluation Judge。
- Feedback Pipeline 只会导出脱敏后的 SFT/DPO 候选数据，还没有 Dataset Registry、模型训练和模型发布流程。
- Docker Compose/Kubernetes 是可复现模板，不代表生产上线。
- Resilience 的通用 Circuit Breaker 仍为单进程 V1；Tool Governance V2.2 已有专用数据库 Outbox、Retry/DLQ、业务幂等和自动对账，但没有通用分布式消息平台，OMS 仍为 Mock。
- Checkpoint 已覆盖审批暂停和跨重启恢复，但没有 TTL/归档、旧 Graph 多版本恢复或通用后台任务队列；相关 DDL 仍需纳入 Migration。
- 真实 Baseline V1 用同一固定 100 条 Dataset 比较时，记录到的通过率为 0.99。这个结果只反映这组回放数据，不能当作生产业务指标。

## 当前优先级

1. 为 Feedback、Memory 和 Durable Execution 新表引入 Alembic migration。
2. 为 Resilience V1 增加故障注入、多副本 Breaker，并将 Tool Governance V2.2 的 Mock 契约接入真实 OMS。
3. 建设 Dataset Registry、人工复核与数据保留策略。
4. 增加 `ticket_status_events`，Tool 审计不再是待办。
5. 使用 Shadow Mode 校准 Qwen3Guard 与 Risk Engine。

最终任务清单始终以 `08_TODO.md` 为准。
