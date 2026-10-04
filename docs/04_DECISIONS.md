# SupportGPT 智能客服 Agent 技术决策记录

> 本文记录项目已经确认的重要技术决策。每项决策均以当前代码与部署配置为准；“暂不采用”不代表永久否定，而是当前阶段的明确边界。

## 决策 1：采用 FastAPI 作为服务入口

### 问题背景

系统需要提供聊天、工单、审批、鉴权、评测和监控接口，并同时处理数据库、Redis、LLM、RAG 等 I/O。

### 候选方案

- FastAPI
- Flask
- Django / Django REST Framework

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| FastAPI | 原生异步、Pydantic Schema、自动 OpenAPI 文档、依赖注入完善 | 复杂后台管理能力不如 Django 完整 |
| Flask | 简单、生态成熟 | 异步与 Schema 需额外组合 |
| Django | ORM、Admin、权限生态完善 | 对当前轻量异步 Agent 服务偏重 |

### 最终方案

采用 FastAPI。

### 为什么选择

Agent 工作流涉及异步数据库访问、可选 Redis、外部 LLM 和检索调用。FastAPI 能以较少框架胶水代码提供异步接口、输入输出校验和 Swagger 文档。

### 工程权衡

选择 FastAPI 降低了 API 层复杂度，但后台运营管理界面和更复杂的企业权限能力需要额外开发，而不是从框架中直接获得。

## 决策 2：采用 LangGraph 编排 Agent Workflow

### 问题背景

客服请求必须依次经过安全检测、业务上下文补全、知识检索、回复生成、质量检查和升级决策，并需要明确的安全短路路径。

### 候选方案

- LangGraph 固定状态图
- 普通 LLM Chain
- ReAct 自主循环
- 自建状态机

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| LangGraph | 节点与条件边显式、便于 Trace、适合状态传递 | 需要维护共享 State 和图定义 |
| 普通 Chain | 开发快 | 条件路由、审计和失败定位不清晰 |
| ReAct | 灵活，可由模型选择步骤 | 工具调用与循环风险高，难以保证 QA 不被绕过 |
| 自建状态机 | 完全可控 | 需要自行实现图执行、状态合并和可视化能力 |

### 最终方案

采用 LangGraph 固定 Workflow：六个业务节点之间增加确定性 Skill Selector，末尾使用 Approval Gate，形成可暂停、可恢复的八节点图。

### 为什么选择

售后流程阶段稳定，安全与审批不可绕过。LangGraph 既比普通 Chain 更适合表达条件路由，也比自由 ReAct 更可控。

### 工程权衡

固定图限制了对开放式复杂任务的自适应能力，但换来稳定、可测试、可审计的执行路径。

## 决策 3：采用逻辑 Multi-Agent 分工，而非自治多智能体协商

### 问题背景

如果单一 Prompt 既做分类，又做检索、工具选择、回复生成和审查，发生问题时就很难找到原因，模型也可能跳过必要的安全步骤。

### 候选方案

- 逻辑 Multi-Agent：Analyzer、Tooling、Retriever、Resolver、QA、Escalation
- 单 Agent 大 Prompt
- 多个自治 Agent 互相协商

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 逻辑 Multi-Agent | 每个节点处理一部分工作，耗时和输出可单独查看，也方便测试 | 需要在节点间传递状态，并维护编排逻辑 |
| 单 Agent | 链路短、实现简单 | 可解释性差，难以独立治理安全和 QA |
| 自治协商 | 适合开放式复杂研究任务 | 调试困难、成本高、结果不稳定 |

### 最终方案

采用固定分工的逻辑 Multi-Agent Workflow。

### 为什么选择

这里的 Multi-Agent 是把工作拆成多个节点，不是让多个模型自由对话。客服流程需要稳定、可预测，因此不适合放开让模型自行协商。

### 工程权衡

节点边界使系统更清晰，但也导致部分上下文需要在共享 `AgentState` 中显式传递。

## 决策 4：使用单一 AgentState，不引入独立 TaskState

### 问题背景

系统需要在节点间传递工单、分类、工具、检索、生成、质量和升级信息。

### 候选方案

- 单一 `AgentState`
- `TaskState + ExecutionState` 双状态模型
- 事件流与事件溯源

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| AgentState | 轻量、与当前固定图匹配、节点输入输出直观 | 长流程下状态会膨胀 |
| TaskState + ExecutionState | 适合子任务、动态计划和恢复 | 需要状态迁移、版本与持久化设计 |
| 事件流 | 可追溯性强 | 基础设施和领域建模复杂度高 |

### 最终方案

当前只使用 `AgentState`，没有独立 `TaskState`。

### 为什么选择

当前工作流短且线性，没有动态子任务、异步长任务或中断恢复需求。

### 工程权衡

方案简单但不适合未来复杂 Planner。若引入长时调查任务，应再拆分 TaskState，而不是直接扩张现有 State。

## 决策 5：不引入独立 Planner，使用固定流程与规则路由

### 问题背景

用户要求可能涉及退款、物流、保修或安全风险，系统需要决定后续处理步骤。

### 候选方案

