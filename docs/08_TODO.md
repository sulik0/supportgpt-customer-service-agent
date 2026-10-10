# 项目任务清单

> 最后更新：2026-10-10。状态以代码、测试和 `03_INTERVIEW_CANON.md` 为准。

## P0

- [x] 增加可独立运行的 PostgreSQL OMS 参考服务及 HTTP Gateway，保存幂等回执，拒绝参数冲突，提供权威查询及幂等补偿；保持默认 Mock 和原审批 / Outbox 流程，不执行真实资金操作。
- [ ] 为参考 OMS 增加 Alembic Migration、密钥轮换、备份恢复、保留期和真实 OMS / 支付渠道契约验证；参考申请提交成功不能作为真实到账证明。

- [x] 建立 `AgentRun`、`AgentRunLink`、`FeedbackEvent` 数据模型。
- [x] 将 `/chat`、`/suggest-response` 的执行快照与 OpenTelemetry Trace ID 关联。
- [x] 修复 Resolver / QA 证据不一致：保存生成时实际使用的 `resolution_evidence`，让 QA 复用物流事实和同一组 KB 引用，不降低质量或风险阈值。
- [x] 对截断回复增加一次有界重写；具体 Tool 名称、失败/重试/降级状态进入 Trace；修复 ISO 时间戳误脱敏，并保证业务别名不会影响内部 Memory。
- [x] 无害范围外请求正常结束：规则 / 同次 Jev 分类判断能力范围，避免无关 Memory 和 Tool / RAG 查询；QA 验证能力说明，业务高风险和真实故障仍升级人工。
- [x] 增加基于 `agent_run_id + feedback_token` 的用户评价 API。
- [x] 将人工审批通过、修改或拒绝的结果自动保存为反馈事件。
- [x] 将在线与离线 Evaluation 结果关联到 Agent Run。
- [x] 增加 PII / 密钥过滤、Tool 字段白名单、会话 HMAC 摘要和独立事务 fail-open。
- [x] 增加 SFT / DPO 候选质量门控、去重、原子导出和 Manifest。
- [x] 跑通 FastAPI `/health` -> Workflow -> Ticket / AgentRun -> FeedbackEvent 的 MVP 持久化链路。
- [x] 修复 LangGraph State 缺少 `sla_hours` 导致的 Workflow 运行失败。
- [x] 固定核心 LangChain / LangGraph / ChromaDB 兼容版本，并使用版本化 ChromaDB 本地目录。
- [x] 将 Prompt Injection 升级为规范化、中英特征、组合启发式、角色提权和 Base64 载荷组合的多层检测。
- [x] 在客户输入、Tool 返回和 RAG 文档三类信任边界检查直接/间接 Prompt Injection。
- [x] 增加 Qwen3Guard-Gen-0.6B OpenAI-compatible Adapter，将三类信任边界的语义安全结果接入 Risk Engine、Trace 和 Metrics。
- [x] 建立独立 Risk Engine，统一输出风险等级、分数、原因、人工与自动化处置建议。
- [x] 将 Risk Engine 结果接入 LangGraph 路由、QA、Escalation、API、结构化日志、OpenTelemetry Trace 与 Metrics。
- [x] 在 Dataset + Workflow Replay 中增加安全混淆矩阵、Precision / Recall / F1 / 误报率和安全处置正确率。
- [x] 将业务回归 Baseline 扩展到 100 条，增加多语言、安全攻击与安全 hard negative 覆盖。
- [x] 增加真实 LLM Regression 专用入口、smoke/full 套件、Dry Run、付费调用前确认、调用预算，并记录模型、Token 与成本。
- [x] 拆分用户咨询页与客服员工后台；用户端采用连续会话展示正常回复或安全的风险/异常状态，异常请求进入受 RBAC 保护的人工审批队列。
- [x] 人工处理台支持按部门、优先级、客户情绪和关键词组合筛选完整待审批队列；工单显示部门，可一键清除筛选，不重新调用 Agent。
- [x] 建立统一 `IntentType`，让规则、LLM Provider、AgentState、Tooling、Risk Engine 和 Agent Evaluation 共用同一套意图枚举与兜底策略。
- [x] 实现 Baseline Workflow Replay V1：固定 100 条完整 Ticket State 回放、六项确定性行为指标、逐 Case 结果和 OTel Trace 同源性能汇总。
- [x] 建立 Evaluation Report 生命周期：清理旧 `report_*.json`、单条评测最多保留 20 份、Baseline 使用时间戳快照与 latest 普通文件副本，并固化完整实验配置。
- [x] Baseline Report 增加 `metric_failure_index`，支持按 Intent、Department、Required/Forbidden Tool、HITL 和 Approval 指标反查失败 Case 与 Trace ID。
- [x] Baseline 每次运行后纯离线生成 Error Analysis 时间戳快照与 latest 副本，覆盖 Failure Breakdown、Intent Confusion Matrix、HITL/Approval mismatch、Tool 问题和逐 FAIL Case 详情。
- [x] 建立 PR Agent Quality Gate：固定 100 条 Dataset 使用 Mock Provider 完整回放 Workflow，校验 Dataset Hash、六项行为指标与新增失败 Case。
- [x] 建立真实 LLM Release Quality Gate：显式付费确认、调用预算、行为/延迟/Token/LLM Calls 阈值与 Actions Artifact。
- [x] 建立门禁后 CD：仅对通过 Release Gate 的同一 Git SHA 构建镜像，发布 GHCR 不可变 SHA Tag 并生成 Provenance Attestation。
- [x] 完成 Resilience V1：LLM/RAG/Tool 统一故障分类、超时、有界 Retry、进程内 Circuit Breaker、Fallback、AgentState/Risk/OTel 联动与高风险禁重试。
- [x] 完成 Tool Governance V2.1：持久化保存脱敏后的 Tool 调用审计；高风险写 Action 的参数经加密保存并用 HMAC 防篡改，审批需要由其他人完成，状态用乐观并只追加事件的方式记录。
- [x] 完成 Tool Governance V2.2：写 Action 业务幂等键、Transactional Outbox、异步 Worker、数据库租约/乐观抢占、unknown 自动对账、Retry/DLQ、状态事件补写、显式补偿和版本化 Policy 回放。
- [x] Tool Governance V2.3：整单退款跨 Action 去重、旧记录保护、Worker 续租与 fencing token、unknown/DLQ 外部核实 API 和人工处理台联动；回复审批保持独立。
- [x] 增加专用 PostgreSQL 多进程故障演练脚本和 CI 门禁，使用持久化模拟 OMS；实际验证结果以运行报告为准。
- [x] 2026-10-09，`c9d1bf0` 的 CI 运行 `37912408689` 已实际通过 PostgreSQL 16 多进程演练：并发申请去重、跨进程续租、外部提交后强杀再对账、旧 Worker fencing。
- [x] 流程演示页增加完整 Action 状态机、四类记录关系说明及六套故障时序图，可逐步查看 Action / Outbox / Lease / Review 快照；前端测试核对全部迁移与后端一致。
- [ ] 完善同一 Action 重复核实：当前 resolved Review 不会自动重新打开，原操作对账关闭核实后再发生补偿 unknown 时，需要明确重新开启与证据历史保留规则。
- [ ] 接入真实 OMS 后核对幂等、退款到账语义、对账权威性和凭证核实流程，不能以 Mock 或演练通过替代真实业务验收。
- [x] 完成 LangGraph Checkpoint + Durable Execution V1：SQLite/PostgreSQL Saver、Approval Gate interrupt/resume、AgentExecution、数据库恢复租约、启动扫描和主管重试 API。
- [x] 完成 Skill Framework V1：6 个版本化 Skill 覆盖 8 个 Intent，确定性 Selector、Registry Hash、Tool Allowlist，并关联 State/Checkpoint/OTel/AgentRun/Baseline。
- [x] 完成 DecisionProvider V1：可选 Jev System One，复核 Analyzer 规则候选与 QA 正向证据，包含脱敏、限长、Trace/Metrics、超时/熔断与规则/LLM 分层 Fallback。
- [ ] 引入 Alembic，并为 Feedback Pipeline 新表生成生产 Migration。
- [ ] 增加训练样本人工复核状态、删除请求和数据保留周期。

