import React, { useId, useState } from 'react';
import { ArrowRight, CheckCircle2, RotateCcw, ShieldCheck } from 'lucide-react';
import { ACTION_STATES, ACTION_TRANSITIONS, FAULT_SCENARIOS, PARTICIPANTS, diagramEdges, outgoingTransitions, stateLabel } from './toolGovernanceDemo.js';
import './tool-governance-diagram.css';

const STATES_BY_ID = Object.fromEntries(ACTION_STATES.map((item) => [item.id, item]));
const EDGES = diagramEdges();

// 固定布局只用于架构演示，所有迁移来自与后端对应的命令清单。
function edgePath(from, to) {
  const a = STATES_BY_ID[from];
  const b = STATES_BY_ID[to];
  if (a.y === b.y) {
    const direction = Math.sign(b.x - a.x);
    const offset = from === 'reconciling' ? 15 : -15;
    return `M ${a.x + direction * 92} ${a.y + offset} L ${b.x - direction * 96} ${b.y + offset}`;
  }
  if (a.x === b.x) return `M ${a.x} ${a.y + 35} L ${b.x} ${b.y - 39}`;
  // 跨阶段的回写路径从节点侧面绕行，避免穿过中间状态卡片。
  const direction = b.y > a.y ? 1 : -1;
  const laneX = from === 'unknown' && to === 'succeeded' ? 1060
    : from === 'reconciling' && to === 'succeeded' ? 1080
      : from.startsWith('compensation') || from === 'compensating' ? b.x - 104
        : Math.min(a.x, b.x) - 105;
  return `M ${a.x} ${a.y + direction * 35} L ${a.x} ${a.y + direction * 70} L ${laneX} ${a.y + direction * 70} L ${laneX} ${b.y - direction * 70} L ${b.x} ${b.y - direction * 70} L ${b.x} ${b.y - direction * 39}`;
}

function StateMachine({ selected, onSelect }) {
  const markerId = `action-arrow-${useId().replaceAll(':', '')}`;
  return <div className="tg-chart-scroll" tabIndex={0} aria-label="完整状态机，可横向滚动；下方提供全部迁移的文字清单">
    <svg className="tg-state-svg" viewBox="0 0 1300 1090" role="group" aria-labelledby={`${markerId}-title ${markerId}-desc`}>
      <title id={`${markerId}-title`}>高风险 Action 完整状态机</title>
      <desc id={`${markerId}-desc`}>包括操作审批、执行、未知结果对账、人工确认和补偿。点击状态查看可执行命令；下方的迁移清单提供等价文字说明。</desc>
      <defs><marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" /></marker></defs>
      <rect x="8" y="12" width="1284" height="135" rx="12" className="tg-stage-background" />
      <text x="24" y="34" className="tg-stage-title">① 申请 → 独立审批 → 异步执行</text>
      <text x="620" y="378" className="tg-stage-title">② 结果不确定 → 查询实际结果 / 人工核实</text>
      <text x="985" y="582" className="tg-stage-title">③ 成功后单独发起补偿</text>
      {EDGES.map(({ from, to }) => <path key={`${from}:${to}`} d={edgePath(from, to)} className={`tg-edge ${selected === from || selected === to ? 'is-highlighted' : ''}`} markerEnd={`url(#${markerId})`} />)}
      {ACTION_STATES.map((item) => {
        const manualOnly = item.id === 'compensation_unknown';
        const terminal = outgoingTransitions(item.id).length === 0;
        return <g key={item.id} role="button" tabIndex={0} aria-label={`${item.label}，${item.id}`} aria-pressed={selected === item.id} className={`tg-state ${selected === item.id ? 'is-selected' : ''} ${terminal ? 'is-terminal' : ''} ${item.id.includes('failed') || item.id === 'rejected' ? 'is-negative' : ''} ${manualOnly || item.id === 'unknown' ? 'needs-attention' : ''}`} onClick={() => onSelect(item.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(item.id); } }}>
          <rect x={item.x - 92} y={item.y - 34} width="184" height="68" rx="9" />
          <text x={item.x} y={item.y - 4} textAnchor="middle" className="tg-state-label">{item.label}</text>
          <text x={item.x} y={item.y + 18} textAnchor="middle" className="tg-state-code">{item.id}</text>
        </g>;
      })}
      <text x="32" y="1058" className="tg-stage-title">连线表示合法迁移；具体触发命令、权限和前置条件见右侧说明及下方清单。</text>
    </svg>
  </div>;
}

