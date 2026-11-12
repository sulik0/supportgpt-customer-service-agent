# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目是什么

SupportGPT：售后客服 Agent 后端（FastAPI + LangGraph）加 React 前端，由一个开源 FAQ/RAG 客服项目改造而来。默认配置是 Mock LLM、SQLite、本地 ChromaDB，不需要 API Key、Redis 或 Collector 就能启动和测试。CRM、OMS、工单、退款都是 Mock Adapter（`src/oms/` 是可选的参考实现，不是真实企业系统）。

文档在 `docs/`，用中文写。改代码前先读 `docs/00_PROJECT_CONTEXT.md`；`docs/03_INTERVIEW_CANON.md` 是唯一可对外引用的事实口径，`docs/05_ENGINEERING_GUIDE.md` 是完整操作手册，`docs/08_TODO.md` 是任务和技术债清单。文档可能落后于代码，以代码和测试为准。

## 常用命令

推荐 Python 3.11（CI 和 Docker 都用 3.11）。

```bash
# 后端
python -m pip install -r requirements/test.txt   # 包含运行时依赖
cp .env.example .env
python scripts/seed_kb.py                        # 初始化 16 篇演示知识；--only-missing 只补缺
uvicorn src.main:app --reload                    # http://127.0.0.1:8000/docs

# 测试
python -m pytest tests/test_tool_governance.py -q              # 单个文件
python -m pytest tests/test_agents.py::test_name -q            # 单个用例
python -m pytest -q                                            # 全量（300+ 用例，较慢）
python -m compileall -q src scripts tests                      # CI 的编译检查

# 格式（pyproject.toml 配了 black / isort，行宽 88；CI 不检查）
black src tests scripts && isort src tests scripts

# 前端（frontend/）
npm install
npm run dev          # 连本地后端，端口 3000
npm run dev:demo     # 配合 npm run demo:api，用 tests/fixtures 的假接口，不需要后端
npm run build
npm test             # node --test tests/*.test.js

# MCP Server（独立轻量环境，不加载后端 conftest）
python -m unittest discover -s tests/mcp -v
```

评测和门禁：

```bash
python scripts/run_ci_quality_gate.py            # PR 门禁：Mock Provider 回放固定 100 条 Baseline
python scripts/run_baseline_eval.py --dry-run    # --confirm-live 才会调用真实模型（付费）
python scripts/check_quality_gate.py --profile release --report evaluation/reports/baseline_v1/baseline_v1_latest.json
python scripts/run_postgres_tool_drill.py --confirm-isolated-database   # 需要 TOOL_DRILL_DATABASE_URL，库名必须以 _test 或 _drill 结尾
```

`run_ci_quality_gate.py` 会覆盖本地 `.env` 的模型、Guard 和 OTel 配置，并使用临时 SQLite/Chroma 目录。带 `--confirm-live` 的脚本会产生真实模型费用，运行前先问用户。本机没有 PostgreSQL 时，不能把跳过演练说成演练通过，应看 CI 的实际结果。

测试用内存 SQLite，`tests/conftest.py` 在导入 `src` 之前强制设置环境变量（Mock LLM、`TOOL_SELECTION_MODE=rules`、关闭 Outbox Worker / OTel / DecisionProvider）。新测试如果依赖别的开关，要在用例内显式设置，不要依赖本机 `.env`。

## 架构

### 请求主链

`src/main.py`（约 2000 行，包含全部路由和 lifespan）→ `src/agents/graph.py` 的 `run_agent_workflow`。Graph 是固定 Workflow，不是自由 Agent：

```
analyzer → skill_selector → context_enrichment（tooling ∥ retriever）→ resolver → qa → escalation → approval_gate
```

- Analyzer 先做安全检查。Prompt Injection / Jailbreak 强命中直接短路到 escalation，跳过 Tool、RAG、Resolver、QA。
- 范围外请求从 Analyzer 直接进 Resolver，但仍经过 QA、Escalation 和 Approval Gate。
- `skill_selector` 和 `approval_gate` 不调用 LLM。Skill 由统一 `IntentType`（`src/models/intents.py`）确定性选择，每个 Intent 只有一个主 Skill。
- `AgentState`（`graph.py` 顶部的 TypedDict）是节点间唯一的数据通道。新增字段要兼容旧 Checkpoint（缺字段时走 `build_ticket_state` 的兼容构造）。
- Resolver 在生成前构造精简证据并存为 `resolution_evidence`（`src/agents/evidence.py`），QA 原样复用，不要让 QA 再独立拼装上下文。

### 暂停与恢复

