# 工程开发指南

> 本文档是 SupportGPT 的唯一工程操作手册，整合原 API、依赖、测试、部署、可观测、安全与 Feedback Pipeline 文档中仍然有效的内容。项目事实以 `00_PROJECT_CONTEXT.md` 和 `03_INTERVIEW_CANON.md` 为准。

## 环境要求

PromptOps / EvalOps V1 的注册、成对评测、晋级、回滚和权限边界见 [Prompt 治理操作](06_PROMPTS.md#promptops--evalops-v1-操作)。默认 Registry 在 `.runtime/promptops`，不提交 Git；需随部署单独备份/交付，生产晋级在同一干净 Git Checkout 执行。

- 推荐 Python 3.11，CI 与 Docker 也使用 Python 3.11。
- Node.js 用于 Vite + React 前端。
- 默认使用 SQLite、Mock LLM、本地 ChromaDB，Redis 可选，因此无外部服务也能运行。
- PostgreSQL、Redis、OpenTelemetry Collector、Prometheus 和 Grafana 由 Docker Compose 提供。

依赖按用途分层：

| 文件 | 用途 |
|---|---|
| `requirements.txt` / `requirements/base.txt` | 后端核心运行时 |
| `requirements/test.txt` | pytest 与 CI |
| `requirements/eval.txt` | Ragas、DeepEval 离线评测 |
| `requirements/load.txt` | Locust 压测 |

## 本地启动

### 后端

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
python scripts/seed_kb.py
uvicorn src.main:app --reload
```

验证地址：

- Health Check：`http://127.0.0.1:8000/health`
- Swagger：`http://127.0.0.1:8000/docs`

如果本地 ChromaDB 曾被其他大版本写入，应修改 `VECTOR_DB_PERSIST_DIR` 并重新执行 `scripts/seed_kb.py`，不得复用不兼容的 SQLite schema。

初始化脚本包含原有 4 篇和新增 12 篇演示知识。新增内容位于 `src/rag/demo_documents.py`，每篇有固定 `doc_id`、类别、`v1` 版本和演示来源；不会修改原有 `v2` 退款政策。SQL 文档和 ChromaDB 索引通过同一初始化入口更新，后台资源管理页可以继续编辑、删除或重建索引。重复运行脚本会更新同名文档，生产环境执行前先备份管理员维护的内容。中文词法检索使用双字滑窗，与向量召回和轻量 rerank 一起工作，不等同于专业中文分词或语义 Embedding；Mock Embedding 仍仅用于离线演示。

新增 Tool 定义在 `src/tools/registry.py`，本地数据和查询在 `src/tools/service_queries.py`。Skill 配置升级为 `v1.1`，每次请求记录当前版本和 Registry Hash。新增查询仍经过现有 Schema、权限、风险、停用开关、Resilience 和审计流程；不增加写权限。替换真实系统时应沿用返回协议，并将客户身份绑定到真实账号，而不是直接信任客户端传入的 `customer_id`。

已有部署使用 `python scripts/seed_kb.py --only-missing` 增量添加，已有同名文档不会覆盖。这个模式按 SQL 是否存在判断，不检查向量是否丢失；向量目录更换或索引损坏时，请使用后台“重建索引”。

### 前端

```bash
cd frontend
npm install
npm run dev
```

默认地址为 `http://127.0.0.1:3000`。前端包含：

- 用户咨询页：使用连续会话提交问题，并用当前浏览器保存的 Session ID 列表查询最近 7 天安全历史；正常结果展示 Agent 回复，风险、质量或依赖异常展示安全的人工介入说明；自动回复后可提交一次性评分和文字评价。
- 客服后台：只处理异常、待审批和需要人工修改的工单，支持原样批准、人工修改和拒绝。
- Agent 可观测页：仅 `manager/admin` 可访问 Agent Run 摘要，支持按工单编号精确查询并以北京时间展示运行时间，可继续跳转 LangSmith Project。

## LangGraph Checkpoint 配置

Durable Execution 默认启用。本地使用独立 SQLite 文件；当 DATABASE_URL 为 PostgreSQL 时，默认使用同一个 PostgreSQL 实例中的 LangGraph 官方 Checkpoint 表：

```dotenv
LANGGRAPH_CHECKPOINT_ENABLED=true
LANGGRAPH_CHECKPOINT_DATABASE_URL=
LANGGRAPH_CHECKPOINT_SQLITE_PATH=./.runtime/langgraph-checkpoints.sqlite
LANGGRAPH_CHECKPOINT_NAMESPACE=supportgpt-workflow-v1
LANGGRAPH_RESUME_LEASE_SECONDS=60
```

LANGGRAPH_CHECKPOINT_DATABASE_URL 可显式覆盖 Saver 数据库。高风险请求在 Approval Gate 暂停；人工审批后使用原 execution ID 恢复。应用启动会扫描人工已决策但未完成的执行，恢复失败则保留 resume_pending，可由主管重试。不要手工删除正在等待审批的 Checkpoint 文件或表。

## Skill Framework V1

Skill 清单在 `src/skills/definitions.py`，协议和 Registry 分别在 `models.py` 与 `registry.py`。正常 Workflow 在 Analyzer 后执行确定性 Skill Selector；选择结果自动进入 AgentState、Checkpoint、OpenTelemetry、AgentRun 和 Baseline Report。

新增 Skill 时必须：

1. 先在统一 `IntentType` 中确定业务边界，不得为了单个 Case 新建意图。
2. 注册唯一 Skill 名称和版本，声明 input/output Schema、required slots、RAG categories、minimum role 和 Tool Allowlist/Forbidden List。
3. 保证每个 Intent 只有一个主 Skill，且引用的 Tool 已在 ToolRegistry 注册。
4. 增加 Skill 选择和跨域 Tool 拒绝测试，并让 AgentRun 记下本次用的 Skill；最后用固定 100 条 Baseline 回归确认改动没有影响原有行为。

V1 的 Skill 共享现有 Workflow，不是运行时动态插件，也不允许 LLM 自由选 Skill。Tool Allowlist 是额外收窄层，不能替代 Schema、RBAC、Risk Engine、Approval Grant 和 Tool Governance。

## LLM Provider 配置

默认 Provider 为 `mock`。`openai` 是通用 OpenAI-compatible 实现，可接 OpenAI、DeepSeek、Qwen 和 vLLM：

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://example.com/v1
LLM_API_KEY=<api-key>
LLM_MODEL_NAME=<model-name>
```

Analyzer 和 QA 可使用独立小模型：

```dotenv
LLM_FAST_BASE_URL=<openai-compatible-endpoint>
LLM_FAST_API_KEY=<api-key>
LLM_FAST_MODEL_NAME=qwen-turbo
LLM_ANALYZER_MODEL_NAME=qwen-turbo
LLM_QA_MODEL_NAME=qwen-turbo
```

未配置 Fast Model 时自动回退主模型。Resolver 始终使用 `LLM_MODEL_NAME`。

### DecisionProvider / Jev

```dotenv
DECISION_PROVIDER=jev
JEV_API_KEY=<api-key>
JEV_BASE_URL=https://api.typesafe.ai
JEV_MODEL=jev-1.13.0
JEV_TIMEOUT_SECONDS=3
JEV_MAX_RETRIES=0
JEV_INTENT_CONFIDENCE_THRESHOLD=0.75
JEV_QA_CONFIDENCE_THRESHOLD=0.75
JEV_NOUL_THRESHOLD=0.7
```

<details>
<summary>历史 V1 初始路由</summary>

`DecisionProvider` 默认为 `disabled`。启用后，Analyzer 只在规则无法确定的歧义请求上调用 Jev；QA 只在确定性校验无法结论时调用。请求先脱敏、过滤密钥和业务标识，由 Resilience 统一处理超时、Retry 和 Circuit Breaker。Jev 不可用、响应非法或置信度不足时，分别回退原 Analyzer / QA LLM。

</details>

当前路由已调整为：Analyzer 对正常请求优先用 Jev 复核规则候选；QA 保留空回复、泄露、无权威证据等硬规则，将正向 Grounding 候选和其余非确定结论交给 Jev。Jev 不可用或置信度不足时，有可验证候选则回退规则，否则回退原 LLM。

Jev 只用于线上封闭决策；当前不作为 Baseline、Ragas 或 DeepEval 的离线 Judge。`Skill Selector`、Tool Allowlist / RBAC / Risk Policy 和 Approval Gate 仍为确定性代码。

可选备用模型与 Resilience 配置：

```dotenv
LLM_FALLBACK_BASE_URL=<openai-compatible-endpoint>
LLM_FALLBACK_API_KEY=<api-key>
LLM_FALLBACK_MODEL_NAME=<fallback-model>
RESILIENCE_LLM_TIMEOUT_SECONDS=20
RESILIENCE_LLM_MAX_RETRIES=1
RESILIENCE_RAG_TIMEOUT_SECONDS=5
RESILIENCE_RAG_MAX_RETRIES=1
RESILIENCE_TOOL_READ_MAX_RETRIES=1
RESILIENCE_CIRCUIT_FAILURE_THRESHOLD=3
RESILIENCE_CIRCUIT_RECOVERY_SECONDS=30
```

`LLM_FALLBACK_*` 三项必须同时配置。仅超时、限流、连接与服务端故障可重试；Auth、Schema / Validation 与错误 JSON 不重试。仅低风险读 Tool 使用自动 Retry；高风险写调用始终单次，结果不确定时由 Tool Governance V2.2 自动对账。

## Tool Governance V2.2

```dotenv
TOOL_POLICY_VERSION=tool-policy-v2.2
TOOL_OUTBOX_WORKER_ENABLED=true
TOOL_OUTBOX_POLL_INTERVAL_SECONDS=1
TOOL_OUTBOX_BATCH_SIZE=20
TOOL_OUTBOX_LEASE_SECONDS=30
TOOL_OUTBOX_MAX_ATTEMPTS=5
TOOL_OUTBOX_RETRY_BASE_SECONDS=1
TOOL_OUTBOX_RETRY_MAX_SECONDS=60
TOOL_RECONCILIATION_DELAY_SECONDS=2
```

Worker 默认随 FastAPI lifespan 启动；需要独立进程时运行 `python scripts/run_tool_outbox_worker.py`。多实例通过 `lease_owner + lease_expires_at + version` Compare-and-Set 抢占事件。`pending/processing/retry/succeeded/dead_letter` 共用 `tool_outbox_events`，Outbox payload 不保存退款原始参数。

审批后的 `/tool-actions/{id}/execute` 只返回 `queued`。排障时先用 `GET /tool-actions/{id}` 查看状态事件，再用 `GET /tool-outbox?action_id=<id>` 查看投递；只有 manager/admin 可对 DLQ 调用 `POST /tool-outbox/{event_id}/retry`。退款超时只能等待 `reconcile` 查询，禁止直接再次调用写 Tool。

## Tool Governance V2.3：重复申请、租约保护与人工核实

- `tool_business_requests` 保存唯一业务申请。当前只覆盖 `orders.create_refund_request` 的整单退款：同一客户、同一订单跨工单提交相同参数时返回原 Action；参数变化返回 409，不能新建 Action 绕过原审批。拒绝、失败或补偿后也不会自动释放去重记录；部分退款和再次退款需另行定义业务申请边界。
- 数据库唯一键解决并发创建，保存点回滚输掉竞争的新 Action。升级前旧 Action 会按解密后的客户/订单核对，不因新表为空而重复创建；密文无法核实时拒绝继续创建，需要人工处理。
- Worker 每隔租约期限的三分之一续租。领取时的 `version` 作为 fencing token（本次执行凭证）；Action 状态提交、成功、Retry 和 DLQ 写回都要求 owner、版本和有效期匹配。过期 Worker 不能复活租约或覆盖新结果。外部调用期间不占 Action/Outbox 行锁；批次内每个已领取事件立即处理和续租。
- `tool_action_reviews` 保存 unknown、补偿不确定和 DLQ 核实任务。人工处理台同时列出这些工单并标记“业务待核实”，不改变原 Ticket 状态、ResponseApproval 或 LangGraph 恢复语义。自动对账确定结果后关闭核实任务；启动时补齐旧记录。
- `GET /tickets/{id}/tool-reviews` 返回脱敏视图。主管通过 `POST /tool-actions/{id}/resolve` 提交 `expected_version`、`outcome`、`evidence_reference` 和 `note`。必须先在外部业务系统核实；提出人不能自确认，存在有效 Worker 租约时返回 409。确认只保存结果和事件，不调用写 Tool；剩余 Outbox 转为 `cancelled`，原回复审批保持不变。
- 人工可确认 `succeeded/failed`，补偿不确定时可确认 `compensated/compensation_failed`。仅入队而从未执行的操作不能直接确认为成功。凭证原文加密，对外仅返回摘要哈希和脱敏说明；系统没有自动验证凭证真伪，真实性由核实人负责。

新增两张表，不修改既有表列。当前仍由 `init_db/create_all` 创建；多实例部署前应先受控执行初始化，避免同时建表，后续统一纳入 Alembic。数据库、Checkpoint 和加密密钥须跨部署保留；Mock Gateway 仍是进程内账本，不代表真实退款完成。

### PostgreSQL 多进程故障演练

```bash
export TOOL_DRILL_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST:5432/supportgpt_drill'
python scripts/run_postgres_tool_drill.py --confirm-isolated-database \
  --report evaluation/reports/tool_drill/postgres.json
```

只能使用名称以 `_test` 或 `_drill` 结尾的专用测试库，必须显式确认。脚本创建随机 schema，结束时只删除该 schema。需要建 schema 权限，禁止使用生产库。脚本不连接真实 OMS、不调用 LLM，也不启动本地 Docker。

覆盖：两个独立进程同时创建同一申请；调用长于租约期限时另一 Worker 不能接管；模拟 OMS 已提交后强杀 Worker，新进程只对账，写 Handler 总次数为 1；旧 fencing token 不能执行或写回。模拟 OMS 的账本独立存入 PostgreSQL，不随 Worker 退出丢失。这是 PostgreSQL 与持久化模拟接口测试，不是真实支付/OMS 验收。

CI 的 `tool-governance-postgres-drill` 使用临时 PostgreSQL 16 执行脚本并保存报告，容器构建依赖该任务成功。本机没有 PostgreSQL 时，不得把跳过或仅编译脚本说成演练通过，应检查 CI 的实际结果。

## 安全配置

输入、Tool Result 和 RAG Document 均经过多层 Prompt Injection 检测；结果与 Qwen3Guard 语义分类共同进入 Risk Engine。Qwen3Guard 默认关闭：

```dotenv
QWEN3_GUARD_ENABLED=true
QWEN3_GUARD_BASE_URL=http://127.0.0.1:18001/v1
QWEN3_GUARD_API_KEY=EMPTY
QWEN3_GUARD_MODEL_NAME=Qwen/Qwen3Guard-Gen-0.6B
```

风险阈值由 `RISK_MEDIUM_THRESHOLD`、`RISK_HIGH_THRESHOLD`、`RISK_CRITICAL_THRESHOLD`、`RISK_LOW_CONFIDENCE_THRESHOLD` 和 `RISK_QA_SCORE_THRESHOLD` 配置。安全检测不得因外部 Guard 服务不可用而使主 API 失败；降级场景会隔离不可信上下文并转人工。

公网 Demo 额外遵循以下边界：

- `STAFF_SELF_REGISTRATION_ENABLED=false`，生产不允许自助创建员工账号；首个 admin 由 `scripts/create_staff_user.py` 初始化。
- `CORS_ALLOWED_ORIGINS` 只填写已部署前端的完整 Origin，生产禁止 `*`，Swagger/OpenAPI 自动关闭。
- `PUBLIC_VISITOR_SECRET` 签发 `HttpOnly + Secure + SameSite=None` 匿名 Cookie，后端由访客身份派生内部 Session ID，不同浏览器不会共享演示对话。
- `/support/requests`、`/support/history`、`/feedback/user` 和登录接口执行 Redis 分布式限流，Redis 故障时降级为有界进程内计数。
- `/chat`、回复建议、情感分析、升级判断和在线 Evaluation 都是员工内部接口，必须通过 RBAC。
- 生产启动会拒绝默认/弱 JWT Secret、缺失的 Fernet Tool Key、缺失的访客密钥、开启自助注册或通配 CORS。

## 主要 API

| 路径 | 用途 | 权限 |
|---|---|---|
| `POST /auth/register` | 仅测试/显式开启时自助注册 | 生产关闭 |
| `POST /auth/users` | 创建员工账号 | `admin` |
| `POST /auth/token` | 获取 JWT | 公开 |
| `GET /auth/users/me` | 查询当前用户 | 登录 |
| `GET /health` | 健康检查 | 公开 |
| `GET /support/history?customer_id=...&session_id=...` | 按一个或多个已知 Session 查询最近 7 天安全历史 | 公开演示入口 |
| `POST /support/requests` | 用户提交客服问题 | 公开演示入口 |
| `POST /chat` | 执行内部对话 Workflow | `agent+` |
| `POST /tickets` | 创建工单并持久化 AgentRun | 登录 |
| `GET /tickets` | 查询工单 | 登录 |
| `GET /staff/review-queue` | 待审批工单 | 客服员工 |
| `GET /approvals/pending` | 待审批记录 | 客服员工 |
| `POST /approvals/{approval_id}` | 通过、修改或拒绝草稿 | 客服员工 |
| `GET /agent-executions/{execution_id}` | 查看暂停/恢复状态及 Trace 关联 | 客服员工 |
| `POST /agent-executions/{execution_id}/resume` | 重试已持久化人工决策的 Workflow | `manager/admin` |
| `POST /tool-actions` | 提议高风险写 Action | `agent+` |
| `POST /tool-actions/{id}/decision` | 独立审批 Action | `manager/admin` |
| `POST /tool-actions/{id}/execute` | 原子入队，不同步执行外部写入 | `manager/admin` |
| `POST /tool-actions/{id}/compensate` | 为成功 Action 发起幂等补偿 | `manager/admin` |
| `GET /tool-actions/{id}/policy-replay` | 按历史 Policy 快照审计回放 | `manager/admin` |
| `GET /tool-outbox` | 查看 Outbox、Retry Queue 与 DLQ | `manager/admin` |
| `POST /tool-outbox/{event_id}/retry` | 显式重放 DLQ 事件 | `manager/admin` |
| `POST /feedback/user` | 提交一次性用户评价 | `agent_run_id + feedback_token` |
| `GET /feedback/runs/{agent_run_id}` | 查看 Run 与反馈关联 | `manager/admin` |
| `GET /observability/runs` | 分页查询 Agent Run 摘要；可传 `ticket_id` 精确筛选 | `manager/admin` |
| `POST /evaluate-response` | 运行单次评估 | `agent+`；关联 Run 时需 `manager/admin` |

Swagger 是最新 Schema 的最终参考；修改 API 时必须同步 Pydantic Model、测试和本文档。

## 数据库与缓存

- PostgreSQL/开发 SQLite 持久化 User、Ticket、Approval、AgentRun、AgentRunLink、AgentExecution 和 FeedbackEvent。
- LangGraph Saver 保存 Graph State 正文；AgentExecution 只保存业务关联、执行状态、恢复租约和 Trace ID。
- Memory V1 以 `ConversationSession` / `ConversationMessage` / `ConversationMemorySnapshot` 为 SQL 事实源；Redis 只缓存最近 final 消息并携带 SQL revision，缓存丢失、过期或 revision 不一致时回退数据库。
- ChromaDB 保存 Embedding 与文档 metadata，支持 `kb_version` 和 category filter。
- 当前 AgentExecution、ToolActionControl、ToolOutboxEvent 等业务新表及 LangGraph Saver 表由启动期 setup/create_all 创建；生产上线前必须统一纳入受控 Alembic/DDL Migration。

## OpenTelemetry 与 LangSmith

应用仅用 OpenTelemetry SDK 采集 Trace 和 Metrics，通过 OTLP 发送 Collector。Collector 将 Agent 主链 Trace 发往 LangSmith，将 Metrics 以 Prometheus 格式暴露在 `:8889`。项目不使用 LangSmith `traceable` 双轨采集。

最小 Collector 启动：

```bash
docker compose -f deployment/docker-compose.yml up -d otel-collector
```

重启：

```bash
docker compose -f deployment/docker-compose.yml restart otel-collector
```

关键配置：

```dotenv
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:4318/v1/metrics
OTEL_COLLECTOR_LANGSMITH_API_KEY=<api-key>
OTEL_COLLECTOR_LANGSMITH_PROJECT=supportgpt-enterprise
```

本地 Collector 不可达时 Backend 在 preflight 阶段跳过 exporter，避免重试刷屏。Collector 启动后需重启 Backend 恢复上报。遥测始终 fail-open，不影响业务主链。

## Memory V1 运行规则

- 新对话使用新 `session_id`；续问必须复用同一 `session_id` 和 `customer_id`。跨客户复用会话标识返回 `409`，且不创建孤儿 Ticket。
- 每轮先读取上一 revision 的有界上下文，再追加 User 消息；最近消息、摘要、实体和上一轮路由分字段进入 `AgentState`。
- 历史内容在每次读取时再次执行 PII 脱敏和 Prompt Injection 检测，并在 Prompt 中标记为不可信客户上下文。
- 只有 final 消息可以被下一轮读取。待审草稿保存为 pending，approved/modified 后幂等转为 final，rejected 草稿永不进入 Prompt。
- 动态订单、退款、物流状态必须重新调用 Tool 确认；Memory 只用于指代、Slot 续接和任务连贯。

与 Memory 相关的可调参数：

```dotenv
MEMORY_ENABLED=true
MEMORY_RECENT_MESSAGES=12
MEMORY_CONTEXT_MAX_CHARS=4000
MEMORY_SUMMARY_MAX_CHARS=1200
MEMORY_SCAN_MESSAGES=48
MEMORY_TTL_SECONDS=86400
```

## 测试

安装并运行完整测试：

```bash
python -m pip install -r requirements/test.txt
python -m compileall src tests
python -m pytest -q
```

定向烟测：

```bash
python -m pytest tests/test_agents.py tests/test_rag.py -q
```

测试使用内存 SQLite，不污染本地开发数据。覆盖率 90%+ 是生产化目标，不得表述为当前已强制达成。

PR Agent Quality Gate：

```bash
python scripts/run_ci_quality_gate.py
```

这个入口会覆盖本地 `.env` 中的模型、Guard 和 OTel 配置，并在临时 SQLite/Chroma 目录中使用 Mock Provider 回放同一组 100 条完整 Workflow。规则存放在 `evaluation/quality_gate_policy.json`：必须使用指定的 Dataset SHA256 和 100 条 Case，六项行为指标都要达到设定目标，也不能增加失败 Case。JSON、Markdown、Baseline 和 Error Analysis 报告保存在 `evaluation/reports/ci_quality_gate/`，GitHub Actions 会保留 14 天供查看。

Locust 压测：

```bash
python -m pip install -r requirements/load.txt
locust -f tests/load_test.py
```

## 评测命令

管道烟测：

```bash
python scripts/run_agent_eval.py --rag-engine local --agent-engine local
```

Ragas + DeepEval 正式评测：

```bash
python -m pip install -r requirements/eval.txt
python scripts/seed_kb.py
python scripts/run_agent_eval.py --rag-engine ragas --agent-engine deepeval
```

真实 LLM 回归：

```bash
python scripts/run_real_llm_regression.py --suite smoke --dry-run
python scripts/run_real_llm_regression.py --suite smoke --confirm-live
```

Baseline 100 Workflow Replay：

```bash
python scripts/run_baseline_eval.py --dry-run
python scripts/run_baseline_eval.py --confirm-live
```

对已有报告执行 Release Gate，不重新调用 Workflow 或 LLM：

```bash
python scripts/check_quality_gate.py \
  --profile release \
  --report evaluation/reports/baseline_v1/baseline_v1_latest.json
```

Release Profile 当前要求：Intent、Department、Required Tool 均为 `1.0`，Forbidden Tool Violation 为 `0`，HITL、Approval 与 Case Pass 至少 `0.99`，P95 不超过 `5s`，平均 Token 不超过 `550`，100 Case 总 LLM Calls 不超过 `100`，Analyzer Rule Hit Rate 至少 `0.95`。已知失败 Case 采用显式 ID 白名单，聚合分数达标也不能掩盖新的 `PASS→FAIL`。

正式 Baseline 每次产生一组不可变时间戳 JSON/Markdown 快照，`baseline_v1_latest.json/md` 是可直接打开的最新普通文件副本，由报告器原子替换。报告目录不进 Git；实验配置、Dataset SHA256、模型、Prompt/Workflow 版本、Token 与 Trace ID 必须写入报告。

报告的 `metric_failure_index` 按六项行为指标组织失败 Case，每条包含 Case ID、Query、指标值、期望值、实际值、失败原因和 Trace ID。Markdown 报告的“按指标定位失败 Case”可直接查看；JSON 可用 `jq` 过滤：

```bash
jq '.metric_failure_index.intent_accuracy.cases' \
  evaluation/reports/baseline_v1/baseline_v1_latest.json

jq '.metric_failure_index.required_tool_hit_rate.failed_case_ids' \
  evaluation/reports/baseline_v1/baseline_v1_latest.json
```

Baseline JSON/Markdown 完成后会自动生成：

- `error_analysis_<run_id>.md`：与本次 Baseline 时间戳对应的不可变快照。
- `error_analysis_latest.md`：原子替换的最新普通文件副本。

Error Analysis 只读取本次 JSON 中 `behavior_evaluation.passed=false` 的 Case，输出 Failure Breakdown、Intent Confusion Matrix、HITL/Approval mismatch、Tool 问题以及每个 Case 的 Expected/Actual/Trace。它不导入 Workflow 执行路径，不调用 LLM，不重新读取或修改 Dataset，不修改 Agent State。

## Feedback Pipeline

`/chat`、`/suggest-response` 和工单 Workflow 将结果持久化为 AgentRun；用户评价、人工修正和 Evaluation 以 FeedbackEvent 关联同一 `agent_run_id` 与 `trace_id`。

导出脱敏后的训练候选：

```bash
python scripts/export_training_candidates.py
```

导入统一评测结果：

```bash
python scripts/import_evaluation_feedback.py \
  --report evaluation/reports/evaluation_latest.json
```

当前只生成 SFT/DPO 候选，不执行训练、自动发布或未经人工治理的数据回流。

## Docker 与部署

启动全部本地组件：

```bash
docker compose -f deployment/docker-compose.yml up --build
```

主要端口：Backend `8000`、PostgreSQL `5432`、Redis `6379`、OTLP gRPC `4317`、OTLP HTTP `4318`、Collector Metrics `8889`、Prometheus `9090`、Grafana `3000`。Kubernetes 模板位于 `deployment/k8s/`，它仅是部署样例，不代表已在生产环境运行。

## CI/CD

- `.github/workflows/ci.yml`：PR/Push 必跑全量 Backend Tests、Frontend Build、100 Case Mock Workflow Gate，并在全部通过后验证 Backend 镜像可构建。
- `.github/workflows/release-quality-gate.yml`：仅手动触发；必须勾选付费调用确认，通过 `release-quality-gate` GitHub Environment 读取真实模型 Secrets，运行同一固定 100 条 Baseline 和 Release Profile。
- `.github/workflows/cd.yml`：只监听成功的 `Release Quality Gate`，检出其 `head_sha`，构建并发布 `latest` 与不可变 `sha-<commit>` 镜像到 GHCR，同时生成 Build Provenance Attestation。

仓库需要在 GitHub `release-quality-gate` Environment 配置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL_NAME`；Fast Model Secrets 可选。建议为该 Environment 设置 Required Reviewer，避免无意触发真实模型成本。CD 当前完成的是经过质量门禁的容器交付，未获得具体集群凭据，因此不会自动修改 Kubernetes 集群。

## 开发约定

- Python 遵循 PEP 8，新函数可增加一两行精简中文注释。
- Pydantic Schema 应提供合理默认值和字段描述。
- 数据库使用 `AsyncSession`，明确事务边界。
- 新 Tool 必须注册到 ToolRegistry，配置 Schema、permission、risk level 和审计字段。
- 高风险 `WRITE` Tool 不得加入 Agent 自动路由；必须使用 `/tool-actions` 提议，由不同 manager/admin 审批，再携带 expected version 入 Outbox。禁止恢复同步直调写 Handler。
- 新增写 Tool 必须提供下游幂等键契约和 `reconciliation_handler`；如业务可逆，再显式提供 `compensation_handler`。超时分支必须验证“写调用次数为 1、后续只查结果”。
- `TOOL_ACTION_ENCRYPTION_KEY` 生产必须使用独立 Fernet Key；审计表只保存 payload HMAC、字段名和脱敏结果，不得新增原始参数或异常正文字段。
- 改动 Agent 节点时同步 State、Trace、Metrics、确定性测试和 Baseline 评测。
- 不得提交 `.env`、API Key、业务评测报告或训练候选数据。
