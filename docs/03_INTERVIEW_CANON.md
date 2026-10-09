# SupportGPT 智能客服 Agent 面试事实口径

> 本文是本项目唯一允许在面试、项目介绍、AI 回答和对外沟通中引用的事实文档。
> 如其他文档、历史聊天或旧版本描述与本文不一致，以本文为准；如本文与当前代码不一致，以当前代码为准，并必须在同一次改动中更新本文。
> 本文不用于包装未实现能力。无法从仓库、已确认项目设定或用户实际经历中证实的事项，必须明确说明“未知”或“不适用”。

## 1. 项目定位

SupportGPT 智能客服 Agent 面向售后服务场景，可在本地运行，用于演示 AI Agent 的工程实现。项目将初版 FAQ / RAG 问答扩展为客服处理平台：系统可理解工单、补充结构化业务信息、检索知识、生成并检查回复，对高风险请求进行拦截并转人工审批。工单也会保留从提交到处理完成的状态记录。

项目用于展示 Agentic RAG、Tool Calling 治理、Human-in-the-Loop 和可观测性等工程能力。它不是已连接真实企业数据、已在生产客服中心上线的系统。

## 2. 项目背景

项目基于开源客服项目进行改造，目标是将单一客服问答体验升级为更接近企业售后工作流的系统。重点业务场景包括退款、保修、物流异常、订单取消、账户问题和技术支持。

CRM、OMS、历史工单和退款资格初筛当前均通过本地 Mock Adapter 模拟。默认 LLM 也为 Mock Provider，以保证本地无 API Key 环境下可复现。

## 3. 项目目标

- 让客户自然语言问题进入可分析、可路由、可审核的工单流程。
- 在回复生成前补充客户、订单和历史工单等结构化业务事实。
- 使用带版本和类别过滤的 Hybrid RAG 提供知识依据与 citation。
- 在输入、工具、输出和工单状态多个层面设置安全与治理边界。
- 高风险、低置信度或紧急问题需由人工审批，不由模型单独决定并处理完成。
- 提供本地可运行、可观测、可替换真实服务的工程骨架。

## 4. 团队情况

仓库与已确认项目资料**没有记录真实团队人数、成员名单、组织关系、协作方式或个人贡献占比**。

因此，对外不得声称：

- 这是多人团队项目，或具体说明团队人数。
- 自己担任过不存在证据支持的职位，例如 Tech Lead、架构负责人或项目经理。
- 自己独立完成了仓库中的全部代码，除非提问者本人能基于真实经历确认。

可以准确说明：这是一个基于开源项目改造的客服 Agent 演示项目，现有仓库展示了客服 Agent、RAG、工具治理、审批和可观测性等能力。

## 5. 我参与的工作

仓库无法证明每个文件由谁编写。面试时请根据你真正参与的工作说明个人贡献，本文不代你虚构经历。

在仅依据仓库可验证事实的情况下，可以描述的**项目改造范围**为：

- 将客服处理流程组织为安全检测、业务上下文补全、RAG、回复生成、QA 和升级决策的显式 Agent Workflow。
- 增加 Tool Calling 的 Schema、RBAC、超时和审计约束。
- 增加 Hybrid RAG、知识库版本/类别过滤和 citation。
- 增加可选 Redis 记忆、SQL 持久化、HITL 审批、工单状态机、Prometheus 与 OpenTelemetry。
- 增加 Agent Run、用户评价、人工修正和评测结果关联，以及脱敏后的 SFT / DPO 训练候选导出。
- 增加中文项目、架构、Mock 边界和 AI 接手文档。

如需使用第一人称“我负责”，应仅陈述回答者实际参与且能在追问中解释的部分，不得把“项目具备”自动等同于“我独立完成”。

## 6. 系统架构

系统由以下层次组成：

```text
客服端 / API Client
        |
        v
FastAPI API 层（鉴权、工单、聊天、审批、评测、Metrics）
        |
        v
LangGraph Agent Workflow
  Analyzer -> Context Enrichment（Tooling 与 Retriever 并行） -> Resolver -> QA -> Escalation
       \-> 客户输入、Tool 结果或 RAG 文档命中安全风险时直接进入 Escalation
        |
        +-- ToolRegistry -> Mock CRM / OMS / Ticketing
        +-- Hybrid RAG -> ChromaDB
        +-- LLM Provider -> Mock / OpenAI / Azure OpenAI
        +-- Risk Engine -> 风险等级 / 人工建议 / 自动化阻断
        +-- HITL -> ResponseApproval + 工单状态机
        |
        +-- SQLAlchemy -> SQLite / PostgreSQL
        `-- Redis（可选会话缓存）

