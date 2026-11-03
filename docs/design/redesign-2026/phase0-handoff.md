# SupportGPT Phase 0 交接

分支：`redesign/phase0-foundation`（从 main 创建）。仅本地提交，不 push、不合并。

## 完成范围

按定稿 README 重写 Token，保留全部旧变量别名；新增基础组件和独立 components.css；删除 layout.css 并按页面迁移；重做员工后台外壳、员工登录页和客户页顶栏。业务请求、API、路由、权限判断和导航守卫未改动。没有增加 npm 或字体依赖，没有暗色模式。

员工侧栏 248px，1023px 及以下使用现有 mobileNav 打开原生 dialog 抽屉，支持焦点隔离、Esc / 关闭按钮 / 遮罩关闭，回到桌面断点时关闭抽屉。客户内容容器最大宽 1280px。375px 使用单列表单、可换行顶栏和受视口约束的抽屉。

## 文件清单

路径均相对于仓库 `/Users/fayefufu/Downloads/居丽叶简历项目6：差旅出行助手/support-gpt-enterprise-review`。

- `frontend/src/styles/tokens.css`：定稿颜色、间距、字号、字体、圆角、阴影、控件尺寸及旧变量别名。
- `frontend/src/styles/base.css`：保留基础元素和可访问性规则；原基础组件样式迁往 components.css，输入框与禁用态改用 Token。
- 新增 `frontend/src/styles/components.css`：旧基础组件及新组件样式；兼容旧类名和 tone。
- 新增 `frontend/src/styles/shell.css`：员工侧栏/顶栏、登录、客户顶栏及公共容器。
- 删除 `frontend/src/styles/layout.css`。
- 新增 `frontend/src/styles/pages/support.css`、`workspace.css`、`observability.css`、`resources.css`：原页面样式及其响应式规则。
- `frontend/src/main.jsx`：改为引入 components.css、shell.css。
- `frontend/src/App.jsx`：外壳与登录页 JSX，复用真实队列计数和健康状态，移动抽屉表现。
- `frontend/src/components/ui.jsx`：新增六个组件；Button 增加 size；Overlay 增加 className、id。
- `frontend/src/components/CustomerSupportPage.jsx`：客户顶栏及 support.css import，正文未改。
- `frontend/src/components/TicketList.jsx`、`TicketDetails.jsx`、`ToolReviewPanel.jsx`、`MetricsGrid.jsx`：仅新增 workspace.css import。
- `frontend/src/components/ObservabilityPage.jsx`、`ResourceManagementPage.jsx`：仅新增对应页面 CSS import。
- 将 `docs/design/redesign-2026/README.md`、`Main.html`、`Login.html`、`Chat.html`、`Monitor.html` 一起纳入提交；增加 `phase0-css-migration.md` 逐段迁移表和本交接记录 `phase0-handoff.md`。

## layout.css 迁移

完整的逐段表见同目录 `phase0-css-migration.md`：记录原文件行号、每组选择器、媒体条件和目标文件，共 407 条归属记录。拆分时混合选择器按归属拆开，声明及原有顺序保留；同一媒体条件的相邻规则合并。

| 原文件区段 | 目标 |
|---|---|
| 3–178 后台框架 | shell.css；其中 mobile-detail-back 进入 workspace.css |
| 180–411 工作区、工单队列 | pages/workspace.css；section-label 为通用规则保留在 shell.css |
| 208–213 混合卡片选择器 | 分到 workspace.css、observability.css、resources.css、shell.css 各自的选择器 |
| 413–457 资源搜索、工具表格、工具审批提示、资源表单 | resource/tool-table/resource-form 进入 resources.css；tool-review-loading/saved-result-notice 进入 workspace.css |
| 458–879 工单详情、上下文、回复审批、评估、工具审批 | pages/workspace.css |
| 883–914 客户容器、顶栏及返回入口 | shell.css（随后按定稿重做） |
| 916–1258 客户页正文、对话、反馈、输入、页脚 | pages/support.css |
| 1260–1295 登录页 | shell.css（随后按定稿重做） |
| 1299–1328 监控/资源共用 hero | 按选择器分别进入 observability.css 与 resources.css |
| 1330–1607 监控 | pages/observability.css；其中 data-table 选择器归 resources.css |
| 1609–1892 资源、工具、Prompt、RAG | pages/resources.css |
| 1894–1918 MetricsGrid | pages/workspace.css |
| 1920–1942，≤1279px | workspace.css 与 resources.css |
| 1944–1976，≤1023px | shell.css、workspace.css、observability.css；外壳断点改为抽屉 |
| 1978–2186，≤767px | 随各选择器进入 shell.css 和四个页面文件；外壳响应式按定稿重做 |

