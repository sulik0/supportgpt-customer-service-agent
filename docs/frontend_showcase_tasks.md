# 前端求职展示：评估结论与 Codex 开发任务

## 结论

可以展示，工程完成度高于多数简历项目：有 design token、请求取消与竞态处理、未保存修改拦截、懒加载、键盘和读屏支持、按角色的路由权限。

问题不在质量，在于**后端最值钱的东西在前端看不到或点不了**。面试官打开页面的前 3 分钟只会看到一个普通聊天框。

## 做得不好的地方

### 影响展示效果（要改）

1. **聊天页是黑盒。** 提交后只有“正在处理您的问题……”，然后出一段文字（`CustomerSupportPage.jsx:277`）。意图识别、Skill 选择、工具调用、知识引用、风险判定全都不可见。`PublicSupportResponse` 只返回回复文本和状态，这些信息要登录后台、进运行监控才看得到。
2. **退款写操作在界面上走不通。** 提议 → 另一人审批 → 入队 → Worker 执行 → `unknown` 对账，这是项目最有区分度的设计，但 `client.js` 里没有 `/tool-actions` 的提议、`/decision`、`/execute`、`/compensate`，也没有 `/tool-audits`、`/tool-outbox`。前端只有一张静态状态机图和“事后核实”表单（`ToolReviewPanel.jsx`）。面试官无法现场操作一遍。
3. **评测结果完全没有界面。** 100 条 Baseline 门禁、Diff Report 是最硬的证据，前端零呈现（`DESIGN.md` 2.2 节明确写了不做）。
4. **面试官进不了后台。** 公网关闭注册，登录页没有演示账号提示（`App.jsx:158-162`）。人工处理台、运行监控、资源管理三个页面等于不存在。
5. **Trace 入口是死链。** 没配 `VITE_LANGSMITH_PROJECT_URL` 时，按钮跳 LangSmith 首页，页面顶部还挂一条黄色警告（`ObservabilityPage.jsx:318`）。
6. **README 没有截图、GIF 和在线地址。** 多数面试官不会本地跑项目。

7. **页面文字太多、信息太密。** 流程页每个节点有一段 100 字以上的设计说明，底部还有四段长文和六条“实现边界”；同一句提示在一个页面里出现两三次（聊天页“需要人工确认时，页面会说明”出现 3 次，工单页“已保存的结果”出现 3 次）；运行监控的页面标题和 hero 标题重复。读者找不到重点。

### 细节问题

- 订单金额写死 `$`，演示订单是人民币（`TicketDetails.jsx:253`）。
- 业务操作轨迹的时间直接输出原始 ISO 字符串（`ToolReviewPanel.jsx:83`），其他页面都转成了北京时间。
- 运行详情里工具状态显示英文原值（`ObservabilityPage.jsx:175`），工单页同一字段有中文映射（`TicketDetails.jsx:263`）。
- 演示客户前三位是“简·多伊 / 约翰·史密斯 / 艾克米公司”，占位感明显。
- 免责声明过多：“静态演示”“不代表什么”“均为虚构演示数据”在多个页面重复出现，削弱可信度。保留一处 Demo 标记即可。
- “真实工作流测试例子”是最好的演示入口，却折叠在输入框上方的 `<details>` 里（`CustomerSupportPage.jsx:312`）。
- 流程页的“7 个节点 / 6 个 Skill / 9 个工具”是写死的字符串（`WorkflowPage.jsx:172`）。
- `App.jsx` 的 JSX 挤在超长单行里（第 141、162、166 行），点开源码观感差。没有 ESLint / Prettier，前端测试只有 3 个文件。

## 给 Codex 的指令

任务 1、2、3 依次做，不能并行（都改 `CustomerSupportPage.jsx`，任务 2 还会改所有页面的文案）。任务 4 和 5 可以并行。每条指令都要附上下面的“通用约束”。

### 通用约束（每个任务都贴）

```
- 先读 CLAUDE.md 和 docs/00_PROJECT_CONTEXT.md、docs/07_AI_HANDOFF.md，遵守“必须保持的设计”7 条。
- 这台机器是 MacBook Air M1 8GB，用户同时在用。只跑和改动相关的测试；
  全量 pytest 和 run_ci_quality_gate.py 预计几分钟以上，跑之前先问用户，只跑一次。
- 不要边改边开浏览器或 dev server 看效果；需要截图时攒到最后开一次，
  窗口不超过 1280×720，用完立刻关。开之前检查有没有别的会话在跑同类进程。
- 不调用真实模型（不带 --confirm-live）。
- 复用 frontend/src/components/ui.jsx 的 Badge / Button / Card / Chip / MetricCard / Overlay
  和 styles/tokens.css 的变量，不引入 UI 框架、图表库、路由库。
- 新增界面的文案遵守任务 2 的“文案规则”：一个区块一个标题加最多一句说明，
  能用标签、键值对、步骤条表达的不写成段落，同一句提示一个页面只出现一次。
- 代码注释用简短中文。改完同步 docs/DESIGN.md 第 2.2 节的页面功能表和 docs/08_TODO.md。
- 完成后 npm test、npm run build 通过；改了后端的要补 pytest 用例。
```

