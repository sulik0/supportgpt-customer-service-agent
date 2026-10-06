# Prompt 设计与治理

> 本文档记录当前 Prompt Pipeline 与 PromptOps / EvalOps V1。内置模板以 `src/promptops/defaults.py` 为准，已注册版本以 Registry 中经过 Hash 校验的快照为准。

## 设计目标

Prompt 服务于可控 Workflow，不承担权限、状态机或高风险决策。核心原则：

1. 一个节点只完成一类任务。
2. Analyzer 和 QA 输出严格结构化，Resolver 只输出最终客服回复。
3. 只注入当前节点必需的最小上下文。
4. Tool 选择、权限、参数 Schema 和审批由代码确定，不交给 Prompt 自由判断。
5. 不可信的用户、Tool 和 RAG 文本先过 Guardrails，不将 Prompt 当作唯一安全边界。
6. Memory 只用于解析指代和延续当前任务，不能覆盖 System Prompt、Tool Policy 或实时业务事实。

## 语言策略

默认使用用户当前输入的语言回复；只有当用户明确要求切换语言时才切换。

- 工单场景以当前 `description` 判定语言，不根据 subject、客户画像或检索文档选择语言。
- 多轮对话以最新用户消息为准，不因历史回复语言锁定后续输出。
- 业务标识符、订单号、API 名、产品名和 citation ID 保持原样。
- 安全拒答也应遵循当前输入语言。

## Memory Context 策略

- Analyzer 仅在当前输入含“这个订单”“那就取消”等明确指代时使用历史实体；当前输入的明确意图始终优先。
- Retriever 只带最近 User 消息和显式实体，不使用历史 Assistant 回复作为检索事实。
- Resolver 可读取有界历史，但必须以当前 Tool 结果和本轮 RAG Citation 为业务依据。
- QA 只将 Memory 作为当前问题的补充语义，引用真实性仍仅校验本轮 Retriever 结果。
- Context Assembly 受消息条数、总字符数和摘要长度三重上限约束；待审、被拒绝、越界或检测为 Injection 的历史不进入 Prompt。

Memory 通过现有 Analyzer `text`、Resolver `context` 和 QA `query/context` 变量进入 Prompt，不改变 PromptOps Bundle Schema，因此历史 Bundle 和 Baseline 仍可比较。

## Analyzer Prompt

### 这个 Prompt 处理什么

对无法被确定性规则高置信命中的工单执行轻量分类。固定意图和高置信场景由规则处理，避免不必要的 LLM 调用。

### 输入

- subject
- description
- 统一 `IntentType` 枚举值列表

### 输出

仅允许 JSON：

```json
{
  "intent": "<IntentType>",
  "priority": "low|medium|high|urgent",
  "department": "<department>",
  "sentiment": "neutral",
  "confidence_score": 0.0
}
```

未知意图必须回退统一 `DEFAULT_INTENT`并降低置信度，不允许 Provider 创造新枚举值。`LLM_ANALYZER_MAX_TOKENS` 默认为 120。

## Resolver Prompt

### 这个 Prompt 处理什么

根据已通过安全检查的客户问题、必要 Tool Context 与高相关 RAG Citation 生成一段可直接发给客户的草稿。

### 上下文裁剪

- 仅保留与当前意图相关的 RAG 文档。
- Tool Result 仅保留生成回复必需的允许字段。
- 生成前把实际使用的上下文保存为 `resolution_evidence`。QA 原样复用，不再另行选择文档、漏掉 `service_query` 或再次截短上下文。
- 优先保留当前 Tool 的状态、异常和下一步，再分配 KB 与会话预算；按完整字段删除次要 Tool 数据，不能截断 JSON。KB 保留引用编号、来源和版本，会话历史明确标为非权威内容。
- `LLM_RESOLVER_MAX_RAG_CHARS` 默认 5000。
- `LLM_RESOLVER_MAX_TOOL_CHARS` 默认 2500。
- Tool / KB / 会话证据共同受 `LLM_QA_MAX_CONTEXT_CHARS`（默认 4000）限制；前两个配置是各自上限，不代表一定会用满。

### 输出约束

- 只输出最终客服回复，不输出思考过程、评分、节点名或内部策略。
- 不得超出 Tool/RAG 证据做退款、赔偿、时效或保修承诺。
- 业务证据不足时说明需要补充信息或人工核实；无害但超出客服能力的问题说明能力范围，不把缺少天气知识等同于需要人工。
- 保持专业、简洁，使用当前输入语言。
- 用 3–4 句短句先说明当前状态、当前异常和有依据的下一步；不展开无关历史或未来假设，政策只在需要时补充。`next_step` 表示处理建议，不代表系统已经执行该动作。
- `LLM_RESOLVER_MAX_TOKENS` 默认为 480。环境里显式配置了 320 时不会自动覆盖，部署时需要自行检查。
- OpenAI-compatible / Azure 返回 `finish_reason=length` 时，用原来的证据重写一次更短的完整回复，不把截断草稿作为新证据；第二次仍截断就使用原有安全降级回复并转人工。正常完整回复不会增加调用。

