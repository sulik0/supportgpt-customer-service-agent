# SupportGPT 前端改版：定稿规范

本目录是 2026 年前端改版的定稿效果图和视觉规范，所有开发会话以此为准。

| 文件 | 内容 |
|---|---|
| `Main.html` | 员工后台外壳 + 人工处理台（队列 / 工单详情 / 工具审批与处理轨迹） |
| `Chat.html` | 客户咨询页 |
| `Monitor.html` | Agent 运行监控 |
| `Login.html` | 员工登录 |

效果图是静态 HTML，样式全部内联，可直接用浏览器打开或读源码取数值。`[N]`、`[x.x s]` 这类方括号内容是占位，实际显示接口数据。

## 视觉方向

企业级 AI 运营台：冷灰底、深色侧栏、靛蓝主色；状态色只用于状态，不做装饰；ID、数字、代码用等宽字体。不使用渐变铺底、左侧色条卡片、emoji。

## Design Tokens

### 颜色

| Token | 值 | 用途 |
|---|---|---|
| `--color-bg` | `#F6F7F9` | 页面底色 |
| `--color-surface` | `#FFFFFF` | 卡片、顶栏 |
| `--color-surface-muted` | `#F9FAFB` | 表头、引用块、次级区域 |
| `--color-surface-sunken` | `#F2F4F7` | 中性标签、进度条底 |
| `--color-border` | `#E4E7EC` | 卡片边框 |
| `--color-border-subtle` | `#EAECF0` | 卡片内分隔线 |
| `--color-border-strong` | `#D0D5DD` | 输入框、次级按钮边框 |
| `--color-text` | `#0F172A` | 正文 |
| `--color-text-secondary` | `#475467` | 说明文字（白底对比度 ≥ 7:1） |
| `--color-text-tertiary` | `#667085` | 占位、未开始状态 |
| `--color-text-control` | `#344054` | 次级按钮文字 |
| `--color-accent` | `#4338CA` | 主按钮、链接、选中 |
| `--color-accent-hover` | `#3730A3` | 主色悬停 / 主色上的文字 |
| `--color-accent-bright` | `#4F46E5` | Logo、侧栏徽标、当前步骤点 |
| `--color-accent-soft` | `#EEF0FF` | 主色浅底标签 |
| `--color-accent-wash` | `#F5F6FF` | 选中行 / 选中卡片底 |
| `--color-accent-border` | `#C7CBFB` | 需审批卡片边框 |
| `--color-success` / `-soft` | `#067647` / `#ECFDF3` | 通过、完成 |
| `--color-success-dot` | `#12B76A` | 状态圆点 |
| `--color-warning` / `-soft` / `-border` | `#B54708` / `#FFFAEB` / `#FEDF89` | 高优先级、等待 |
| `--color-danger` / `-soft` / `-border` | `#B42318` / `#FEF3F2` / `#FDA29B` | 失败、驳回 |
| `--color-nav-bg` | `#0E1325` | 侧栏底 |
| `--color-nav-active` | `#1E2540` | 侧栏当前项 |
| `--color-nav-panel` | `#161C33` | 侧栏底部用户卡 |
| `--color-nav-text` | `#C7CDDB` | 侧栏文字 |
| `--color-nav-text-muted` | `#98A2B8` | 侧栏次级文字 |
| `--color-code-bg` / `--color-code-text` | `#0E1325` / `#D6DAF5` | 工具参数代码块 |

### 字体

- 正文：`'Inter', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', system-ui, sans-serif`
- 等宽：`'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace`（用于工单号、运行 ID、指标数字、工具名、JSON）
- 不新增字体依赖，不从外网加载字体；本机没有时走后备字体。
- 字号：12 / 13 / 14（正文）/ 15（卡片标题）/ 20（页面标题）/ 22（工单标题）/ 26（指标数字）/ 34（登录页大标题）
- 字重：400 / 500 / 600

### 间距、圆角、阴影

- 间距沿用 4 的倍数：4 / 8 / 12 / 16 / 20 / 24 / 28 / 32
- 圆角：标签 6、控件 8、代码块与内嵌块 10、卡片 12、对话卡与登录卡 16、胶囊按钮 999
- 阴影：只有浮层（登录卡、弹窗、抽屉）用 `0 12px 32px -12px rgba(16,24,40,.12)`；卡片只用边框
- 控件高度：主要操作按钮 44、顶栏按钮 / 输入框 40、筛选胶囊 30–32

## 组件约定

- **Button**：`primary`（靛蓝实底白字）、`secondary`（白底灰边）、`danger`（白底红边红字）、`ghost`（无边框）；尺寸 `md` 40 / `lg` 44
- **Badge**：`neutral` / `accent` / `success` / `warning` / `danger`，6px 圆角，12px 字号，浅底深字
- **Card**：白底、1px 边框、12px 圆角；可选标题行（15px/600，下方 1px 分隔线）
- **MetricCard**：13px 说明 + 26px 等宽数字
- **Chip**：30px 高胶囊筛选，选中态为 accent-soft 底 + accent 边
- **Skeleton / EmptyState / LoadingState / Notice**：统一用以上 Token

## 外壳布局

- 员工后台：左侧 248px 深色侧栏（Logo、分组标题"工作台"、导航项带计数徽标、底部服务状态 + 用户卡 + 退出），右侧内容区；内容区顶栏白底，含页面标题、说明和页面级操作。
- 客户页：无侧栏，白色顶栏 + 居中最大宽 1280 的内容区。
- 1023px 以下侧栏收起为抽屉，375px 宽不出现横向滚动。

## 范围规则

- 只改外观和布局，不改业务逻辑、接口、路由、权限判断。
- 效果图里的元素如果当前功能没有对应数据或行为（例如"转交其他团队"、时间范围切换），不要新增功能，直接省略或保留现有控件。
- 中文文案含义不变，可以微调措辞使其更短。