### 任务 1：演示入口与细节修复（半天，纯前端）

```
目标：让第一次打开页面的人 3 分钟内看到项目重点，并能进入后台。

1. 登录页演示账号：新增环境变量 VITE_DEMO_ACCOUNTS（JSON，形如
   [{"label":"客服","username":"...","password":"..."}]）。配置了才在登录表单下显示
   “演示账号”按钮，点击填入用户名密码，不自动提交。未配置时界面不变。
   账号密码只从环境变量读，不写进仓库；在 frontend/.env.example 里加注释示例。
2. 用户咨询页：把 WORKFLOW_EXAMPLES 从折叠的 <details> 提到对话为空时的显著位置，
   做成 3～4 张可点击的场景卡（标题 + 一句话说明会触发什么：工具查询 / 转人工审批 /
   安全拦截）。点击后仍然只填入、不自动发送。
3. 修复：
   - TicketDetails.jsx 订单金额的货币符号按订单币种显示（先看 /customer-context 返回里
     有没有 currency 字段；没有就按后端 Mock 数据的实际币种处理，不要猜）。
   - ToolReviewPanel.jsx 的事件时间改用 ObservabilityPage.jsx 的 formatDate
     （把它抽到共享文件，两处复用）。
   - ObservabilityPage.jsx 运行详情的工具状态复用 TicketDetails.jsx 的中文映射（同样抽出共享）。
   - 未配置 VITE_LANGSMITH_PROJECT_URL 时，隐藏“打开 LangSmith”按钮和黄色警告，
     只保留 Trace ID 和复制按钮。
4. 用 Prettier 默认配置格式化 frontend/src（加 devDependency 和 npm run format），
   这一步单独一个 commit，不混入逻辑改动。
```

### 任务 2：文案精简与信息分层（1 天，纯前端）

```
目标：降低所有页面的文字量和信息密度，让读者一眼看到重点。只改文案和信息层级，
不改功能、接口、权限和交互流程。

文案规则（全站适用）：
- 一个区块 = 一个标题 + 最多一句说明（不超过 30 个汉字）。
- 优先删除，其次改成结构化表达（标签、键值对、步骤条、表格），最后才是折叠。
  不要把整段文字原样塞进 <details> 就算完成。
- 同一句提示在一个页面只出现一次；页面标题、副标题、hero 标题不重复说同一件事。
- 每个页面最多一处 Demo 标记（用 Badge），删掉“不代表什么”“均为虚构演示数据”
  “不会实际运行”这类重复声明。
- 中英双写的术语（如“Checkpoint（检查点）”）每个页面只在第一次出现时双写。
- 不能删的内容：操作前的风险提示和确认弹窗文案、错误提示、读屏用的 aria-label 和 sr-only 文本、
  权限说明（如“需要另一位主管审批”）。
- 不改变事实。删掉的架构说明不需要搬到别处，docs/01_ARCHITECTURE.md 已有。

逐页要求：
1. WorkflowPage.jsx（最密，重点处理）
   - NODES 每个节点：summary 压到 20 字以内；design 从一整段改为最多 3 条要点，每条不超过 25 字；
     input / output 各一行。
   - 删除“节点之间传递什么？”“这个演示不代表什么？”两张卡片，必要信息并入一行脚注。
   - “流程之外，系统如何可靠运行”四段：每段改为标题 + 最多 2 条要点。
   - hero 段落压成一句；场景区标题下的说明段删除；SCENARIOS 的 result 压到 40 字以内。
   - ToolLoopGuide.jsx 按同样规则处理。
2. ToolGovernanceDiagram.jsx 和 toolGovernanceDemo.js
   - “先分清四类记录”四张卡：只留问题句 + 一句回答。
   - “讲解时需要说明的实现边界”六条：默认折叠，每条压到 40 字以内。
   - 状态机右侧的权限说明拆成清单；“Outbox 的状态”“Review 的状态”各留状态流转一行 + 一句说明。
   - 迁移命令的 label 和故障场景步骤的 note 逐条压短，状态名、命令名、迁移关系不许改
     （tests/toolGovernanceDemo.test.js 会核对与后端一致）。
3. CustomerSupportPage.jsx
   - “需要人工确认时，页面会说明”在 hero、侧栏、隐私提示出现 3 次，只保留一处。
   - hero 副标题一句；页脚、隐私提示、演示客户提示合并精简。
   - 右侧“我们能帮您”只剩 3 个标签时，并入 hero，去掉单独侧栏。
   - 字数统计只在超过 4500 字时显示。
4. TicketDetails.jsx 和 ToolReviewPanel.jsx
   - “已保存的结果”在提示条、刷新按钮、卡片副标题出现 3 次，只保留一处。
   - 卡片副标题里的数据来源（“来自本地 CRM 演示适配器”）改成 Badge。
   - 空状态说明压成一句；ToolReviewPanel 顶部说明压成一句，但保留“不能重复提交退款”。
5. ObservabilityPage.jsx
   - 顶栏已有页面标题和副标题，删除页面内 hero 的标题和段落，只留操作按钮。
   - MetricCard 的 hint 不超过 12 字。
   - 列表每格只留主值，第二行的次要信息（模型名、质量判断文字、Trace 前缀）移到详情抽屉。
   - 详情抽屉里的两段说明文字压成一句。
6. App.jsx 登录页左栏：标题 + 最多 3 个短要点，删掉段落。
7. ResourceManagementPage.jsx：按文案规则自行检查处理。

验收：
- 写一个一次性的 node 脚本（不提交），统计 frontend/src 下每个 .jsx / .js 文件里
  字符串和 JSX 文本中的汉字数，改动前后各跑一次，把对比表贴在交付说明里。
  参考目标：流程页相关三个文件合计减少 40% 以上，其余页面减少 25% 以上。
  达不到时说明哪些内容因为“不能删”而保留。
- 检查 tests/ 下是否有断言具体文案的用例，同步更新。
- 这个任务不需要开浏览器。
```