Prometheus + OpenTelemetry 覆盖 API、Agent、工具、RAG 和审批过程。
```

正常请求的固定顺序为：Analyzer → Skill Selector → Context Enrichment（Tooling 与 Retriever 并行）→ Resolver → QA → Escalation → Approval Gate。客户输入命中 Prompt Injection 或 Jailbreak 时，系统从 Analyzer 直接进入 Escalation；Tool 返回或 RAG 文档命中间接 Prompt Injection 时，系统清空受污染上下文，从 Context Enrichment 直接进入 Escalation，不调用后续 Resolver / QA。所有路径最终经过 Approval Gate：普通请求结束，高风险请求持久化 Checkpoint 并暂停等待人工决策。

## 7. 技术栈

| 领域 | 当前技术 |
|---|---|
| 后端与 API | Python、FastAPI、Pydantic |
| Agent 编排 | LangGraph、LangGraph Checkpoint（SQLite / PostgreSQL Saver） |
| 数据访问 | SQLAlchemy Async |
| 本地数据库 | SQLite |
| 容器化数据库 | PostgreSQL |
| 短期会话缓存 | Redis（可选） |
| 向量数据库 | ChromaDB |
| 检索 | Embedding、Hybrid RAG、BM25 风格词法打分、轻量 rerank |
| LLM | Mock LLM、OpenAI-compatible（OpenAI / DeepSeek / Qwen / vLLM）、Azure OpenAI Provider |
| 决策模型 | 可选 DecisionProvider + Jev System One（默认关闭） |
| 安全 | JWT、RBAC、PII 脱敏、确定性 Prompt Injection 规则、Qwen3Guard-Gen-0.6B 语义分类、Jailbreak、Response Filter、独立 Risk Engine |
| 审批与 Tool 治理 | Human-in-the-Loop、工单状态机、ToolAction 状态机、Transactional Outbox、业务幂等、自动对账、Retry/DLQ、补偿、Policy 回放 |
| 可观测 | OpenTelemetry、LangSmith、Prometheus、Grafana |
| 部署与验证 | Docker、Docker Compose、Kubernetes manifests、pytest、GitHub Actions |

未使用或未实现的技术包括：MCP、pgvector、独立 TaskState、动态 Planner、自动 Reflection Loop、多租户知识隔离、生产搜索后端、Prompt 版本灰度、通用分布式任务队列和旧 Graph 多版本恢复。

## 8. Agent 数量与节点分工

当前有 **6 个业务 Agent 节点、1 个确定性 Skill Selector 和 1 个 Approval Gate 控制节点**。六个 Agent 节点分别处理单个 LangGraph Workflow 中的不同阶段，并不是六个独立部署的模型服务。Skill Selector 和 Approval Gate 不调用 LLM，也不是自治 Agent。

| Agent | 这个节点负责什么 |
|---|---|
| Analyzer | 确定性 Prompt Injection/Jailbreak、PII 脱敏、Qwen3Guard 语义检测；规则生成候选，Jev 启用时优先复核，再按规则/LLM 分层回退，最后执行初始 Risk Engine 评估 |
| Skill Selector（控制节点） | 根据统一 Intent 选择有版本的 Skill，并确定可用 Tool、RAG 类别和必填信息 |
| Tooling | 调用受治理的业务工具，补充客户、订单和历史工单上下文，并检查工具返回的间接注入 |
| Retriever | 按知识库版本与类别进行 Hybrid RAG 检索，返回 citation，并在生成前检查文档间接注入 |
| Resolver | 汇总工单、RAG citation 和 Tool Context，生成客服草稿 |
| QA | 安全硬失败由规则短路；正向 Grounding 和非确定场景由 Jev 类型化评判，再按规则/LLM 分层回退，并执行输出泄露过滤 |
| Escalation | 调用 Risk Engine 生成最终风险结论，计算 SLA，判断升级与人工审批需求 |
| Approval Gate（控制节点） | 无需审批时结束；需要审批时 interrupt，人工决策后从原 Checkpoint Thread 恢复 |

当前没有独立 Planner、LLM Selector、Validator Agent 或 Reflection Agent。Skill Selector 是确定性控制节点；QA 负责检查回复；安全、工具和状态验证则由分层规则处理。

Resolver 在生成前保存 `resolution_evidence`，QA 的规则和 Jev / LLM 评判直接复用这份证据。它包含必要 Tool 事实和带编号、来源、版本的 KB 片段，不是整个 State 的 dump。回复优先说明当前状态、异常和下一步；默认输出上限 480 tokens，截断时最多用同一证据重写一次，仍失败则转人工。这些机制避免证据遗漏和半句话输出，但不代表已经验证了真实 Jev 在所有业务上的判断准确率。

系统已区分“无害但超出客服能力”与“业务回答或执行存在风险”。天气等明确主题先按规则处理，其余主题可由同一次 Jev 分类中的范围判断识别。确认范围外后直接生成能力说明，不查无关 Tool / RAG，不使用上一轮订单实体；QA 验证纯能力说明后正常结束。业务、安全、真实依赖故障和 unsupported business claim 仍执行原有人工升级规则，没有降低 QA、Risk 或 Jev 阈值。规则未覆盖且 Jev 不可用/不确定时继续走原有保守路径，尚不保证识别所有泛化问题。

## 8.1 Skill Framework

Skill Framework V1 共有 **6 个 Skill**：`refund_support`、`order_support`、`account_support`、`api_incident_triage`、`warranty_support`、`general_support`，覆盖全部 8 个 `IntentType`。`SkillDefinition` 保存版本、输入/输出 Schema、Tool Allowlist/Forbidden List、RAG 类别、必需槽位与最低角色；`SkillRegistry` 拒绝重复 Intent 注册，并为整份 Registry 生成内容 Hash。

选择结果写入 `AgentState` 和 LangGraph Checkpoint，并关联 OpenTelemetry Span/Metric、`AgentSkillSelection` 持久化记录与 Baseline Report。ToolRegistry 在 Handler 前校验 Skill 版本和 Allowlist，但不替代现有 Schema、RBAC、Risk、Approval Grant 与 Tool Governance。V1 共享同一份 Workflow，没有每 Skill 独立 Subgraph、动态 Skill 组合、LLM 选 Skill 或运行时插件加载。

## 9. Tool 数量与 Tool Calling

当前 ToolRegistry 中注册 **9 个 Tool**：

| Tool | 权限 | 当前用途 |
|---|---|---|
| 客户画像查询 | agent 及以上 | 返回客户等级和未结工单数量 |
| 订单历史查询 | agent 及以上 | 返回近期订单、状态和付款信息 |
| 历史工单查询 | agent 及以上 | 返回过去工单与处理结果 |
| 退款资格初筛 | manager 及以上 | 高风险 Mock 初筛；主 Workflow 不会自动调用 |
| 创建退款请求 | manager 及以上 | 高风险 Mock 写 Tool；只能使用已批准 Action 的一次性执行授权，主 Workflow 不会自动调用 |
| 物流查询 `shipping.get_shipments` | agent 及以上 | 返回配送状态、跟踪编号和异常跟进，供订单查询及取消请求参考 |
| 权益查询 `warranty.get_entitlements` | agent 及以上 | 返回保修或服务支持权益；无记录不等于拒保 |
| 账务查询 `billing.get_payment_invoices` | agent 及以上 | 返回支付、金额、币种和开票状态，不提供支付密钥 |
| 服务状态 `services.get_status` | agent 及以上 | 返回模拟 API 健康状态，不代表实时生产监控 |

共 8 个读工具和 1 个写工具。客户画像和历史工单在正常请求中调用；订单历史只在账单和订单意图下调用。新增工具分别按订单、账务、保修、API 故障意图选择，与已有查询并行执行，返回结果经过安全扫描后送入 Resolver。Skill 配置升级为 `v1.1`，只有对应 Skill 可以调用这些工具。每次调用经过 Schema、RBAC、策略门禁和 Resilience，并持久化脱敏审计。

高风险写 Tool 采用 Tool Governance V2.2：主状态机为 `proposed -> pending_approval -> approved/rejected -> queued -> executing -> succeeded/failed/unknown`。参数加密存储，payload 和创建时的 Policy 快照使用 HMAC 防篡改；每个写 Action 生成唯一业务幂等键；提议人不能自批；API 原子保存 `queued + Outbox`，Worker 使用数据库租约和乐观版本竞争消费。

写调用超时或 Worker 中断不会直接重试，而是进入 `unknown`，按相同幂等键调用 Mock OMS 查询接口自动对账。确认结果后补写成功/失败状态事件；暂无结果时只 Retry 对账查询，耗尽进入 DLQ 并转人工。成功 Action 支持主管显式发起幂等补偿。Policy 回放是 deterministic 离线校验，不调用 Tool/LLM。上述能力仍基于 Mock OMS 契约，不代表真实资金系统集成或 exactly-once 保证。

所有这些 Tool 当前均是本地 Mock Adapter。不得说成已经接入真实 CRM、OMS、工单系统，或能够执行真实退款、改订单、写 CRM 等操作。

知识库初始化现在有 16 篇文档：原有 4 篇不变，新增 12 篇中文演示售后手册。新增内容覆盖退款资料和进度、支付发票、取消订单、物流延迟及未收到、保修与破损退换货、账户恢复、API 排查和资料补全。中文精确召回使用双字滑窗；部署后需显式执行 `scripts/seed_kb.py` 入库，不随应用启动自动导入。

## 10. MCP

当前 **MCP 数量为 0**。系统没有 MCP Client、MCP Server、MCP Tool、MCP Resource 或 MCP Prompt 集成。

当前使用本地 ToolRegistry 管理业务工具。未来如对接真实外部系统，可评估 MCP，但在实现前不得声称项目采用了 MCP。

## 11. TaskState 与任务规划

当前没有独立 `TaskState`。LangGraph 使用单一 `AgentState` 传递工单输入、分类结果与置信度、Skill 版本/策略/Policy 快照、工具上下文、citation、回复草稿、QA、安全信号、`risk_level`、`risk_score`、`risk_reasons`、人工/自动化建议、降级等级与脱敏依赖事件、升级结论、token、成本和错误信息。

当前也没有独立 Planner 或动态任务分解。系统采用固定 Workflow，并根据安全结果、部门、意图和优先级做有限的规则路由。当前唯一的受限重新规划是：类别检索无结果时，保留知识库版本并放宽类别进行一次回退检索。

不得表述为：系统具备动态 Planner、子任务拆分、自动 Plan Revision、任务队列或自主多 Agent 协商。

系统已经实现 LangGraph Checkpoint + Durable Execution：本地使用 AsyncSqliteSaver，PostgreSQL 部署使用 AsyncPostgresSaver；高风险回复在 Approval Gate 调用 interrupt，人工决策后用相同 thread_id 和 Command(resume) 续跑。AgentExecution 持久化工单、审批、Agent Run、Trace、状态和恢复租约，应用启动时会扫描并续跑“决策已提交但 Graph 未完成”的执行。该能力只覆盖当前审批等待场景，不等同于通用异步任务队列。

## 12. Memory

系统已实现 Memory V1：将有界多轮历史、确定性摘要、显式业务实体和上一轮路由结果注入当前 Agent Workflow。

| 层次 | 当前事实 |
|---|---|
| SQL | `ConversationSession` 绑定客户归属，`ConversationMessage` 以追加为主保存消息，`ConversationMemorySnapshot` 保存摘要/实体/上一轮路由 |
| Redis | 可选缓存最近 12 条 final 消息，TTL 24 小时，必须与 SQL revision 一致 |
| 节点使用 | Analyzer 做显式指代续接，Retriever 使用历史 User 问题/实体，Resolver 注入受限会话上下文，QA 校验解析实体 |
| 安全 | 历史重新脱敏并检测 Injection；pending/rejected 草稿不进入 Prompt；Tool 仍重查实时业务事实 |
| 当前限制 | 无向量长期记忆、用户偏好学习、真实多租户身份接入和多轮 Evaluation Gate |

可以说“已实现受控的多轮短期 Memory”；不能说“已实现向量长期记忆”、“已完成多租户隔离”或“已通过真实多轮业务指标验证”。

## 13. Prompt

当前已实现 PromptOps / EvalOps V1：Analyzer、Resolver、QA 共用内容寻址 Prompt Bundle，支持不可变快照、模板变量校验、请求级版本绑定和并发实验隔离。AgentRun 的 `prompt_version` 保存实际 Bundle Hash，报告保存完整静态模板与节点 Hash，Trace 关联 Hash。

CLI 可将当前/候选版本在相同 100 条 Baseline 上成对回放，生成质量门禁与指标/Case Diff，保留实验快照、Policy Hash 和 Git 工作树指纹。staging 可使用 Mock 验证；production 晋级需要真实模型、当前版本对比、相同干净 Git Revision、完整 Dataset 和通过门禁，并禁止新增失败 Case。支持带操作人/原因的显式晋级与回滚。没有线上 A/B、自动回滚、独立留出集认证或人工校准的语义发布门禁。

| Prompt 阶段 | 当前约束 |
|---|---|
| Analyzer | 规则先生成高置信度候选；DecisionProvider 启用时由 Jev 优先输出封闭 `IntentType`，低置信度或故障时有候选则回退规则，无候选再调用原 LLM；各层统一使用 8 项 `IntentType` |
| Resolver | 只依据 Top-2 citation 和必要 Tool 字段生成最终客服回复，限制输入字符数与输出 token |
| QA | 确定性失败由规则短路；其余可用 Jev 一次输出 Grounding、完成度、citation、未授权承诺和人工建议，低置信度或故障时回退轻量 LLM Judge |
| 输出过滤 | 删除可能泄露内部角色、指令或工作流的内容 |

OpenAI 与 Azure OpenAI Provider 使用 `temperature=0.0`；默认 Mock Provider 用于离线可复现。可以表述 Prompt 已内容版本化、已有离线发布门禁；不能表述已灰度、已通过线上实验提升质量，也不能把 Mock 成对实验当作 Prompt 语义效果验证。

OpenAI-compatible Provider 支持主模型与 Fast Model 分离：Resolver 使用 `LLM_MODEL_NAME`，Analyzer 与 QA 优先使用 `LLM_FAST_MODEL_NAME`，并可通过节点级模型名覆盖。Fast Model 可配置独立 Base URL 与 API Key，例如接入 Qwen Turbo；未配置时回退主模型。

LLM 已禁用 SDK 内建重试，由统一 Resilience Executor 执行超时、瞬时故障有界 Retry 和进程内 Circuit Breaker。可选通过 `LLM_FALLBACK_MODEL_NAME / BASE_URL / API_KEY` 切换独立 OpenAI-compatible 备用模型。默认 Retry 上限为 1，Auth、Validation 和 Malformed Response 不重试。

DecisionProvider V1 位于 `src/decision/`，通过 Jev System One HTTP API 处理 `Choice / Score / Noul` 问题集。请求在出站前脱敏、过滤密钥/业务字段并限长；Trace、Metrics 和 `decision_records` 保存版本、置信度、Token、耗时与回退原因。Jev 结果不能绕过 Skill Selector、Tool Governance、Risk Engine 或 Approval Gate。当前默认关闭，未接入离线 Evaluation Judge，没有真实 Jev 效果数据。

## 14. 安全与 Risk Engine

Prompt Injection 不再只是英文关键词检测，当前实现为确定性多层检测：

1. Unicode NFKC 规范化，清理零宽字符与多余空白，同时识别分隔符混淆。
2. 组合中英文直接特征、操作与指令边界启发式、敏感对象提取与角色提权检测。
3. 受限解码 Base64 / URL-safe Base64 载荷，对解码内容再扫描。
4. 在 `user_input`、`tool_result` 和 `rag_document` 三类信任边界执行，同时防护直接与间接 Prompt Injection。
5. 返回 `risk_score`、`confidence`、`layers` 和不含敏感原文的 `signals`；命中后清空不可信上下文并直接转人工。

确定性规则未命中时，可选调用独立 `Qwen3Guard-Gen-0.6B` OpenAI-compatible 端点。该 Adapter 已接入 `user_input`、`tool_result` 和 `rag_document`，并将 `Safe / Controversial / Unsafe`、Categories、延迟与降级状态写入 AgentState 和 OpenTelemetry。`Unsafe` 或 `Jailbreak` 类别阻断自动化；`Controversial` 默认进入 Risk Engine 并要求人工处理。Guard 服务默认关闭，不得宣称已有生产运行指标。

独立 `RiskEngine` 综合安全分数、优先级、情绪、退款/拒付/投诉等高风险意图、Analyzer 置信度、QA、幻觉和 Workflow 错误。默认风险阈值为 `medium >= 0.4`、`high >= 0.7`、`critical >= 0.9`；Analyzer 低置信度阈值为 `0.65`，QA 阈值为 `0.8`。`high` / `critical` 要求人工处理，安全威胁额外阻断自动化。

`/chat` 与 `/suggest-response` 会返回 Analyzer 置信度、风险等级、分数和原因。Trace 和结构化日志记录风险字段，OpenTelemetry Metrics 记录最终风险评估数与分数分布。

可以说已实现“确定性规则 + Qwen3Guard-Gen-0.6B + Risk Engine”可降级链路；不能说 Guard 已在生产强制启用、阈值已经真实数据校准，或已建成策略中心与持久化安全事件平台。

## 15. 数据库

系统使用 SQLAlchemy Async 访问数据库。

| 环境 | 数据库 | 用途 |
|---|---|---|
| 本地默认 | SQLite | 降低启动门槛，支持无额外服务运行 |
| Docker Compose | PostgreSQL | 提供更接近生产的并发与连接池环境 |

持久化实体包括用户、工单、会话记忆、知识文档、回复审批记录、AgentRun、AgentSkillSelection、AgentRunLink、AgentExecution、FeedbackEvent、ToolAction、ToolActionControl、ToolActionEvent、ToolOutboxEvent 和 ToolInvocationAudit。AgentExecution 只保存业务关联、状态、租约和 Trace ID；Graph State 正文由 LangGraph Saver 的官方表保存。当前没有数据库迁移工具、读写分离、分库分表、ticket_status_events 审计表或多租户数据隔离。

Tool Governance V2.3 另外增加 `ToolBusinessRequest` 和 `ToolActionReview`：同一客户/订单的整单退款申请跨 Action 去重；Worker 续租并按领取版本阻止旧 Worker 写回；unknown/DLQ 核实任务关联工单，由主管填写外部凭证确认，不重复执行写工具，也不替代回复审批。PostgreSQL 多进程演练使用持久化模拟 OMS，包括并发创建、续租、强杀后的对账和旧版本保护；是否实际通过以该次 CI/脚本报告为准，不宣称已接真实 OMS 或 Exactly Once。

2026-10-09，提交 `c9d1bf0` 的 [CI 运行](https://github.com/sulik0/supportgpt-customer-service-agent/actions/runs/37912408689) 中，`PostgreSQL Tool Governance Fault Drill` 任务成功，以上四项演练已在 PostgreSQL 16 上实际通过。该次本地全量测试为 344 项通过，固定 100 条 Mock Baseline 为 100/100；这些结果不等于真实退款业务验收。

## 16. Redis

Redis 是可选组件，不是系统启动或处理工单的强依赖。

- Redis 配置可用时，缓存会话最近 12 条 final 消息，TTL 为 24 小时，Key 不包含原始客户/会话标识。
- Redis 未配置、连接失败、Cache Miss 或 revision 不一致时，主流程继续运行并回退 SQL。
- Docker Compose 会启动 Redis；本地默认配置不要求 Redis。

不能说 Redis 是唯一的记忆存储，或说项目必须依赖 Redis。目前 Redis 不用来实现分布式锁、任务队列、Checkpoint 或限流。

## 17. Embedding

系统通过 Embedding Provider 生成知识库分块和查询向量。

| Provider 模式 | 当前 Embedding 事实 |
|---|---|
| 默认 Mock 模式 | 使用稳定的 1536 维 Mock 向量，保证本地可复现 |
| OpenAI 或 Azure 模式 | 使用 OpenAI `text-embedding-3-small` |

当前不使用本地训练 Embedding 模型、向量微调、Embedding A/B 评测或多向量检索。

## 18. RAG

当前 RAG 为 ChromaDB 上的 Hybrid RAG：

1. 知识文档被解析、切分并写入 ChromaDB，同时保留知识库版本和类别 metadata。
2. 检索时使用工单主题与描述构造查询，必须限定 `kb_version`。
3. 系统优先按业务类别过滤；没有结果时，保留版本并放宽类别再查询一次。
4. 系统融合向量召回、进程内 BM25 风格关键词打分和轻量 rerank。
5. 最终候选在交给 Resolver 前扫描间接 Prompt Injection；命中时清空 citation 并转人工，未命中时返回最多 3 条 citation，每条包含来源、文本、分数和版本。

当前默认分块参数为 600 字符、120 字符 overlap。ChromaDB 是当前向量数据库；系统没有使用 pgvector、OpenSearch、Elasticsearch、Milvus、Pinecone、Cross-encoder 或 LLM Reranker。

可以说“实现 Hybrid RAG、版本/类别过滤、citation 与轻量 rerank”；不能说“已实现生产级搜索集群、多租户 RAG 隔离或训练型 Reranker”。

## 19. 评测体系

项目包含两层质量评估：

| 层次 | 当前事实 |
|---|---|
| 在线 QA | 每次正常草稿生成后评估 QA 分数、幻觉风险和输出泄露；低分或幻觉触发审批 |
| 离线评测 | 基于 Dataset + Workflow Replay；提供 RAGAS、DeepEval、确定性 Security Evaluator 和本地启发式指标的统一适配入口 |
| 评测指标 | RAG 指标、Agent 行为指标、安全 TP/FP/TN/FN、Precision、Recall、F1、误报率和安全处置正确率 |
| 当前通过阈值 | 综合质量分数 `>= 0.75` 且 Hallucination Rate `< 0.35` |
| 报告 | 统一生成 JSON / Markdown 的 RAG + Agent + Security Evaluation 报告，并记录 Trace ID |

当前有 13 条 Synthetic Golden Dataset，并有一组 100 条 Baseline Dataset，其中 14 条攻击样本和 86 条非攻击样本可形成安全混淆矩阵；非攻击样本中包含 6 条安全语义 hard negative。统一报告包含 citation hit rate、RAG 指标、Agent 行为指标、安全检测与处置指标、用例 Pass/Fail、Workflow Path 与 Trace ID。Baseline V1 已建立可执行的 PR/Release 质量阈值，但尚无人工标注的生产标准答案或真实线上评测数据。无 API Key 时的本地 RAG / Agent 评测是确定性启发式降级，安全指标本身为确定性断言。

Baseline V1 是独立的真实回放报告：固定读取上述 100 条数据，逐条构造完整 Ticket State，Case Pass 只检查 Intent、Department、Required/Forbidden Tool、HITL 和 Approval。reference answer、expected priority、expected nodes、安全标签等字段原样保留但不参与本版判定。性能数据与当前 OTel / LangSmith Trace 同源，包含端到端及五个关键节点的 Average / P50 / P95、Input / Output / Total Token、模型、Analyzer Rule Hit Rate、LLM 调用次数和每条 Case 的 Agent Trace ID。

报告版本策略是“时间戳快照 + latest 普通文件副本”：每次运行保留独立 JSON / Markdown，latest 原子替换为最新内容，便于 Typora 等工具直接打开。实验配置固定包含 Dataset 名称、版本与 SHA256、启用/忽略指标、Workflow/Prompt 版本、Resolver/Analyzer/QA 模型、Token/Context 限制、Risk 阈值和 OTel/LangSmith 项目配置。旧单条评测不再堆积在报告根目录，而是隔离保存并只保留最近 20 份；运行报告目录不提交 Git。

每次 Baseline 完成后还会读取本次时间戳 JSON，生成同 Run ID 的 `error_analysis_<run_id>.md` 和 `error_analysis_latest.md`。该后处理只分析 FAIL Case，包含 Failure Breakdown、Intent Confusion Matrix、HITL/Approval mismatch、Tool 问题以及逐 Case Expected/Actual/Trace；过程纯离线、deterministic，不调用 LLM，不重放 LangGraph，也不修改 Dataset 或 Agent。

项目已提供真实 LLM Regression 专用入口：`smoke` 固定选取 12 条并预计 27 次 Workflow LLM 调用，`full` 运行 100 条并预计 258 次；入口拒绝 Mock，需要显式 `--confirm-live`，并在报告中记录 Provider、Model、Endpoint Host、Token、成本和延迟。2026-08-26 首次 DeepSeek + Qwen 真实 100 条 Baseline 为 Case Pass `0.54`、平均 `2.50s`、P95 `4.15s`、平均总 Token `715.59`、LLM Calls `217`；2026-08-30 在完全相同 Dataset SHA256 下复测达到 Case Pass `0.99`、平均 `1.62s`、P95 `3.24s`、平均总 Token `453.29`、LLM Calls `87`。这些是特定 Dataset、Prompt、模型和阈值组合下的离线实验结果，不代表生产流量质量。

CI/CD 采用两级门禁：PR/Push 强制使用 Mock Provider 回放固定 100 条 Workflow，要求六项行为指标全部达到确定性目标且无新失败 Case；Release Gate 需要人工确认付费调用，使用真实 LLM 完整回放，固定检查行为、P95、Token、LLM Calls 和 Analyzer Rule Hit Rate。仅 Release Gate 通过的同一 Git SHA 可由 CD 发布到 GHCR；当前没有自动部署到生产 Kubernetes 集群。

## 20. Feedback Pipeline

第一阶段已可以收集用户反馈和人工修改，并导出经脱敏处理的训练候选数据：

- 用户咨询页通过 `POST /support/requests` 创建唯一工单并立即执行 Workflow；普通请求返回安全回复，需要审批时返回安全的占位回复和人工介入类别，不向用户暴露 Tool、QA、内部风险规则、Trace 或未经审批的草稿。
- 客服员工后台通过受 RBAC 保护的 `GET /staff/review-queue` 仅加载待审批工单；打开详情调用 `GET /tickets/{ticket_id}/agent-result` 读取最新持久化结果，不会重新执行 Agent，也不会新增 Ticket、AgentRun 或审批记录。
- 用户通过 `agent_run_id + feedback_token` 提交评分；数据库只保存 Token 的 SHA-256 摘要，每个 Run 只接受一条不可变用户反馈。
- 人工审批的通过、修改和拒绝结果自动写入 `FeedbackEvent`；人工修改可形成 SFT 与 DPO 候选。
- 可信评测结果可关联 Agent Run；离线导入同时要求 Agent Evaluation 通过、citation 命中和 RAG 平均分达到 `0.75`。
- 导出脚本生成脱敏、去重、原子写入且权限为 `0600` 的 `sft_candidates.jsonl`、`dpo_candidates.jsonl` 和 `manifest.json`。
- `/chat` 与 `/suggest-response` 的 Feedback 采集使用独立事务并 fail-open；`/support/requests` 与工作台的 `POST /tickets` 将 AgentRun 作为详情结果来源，必须在 AgentRun 和审批关联写入成功后才返回成功，避免产生没有可读处理结果的成功响应。

本阶段只生成训练候选，不执行 SFT / DPO 训练，不包含 Dataset Registry、人工标注平台、训练任务编排、模型自动发布或 vLLM Serving。

## 21. 性能指标

### 当前已采集的指标

系统通过 OpenTelemetry Metrics 统一采集并经 Collector 导出到 Prometheus：

- HTTP 请求数量和请求延迟。
- Agent 节点执行耗时。
- LLM 输入/输出 token 与估算成本。
- QA 分数分布。
- 情绪分类计数、Guardrail 违规计数和工单升级计数。
- 最终 Risk Engine 评估数和风险分数分布。
- Feedback Event 和 SFT / DPO 候选导出计数。

LLM 延迟、Agent 执行次数和活跃会话指标已定义，但当前没有完整的更新逻辑，不能当作可用的实测监控数据。

OpenTelemetry Span 覆盖 HTTP 请求、Agent Workflow、各 Agent 节点、工具调用、RAG 查询与回退、审批创建和审批处理。

React 前端已拆分为用户咨询页与客服员工后台。用户页采用连续会话交互，并以当前浏览器保存的 Session ID 列表为能力凭据，从 SQL 查询最近 7 天历史；仅返回最终用户/Assistant 消息，待审或拒绝的草稿被替换为安全状态文案。员工后台仅加载待审批异常工单。`manager/admin` 还可分页或按工单编号查询 Agent Run，查看北京时间、Workflow Path、Trace ID、延迟、Token、QA、Tool 和 citation 摘要，并跳转配置的 LangSmith Project。前端不保存 LangSmith API Key，当前也不从 LangSmith API 回读 Span。

### 当前没有的性能数据

没有可长期引用的 P50、P95、P99 延迟，QPS、并发上限、吞吐量、RAG Recall、工具成功率、缓存命中率或成本预算实测数据。

历史文档中出现过单次 Mock Workflow 的本地示例延迟；该值受机器、数据、Provider 和运行环境影响，不是基准测试结果，不得作为性能指标对外引用。

## 22. 上线指标

当前**没有上线指标**，原因是项目没有已证实的真实生产部署、真实客户流量、真实 SLA、真实工单量、真实审批率或真实业务转化数据。

不得声称：

- 已上线到真实客服中心。
- 已服务真实客户或处理真实订单。
- 提升了首问解决率、客服效率、满意度或人工成本。
- 达到某个真实 SLA、可用性或业务增长指标。

可以说：Docker Compose 和 Kubernetes manifests 提供了本地或生产风格部署基础，但不代表已生产发布。

## 23. 已知问题与解决方案

| 已知问题 | 当前事实 | 当前解决方案 | 不应夸大的内容 |
|---|---|---|---|
| Python 3.13 下 pytest 崩溃 | 旧 `.venv` 曾混装 Evaluation 与不兼容 LangGraph 依赖，可以 `exit code 139` 退出 | 核心版本已固定；2026-09-08 当前环境 231 条全量测试通过，PR Agent Quality Gate 通过；CI / Docker 使用 Python 3.11 | 不要把旧环境崩溃解释为业务断言失败，也不要声称所有可选 Evaluation 依赖已完成全量兼容验证 |
| ChromaDB 本地 schema 不兼容 | 其他 ChromaDB 大版本写入的旧持久化目录不能保证反向兼容 | 本地默认使用 `.runtime/chromadb-0.5` 版本化目录，必要时重新执行 `seed_kb.py` | 不要说 ChromaDB 任意版本间可原地升降级 |
| Redis 不可用 | Redis 是可选组件 | 自动回退 SQL 历史 | 不要说 Redis 已高可用或具备集群容灾 |
| 类别检索无结果 | 分类可能不完全匹配知识类别 | 保留版本，放宽类别回退一次 | 不要说已实现通用检索重试或生产级召回保证 |
| 工具、LLM、RAG 或 QA 异常 | 外部能力或 Provider 可能失败 | 统一故障分类、有界 Retry、进程内 Circuit Breaker、LLM/RAG Fallback、安全降级与人工审批；高风险写调用不重试，专用 Tool Outbox 只 Retry 幂等对账 | 不要说已实现分布式 Breaker、通用消息平台或生产故障演练 |
| Memory 上下文污染 | 历史可携带 PII、Injection 或未审草稿 | 重新脱敏/扫描，仅 final 消息入 Prompt，审批后幂等回写 | 不要把 Memory 当作可信指令或实时业务事实 |
| Tool 高风险写入的不确定结果 | 写 Action、Outbox、调用审计和状态事件已持久化；不确定结果进入 `unknown` | 业务幂等键 + 自动 Reconciliation Worker 查询 Mock OMS，确认后补写状态；查询 Retry 耗尽进入 DLQ/人工 | 不要说已接真实 OMS、实现跨系统 exactly-once 或通用 Saga 平台 |
| Collector 或下游不可用 | 应用通过 OTLP 统一上报 | 遥测 fail-open，业务继续；本地启动前检不可达时跳过 exporter，Collector 恢复后重启 Backend 恢复上报 | 不要说当前已有 Collector 高可用或 Trace 持久化兜底 |
| Feedback 新表迁移 | 当前使用 SQLAlchemy `create_all` 创建新表 | 本地可直接运行；生产发布前补 Alembic migration | 不要说已经具备生产 Schema Migration |
| 多层安全检测覆盖边界 | 确定性规范化、特征、启发式和编码载荷，再接 Qwen3Guard 语义分类 | 输入、Tool、RAG 命中 Unsafe 时阻断，Guard 失败时隔离外部上下文并转人工 | 不要说默认已启用 Guard 服务或已建成完整攻防平台 |
| Risk Engine 阈值 | 默认阈值可通过环境变量配置，但尚无真实运营数据校准 | high / critical 保守转人工，安全威胁阻断自动化 | 不要说阈值已用生产样本训练或自适应调优 |
| Jev DecisionProvider | 线上 Adapter、脱敏/限长、置信度门禁、Trace/Metrics 和 LLM Fallback 已实现 | 默认关闭，不可用时回退，业务授权仍为确定性策略 | 不要说已有生产准确率/延迟数据、已接入离线 Judge 或 Jev 可自主授权 Tool |

## 24. 未来规划

以下均为规划，尚未实现：

1. 建设 Dataset Registry、训练集版本、人工复核状态、数据删除和保留周期，扩充 Synthetic Golden Dataset 并建立稳定基线。
2. 引入训练任务与模型 Registry，在人工门禁下消费 SFT / DPO 候选；模型优化尚未实现。
3. 增加 vLLM 自托管 Serving，并采集 TTFT、TPOT、吞吐、并发和 Token 成本；当前尚未实现。
4. 为知识文档与检索 metadata 增加 `tenant_id`，强制 `tenant_id + kb_version` 过滤，实现多租户隔离测试。
5. 抽象 `SearchBackend`，保留 Chroma 本地方案并设计 OpenSearch Hybrid Search 方案。
6. 增加 `ticket_status_events`，并将 Tool Governance V2.2 的 Mock 幂等/对账/补偿契约接入真实 OMS，补 Alembic Migration 和故障演练。
7. 完成客服工作台，展示工单、AI 草稿、Tool Context、citation、QA、风险原因与审批动作。
8. 在已完成 Prompt Registry、快照、离线门禁晋级与回滚基础上，补充独立留出集、语义评测校准和真实流量灰度。
9. 为 OpenTelemetry Collector 增加 Jaeger、Tempo 或其他 APM exporter，并完善采样、容量与高可用设计。
10. 将会话历史按受控方式注入 Agent 推理上下文，并补充隐私、长度控制和回归测试。
11. 启用 Qwen3Guard Shadow Mode，建设安全样本库、策略版本与持久化安全事件，用真实数据校准语义结果与 Risk Engine 阈值。
12. 以 Shadow Mode 校准 Jev 意图/QA 置信度阈值和故障率；离线 Jev Judge 暂不接入。

## 25. 长期一致性规则

未来任何回答都必须遵守以下规则：

1. 已实现、部分实现、规划中和未知信息必须明确区分。
2. 所有 CRM、OMS、Ticketing、退款初筛和默认 LLM 均为 Mock，除非代码与凭据明确变为真实集成。
3. Agent 数量固定表述为 6 个逻辑业务 Agent 节点 + Skill Selector + Approval Gate；Tool 数量为 9 个注册 Tool，其中 1 个为只能经审批 Action 执行的 Mock 高风险写 Tool。
4. MCP 数量为 0；独立 TaskState、动态 Planner、自动 Reflection 和 pgvector 均未采用。Checkpoint 已实现，但只覆盖固定 Workflow 的审批暂停与恢复；Tool Outbox 是高风险写操作专用队列，不是通用 Agent 任务队列。
5. Memory V1 以 SQL 结构化消息为事实源、Redis 为 revision Cache，有界历史已注入 Agent；它不等于向量长期记忆。
6. ChromaDB 是当前向量数据库；Hybrid RAG 是当前检索方案。
7. 项目没有真实生产上线数据、线上 KPI 或真实客户业务数据。
8. 团队人数和个人贡献归属没有仓库事实依据，必须由回答者的真实经历补充，不能推测。
9. 本文中的计数、阈值、组件和边界发生变化时，必须在同一提交中更新本文。
10. Feedback Pipeline 已输出训练候选，但 SFT / DPO 训练、模型 Registry、自动发布和 vLLM Serving 均未实现。
11. Prompt Injection 采用确定性多层检测 + Qwen3Guard 语义 Adapter，覆盖用户输入、Tool 返回和 RAG 文档；Guard 服务默认关闭，暂无真实运行分数。
12. Risk Engine 是独立规则模块，high / critical 转人工，安全威胁阻断自动化；阈值未经真实生产数据校准。
