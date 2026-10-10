# SupportGPT 智能客服 Agent

[![CI](https://github.com/sulik0/support-gpt-enterprise-resume/actions/workflows/ci.yml/badge.svg)](https://github.com/sulik0/support-gpt-enterprise-resume/actions/workflows/ci.yml)
[![Release Gate](https://github.com/sulik0/support-gpt-enterprise-resume/actions/workflows/release-quality-gate.yml/badge.svg)](https://github.com/sulik0/support-gpt-enterprise-resume/actions/workflows/release-quality-gate.yml)
[![CD](https://github.com/sulik0/support-gpt-enterprise-resume/actions/workflows/cd.yml/badge.svg)](https://github.com/sulik0/support-gpt-enterprise-resume/actions/workflows/cd.yml)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-workflow-orange)](https://github.com/langchain-ai/langgraph)

SupportGPT 智能客服 Agent 面向售后服务场景，能够理解用户问题、查询业务信息和知识库、生成并检查回复，并在需要时转交人工处理。项目基于 LangGraph Workflow，包含 Tool 调用、Hybrid RAG、安全治理、审批、OpenTelemetry 可观测和离线评测能力。

## 核心能力

| 领域 | 当前能力 |
|---|---|
| Agent Workflow | 六个业务节点 + Skill Selector + Approval Gate；Tool/RAG 并行执行 |
| Skill Framework | 6 个版本化 Skill；系统根据 Intent 选择 Skill，并在 Trace、AgentRun 和 Evaluation 中保留选择结果 |
| Durable Execution | SQLite/PostgreSQL Checkpoint、interrupt/resume、AgentExecution、恢复租约与重启扫描 |
| LLM | `mock/openai/azure`；`openai` 兼容 OpenAI、DeepSeek、Qwen 和 vLLM |
| DecisionProvider | 可选 Jev System One；复核 Analyzer 规则候选并评判 QA 正向证据，规则/LLM 作为分层回退 |
| RAG | ChromaDB、Hybrid Search、轻量 rerank、版本/类别过滤、citation |
| Tool Calling | 9 个演示 Tool，覆盖客户、订单、工单、物流、保修、支付发票及服务状态；ToolRegistry、Schema、RBAC、持久化脱敏审计 |
| Tool Governance | V2.2：业务幂等键、Transactional Outbox、Worker、unknown 自动对账、Retry/DLQ、补偿与 Policy 回放 |
| Safety | 多层 Prompt Injection 规则、Qwen3Guard Adapter、Risk Engine、PII/泄露过滤 |
| HITL | 高风险、低置信度、低 QA、投诉与退款场景在 Graph 内暂停审批，完成后从原 Thread 恢复 |
| Observability | OpenTelemetry 统一采集，Collector 导出 LangSmith Trace 和 Prometheus Metrics |
| Evaluation | Ragas、DeepEval、确定性 Agent/Security Evaluator、100 条 Baseline Workflow Replay |
| PromptOps / EvalOps | 内容 Hash 快照、请求级版本绑定、成对实验、staging/production 门禁晋级、显式回滚与发布审计 |
| Feedback | AgentRun + FeedbackEvent + Trace 关联，脱敏 SFT/DPO 候选导出 |
| Resilience | LLM/RAG/Tool 统一超时、有界 Retry、Circuit Breaker、Fallback 与风险降级 |
| Memory | SQL 结构化会话为事实源、Redis revision Cache、有界上下文、实体续接与 HITL 结算 |
| Frontend | 用户咨询与评价页、客服审批后台、Agent 可观测页 |

用户页顶部“项目架构”和后台“Agent 处理流程”可打开完整流程说明。也可以直接访问前端地址的 `/#workflow`，无需登录。页面支持节点详情和四种场景的逐步演示，展示同时进行的业务查询与知识检索，以及发现安全风险或需要人工审批时怎样处理。不调用后端或模型，不展示真实客户数据，也不代替实际请求的 Trace。

## 快速启动

### 后端

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python scripts/seed_kb.py
uvicorn src.main:app --reload
```

- Health：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Swagger：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

默认使用 Mock LLM、SQLite 和本地 ChromaDB，Redis 未启动时可正常降级。

`scripts/seed_kb.py` 现在包含 16 篇知识文档：原有 4 篇保留，新增 12 篇中文售后说明，覆盖退款、发票、取消订单、物流、保修、账户和 API 排障。部署更新后需要在后端环境执行此脚本，才能将新增文档存入数据库并建立向量索引；应用启动不会自动导入。重复执行会按 `doc_id` 更新同名文档，因此不要用它覆盖管理员已修改的同名内容。新增文档属于演示手册，不代表企业正式政策。

新增查询工具按意图自动调用，管理员可在资源管理页查看和停用。示例客户 `cust_102` 有物流延迟和未申请发票记录，`cust_103` 有服务支持权益。未知客户返回 `not_found`；这些适配器没有接入真实物流、财务或维修服务。

已有环境建议使用 `python scripts/seed_kb.py --only-missing`，只补充缺失文档，保留后台已编辑的内容。若 SQL 文档存在但向量目录丢失，应从后台执行重建索引，不能依赖此增量模式修复索引。

用户页还可选择 3 个中文演示客户：张晓雨 `cust_201`（订单 `ORD-12001`，物流延迟）、李明 `cust_202`（订单 `ORD-12002`，设备保修）和星河科技联系人陈晨 `cust_203`（订单 `ORD-12003`，API 与发票）。这些人民币订单、客户与历史工单全部虚构，随代码加载，不需要数据库初始化。若 Railway 或本地环境已配置 `PUBLIC_DEMO_PROFILE_IDS`，需要将 `cust_201,cust_202,cust_203` 加入原有名单并重启后端，否则公开入口会拒绝新客户。
LangGraph Checkpoint 默认启用：本地写入独立的 `.runtime/langgraph-checkpoints.sqlite`；使用 PostgreSQL DATABASE_URL 时自动切换到官方 PostgreSQL Saver。高风险请求返回审批草稿后 Workflow 保持暂停，人工审批会恢复原执行而不是重跑前置节点。

Memory V1 在 `/chat` 和用户咨询页中按 `session_id + customer_id` 续接会话。SQL 保存结构化消息、摘要和显式业务实体，Redis 只是带 revision 的可选缓存；待审草稿不会进入后续 Prompt，仅在审批通过或人工修改后结算为 final 消息。

Tool Outbox Worker 默认随 FastAPI 启动，也可独立运行：

```bash
python scripts/run_tool_outbox_worker.py
```

高风险写 Action 审批后，`/execute` 只原子写入 `queued + Outbox`，外部写入由 Worker 异步执行。退款超时进入 `unknown`，系统按业务幂等键查询 OMS 权威结果，不会盲目重复退款；对账暂未得到终态时进入 Retry Queue，耗尽后进入 DLQ 并转人工。`/tool-outbox` 可查看队列，`/policy-replay` 可按历史 Policy 快照重放审计。

### 前端

```bash
cd frontend
npm install
npm run dev
```

打开 [http://127.0.0.1:3000](http://127.0.0.1:3000)。

公网 Demo 默认关闭员工自助注册，公开入口只保留 `/support/*` 和一次性用户评价。生产启动会校验 JWT、Tool 加密、访客签名密钥和 CORS 白名单；可先在本地安全旋转密钥：
公开构建还应设置 `VITE_STAFF_ENTRY_ENABLED=false`，隐藏员工入口；后端 RBAC 仍是真正的安全边界。

```bash
python scripts/generate_production_secrets.py --env-file .env --confirm
```

关闭公开注册后，首个管理员使用服务端脚本创建，后续账号由 admin 通过受保护的 `POST /auth/users` 创建：

```bash
python scripts/create_staff_user.py admin --role admin
```

### 真实 LLM

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://example.com/v1
LLM_API_KEY=<api-key>
LLM_MODEL_NAME=<model-name>
```

Analyzer 和 QA 可配置 `LLM_FAST_*`、`LLM_ANALYZER_MODEL_NAME` 和 `LLM_QA_MODEL_NAME` 使用小模型。默认回复语言与用户当前输入一致，除非用户明确要求切换。

可选配置 `LLM_FALLBACK_*` 指向独立备用模型。SDK 内建重试已关闭，由 Resilience 模块统一执行超时、有界 Retry 和 Circuit Breaker；仅低风险读 Tool 可自动重试。

可选启用 Jev `DecisionProvider`：

```dotenv
DECISION_PROVIDER=jev
JEV_API_KEY=<api-key>
JEV_BASE_URL=https://api.typesafe.ai
JEV_MODEL=jev-1.13.0
```

Jev 只返回 `Choice / Score / Noul` 封闭决策，不会直接选择或执行 Tool。启用后，Analyzer 使用 Jev 复核规则候选；QA 保留安全硬门禁，将正向 Grounding 和非确定结论交给 Jev。Jev 低置信度或不可用时，有可验证规则候选则回退规则，否则回退原 LLM。当前未将 Jev 接入离线 Evaluation Judge。

## 测试与评测

PromptOps V1 使用 `python scripts/promptops.py` 管理 Analyzer / Resolver / QA 模板。默认模板保持现有行为；`evaluate --bundle default --mock` 会在隔离环境中将当前版本与内置版本各回放 100 条，保存不可变实验、完整 Prompt/配置、指标 Diff 和逐 Case Trace。Mock 只验证工程链路，不能用于 production 晋级。

完整操作步骤、付费实验和回滚说明见 [Prompt 治理](docs/06_PROMPTS.md#promptops--evalops-v1-操作)。

```bash
python -m pip install -r requirements/test.txt
python -m compileall src tests
python -m pytest -q
```

免费、确定性的 PR Agent Quality Gate：

```bash
python scripts/run_ci_quality_gate.py
```

该命令强制使用 Mock Provider，在隔离 SQLite/Chroma 目录中回放固定 100 条完整 Workflow，并按 `evaluation/quality_gate_policy.json` 检查 Dataset Hash、六项行为指标和新增失败 Case。它不会读取本地真实模型配置、调用付费 API 或上报遥测。

Baseline 100 真实 Workflow Replay：

```bash
python scripts/run_baseline_eval.py --dry-run
python scripts/run_baseline_eval.py --confirm-live
```

对已有真实 Baseline 报告执行纯离线 Release Gate：

```bash
python scripts/check_quality_gate.py \
  --profile release \
  --report evaluation/reports/baseline_v1/baseline_v1_latest.json
```

Ragas + DeepEval：

```bash
python -m pip install -r requirements/eval.txt
python scripts/run_agent_eval.py --rag-engine ragas --agent-engine deepeval
```

评测报告保存在 `evaluation/reports/`，该目录不进 Git。正式 Baseline 同时生成不可变时间戳 JSON/Markdown 快照与可直接打开的 `latest` 普通文件副本，并固定记录 Dataset Hash、模型、Prompt/Workflow 版本、阈值、Token、延迟与 Trace 配置。每次还会基于已生成 JSON 纯离线生成 `error_analysis_<run_id>.md` 和 `error_analysis_latest.md`，只分析 FAIL Case，不重放 Workflow 或调用 LLM。

GitHub Actions 分为三层：`CI` 执行全量后端测试、前端构建、100 Case PR Gate 和容器构建；`Release Quality Gate` 需要人工确认及 GitHub Environment/Secrets，运行真实模型 100 Case Baseline，并约束 Case Pass、HITL、Token、P95 和 LLM Calls；只有该 Workflow 成功，`CD` 才会将完全相同 Git SHA 的镜像发布到 GHCR。当前 CD 交付到镜像仓库，不代表已经部署到生产 Kubernetes 集群。

## 可观测

最小启动 OpenTelemetry Collector：

```bash
docker compose -f deployment/docker-compose.yml up -d otel-collector
```

应用只使用 OpenTelemetry SDK：

```text
Application
  -> OTLP Collector
      -> LangSmith Trace
      -> Prometheus Metrics
          -> Grafana
```

Collector 未启动时后端会 fail-open，不影响 Agent 主流程。

## 文档

本项目只维护以下统一文档：

1. [项目概览](docs/00_PROJECT_CONTEXT.md)
2. [技术架构](docs/01_ARCHITECTURE.md)
3. [业务流程](docs/02_BUSINESS_LOGIC.md)
4. [事实口径](docs/03_INTERVIEW_CANON.md)
5. [技术决策](docs/04_DECISIONS.md)
6. [工程指南](docs/05_ENGINEERING_GUIDE.md)
7. [Prompt 设计](docs/06_PROMPTS.md)
8. [AI 交接](docs/07_AI_HANDOFF.md)
9. [任务清单](docs/08_TODO.md)
10. [面试问答](docs/09_INTERVIEW_QA.md)

未来任何 AI 或开发者参与项目前，应先阅读 [docs/00_PROJECT_CONTEXT.md](docs/00_PROJECT_CONTEXT.md)。

## 实现边界

- CRM、OMS、Ticketing 和默认 LLM 是 Mock Adapter，架构保留替换边界，但不得表述为已接入真实企业系统。
- 退款默认使用 Mock OMS，也可显式连接新增的 PostgreSQL OMS 参考服务，验证跨进程幂等、参数冲突和权威查询。参考服务只保存演示退款申请，不移动真实资金；真实企业 OMS 仍需实现并验证相同契约，且补充 Alembic Migration。启动方法见工程指南的「PostgreSQL OMS 参考服务」。
- Docker Compose 和 Kubernetes 是可复现部署模板，不代表已生产上线。
- 公网限流在 Redis 不可用时会降级到单进程内存；多副本上线前必须使用共享 Redis 并校准可信代理头。
- Qwen3Guard 默认关闭，Risk Engine 阈值尚未用真实客服数据校准。
- Feedback Pipeline 当前只导出脱敏训练候选，尚未执行 SFT/DPO 训练和自动发布。