function SequenceChart({ scenario, currentStep }) {
  const markerId = `sequence-arrow-${useId().replaceAll(':', '')}`;
  const x = (lane) => 112 + lane * 213;
  const height = 120 + scenario.steps.length * 80;
  return <div className="tg-chart-scroll" tabIndex={0} aria-label="故障时序图，可横向滚动；下方步骤列表提供文字说明">
    <svg className="tg-sequence-svg" viewBox={`0 0 1280 ${height}`} role="img" aria-labelledby={`${markerId}-title`}>
      <title id={`${markerId}-title`}>{scenario.title}：参与者之间的消息时序</title>
      <defs><marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" /></marker></defs>
      {PARTICIPANTS.map((label, index) => <g key={label}><rect x={x(index) - 94} y="12" width="188" height="42" rx="7" className="tg-participant" /><text x={x(index)} y="38" textAnchor="middle" className="tg-state-label">{label}</text><line x1={x(index)} y1="54" x2={x(index)} y2={height - 22} className="tg-lifeline" /></g>)}
      {scenario.steps.map((item, index) => {
        const y = 105 + index * 80;
        const fromX = x(item.from);
        const toX = x(item.to);
        const self = item.from === item.to;
        const direction = Math.sign(toX - fromX);
        const path = self ? `M ${fromX} ${y} h 64 v 24 h -60` : `M ${fromX} ${y} L ${toX - direction * 5} ${y}`;
        return <g key={index} className={`tg-message ${index === currentStep ? 'is-current' : ''} ${index > currentStep ? 'is-future' : ''}`}>
          {index === currentStep && <rect x="6" y={y - 32} width="1268" height="65" rx="7" className="tg-sequence-highlight" />}
          <path d={path} markerEnd={`url(#${markerId})`} />
          <text x={self ? fromX - 8 : (fromX + toX) / 2} y={y - 12} textAnchor={self ? 'end' : 'middle'}>{index + 1}. {item.text}</text>
        </g>;
      })}
    </svg>
  </div>;
}