需要审批的请求在 `approval_gate_node` 调用 LangGraph `interrupt`，人工决策后用原 `thread_id` 通过 `Command(resume=...)` 恢复（`resume_agent_workflow`）。Checkpoint 存 Graph State（本地 SQLite 文件在 `.runtime/`，PostgreSQL 时用同一实例）；`AgentExecution` 表只存业务关联、状态和恢复租约（`src/agents/durable_execution.py`）。应用启动时会扫描“已决策但未完成”的执行并续跑。

### Tool 的两条路径

所有业务 Tool 都经过 `src/tools/registry.py` 的 ToolRegistry（Schema、RBAC、Skill Allowlist、风险等级、审计），Agent 不直接调 Adapter。

- 读 Tool（8 个）：由 `src/agents/tooling.py` 在 Workflow 内自动调用。选择方式由 `TOOL_SELECTION_MODE` 控制：`rules`（默认）或 `react`（`src/agents/tool_planner.py`，受控循环，只能选只读 Tool）。
- 写 Tool（1 个，退款申请）：不在 Agent 自动路径上。走 `/tool-actions` 提议 → 另一位 manager/admin 审批 → `/execute` 只原子写入 `queued + Outbox` → Worker（`src/tools/outbox.py`，租约 + fencing token）执行。状态机在 `action_state_machine.py`，治理逻辑在 `governance.py`。超时进入 `unknown`，只能对账查询，不能重试写入。

### 其他跨模块约定

- 三个信任边界（用户输入、Tool 结果、RAG 文档）都经过 `src/guardrails/` 的检测，结果汇入 `src/risk/engine.py`。Guard 服务不可用时降级为隔离上下文并转人工，不能让主 API 失败。
- LLM、RAG、读 Tool 的超时、重试、熔断统一走 `src/resilience/`。
- RAG（`src/rag/vector_store.py`）是 ChromaDB 向量召回加进程内 BM25 和轻量 rerank，默认 Mock Embedding。
- Memory（`src/memory/`）以 SQL 为事实源，Redis 只是带 revision 的缓存。只有 final 消息进入下一轮 Prompt；订单、退款、物流状态必须重新调 Tool，不能从 Memory 读。
- 工单状态只经 `src/tickets/state_machine.py` 流转。
- Trace 和 Metrics 只用 OpenTelemetry（`src/observability/`），上报前脱敏，失败不影响主流程。不要引入 LangSmith SDK 的 `traceable`。
- `src/mcp_server/` 是独立 stdio 进程，只通过 HTTP 调员工 API，不导入后端代码；依赖在 `requirements/mcp.txt`。
- 数据库表由启动期 `create_all` 创建，没有 Alembic。

## 必须保持的设计

来自 `docs/07_AI_HANDOFF.md`，改动时不要破坏：

1. 默认 Mock 模式在无 API Key、无 Redis、无 Collector 时必须能启动和通过测试。
2. 退款、投诉、越权写操作、低置信度、低 QA 分、高风险结果保持人工审批。
3. 高风险写 Tool 不得加入 Agent 自动路由，不得恢复同步直调写 Handler。新增写 Tool 必须提供下游幂等键契约和 `reconciliation_handler`。
4. 审批恢复必须从原 Checkpoint 继续，不能从头重跑。打开工单详情只读已保存的 AgentRun，不触发新 Workflow。
5. DecisionProvider（Jev）和 LLM 的输出不是授权：Skill 选择、Tool 权限、Risk Engine、Approval Gate 保持确定性代码。
6. 评测必须真实回放当前 Workflow；不得通过降低 `evaluation/baseline` 数据集的期望来提高通过率。数据集的 SHA256 固定在 `evaluation/quality_gate_policy.json`，改数据集门禁会失败。
7. 审计表只存 payload HMAC、字段名和脱敏结果，不新增原始参数或异常正文字段。

## 改动后要同步的地方

- 改 Agent 节点：同步 State、Trace 属性、Metrics、确定性测试，并跑一次 `run_ci_quality_gate.py` 确认 100 条 Baseline 没有新增失败。
- 改 API：同步 Pydantic Model、测试和 `docs/05_ENGINEERING_GUIDE.md` 的 API 表。
- 改功能边界或完成状态：同步 `docs/00_PROJECT_CONTEXT.md`、`docs/03_INTERVIEW_CANON.md`、`docs/08_TODO.md`。
- 新增 Skill 或 Tool：步骤见 `docs/05_ENGINEERING_GUIDE.md` 的“Skill Framework V1”和“开发约定”。
- 代码注释用简短中文。不提交 `.env`、`evaluation/reports/`、`.runtime/`、训练候选数据。