- 固定 Workflow + Analyzer 分类 + 规则路由
- LLM Planner 生成动态计划
- 人工预配置流程模板

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 固定流程 | 可预测、易审计、不会跳过关卡 | 灵活性有限 |
| LLM Planner | 可处理更多开放式任务 | 计划幻觉、循环、难以验证和恢复 |
| 流程模板 | 业务可配置 | 模板维护成本高，覆盖有限 |

### 最终方案

按固定流程处理请求，并根据意图分类结果选择对应分支，暂不单独引入 Planner。

### 为什么选择

客服处理路径稳定，高风险节点必须固定存在。动态计划的收益不足以覆盖其治理成本。

### 工程权衡

当前只能进行有限的 RAG 类别回退，不能自主重新规划。人工拒绝草稿后由人工重新处理，而不是让模型无限重试。

## 决策 6：采用确定性 Skill Selector 与版本化 Skill Registry

### 问题背景

系统需要按 Intent 配置不同版本的处理方式，记录每次使用的版本，并限制每类请求可以调用的 Tool。

### 候选方案

- 确定性 `IntentType -> SkillDefinition` Registry
- LLM Selector / Router
- 学习型策略模型
- 每个 Skill 独立 LangGraph Subgraph

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 确定性 Skill Registry | 可解释、可版本化、可测试、权限风险低 | 新增 Intent 需显式注册，V1 共享 Workflow |
| LLM Selector | 适应表达变化 | 可能误选高风险工具或错误路径 |
| 学习型模型 | 可根据数据优化 | 需要标注数据、线上反馈和治理机制 |
| 独立 Subgraph | 每个能力编排自治、扩展强 | 图版本、Checkpoint 迁移和测试成本高 |

### 最终方案

采用不调用 LLM 的 Skill Selector 控制节点，通过内容 Hash Registry 管理 6 个版本化 Skill。选择快照进入 State、Checkpoint、Trace、AgentRun 和 Evaluation；ToolRegistry 额外校验 Skill 版本与 Tool Allowlist。

### 为什么选择

能力选择直接影响 Tool 权限与业务风险。统一 Intent 已是稳定分类边界，使用唯一映射可在不增加 LLM 调用的情况下得到可复现结果。

### 工程权衡

V1 的 Skill 用来约束策略并记录每次选用的能力；它不是独立 Subgraph，也不能在运行时动态加载。这种做法不需要改动已有的退款、HITL、Resilience 和 Durable Execution 流程。

## 决策 7：采用 Tool Calling 与 ToolRegistry

### 问题背景

回复需要客户等级、订单状态和历史处理结果等结构化业务事实，同时必须防止越权调用和错误参数。

### 候选方案

- ToolRegistry 统一治理
- Agent 直接调用 Adapter
- LLM Function Calling 直接执行
- API 层预取全部上下文

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| ToolRegistry | Schema、RBAC、超时、Mock 标记和审计集中 | 需要维护工具定义 |
| 直接调用 Adapter | 代码少 | 容易绕过权限和审计 |
| LLM Function Calling | 模型可动态选择工具 | 需要额外的调用验证和副作用控制 |
| API 预取 | Agent 逻辑简单 | 可能读取不必要数据，缺少按意图治理 |

### 最终方案

采用 ToolRegistry，所有业务工具必须经由统一入口调用。

### 为什么选择

统一入口能把参数校验、角色权限、超时和审计做成系统约束，而不是依赖每个 Agent 自觉实现。

### 工程权衡

当前调用审计已持久化，高风险写 Tool 已强制独立审批 Action，并在 V2.2 增加业务幂等键、Transactional Outbox、自动对账与补偿契约；但工具仍是本地 Mock Adapter，不能被表述为真实企业系统集成。

## 决策 8：暂不采用 MCP

### 问题背景

MCP 可以用一套统一协议连接外部工具、资源和 Prompt。当前项目尚未接入真实外部服务，优先把本地客服流程做稳。

### 候选方案

- 本地 ToolRegistry + Adapter
- MCP Client + MCP Server
- 直接 REST / gRPC Client

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| ToolRegistry | 本地可复现、依赖少、治理逻辑集中 | 跨宿主工具发现与复用能力有限 |
| MCP | 协议标准化，适合跨应用工具集成 | 需要处理连接、鉴权、资源治理、协议版本和审计边界 |
| 直接 Client | 对接真实服务直接 | 每个服务的权限和异常逻辑容易分散 |

### 最终方案

当前不集成 MCP，使用 ToolRegistry + Mock Adapter。

### 为什么选择

项目没有需要接入的真实外部 MCP Server；先验证工具治理模型比提前引入协议基础设施更重要。

### 工程权衡

未来可将真实 CRM、OMS 或工单服务封装成 MCP Server，但 MCP 调用仍需经过权限、参数、超时和审计治理，不能成为绕过 ToolRegistry 的通道。

## 决策 9：默认采用 Mock LLM，Provider 支持 OpenAI 与 Azure OpenAI

### 问题背景

项目需在无 API Key、无网络和测试环境下稳定运行，同时保留接入真实模型的能力。

### 候选方案