export default function ToolGovernanceDiagram() {
  const [selected, setSelected] = useState('unknown');
  const [scenarioKey, setScenarioKey] = useState('timeout');
  const [currentStep, setCurrentStep] = useState(0);
  const scenario = FAULT_SCENARIOS[scenarioKey];
  const current = scenario.steps[currentStep];
  const transitions = outgoingTransitions(selected);

  return <div className="tg-demo">
    <section className="tg-panel">
      <div className="wf-section-heading"><div><h2>先分清四类记录</h2><p>同一笔业务可以有多个执行或对账消息。消息投递成功，不代表业务操作成功。</p></div><span className="wf-demo-label">静态演示 · 不连接真实业务</span></div>
      <div className="tg-relationship-grid">
        {[
          ['Action', '这笔业务目前是什么结果？', '保存操作参数、审批、业务状态、版本和审计事件。跨工单的相同整单退款申请复用同一个 Action。'],
          ['Outbox', '接下来哪个后台任务要处理？', '通过 tool_action_id 关联 Action。执行、对账和补偿使用不同消息；pending / retry / dead_letter 是投递状态。'],
          ['Worker Lease', '现在哪个 Worker 有资格处理？', '租约保存在 Outbox 行中，不是独立表。owner + version + expires_at 决定领取资格，Heartbeat 续租，Fencing 校验写回资格。'],
          ['Review', '哪笔业务还需要人工查证？', '通过 tool_action_id 和 ticket_id 关联操作与工单，每个 Action 最多一条核实记录。自动对账或人工确认后关闭。'],
        ].map(([name, question, text]) => <article key={name}><code>{name}</code><h3>{question}</h3><p>{text}</p></article>)}
      </div>
      <div className="tg-relation-bar" aria-label="数据关联">工单 <ArrowRight size={15} /> Action <span>1 : N</span> Outbox（含 Lease）<span className="tg-relation-divider">｜</span>Action <span>1 : 0..1</span> Review <ArrowRight size={15} /> 人工处理队列</div>
      <p className="tg-note">回复审批与操作审批、业务结果核实是不同流程。Review 不会直接批准 AI 回复，也不会自动恢复 LangGraph。</p>
    </section>

    <section className="tg-panel">
      <h2>完整 Action 状态机</h2><p>覆盖当前后端的全部 15 个状态和 25 条命令迁移。点击状态查看下一步；人工命令并不表示任意员工都能直接改状态。</p>
      <div className="tg-machine-layout">
        <StateMachine selected={selected} onSelect={setSelected} />
        <aside className="tg-state-detail" aria-live="polite"><span className="wf-eyebrow">所选状态</span><h3>{stateLabel(selected)}</h3><code>{selected}</code>
          {transitions.length ? <ul>{transitions.map((item) => <li key={item.command}><code>{item.command}</code><strong>→ {stateLabel(item.to)}</strong><p>{item.label}</p></li>)}</ul> : <p>当前状态没有后续命令，不能重复执行或直接覆盖终态。</p>}
          {selected === 'succeeded' && <p className="tg-note">成功结果不能直接覆盖；仍可通过单独的补偿流程处理。它不是整个生命周期的不可变终点。</p>}
          <div className="tg-guard"><ShieldCheck size={17} /><p>人工确认需 manager/admin、非原申请人、存在待核实记录、版本一致且没有有效 Worker 租约。排队中的操作不能人工确认为成功。</p></div>
        </aside>
      </div>
      <details className="tg-transition-list"><summary>展开全部迁移命令（含自动与人工路径）</summary><div className="tg-table-scroll"><table><thead><tr><th>当前状态</th><th>命令</th><th>下一状态</th><th>发生什么</th></tr></thead><tbody>{ACTION_TRANSITIONS.map((item) => <tr key={`${item.from}:${item.command}`}><td>{stateLabel(item.from)}<code>{item.from}</code></td><td><code>{item.command}</code></td><td>{stateLabel(item.to)}<code>{item.to}</code></td><td>{item.label}</td></tr>)}</tbody></table></div></details>
      <div className="tg-secondary-states"><article><h3>Outbox 的状态</h3><p><code>pending → processing → succeeded</code></p><p>失败时进入 <code>retry</code>，到期后重新领取；次数耗尽进入 <code>dead_letter</code>。processing 租约到期也可重新领取，版本会更新。人工确认可取消剩余消息：<code>cancelled</code>。</p></article><article><h3>Review 的状态</h3><p><code>无记录 → pending → resolved</code></p><p>unknown、dead_letter 或 compensation_unknown 创建核实任务。自动确认结果或人工核实后关闭；已有 pending 任务只更新原因，不重复创建。</p></article></div>
    </section>

    <section className="tg-panel">
      <div className="wf-section-heading"><div><h2>故障发生后，四类记录怎样一起变化？</h2><p>选择场景，逐步查看消息顺序和每一步保存的状态。这里的 v1 / v2 是示意，不是固定数据库版本。</p></div></div>
      <div className="wf-tabs">{Object.entries(FAULT_SCENARIOS).map(([key, item]) => <button type="button" key={key} className={scenarioKey === key ? 'active' : ''} aria-pressed={scenarioKey === key} onClick={() => { setScenarioKey(key); setCurrentStep(0); }}>{item.title}</button>)}</div>
      <div className="wf-example"><div><small>场景说明</small><p>{scenario.summary}</p></div><div className="wf-controls"><button type="button" className="btn btn-secondary" onClick={() => setCurrentStep(0)}><RotateCcw size={15} /> 重置</button><button type="button" className="btn btn-primary" disabled={currentStep === scenario.steps.length - 1} onClick={() => setCurrentStep((index) => Math.min(index + 1, scenario.steps.length - 1))}>{currentStep === scenario.steps.length - 1 ? '演示完成' : '下一步'}<ArrowRight size={15} /></button></div></div>
      <div className="tg-snapshot" role="status" aria-live="polite">{[['Action', `${stateLabel(current.action)} · ${current.action}`], ['Outbox', current.outbox], ['Worker Lease', current.lease], ['Review', current.review]].map(([name, value]) => <div key={name}><span>{name}</span><strong>{value}</strong></div>)}</div>
      <p className="tg-current-note"><CheckCircle2 size={17} /><span>第 {currentStep + 1} / {scenario.steps.length} 步：{current.note}</span></p>
      <SequenceChart scenario={scenario} currentStep={currentStep} />
      <ol className="tg-step-list" aria-label="选择时序步骤">{scenario.steps.map((item, index) => <li key={index}><button type="button" aria-current={currentStep === index ? 'step' : undefined} onClick={() => setCurrentStep(index)}><span>{index + 1}</span><div><strong>{item.text}</strong><small>{PARTICIPANTS[item.from]} → {PARTICIPANTS[item.to]}</small></div></button></li>)}</ol>
    </section>

    <section className="tg-panel tg-boundaries"><h2>讲解时需要说明的实现边界</h2><ul><li>去重当前只覆盖同一客户、同一订单的整单退款申请；失败或拒绝后不会自动创建第二个申请。</li><li>PostgreSQL 16 多进程演练已验证并发申请、慢调用续租、外部成功后进程崩溃及旧凭证失效。外部系统使用持久化 Mock，不等于真实支付或 OMS 验收。</li><li>Lease 和 Fencing 保护本地状态，不能撤销已发出的外部请求。真实接口仍需支持幂等键与可靠的结果查询，不能宣称分布式 Exactly Once。</li><li>DLQ 不是业务失败。人工确认必须查证外部事实；当前系统保存证据和审计记录，不自动验证外部凭证真伪。</li><li>当前已 resolved 的 Review 不会自动重新打开。如果原操作曾经对账关闭核实任务，后续补偿又出现未知结果，这个重复核实场景仍需要完善。本次只展示现有实现，不修改业务行为。</li><li>页面为代码对应的流程说明，不读取线上任务、客户资料或真实租约，不提供审批、重试或退款按钮。</li></ul></section>
  </div>;
}