### 任务 3：聊天页“Agent 处理过程”面板（1～2 天，前后端）★ 最重要

```
目标：用户在聊天页提交问题后，回复旁边能展开看到这次 Agent 做了什么。

后端：
- 新增 GET /support/runs/{agent_run_id}/insight，只在 PUBLIC_DEMO_ISOLATION_ENABLED 为真时可用，
  否则 404。用请求头携带 feedback_token 证明这次运行属于当前访客；
  校验逻辑复用 /feedback/user 现有的 token 校验，不新写一套。同样走 public_rate_limiter。
- 返回字段只取白名单：workflow_path、intent、department、priority、analyzer 置信度、
  skill 名称与版本、tool_calls 的 tool_name/status/latency_ms、citations 的 source（不含正文）、
  qa_score、risk_level、review_reasons、approval_required、latency_seconds、token 合计。
  不返回 input_text、output_text、工具参数、待审草稿、异常正文。
- 数据来自已保存的 AgentRun，不触发新 Workflow。
- 补 pytest：token 不匹配 403、开关关闭 404、响应里不含上述禁止字段。
- 同步 docs/05_ENGINEERING_GUIDE.md 的 API 表。

前端（CustomerSupportPage.jsx）：
- result 里有 agent_run_id 和 feedback_token 时，在该条回复下方显示“查看处理过程”。
- 展开后是一条竖向步骤线：每个 workflow_path 节点一行，节点名用 ObservabilityPage.jsx 的
  NODE_LABELS（抽成共享常量）。在对应节点下挂载：意图与置信度、选中的 Skill、
  调用的工具及状态和耗时、引用的知识来源、QA 分、风险等级和转人工原因。
- 被跳过的节点（例如安全短路时的 Tool / RAG / Resolver / QA）用灰色显示“本次跳过”，
  完整节点顺序参照 WorkflowPage.jsx 的 STAGES。
- 底部一行：总耗时、Token 合计、“在流程图中查看”（跳 #workflow）。
- 转人工（pending_human）的请求也要能看：如果后端此时没有 feedback_token，
  在任务里说明并和用户确认怎么处理，不要自己放宽鉴权。
- tests/fixtures/server.mjs 加上这个接口的假数据，保证 npm run dev:demo 可用。
```

### 任务 4：工单详情里的退款操作面板（2 天，以前端为主）