- 默认 Mock LLM + Provider 抽象
- 默认强依赖 OpenAI
- 仅使用本地开源模型

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Mock + Provider | 可复现、低成本、测试稳定、可切换外部模型 | Mock 质量不代表真实模型表现 |
| 强依赖 OpenAI | 真实生成能力强 | 密钥、成本、网络和测试不稳定 |
| 本地模型 | 数据不出本地 | 部署和推理资源成本高 |

### 最终方案

默认 `LLM_PROVIDER=mock`，通过统一 Provider 接口支持 OpenAI 与 Azure OpenAI。

### 为什么选择

本项目是本地可演示的工程项目，默认可复现优先于默认模型能力。

### 工程权衡

任何基于 Mock 的质量指标都不能当作生产结果；真实模型接入后需重新评估 Prompt、成本、延迟与 QA 阈值。

## 决策 10：采用规则 + Qwen3Guard + Risk Engine 的分层 Guardrails

### 问题背景

客服输入可能包含 PII、Prompt Injection 和 Jailbreak，生成输出也可能泄露内部指令或工作流信息。

### 候选方案

- 确定性规则 + PII 脱敏 + 输出过滤
- 只使用独立语义安全分类模型
- 确定性规则 + Qwen3Guard-Gen-0.6B + Risk Engine
- 只依赖 System Prompt
- 仅人工审核
- 专用安全网关服务

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 多信任边界确定性规则 | 低延迟、可解释、可离线复现 | 语义变体和未知攻击覆盖有限 |
| 只用语义安全模型 | 语义覆盖更广 | 存在延迟、误判、服务失效与输出解析风险 |
| 规则 + Qwen3Guard + Risk Engine | 确定性快速拦截与语义检测互补，处置策略集中可审计 | 增加模型服务、调用延迟和阈值校准成本 |
| 只靠 Prompt | 实现最少 | 对攻击和提示泄露不可靠 |
| 仅人工审核 | 安全高 | 成本和响应延迟高 |
| 安全网关 | 可集中治理 | 引入额外服务和集成复杂度 |

### 最终方案

采用用户输入、Tool 返回、RAG 文档与生成输出的分层 Guardrails。在前三个不可信边界，确定性规则先扫描 Unicode/零宽字符、中英特征、组合启发式、角色提权和 Base64 载荷；未命中时，将脱敏与敏感字段过滤后的文本交给独立 Qwen3Guard-Gen-0.6B 服务。Risk Engine 将 `Safe / Controversial / Unsafe`、规则信号和业务风险融合为统一处置。

### 为什么选择

客户输入风险应在调用工具与业务模型前被拦截；Tool 和 RAG 结果也是不可信数据，必须在进入生成 Prompt 前检查。规则对已知攻击快速、可解释；Qwen3Guard 用小型多语言安全模型覆盖语义改写；Risk Engine 确保模型分类不直接决定高风险业务放行。

### 工程权衡

Qwen3Guard 为可选独立 OpenAI-compatible 服务，默认关闭，不影响本地 Mock 复现。启用后，正常请求最多新增用户、Tool、RAG 三次安全分类调用；明确规则命中会提前短路。`Unsafe` 或 `Jailbreak` 阻断自动化，`Controversial` 默认交给 Risk Engine 转人工。语义服务失效时，用户边界保留规则并降级，Tool / RAG 边界隔离未扫描内容。当前尚未使用真实业务攻击集校准误报率和延迟。

## 决策 11：采用 ChromaDB 作为当前向量数据库，不采用 pgvector

### 问题背景

系统需要保存知识库分块、Embedding 和 metadata，并支持本地开发与类别、版本过滤。

### 候选方案

- ChromaDB
- PostgreSQL + pgvector
- OpenSearch / Elasticsearch
- Milvus / Pinecone

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| ChromaDB | 本地集成简单、持久化目录、开发门槛低 | 大规模检索、运维和混合搜索能力有限 |
| pgvector | 与 PostgreSQL 事务和元数据统一 | 需要扩展配置与索引调优，词法检索仍需额外设计 |
| OpenSearch | 强大的 BM25、过滤与生产搜索能力 | 部署、索引和运维成本较高 |
| Milvus / Pinecone | 专业向量检索能力 | 引入外部服务或托管依赖 |

### 最终方案

当前采用 ChromaDB，未采用 pgvector。

### 为什么选择

ChromaDB 满足本地 Demo、版本过滤、向量召回和持久化的需求，能够在不启动额外数据库扩展的前提下跑通 RAG。

### 工程权衡

选择 ChromaDB 意味着向量数据与业务 SQL 数据分开管理；未来若需更高并发、统一存储或生产检索，可评估 pgvector 或 OpenSearch，但不能声称当前已使用 pgvector。

## 决策 12：采用 Hybrid RAG，而非纯向量检索

### 问题背景

客服政策常包含退款窗口、产品型号、订单词、政策编号等精确词，纯向量检索容易遗漏这些信号。

### 候选方案