## P1

- [x] 售后 Tool / RAG 扩充：9 个注册工具、12 篇新增中文演示文档、中文词法召回，新增查询按意图进入 Workflow 和 Resolver，并经过 Skill `v1.1` 权限及审计。
- [ ] 将物流、账务、保修和服务状态演示适配器替换为真实接口，并补充认证、契约测试、数据更新时间和生产可用性检查；目前不宣称真实业务接入。

- [x] 实现 Memory V1：Conversation/Message/Snapshot 结构化持久化、追加为主的消息、Redis revision Cache、会话归属、有界 Context Assembly、实体续接和 HITL 回写。
- [ ] 增加多轮 Dataset/Workflow Replay 与 Quality Gate，覆盖指代、Slot 延续、跨会话隔离、Memory Poisoning、Token 和延迟增量。
- [ ] 将用户页 Demo 客户选择器替换为真实身份/租户绑定，再宣称生产级会话隔离。
- [ ] 为 Conversation/Memory 表增加 Alembic Migration、保留期、删除请求和归档策略。
- [ ] 建设 Dataset Registry、数据版本和不可变 Snapshot。
- [ ] 增加 Train / Validation / Test 划分及数据泄漏检查。
- [ ] 扩充 Synthetic Golden Dataset，并建立稳定回归基线。
- [x] 增加 PromptOps / EvalOps V1：为三个节点保存内容快照，在一次运行中绑定 Prompt 版本，让 Agent Run 和 Trace 能查到使用的版本，并支持成对评测、校验证据、晋级和回滚。
- [ ] 增加按会话灰度、线上 A/B、自动回滚和人工校准的语义发布门禁；V1 的 Mock 通过不代表 Prompt 语义质量通过。
- [ ] 为 Jev DecisionProvider 增加 Shadow Mode 数据校准和故障率/置信度分布看板；暂不将 Jev 用作离线 Evaluation Judge。
- [x] 根据首次真实 100 条 Baseline 定位并修复问题，Case Pass 从 `0.54` 提升到 `0.99`；同时记录 Release Gate 的阈值和已知失败 Case 白名单。
- [ ] 增加 `ticket_status_events`；Tool Calling 持久化审计已在 V2.1 完成。
- [ ] 建设安全样本库、持久化安全事件、策略版本与 Risk Engine 阈值回放校准。
- [ ] 启用 Qwen3Guard Shadow Mode，用中英文安全数据校准 `Controversial / Unsafe` 处置策略。
- [ ] 将 Tool Governance V2.2 的 Mock OMS 幂等/对账/补偿契约接入真实 OMS，并完成跨服务契约测试。
- [ ] 为 Resilience 增加多副本 Circuit Breaker、故障注入/混沌测试；当前 Tool Outbox Retry/DLQ 不等于通用任务消息平台。
- [ ] Skill Framework V2：在需要不同节点组合时引入受控 Subgraph，增加 Registry 签名发布、变更 Diff 和 Skill 级评测门禁；V1 不支持动态插件或 LLM 自由选 Skill。