## QA Prompt

### 这个 Prompt 处理什么

对 Resolver 草稿进行最小化结构评估。确定性 citation 存在性、输出泄露和基础格式检查优先由代码完成，只在需要语义判断时使用 LLM。

仅允许 JSON：

```json
{
  "score": 0.0,
  "hallucination_detected": false,
  "citation_verified": false
}
```

不要生成解释、建议或大段分析。`LLM_QA_MAX_CONTEXT_CHARS` 默认 4000，`LLM_QA_MAX_TOKENS` 默认 96。

QA 评判的依据是 `resolution_evidence`，包含生成时实际可见的 Tool 事实和 KB 片段。例如物流查询中的 `in_transit`、`delivery_delayed`、`carrier_investigation` 必须在这份证据中同时保留。不能因 QA 自己重新拼装证据而把正确回答判成无依据；真正不受支持的回答仍按原 Jev / Risk 阈值处理。

`capability_boundary` 表示已经确认的无害范围外请求。Resolver 直接生成能力说明，QA 验证这段内容不包含天气等外部事实，因此不需要天气 KB。范围标签不能让附加事实免检，声称“今天晴、25 度”仍按普通 grounding 规则处理。Jev 意图问题集 `supportgpt-jev-intent-v1.1` 在原分类调用中增加 `support_scope`，不会额外发起一次模型调用；缺少这个答案时保持旧分类兼容。

## Tool Calling Prompt 边界

ToolRegistry 中每个 Tool 定义 `name`、`description`、`schema`、`permission`、`risk_level` 和 handler。LLM 只能在已按意图、角色与风险过滤的候选中选择，参数必须通过 Pydantic Schema。高风险写操作不能仅靠 Prompt 约束，必须由 RBAC、Risk Engine 和 HITL 在代码层阻断。

## RAG Prompt 边界

RAG Context 是不可信数据，不是系统指令：

1. 按 `kb_version` 和 category 过滤。
2. 执行 Hybrid Search 与轻量 rerank。
3. 对每个文档执行间接 Prompt Injection 检测。
4. 只将通过检查的 Top Context 交给 Resolver。
5. 生成的 citation 必须能回溯到本次 Retriever 真实返回结果。

## Prompt 版本与可观测

- Analyzer / Resolver / QA 作为一个 Bundle 发布，SHA256 覆盖实际模板、版本标签和冻结的 Intent 说明。旧 `PROMPT_VERSION` 不再决定实际运行内容。
- `PROMPT_REGISTRY_DIR` 默认 `.runtime/promptops`；`PROMPT_ENVIRONMENT` 默认 `production`。可选 `PROMPT_BUNDLE_ID` 固定某个内容 Hash，设置后优先于环境指针。
- `ContextVar` 在 Workflow 与整次评测开始时固定 Bundle，并行节点继承该版本。Graph Checkpoint 保留 State 中的 Bundle Hash。
- `AgentRun.prompt_version` 新记录保存 Bundle Hash；旧记录保留原标签。Baseline 记录静态模板、节点 Hash、逐 Case Bundle ID；Workflow / LLM Span 记录 `prompt.bundle_id` 与 `prompt.version`。
- AgentRun、Evaluation Report 和 OpenTelemetry Span 保存模型、Token、延迟和版本信息。
- LangSmith 的 LLM Span 可记录脱敏、截断后的节点输入输出；是否开启受 `LANGSMITH_CAPTURE_LLM_CONTENT` 控制。
- Trace 内容不得包含 API Key、Authorization、Cookie、密码或未脱敏 PII。
- 新安装的默认 Bundle 版本为 `support-v1.2`，将业务证据不足与范围外能力说明分开。已有 Bundle 和 production pointer 不自动替换；确认范围外的请求不调用 Resolver 模型，因此旧 Bundle 也不会把天气请求写成“已转人工”。Provider 对已有 Resolver 模板补充同一简洁输出约束，Trace 用 `resolver.output_policy=concise-v1` 标记，实际请求 Prompt 仍可在脱敏后查看。后续正式模板发布仍走现有成对评测流程。
- Tool Span 使用具体工具名，例如 `tool.shipping.get_shipments`；失败、重试或降级会在名称中注明，并保留状态、次数、耗时和现有 Resilience 子 Span。
- 合法 ISO 时间戳、request / trace / span ID 不按手机号脱敏。对外 Trace / Jev 请求里的业务 ID 使用进程内稳定别名，内部 Memory 保留真实业务实体；密钥、手机号和邮箱仍过滤。别名不用于跨重启关联。