- Chroma 向量召回 + BM25 风格词法召回 + 轻量 rerank
- 纯向量检索
- 纯 BM25
- Cross-encoder / LLM Reranker

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Hybrid RAG | 同时兼顾语义相似与精确匹配 | 需要合并候选和维护评分逻辑 |
| 纯向量 | 架构简单，适合语义表达 | 精确政策词召回不稳定 |
| 纯 BM25 | 精确词强、成本低 | 对同义表达和自然语言变体较弱 |
| Cross-encoder / LLM Reranker | 排序质量潜力高 | 延迟、成本和依赖更高 |

### 最终方案

采用向量召回、进程内 BM25 风格打分和轻量 rerank 的 Hybrid RAG。

### 为什么选择

该方案以较低复杂度解决客服精确规则问题，同时保留向量检索对自然语言表达的鲁棒性。

### 工程权衡

进程内词法搜索适合 Demo 和小规模知识库，不适合大规模索引。未来需抽象 SearchBackend 并考虑 OpenSearch 或训练型 Reranker。

## 决策 13：采用知识库版本与类别过滤，并设置类别回退

### 问题背景

售后政策会迭代，且不同业务部门的知识不应无差别混入回复上下文。

### 候选方案

- `kb_version` + `category` metadata filter，空结果时放宽类别
- 无过滤全库检索
- 仅版本过滤
- 固定业务知识库分库

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 版本 + 类别 + 回退 | 兼顾精度、灰度和召回兜底 | 分类错误时会多一次查询 |
| 全库检索 | 实现简单，召回高 | 容易引入不相关或过期政策 |
| 仅版本 | 控制版本 | 部门噪声较大 |
| 分库 | 隔离清晰 | 运维和跨域查询复杂 |

### 最终方案

先按知识库版本与部门类别过滤，类别为空时保留版本并放宽类别重试一次。

### 为什么选择

版本保证政策可回滚，类别提高相关性；单次回退防止分类误差导致零召回。

### 工程权衡

当前不支持 `tenant_id` 多租户隔离；版本与类别不是完整的数据权限方案。

## 决策 14：采用 SQLAlchemy Async，SQLite 本地默认、PostgreSQL 容器化部署

### 问题背景

系统需要保存工单、用户、会话、知识文档和审批记录，同时兼顾本地启动和生产风格部署。

### 候选方案

- SQLAlchemy Async + SQLite / PostgreSQL
- 仅 SQLite
- 仅 PostgreSQL
- NoSQL 数据库

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| SQLite + PostgreSQL | 本地门槛低，部署时具备事务与并发能力 | 需要兼容两种数据库行为 |
| 仅 SQLite | 零运维 | 并发、锁与生产能力有限 |
| 仅 PostgreSQL | 环境一致性高 | 本地 Demo 必须启动数据库服务 |
| NoSQL | Schema 灵活 | 工单状态与审批事务约束实现成本更高 |

### 最终方案

本地默认 SQLite，Docker Compose 使用 PostgreSQL，访问层统一使用 SQLAlchemy Async。

### 为什么选择

该方案平衡了本地可复现与生产风格架构；PostgreSQL 提供更合理的连接池和并发事务能力。

### 工程权衡

当前没有数据库迁移工具、读写分离、审计事件表或多租户数据隔离。

## 决策 15：Redis 作为可选短期记忆，SQL 作为持久化兜底

### 问题背景

会话需要保存最近消息，但本地 Demo 不能因 Redis 未部署而不可用。

### 候选方案

- Redis 短期缓存 + SQL 持久化历史
- 仅 Redis
- 仅 SQL
- 向量长期记忆

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Redis + SQL | 热数据读取快，缓存故障可回退 | 两层数据可能出现短暂不一致 |
| 仅 Redis | 延迟低 | Redis 故障或过期会丢失历史 |
| 仅 SQL | 简单、耐久 | 热会话读取延迟更高 |
| 向量记忆 | 可做语义长期召回 | 需要额外检索、隐私与摘要治理 |

### 最终方案

采用 Memory V1：SQL 的 `ConversationSession` / 追加为主的 `ConversationMessage` / `ConversationMemorySnapshot` 作为事实源，Redis 仅缓存最近 final 消息并设置 24 小时 TTL。每份 Cache 带 SQL revision，不一致即回退 SQL；旧 `SessionMemory` 数据在首次读取时兼容迁移。

### 为什么选择

缓存不能成为客服主链路的强依赖；同时历史在注入 Analyzer/Retriever/Resolver/QA 前必须有界截取、PII 脱敏和 Prompt Injection 复检。

### 工程权衡

V1 只实现有界短期 Memory 和确定性实体/摘要；不保存长期偏好，不做向量语义召回。待审草稿仅为 pending，审批通过/修改后才进入 final 历史，被拒绝草稿永不进入 Prompt。新表仍依赖 `create_all`，生产需 Alembic Migration。

## 决策 16：采用 LangGraph Checkpoint + Durable Execution

### 问题背景

人工审批会跨越原 HTTP 请求甚至应用重启。如果只保存 ResponseApproval 而不保存 Graph State，审批后只能结束在 Graph 外或从 Analyzer 重新执行，既浪费 LLM/RAG/Tool 成本，也可能产生重复副作用。