## P2

- [ ] 接入 SFT / DPO 训练任务与 Model Registry；当前只导出候选数据。
- [ ] 引入 vLLM 自托管 Serving。
- [ ] 采集 TTFT、TPOT、吞吐、并发、GPU 利用率和 Token 成本。
- [ ] 完成候选模型的发布流程：先做离线评测，再小范围灰度上线，出现问题时可回滚。

## 已知问题与风险

- [x] 2026-09-06 PromptOps V1 完成全量回归、原 100 条 PR Gate、当前/候选各 100 条 Mock 实验及独立 staging 晋级/回滚验证；未调用付费模型，未切换实际 production 指针。
- [ ] PromptOps V1 使用文件型 Registry，后续需增加分布式发布、签名制品、独立留出集和人工语义校准；不能将 Mock 实验结果写成真实 Prompt 效果提升。

- [x] 2026-09-08 完成 231 条全量测试，并通过固定 100 条 Baseline 的 PR Agent Quality Gate；CI / Docker 使用 Python 3.11。
- [ ] 旧的 Python 3.13 `.venv` 仍是混装环境，不再作为项目验收环境。
- [ ] 当前新增表依赖 SQLAlchemy `create_all`，不等同于生产 Schema Migration。
- [ ] Memory V1 的摘要/实体为确定性轻量实现，尚未建立长期语义 Memory；同 session 并发请求可安全追加，但两请求的起始上下文仍可能同时取到旧 revision。
- [ ] Checkpoint 尚无 TTL/归档、旧 Graph 多版本恢复与定期清理；AgentExecution 和 Saver DDL 尚未纳入 Alembic/受控 Migration。
- [ ] 默认 LLM、CRM、OMS 和工单 Adapter 仍为 Mock，尚无真实线上数据。
- [x] 用户咨询页已接入一次性评分和文字评价，Feedback Token 只在当次评价提交时使用。
- [x] 用户咨询页用浏览器已知 Session 列表从 SQL 加载最近 7 天安全历史，待审或拒绝草稿不向用户公开。
- [ ] 训练候选属于敏感数据资产，生产环境还需对象存储加密、访问审计和生命周期策略。
- [ ] Qwen3Guard 默认未启用且尚无本项目真实运行指标，未知语义变体与误报率仍需通过持续红队样本验证。
- [ ] Risk Engine 阈值尚未基于真实客服运营数据校准，当前采用保守的 high / critical 转人工策略。
- [ ] Circuit Breaker 当前只在单进程内生效；`asyncio.to_thread` 超时不能终止已运行线程。V2.2 已用幂等键和对账保护受治理写 Tool，但真实 OMS 仍需验证同一契约。
- [ ] 用户咨询页当前使用演示客户选择器；生产接入前必须绑定真实用户身份与工单归属，并增加限流和异步处理完成通知。