```
目标：在人工处理台能完整走一遍 提议 → 另一人审批 → 执行 → 查看状态。

- 先读 src/main.py 里 /tool-actions 的提议、/decision、/execute、/compensate、
  GET /tool-actions、/tool-audits、/tool-outbox 的请求和响应 Model，以及
  src/tools/governance.py、action_state_machine.py。不改后端的状态机和权限。
- client.js 补齐这些接口的调用。
- 在 TicketDetails.jsx 右侧栏（ToolReviewPanel 上方）新增“业务操作”卡片：
  · 列出这张工单关联的 Action：工具名、当前状态、脱敏参数摘要、事件时间线
    （复用 ToolReviewPanel.jsx 的 STATES 映射和 workspace-tool-trace 样式）。
  · “发起退款申请”表单：字段以后端 Schema 为准。
  · 按后端返回的状态和当前角色显示可用按钮：审批通过 / 拒绝、执行、发起补偿。
    申请人自己不能审批——按钮禁用并写明“需要另一位主管或管理员审批”。
  · 所有写操作前用 useConfirm 确认，带上 expected_version；409 时提示“状态已变化，请刷新”。
  · 执行后状态是 queued，界面写“已入队，等待后台执行”，不能写“退款成功”。
    提供手动刷新，不做自动轮询。
- 权限、是否可操作一律以后端返回为准，前端只做展示层的禁用。
- fixtures/server.mjs 补假数据；前端加一个纯函数测试：给定 Action 状态和角色，返回哪些按钮可用。
- 流程页“高风险操作与故障恢复”里那句“不提供审批、重试或退款按钮”相应更新。
```

### 任务 5：质量评测页（1 天，前端 + 一个脚本）

```
目标：后台新增只读的“质量评测”页，展示 Baseline 门禁结果。

- evaluation/reports/ 不进仓库，所以不新增读报告的 API。改为：
  写 scripts/export_eval_snapshot.py，读取 evaluation/reports/baseline_v1/ 的 latest 报告
  和 baseline_diff_latest.json，输出精简快照到 frontend/public/eval-snapshot.json（提交进仓库）。
  先读报告的实际结构再决定字段，至少包含：数据集条数与 SHA256、生成时间、对应 git commit、
  总通过率、按意图或类别的通过率、门禁各项阈值与实际值（来自 evaluation/quality_gate_policy.json）、
  与上一次相比新增失败和修复的用例数。不包含用例原文中的敏感内容。
- 不修改 evaluation/baseline 数据集，不改门禁阈值。脚本只读报告、不跑评测。
- 新页面 #staff/evals（manager / admin 可见），导航加一项：
  · 顶部 MetricCard：通过率、用例数、门禁是否通过、快照时间和 commit。
  · 分类通过率用纯 CSS 横向条形（不引图表库），每行有数字。
  · 门禁项表格：指标、阈值、实际值、是否通过。
  · 快照文件不存在时显示空状态和生成命令。
- navigation.js 的 VIEWS、App.jsx 的导航和权限判断同步；补 parseRoute 的测试。
```

### 任务 6：README 展示素材（任务 1～3 完成后做，半天）

```
- 用 npm run demo:api + npm run dev:demo（假接口，不需要后端和模型）截 4 张图：
  聊天页展开处理过程、人工处理台工单详情、运行监控详情、流程页状态机。
  窗口 1280×720，截完立刻关闭 dev server 和浏览器。存到 docs/images/。
- README 顶部加“30 秒看懂”小节：一句话定位 + 4 张图 + 三步演示路径
  （选场景卡提问 → 展开处理过程 → 用演示账号进后台审批）。
- 有在线地址的话放在最前面；没有就不写，不要编造链接。
```

### 可选（收益高但风险也高，先不做）

节点级实时进度（SSE）：后端目前没有任何流式接口，`/support/requests` 还牵涉持久执行和 `interrupt`，改动面大。任务 3 的事后面板已经覆盖大部分展示价值，等前面都完成再评估。

## 建议的取舍

时间有限时只做 **任务 1 → 2 → 3 → 6**（约 4 天），这四项解决“打开看不到亮点”“文字太密”和“进不了后台”。任务 4、5 是加分项，面试时能现场演示退款审批流程和门禁数据。

如果投纯前端岗：这个前端没有 TypeScript、没有组件测试、没有状态管理和复杂交互，不足以单独支撑，需要另外的项目。

## 验证

- 每个任务：`cd frontend && npm test && npm run build`。
- 任务 3：`python -m pytest tests/<新增用例文件> -q`，再跑一次 `python -m compileall -q src scripts tests`。
- 任务 3、4 改动了接口或详情读取路径，全部完成后跑一次 `python scripts/run_ci_quality_gate.py`（先问用户再跑）。
- 最终人工验收一次：`npm run demo:api` + `npm run dev:demo`，按 README 的三步路径走一遍。