### 候选方案

- 无 Checkpoint，单请求内执行完 Workflow
- MemorySaver（仅进程内）
- SQLite / PostgreSQL 官方 Checkpointer
- Redis Checkpoint
- 专用工作流引擎

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 无 Checkpoint | 链路简单、无状态迁移负担 | 请求中断后不能恢复 Graph |
| MemorySaver | 接入简单，适合单元测试 | 进程退出即丢失 |
| SQLite / PostgreSQL Checkpointer | 官方集成、本地可复现、生产可耐久恢复 | 需要状态兼容、DDL、租约和清理治理 |
| Redis Checkpoint | 访问快 | 耐久性和清理策略需额外设计 |
| 工作流引擎 | 支持长事务与重试 | 基础设施与学习成本高 |

### 最终方案

使用官方 AsyncSqliteSaver / AsyncPostgresSaver 保存 LangGraph Checkpoint；本地默认独立 SQLite，PostgreSQL 部署默认复用业务数据库。每次业务执行使用稳定 UUID thread_id，并以 AgentExecution 关联 Ticket、ResponseApproval、AgentRun、Trace 和恢复状态。

### 为什么选择

Approval Gate 使用 interrupt 强制暂停，人工决策持久化后再以 Command(resume) 恢复原 Thread。这样审批是 Graph 内不可绕过的控制点，Analyzer、Tool、RAG、Resolver、QA 和 Escalation 不会重复执行。数据库恢复租约防止多个 Worker 重复续跑，启动扫描和主管恢复 API 处理决策已提交但续跑未完成的情况。

### 工程权衡

方案增加了 Checkpoint 表、AgentExecution 状态机、Graph 版本兼容和清理责任。当前仅对 Human-in-the-Loop 等待实现自动 Durable Resume，不等同于通用异步任务平台；尚无 Checkpoint TTL/归档、旧 Graph 多版本恢复、Queue/DLQ，新增业务表和 Saver DDL 也仍需纳入 Alembic。

## 决策 17：采用 Review Agent 与 Response Filter，不采用自动 Reflection Loop

### 问题背景

生成回复可能缺乏依据、产生幻觉或泄露内部信息，需要独立检查。

### 候选方案

- QA Review Agent + Response Filter + 人工升级
- Resolver 自检
- 自动 Reflection 后重写
- 人工全量审核

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 独立 Review | 生成与审查分离，结果可观测 | 增加一次模型调用或规则处理 |
| 自检 | 链路短 | 同一模型容易放过自身错误 |
| Reflection Loop | 有机会改善低质量表达 | 容易循环、增加成本，可能反复生成错误事实 |
| 全量人工 | 风险最低 | 效率低，无法体现自动化价值 |

### 最终方案

采用 QA Review Agent 与 Response Filter；低分或幻觉进入 Human-in-the-Loop，不采用自动 Reflection Loop。

### 为什么选择

在客服政策与退款等场景中，遇到质量风险时优先人工接管比让模型反复自改更可靠。

### 工程权衡

这种方式可自动处理的情况比自动重写少，但风险、成本和处理结果都更容易控制。

## 决策 18：根据风险决定是否转人工处理

### 问题背景

高风险回复不能仅由模型决定，且 AI 草稿需要和真实工单生命周期一致。

### 候选方案

- 根据风险判断是否审批，用明确的状态机记录处理进度
- 所有回复自动发送
- 所有回复人工审核
- 直接修改工单状态字段

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 风险审批 + 状态机 | 平衡效率与安全，阻止非法流转 | 需要审批队列和状态规则 |
| 全自动 | 延迟低 | 退款、安全和低置信度风险高 |
| 全量人工 | 风险低 | 人力成本与处理延迟高 |
| 直接改状态 | 实现简单 | 容易产生审批前关闭等非法状态 |

### 最终方案

通过独立 Risk Engine 统一综合安全、优先级、情绪、高风险业务意图、Analyzer 置信度、QA、幻觉和 Workflow 错误。`high` / `critical` 触发人工审批，安全威胁额外阻断自动化；工单状态只能沿合法状态机流转。

### 为什么选择

如果每个 Agent 都自己书写 if/else 判断，很容易出现各自阈值不一致的情况。Risk Engine 把这些判断集中到一处：低风险问题可以自动处理，高风险问题由人工最终确认。

### 工程权衡

阈值已通过 `RISK_*` 环境变量集中配置，但未基于真实运营数据校准、无策略版本和灰度机制；状态事件尚未单独持久化。

## 决策 19：采用统一有界 Resilience，而非无差别自动 Retry

### 问题背景

网络、检索、工具和模型可能失败，但客服和高风险动作不能发生无限重试或重复副作用。

### 候选方案

- 按故障类型的有界 Retry + Circuit Breaker + Fallback + 人工接管
- 无差别自动 Retry
- Circuit Breaker + Queue + Dead Letter Queue 的完整分布式方案
- 失败即终止

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| 有界 Resilience | 故障分类、次数、退避、熔断和降级统一可观测 | 增加状态与策略配置复杂度 |
| 通用 Retry | 对瞬态故障友好 | 可能重复调用工具或反复消耗 LLM 成本 |
| 完整韧性体系 | 适合生产外部依赖 | 引入幂等、队列和运维复杂度 |
| 失败即终止 | 实现最简单 | 客户体验和可用性差 |