源码比对确认 **382 个页面选择器**的声明和媒体条件完整保留。页面中原有硬编码颜色、8px 圆角、选中工单左色条等本轮不重做，留给 Phase 1。

## ui.jsx 新组件 API

新组件均支持 `className` 与原生元素属性（`...props`）。

| 组件 | 专用 props | 行为 |
|---|---|---|
| Card | `title?`, `actions?`, `children` | section 容器；可选带分隔线的标题/操作行，正文 padding 16 |
| Badge | `tone='neutral'`, `children` | neutral/accent/success/warning/danger；error 兼容 danger 样式 |
| MetricCard | `label`, `value`, `hint?` | 13px 说明、26px 等宽值；null/undefined 显示 `—`，0 保留 |
| Chip | `selected=false`, `children`, `onClick`, `disabled` | 原生 button，默认 type=button，aria-pressed 表示选中 |
| EmptyState | `icon?`, `title='暂无内容'`, `description?`, `actions?`, `children?` | 居中空态；icon 为 React 节点，作为装饰隐藏于读屏 |
| Skeleton | `width='100%'`, `height=16`, `style?` | 静态占位 span，aria-hidden；数值按 React CSS 尺寸处理；无持续动画 |

Button 增加 `size='md' | 'lg'`（40/44px），原 `tone`、`busy`、`disabled` 等 API 保留；primary/secondary/danger/ghost 与旧 quiet/danger-outline 可用。StatusBadge、Notice、LoadingState、NavLink 的既有 API 不变；Overlay 仅扩充可选 `className` 与 `id`，旧 drawer 行为保留。

## 省略的效果图元素

- 顶栏搜索框及 `/` 快捷键提示：没有顶栏搜索行为，原队列搜索保留。
- 登录页“服务连接正常”：登录前没有健康检查，不新增请求或显示假状态。
- 监控/资源等导航计数：没有现成数据，不填占位数。
- 处理台计数只在处理台队列加载成功时显示真实 `tickets.length`，其他页面不显示可能过期的计数。
- 不新增转交团队、时间范围切换等功能；效果图中的页面正文、示例 KPI 和模拟数据不属于本阶段。

## 验证与限制

`npm --prefix frontend test` 仅运行一次，结尾输出：

```text
1..19
# tests 19
# suites 0
# pass 19
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 71.68075
```

`npm --prefix frontend run build` 仅运行一次，结尾输出：

```text
dist/assets/TicketDetails-B0XInvbj.js             15.88 kB │ gzip:  6.02 kB
dist/assets/WorkflowPage-D4ArIxd1.js              30.95 kB │ gzip: 15.38 kB
dist/assets/index-Bai2lH3C.js                    170.90 kB │ gzip: 56.78 kB
✓ built in 1.17s
```

没有启动浏览器、截图、dev server，没有安装依赖。375px 的布局约束已在源码落实，但没有进行浏览器实测，不能将测试/构建通过视为视觉验收通过。

## Phase 1 注意事项

- 按四个页面 CSS 文件分工，处理台的四个组件共同维护 workspace.css，避免各自修改公共 shell.css。
- 懒加载页面自行 import 样式；混合选择器已经拆开，新增样式继续保持页面归属，避免依赖其他页面先加载。
- 新基础组件已备好，页面正文暂未批量替换；Phase 1 逐页采用并替换遗留硬编码样式。
- 不要删除旧 Token 别名：workflow.css、tool-governance-diagram.css 仍在使用。
- 审批会话重点验收 375/768/1023/1024px：顶栏换行、登录双栏转单栏、抽屉焦点与遮罩关闭、页面切换、详情/表格是否产生横向溢出。长用户名、错误文案和各权限角色也需覆盖。
- 原测试覆盖业务工具和导航等纯逻辑，不覆盖浏览器布局；视觉验收尚待审批会话。