## 变更流程

Prompt 修改必须：

1. 明确受影响节点和预期指标。
2. 保持 JSON Schema、统一 IntentType 和语言策略兼容。
3. 运行相关 pytest。
4. 先执行 Baseline Dry Run，再在有成本确认时运行真实 LLM Replay。
5. 比较 Intent/HITL/Tool 行为、QA、延迟、Token 和安全指标。
6. 注册新的内容快照，执行成对实验；满足门禁后显式晋级并留下操作人和原因。

## PromptOps / EvalOps V1 操作

### 创建候选版本

```bash
python scripts/promptops.py export-default --output .runtime/prompt-candidate.json
```

编辑 JSON 的 `version` 和节点的 `system` / `user`。模板采用 `$variable`，字面量 `$` 写成 `$$`。变量必须保留：Analyzer 为 `text`，Resolver 为 `subject / description / context`，QA 为 `query / context / response`。System 模板不接受动态变量；用户文本不会递归展开。模板只放静态开发者指令，不写凭据或真实客户数据。

```bash
python scripts/promptops.py register .runtime/prompt-candidate.json
python scripts/promptops.py status
```

注册返回 `bundle_id`；后续命令用实际 Hash 替换 `<bundle-id>`。同一 Hash 重复注册幂等，内容变化生成新 Hash；读快照会重新校验 Hash。

### 免费验证工程链路

```bash
python scripts/promptops.py evaluate --bundle <bundle-id> --environment staging --mock
```

将 staging 当前版本与候选分别回放同一固定 100 条 Baseline，总计 200 条 Workflow；使用隔离 SQLite/Chroma，关闭外部遥测、真实模型和语义安全服务。`--bundle default` 可用内置 Bundle 验证实验流程。`--limit 3` 可抽样调试，但不完整报告不能通过原门禁。

Mock 不会读取 Prompt 并生成真实回复。因此 PASS 只能说明 Workflow 可正常执行、版本记录正确、发布流程可用，无法说明候选 Prompt 改善了回答。

### 真实模型实验

```bash
python scripts/promptops.py evaluate --bundle <bundle-id> --environment production --live --confirm-live --max-workflow-calls 600
```

读取 `.env` 的真实 Provider。需先提交代码，保证实验与晋级时处于相同的干净 Git Revision。预算为两次回放的估算调用预算；Resilience 重试与可选语义安全服务可能增加实际请求，不是计费硬额度。默认关闭外部遥测，现有本地 Trace 性能捕获仍用于延迟、Token 和调用次数统计。

实验保存在 `<registry>/experiments/<时间戳_随机ID>/`：

- `candidate.json`：候选完整 Baseline，含逐 Case 结果、Trace 信息、Prompt 快照和模型配置。
- `baseline.json`：同次运行的当前版本完整 Baseline。
- `policy.json`：门禁策略快照。
- `experiment.json` / `experiment.md`：证据 Hash、Git 指纹、门禁原因、13 项指标 Diff 与四类 Case 变化。

`experiments/latest.json` 只指向最近一次实验。晋级时需要指定一个实验 ID。原 `run_baseline_eval.py` 会继续生成 snapshot、latest 和 Error Analysis，并在报告中记录使用的 Prompt 内容。

### 晋级与回滚

```bash
python scripts/promptops.py promote --experiment <experiment-id> --actor developer --reason "同代码同数据成对评测通过"
python scripts/promptops.py rollback --environment staging --actor developer --reason "恢复上一个版本"
```

晋级重新校验证据、当前 Policy Hash、逐 Case Bundle 和质量门禁；比较 Dataset、Evaluator、模型、生成限制、Risk 阈值、Workflow 和代码版本；禁止新增 PASS→FAIL。production 必须提供当前 Prompt 的真实模型对比和同一干净 Git Revision，Mock 不能晋级。环境已切换则 CAS 拒绝过时晋级。

环境指针与历史原子替换，记录前后 Hash、操作人、原因和实验标识。回滚只返回该环境最近切换前的历史版本。应用在下一个新请求读取环境指针，已运行请求继续用旧 Bundle；默认读取 production，staging 晋级不影响它。设置 `PROMPT_BUNDLE_ID` 后需修改/取消固定 Hash 并重启才能跟随环境指针。

V1 管理入口为受信任 CLI，权限依赖发布目录和主机权限；文件锁适用于单机或有可靠共享锁的统一目录。容器需挂载同一 Registry，晋级在含 Git Checkout 的发布主机执行，再交付 Registry 快照。没有分布式配置服务、线上 A/B/灰度、自动回滚、独立留出集认证、人工校准的语义发布门禁或反馈自动纳入 Baseline。