### 最终方案

LLM、RAG 与 Tool 统一经过 Resilience Executor。仅 Timeout、Rate Limit、Connection 和 Server Error 进行默认一次有界 Retry；LLM 可选切换 OpenAI-compatible 备用模型；Hybrid RAG 向量/词法单路失败时使用另一路；仅低风险读 Tool 可重试，高风险或非幂等写操作禁止重试。降级事件进入 AgentState、Risk Engine、Trace 和 Metrics。

### 为什么选择

当前工具以读操作和 Mock 为主，但真实 Provider 的瞬时失败需要有界自恢复。通过 Operation Type 和 Risk Level 限制 Retry，比全局统一重试更安全。

### 工程权衡

当前 Circuit Breaker 为单进程内状态，多副本不共享；通用 LLM/RAG/Tool 调度没有消息队列。V2.2 只为受治理写 Tool 增加数据库 Outbox、Retry/DLQ、业务幂等键和结果对账。`asyncio.to_thread` 超时后不能强制杀死底层线程，因此写调用仍不重试，只重试幂等对账查询。

## 决策 20：采用 Prometheus + OpenTelemetry 双层可观测

### 问题背景

需要既能了解整体延迟、成本和风险趋势，也能定位单次请求在 Agent、工具、RAG 或审批环节的耗时。

### 候选方案

- Prometheus Metrics + OpenTelemetry Trace
- 仅日志
- 仅 Prometheus
- 仅 Trace

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Metrics + Trace | 趋势分析与单请求诊断互补 | 需要维护两类观测数据 |
| 仅日志 | 简单 | 聚合、告警和链路关联困难 |
| 仅 Metrics | 适合趋势 | 无法定位单次调用路径 |
| 仅 Trace | 适合排障 | 不擅长长期聚合和告警 |

### 最终方案

采用 OpenTelemetry 统一采集请求、节点、token、成本、QA、Guardrail 等 Metrics，并串联 API、Workflow、LLM、工具、RAG 和审批 Span；通过 OTLP Collector 分别转发 LangSmith 与 Prometheus。

### 为什么选择

Agent 系统既需要运营指标，也需要排查“哪一步慢、哪一步失败”的请求级证据。

### 工程权衡

统一 Collector 降低应用侧多套 SDK 的维护和脱敏成本，但 Collector 成为需要监控与容量规划的基础设施；Tool 业务审计另行持久化到 SQL，不依赖 Trace 后端作为唯一证据。

## 决策 21：采用 Docker Compose、分层依赖和 Python 3.11 CI

### 问题背景

项目要同时支持本地 Demo、测试、评测、负载依赖和容器化验证。

### 候选方案

- Docker Compose + requirements 分层 + Python 3.11 CI
- 单一 requirements 文件 + 本机运行
- 完整 Kubernetes 优先部署

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Compose + 分层依赖 | 本地组件可复现，运行时依赖更轻 | 维护多个依赖文件 |
| 单文件依赖 | 简单 | 测试、评测依赖污染运行环境 |
| Kubernetes 优先 | 接近生产 | 对当前项目的部署与调试门槛过高 |

### 最终方案

采用 Docker Compose 编排 backend、PostgreSQL、Redis 和 Prometheus；依赖分为 runtime、test、eval、load；CI 固定 Python 3.11，执行全量后端测试、前端构建、Agent PR Quality Gate 和容器构建验证。

### 为什么选择

该方案能保证主要路径可复现，也规避本机 Python 3.13 下部分 native dependency / pytest 插件崩溃问题。

### 工程权衡

Kubernetes manifests 已存在，但不代表完成生产发布。当前 CD 只在真实 LLM Release Gate 通过后将同一 Git SHA 的镜像交付到 GHCR，不会自动修改未授权的集群。

## 决策 22：采用 PromptOps / EvalOps V1，暂缓线上 A/B

### 问题背景

Analyzer、Resolver 与 QA 都依赖 Prompt，后续需要可追踪地比较 Prompt 变更效果。

### 候选方案

- Provider 内维护 Prompt
- Prompt Registry + 版本化
- 配置中心 + A/B 灰度

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Provider 内维护 | 简单，当前改动少 | 不易查清每个版本对回答效果的影响 |
| Prompt Registry | 可记录版本与元数据 | 需要存储、发布和回滚机制 |
| A/B 灰度 | 可基于指标优化 | 需要 Golden Set、流量和统计设计 |

### 最终方案

采用文件型内容寻址 Prompt Registry、请求级 Bundle 绑定、成对 Baseline 实验和门禁控制的环境指针；支持显式晋级、回滚与发布审计。暂不引入线上 A/B、自动回滚或分布式配置中心。

### 为什么选择

项目已有固定 100 条 Baseline、Diff 和 CI/CD 门禁，复用它们即可让 Prompt 变更与评测证据绑定。静态模板快照没有企业服务依赖，适合本地复现；production 拒绝 Mock 证据，避免把工程验证误作模型效果验证。

