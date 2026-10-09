// 演示数据对应后端状态机；不触发审批、退款或 Worker 执行。
export const ACTION_STATES = [
  ['proposed', '已提出申请', 110, 85],
  ['pending_approval', '等待操作审批', 325, 85],
  ['approved', '操作已批准', 540, 85],
  ['queued', '等待后台执行', 755, 85],
  ['executing', '正在执行', 970, 85],
  ['succeeded', '执行成功', 1185, 85],
  ['rejected', '申请被拒绝', 325, 270],
  ['failed', '确认执行失败', 755, 270],
  ['unknown', '执行结果未知', 970, 465],
  ['reconciling', '正在查询实际结果', 755, 465],
  ['compensation_pending', '等待补偿', 1185, 465],
  ['compensating', '正在补偿', 1185, 650],
  ['compensation_unknown', '补偿结果未知', 1185, 835],
  ['compensated', '补偿完成', 970, 1020],
  ['compensation_failed', '确认补偿失败', 755, 1020],
].map(([id, label, x, y]) => ({ id, label, x, y }));

export const ACTION_TRANSITIONS = [
  ['proposed', 'pending_approval', 'request_approval', '提交操作审批'],
  ['pending_approval', 'approved', 'approve', '独立审批人批准'],
  ['pending_approval', 'rejected', 'reject', '独立审批人拒绝'],
  ['approved', 'queued', 'enqueue', '在同一事务内保存执行消息'],
  ['queued', 'executing', 'start_execution', 'Worker 持有效租约开始执行'],
  ['queued', 'failed', 'manual_failure', '满足人工核实条件后确认失败'],
  ['executing', 'succeeded', 'succeed', '外部返回明确成功'],
  ['executing', 'failed', 'fail', '外部返回明确失败'],
  ['executing', 'unknown', 'mark_unknown', '超时、失联或发现上次执行中断'],
  ['unknown', 'reconciling', 'start_reconciliation', '按原幂等键查询实际结果'],
  ['unknown', 'succeeded', 'manual_success', '人工凭外部证据确认成功'],
  ['unknown', 'failed', 'manual_failure', '人工凭外部证据确认失败'],
  ['reconciling', 'succeeded', 'reconcile_success', '查询确认外部成功'],
  ['reconciling', 'failed', 'reconcile_failure', '查询确认外部失败'],
  ['reconciling', 'unknown', 'reconcile_pending', '暂时无法确认，等待下一次查询'],
  ['reconciling', 'succeeded', 'manual_success', '人工凭外部证据确认成功'],
  ['reconciling', 'failed', 'manual_failure', '人工凭外部证据确认失败'],
  ['succeeded', 'compensation_pending', 'request_compensation', '单独发起补偿，不自动撤销成功结果'],
  ['compensation_pending', 'compensating', 'start_compensation', 'Worker 开始已获授权的补偿'],
  ['compensation_pending', 'compensation_failed', 'manual_compensation_failure', '满足核实条件后人工确认补偿失败'],
  ['compensating', 'compensated', 'compensate_success', '外部明确确认补偿成功'],
  ['compensating', 'compensation_failed', 'compensate_failure', '外部明确确认补偿失败'],
  ['compensating', 'compensation_unknown', 'mark_compensation_unknown', '补偿超时或进程中断，不盲目重复补偿'],
  ['compensation_unknown', 'compensated', 'manual_compensated', '人工核实补偿已经完成'],
  ['compensation_unknown', 'compensation_failed', 'manual_compensation_failure', '人工核实补偿失败'],
].map(([from, to, command, label]) => ({ from, to, command, label }));

export const PARTICIPANTS = ['客服 / 主管', 'Action / Review', 'Outbox / Lease', 'Worker A', 'Worker B', '外部订单系统'];

const step = (from, to, text, action, outbox, lease, review, note) => ({ from, to, text, action, outbox, lease, review, note });