### 工程权衡

文件锁和原子指针适用于单机/统一发布目录，不提供跨节点分布式配置一致性。晋级需同一干净代码版本的成对真实评测；独立留出集、人工语义校准、签名制品和流量灰度仍需后续建设。

## 决策 23：采用 RAGAS / DeepEval Adapter 与本地评测降级

### 问题背景

RAG 质量不能只依靠主观体验，需要评估 Faithfulness、Context Precision / Recall、Answer Relevance 和 Hallucination 风险。

### 候选方案

- RAGAS / DeepEval Adapter + 本地启发式降级
- 强依赖云端 LLM Judge
- 仅人工抽查
- 暂不做评测

### 优点与缺点

| 方案 | 优点 | 缺点 |
|---|---|---|
| Adapter + 本地降级 | 无 API Key 也能跑通评测管道 | 本地指标不等价于真实 LLM Judge |
| 强依赖云端 Judge | 指标能力完整 | 成本、密钥与网络依赖高 |
| 人工抽查 | 贴近业务 | 难以自动化回归 |
| 不评测 | 开发快 | 无法量化质量变化 |

### 最终方案

采用 RAGAS / DeepEval Adapter，并在无可用 API Key 时回退到本地确定性指标。

### 为什么选择

项目需要同时具备离线可运行性和后续接入真实评测框架的能力。

### 工程权衡

当前尚无人工标注 Golden Set，RAGAS 的简化 Ground Truth 也不是标准答案。因此不可宣称已有生产级评测结果。

## 决策 24：采用 PR / Release 两级 Evaluation Quality Gate

### 问题背景

单元测试只能验证局部代码，不能证明固定 Dataset 上的完整 Agent 行为没有回归；但在每个 PR 上调用真实 LLM 会引入成本、网络依赖和非确定性。

### 候选方案

- 只跑 pytest
- 每个 PR 都跑真实 LLM Baseline
- PR Mock Workflow Gate + 手动 Real-LLM Release Gate
- 只生成报告，不阻断发布

### 最终方案

PR/Push 对固定 100 条 Dataset 执行 Mock Provider 完整 Workflow Replay，要求六项行为指标达到确定性目标且无新失败 Case。Release Gate 由人工显式确认付费调用，使用真实 LLM 报告检查行为、P95、Token、LLM Calls 和 Analyzer Rule Hit Rate。门禁策略作为 JSON 进入 Git，报告作为 Actions Artifact 保留。

### 为什么选择

两级设计使每次变更都有免费、快速、可重复的端到端保护，同时把真实模型质量与成本验证放到有人工授权的发布边界。失败 Case 白名单防止相同聚合分数下的 `PASS→FAIL` 被掩盖。

### 工程权衡

Mock Gate 只验证确定性路由和治理逻辑，不代表真实生成质量或延迟。Release Gate 仍受外部模型波动影响，因此使用绝对下限、性能上限、固定 Dataset Hash 与显式已知失败组合管理，不用单次 LLM Judge 分数直接发布。

## 决策 25：CD 只发布通过 Release Gate 的不可变镜像

### 问题背景

如果评测与镜像构建使用不同 Revision，质量结论不能证明交付物。直接使用 `latest` 也无法稳定回滚。

### 最终方案

CD 只监听成功的 `Release Quality Gate`，使用该 Workflow 的 `head_sha` 构建容器，同时发布 `latest` 和 `sha-<commit>` Tag 到 GHCR，并生成 Build Provenance Attestation。

### 工程权衡

当前没有具体生产集群与凭据，因此 CD 终止于受控镜像交付，不伪造自动部署。未来接入集群后，应使用不可变 SHA Tag 或 Digest 部署，并增加灰度健康检查与自动回滚。

## 决策 26：高风险写 Tool 采用 Action 状态机与 Transactional Outbox

### 问题背景

退款等写操作具有业务副作用，仅依赖 Prompt、RBAC 或一个布尔审批字段，无法防止 LLM 误调、自批、并发重放和参数篡改。

### 候选方案

- 仅使用 RBAC 限制 manager
- 复用回复审批表
- 应用内确定性 Action 状态机
- 直接引入 Temporal / Camunda 等外部 Workflow Engine

### 最终方案

V2.1 新建 `ToolAction`、Append-only `ToolActionEvent` 和 `ToolInvocationAudit`。V2.2 增加 `ToolActionControl` 与 `ToolOutboxEvent`：Action 创建时冻结 Tool Policy 快照/HMAC 并生成业务幂等键；审批后的 API 事务只写入 `queued + Outbox`，Worker 再按数据库租约和乐观版本执行。主路径为 `proposed -> pending_approval -> approved/rejected -> queued -> executing -> succeeded/failed/unknown`。

`unknown` 状态的写操作必须先使用原有幂等键查询外部系统，再进入 `reconciling`。对账成功或失败后会补写状态记录；暂时查不到结果时按指数间隔重试，超过次数上限就进入 DLQ 交由人工处理。主管可对已成功的 Action 单独发起幂等补偿。Policy 回放会根据历史快照检查版本、意图、审批人分工、payload HMAC 和幂等键，不会调用外部系统。

### 为什么选择

当前只有少量高风险动作，应用内状态机可以用较小依赖建立不可绕过的安全边界，并与现有 FastAPI、SQLAlchemy、RBAC 和 OTel 直接集成。

### 工程权衡

Action 参数使用 Fernet 加密，HMAC 用于 payload 与 Policy 快照完整性校验，审计与 Outbox 不保存原始参数。数据库租约 + `version` Compare-and-Set 支持多 Worker 竞争，不额外引入 Redis 分布式锁；Retry/DLQ 复用 Outbox 表以降低基础设施成本。该设计提供 at-least-once 投递与业务幂等，不宣称跨系统 exactly-once。当前 OMS 是进程内 Mock，新表仍依赖 `create_all`，生产需补 Alembic Migration、真实 OMS 契约测试和数据保留策略。

## 决策 27：封闭语义判断采用可回退的 DecisionProvider

### 问题背景

Analyzer 意图分类和 QA Review 本质上是有限选项判断。直接使用 Chat LLM 会产生长 Prompt、JSON 解析和额外 Token，但完全交给外部决策模型又会引入可用性与阈值风险。

### 候选方案

- 继续使用 Analyzer / QA 轻量 Chat LLM。
- 仅用确定性规则。
- 直接将 Jev 嵌入各节点。
- 增加可替换 `DecisionProvider`，保留安全硬规则和分层 Fallback。

### 最终方案

建立 `DecisionProvider` 和领域 `DecisionService`。V1 通过 Jev System One HTTP API 合并提交版本化 `Choice / Score / Noul` 问题；启用后，Analyzer 用 Jev 复核规则候选，QA 将正向 Grounding 与非确定校验交给 Jev。Jev 不可用、响应非法、State 超长或低置信度时，有可验证候选则回退规则，否则回退原有 LLM。

### 为什么选择

这个边界将“语义建议”和“业务授权”分离。Jev 只输出类型化信号，Intent Taxonomy、Skill Selector、Tool Governance、Risk Engine 与 Approval Gate 继续由确定性代码约束。

### 工程权衡

外部请求增加脱敏、限长、超时、Circuit Breaker、Trace 和 Metrics。为避免与当前 LangChain 的 `tenacity<9` 约束冲突，V1 使用已有 `httpx` 调用官方 System One HTTP 协议，不引入强制 `tenacity>=9` 的 SDK。当前默认关闭，需 Shadow 运行和人工校准后才能调整阈值；且按要求暂不接入离线 Evaluation Judge。

## 决策总览

| 领域 | 最终决策 | 当前边界 |
|---|---|---|
| Agent 编排 | LangGraph 固定工作流 + Skill Selector + Approval Gate | 无动态 Planner / 自治协商；V1 Skill 共享 Workflow |
| 状态 | 单一 AgentState + SQLite/PostgreSQL Checkpoint + AgentExecution | 无独立 TaskState；无 TTL/旧 Graph 兼容 |
| 工具 | ToolRegistry + Mock Adapter + Tool Governance V2.2 | 无 MCP；写 Tool 仍为 Mock，但已有业务幂等、Outbox、自动对账、Retry/DLQ、补偿与 Policy 回放契约 |
| 模型 | Mock 默认，OpenAI / Azure 可选 | 无真实生产模型效果承诺 |
| 决策模型 | 规则 + 可选 Jev DecisionProvider + LLM Fallback | 默认关闭；未接入离线 Judge，未经真实数据校准 |
| 安全 | 用户 / Tool / RAG 规则 + Qwen3Guard + Risk Engine，输出 Filter + QA | 语义安全默认关闭，尚无真实业务攻击集校准与生产可用性数据 |
| RAG | ChromaDB Hybrid RAG | 无 pgvector、无生产搜索后端 |
| 数据 | SQLite 本地、PostgreSQL Compose | 无迁移、读写分离、多租户 |
| 记忆 | Redis 可选 + SQL 兜底 | 历史未注入生成 Prompt |
| 审批 | 独立 Risk Engine + HITL + 状态机 | 阈值可配置但未用生产数据校准，状态事件未持久化 |
| 恢复 | LLM/RAG/Tool 有界 Retry + Checkpoint/HITL Durable Resume + Tool Outbox/Reconciliation | 无分布式 Breaker、通用 Queue/DLQ 和 Checkpoint 清理；专用 Tool Queue/DLQ 已实现 |
| 观测 | OpenTelemetry + OTLP Collector + LangSmith / Prometheus | Collector 尚未高可用，未接 Jaeger / Tempo |
| 评测 | Adapter + 本地降级 + 固定 100 条 Baseline | 无人工标注生产 Ground Truth 和真实线上基线 |
| 发布治理 | PR Mock 100 Case Gate + Real-LLM Release Gate | V1 只以六项确定性行为指标决定 Case Pass，语义质量尚未纳入门禁 |
| CD | Release Gate 通过后发布同 Git SHA 镜像到 GHCR | 尚未部署到生产 Kubernetes 集群 |