export const FAULT_SCENARIOS = {
  timeout: {
    title: '退款超时，先查询实际结果',
    summary: '超时只说明没有及时收到结果，不说明退款失败。执行消息完成后，另一个对账消息负责查结果。',
    steps: [
      step(0, 1, '批准操作并提交执行', 'queued', 'execute: pending', '尚未领取', '无', 'Action 的 queued 状态与 execute 消息在同一事务中保存。'),
      step(3, 2, '领取 execute 消息并开始续租', 'executing', 'execute: processing', 'A 持有 v1，有效', '无', '领取版本仅为示例；实际版本由数据库更新产生。调用外部系统前先保存 executing。'),
      step(3, 5, '发送退款，使用固定幂等键', 'executing', 'execute: processing', 'A 定期续租', '无', '外部网络调用期间不长时间持有数据库行锁。'),
      step(5, 3, '请求超时，未收到明确结果', 'unknown', 'execute: processing；reconcile: pending', 'A 仍需通过租约校验', 'pending · unknown', '保存 unknown、对账消息和核实任务。不会重新发送退款。'),
      step(3, 2, '结束 execute 消息，释放租约', 'unknown', 'execute: succeeded；reconcile: pending', 'execute 租约释放', 'pending · unknown', 'execute 消息处理完成不等于退款成功；业务结果仍由 Action 表示。'),
      step(4, 2, '领取 reconcile 消息', 'reconciling', 'reconcile: processing', 'B 持有对账消息租约', 'pending · unknown', '对账使用单独的 Outbox 消息和租约。这里由 B 处理只是示例，也可以由 A 领取。'),
      step(4, 5, '按原幂等键查询退款结果', 'reconciling', 'reconcile: processing', 'B 定期续租', 'pending · unknown', '只查结果，不再次执行写操作。'),
      step(5, 4, '查到退款已成功，保存终态', 'succeeded', 'reconcile: succeeded', '对账租约释放', 'resolved · 系统确认', '写回前再次校验租约。系统记录成功事件，并自动关闭人工核实任务。'),
    ],
  },
  crash: {
    title: '外部成功后，Worker 崩溃',
    summary: '外部已经完成，本地还没有保存结果。新进程必须先对账，不能把 executing 当作重新退款的许可。',
    steps: [
      step(3, 2, 'A 领取消息，保存 executing', 'executing', 'execute: processing', 'A 持有 v1，有效', '无', '本地已经记录“开始执行”。'),
      step(3, 5, '外部接受退款并保存结果', 'executing', 'execute: processing', 'A 持有 v1', '无', '持久化模拟 OMS 在独立连接中保存结果，进程退出不会删除它。'),
      step(3, 3, 'A 被终止，来不及写回成功', 'executing', 'execute: processing', 'A 不再续租', '无', '本地状态不能证明外部失败。'),
      step(4, 2, '租约到期后 B 重新领取', 'executing', 'execute: processing', 'B 持有新版本 v2', '无', '数据库条件更新决定接管者，旧版本失效。'),
      step(4, 1, '发现中断，转 unknown 并安排对账', 'unknown', 'execute: succeeded；reconcile: pending', '旧执行消息租约释放', 'pending · unknown', '不再次调用退款 Handler。'),
      step(4, 5, '领取对账消息，查询原幂等键', 'reconciling', 'reconcile: processing', '新的对账租约', 'pending · unknown', '以外部实际记录为准，而不是猜测上一次有没有成功。'),
      step(5, 4, '查询确认成功，关闭核实任务', 'succeeded', 'reconcile: succeeded', '租约释放', 'resolved · 系统确认', 'PostgreSQL 多进程演练已经验证：该路径下外部写 Handler 只调用一次。'),
    ],
  },
  heartbeat: {
    title: '调用较慢，Heartbeat 防止误接管',
    summary: '只要 A 仍持有效租约并持续续租，B 就不能因为调用超过初始租约时间而抢走任务。',
    steps: [
      step(3, 2, 'A 领取任务，取得版本凭证', 'executing', 'execute: processing', 'A 持有 v1，有效', '无', '租约包含 owner、version 和 expires_at。'),
      step(3, 5, '外部调用持续较长时间', 'executing', 'execute: processing', 'A 的租约仍有效', '无', '调用尚未超过 Tool 超时，但可能超过初始租约时长。'),
      step(3, 2, 'Heartbeat 使用独立连接续租', 'executing', 'execute: processing', 'A / v1，到期时间延后', '无', '约每 lease_seconds / 3 续租一次，不改变本次领取版本。'),
      step(4, 2, 'B 尝试领取，条件不满足', 'executing', 'execute: processing', '仍由 A 持有', '无', '续租后的到期时间尚未到达，B 领取不到这条消息。'),
      step(5, 3, '外部成功，A 校验凭证后写回', 'succeeded', 'execute: succeeded', '租约释放', '无', '慢调用不会仅因为耗时较长就生成未知结果或人工任务。'),
    ],
  },
  stale: {
    title: '旧 Worker 返回，不能覆盖新结果',
    summary: '续租防止误接管；Fencing（领取版本校验）防止已经失去资格的 Worker 再写回。',
    steps: [
      step(3, 2, 'A 领取任务，持有 v1', 'executing', 'execute: processing', 'A / v1，有效', '无', '本次 owner 和 version 被保存为不可变的领取凭证。'),
      step(3, 3, 'A 失联，Heartbeat 停止', 'executing', 'execute: processing', 'A 的租约过期', '无', '到期不是退款失败，只表示 A 不能再代表系统提交状态。'),
      step(4, 2, 'B 接管，取得 v2', 'executing', 'execute: processing', 'B / v2，有效', '无', '只有当前 owner、当前版本且未过期的 Worker 有资格更新状态。'),
      step(5, 3, 'A 的旧调用晚到返回', 'executing', 'execute: processing', '仍由 B / v2 持有', '无', '旧 HTTP 调用可能继续完成，租约不能撤销已经发生的外部操作。'),
      step(3, 2, 'A 用 v1 写回或续租，被拒绝', 'executing', 'execute: processing', 'B / v2 不受影响', '无', 'A 回滚并记录 lease lost，不修改 Action、不安排自己的重试，也不能覆盖 B 的租约。'),
      step(4, 1, 'B 将中断执行交给对账流程', 'unknown', 'execute: succeeded；reconcile: pending', '后续由对账消息领取', 'pending · unknown', '外部幂等和查询接口仍然必要；本地 Fencing 不是分布式 Exactly Once 保证。'),
    ],
  },
  manual: {
    title: '对账耗尽，主管人工确认',
    summary: 'DLQ 表示不再自动投递，不代表退款失败。员工必须查外部凭证，再确认结果。',
    steps: [
      step(4, 5, '多次对账仍无法确认', 'unknown', 'reconcile: retry', '每次领取都有独立凭证', 'pending · unknown', '暂时查不到或查询超时，不等于外部明确失败。'),
      step(4, 2, '重试次数耗尽，进入 DLQ', 'unknown', 'reconcile: dead_letter', '无有效租约', 'pending · dead_letter', '核实任务保留；Action 不会因为进入死信队列自动变 failed。'),
      step(1, 0, '工单显示“业务待核实”', 'unknown', 'reconcile: dead_letter', '无有效租约', 'pending · dead_letter', '队列合并回复待审批工单与业务待核实工单，但不混淆两种审批。'),
      step(0, 5, '主管查看外部退款凭证', 'unknown', 'reconcile: dead_letter', '无有效租约', 'pending · dead_letter', '确认人必须具有 manager/admin 权限，且不是原操作申请人。'),
      step(0, 1, '提交成功结论、凭证及当前版本', 'succeeded', '未完成消息: cancelled', '清除租约并增加版本', 'resolved · 人工确认', '事务内记录终态、加密证据、审计事件并取消剩余消息。若仍有有效 Worker 租约，则返回 409，不允许确认。'),
      step(1, 0, '关闭业务核实，回复审批独立保留', 'succeeded', '已完成消息不变；其余 cancelled', '旧 Worker 无法再写回', 'resolved · 人工确认', '人工确认不再调用退款 Tool，也不会自动批准 AI 回复或恢复 LangGraph。'),
    ],
  },
  compensation: {
    title: '补偿中断，不能盲目再补偿',
    summary: '成功后的补偿是单独操作。补偿结果未知时，当前实现由人工核实收尾，不展示不存在的自动补偿对账。',
    steps: [
      step(0, 1, '对成功操作单独申请补偿', 'compensation_pending', 'compensate: pending', '尚未领取', '无', '不会因为退款成功就自动发起撤销；沿用已有补偿授权条件。'),
      step(3, 2, 'A 领取补偿消息', 'compensating', 'compensate: processing', 'A 持有有效租约', '无', '调用前保存 compensating，并使用现有补偿幂等键。'),
      step(3, 5, '补偿调用超时或 Worker 中断', 'compensation_unknown', '超时可处理完消息；中断则待接管', '写回必须校验当前租约', 'pending · compensation_unknown', '无法确认外部是否完成补偿，不再次自动调用补偿 Handler。'),
      step(0, 5, '主管查询真实补偿结果', 'compensation_unknown', '不盲目重发补偿', '确认前必须没有有效执行租约', 'pending · compensation_unknown', '凭真实外部记录选择补偿完成或补偿失败。'),
      step(0, 1, '确认补偿完成，记录证据与事件', 'compensated', '剩余未完成消息: cancelled', '租约清除，旧凭证失效', 'resolved · 人工确认', '当前没有 compensation_unknown → 自动对账 的状态迁移；页面与代码保持一致。'),
    ],
  },
};

export const stateLabel = (id) => ACTION_STATES.find((item) => item.id === id)?.label || id;

export function outgoingTransitions(id) {
  return ACTION_TRANSITIONS.filter((item) => item.from === id);
}

// 同一条源/目标连线合并展示，但命令清单保留自动与人工两种路径。
export function diagramEdges() {
  const edges = new Map();
  for (const transition of ACTION_TRANSITIONS) {
    const key = `${transition.from}:${transition.to}`;
    if (!edges.has(key)) edges.set(key, { from: transition.from, to: transition.to });
  }
  return [...edges.values()];
}
