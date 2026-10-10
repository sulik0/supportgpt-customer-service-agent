import React, { useState } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, CheckCircle2, Database, GitBranch, Layers, RotateCcw, ShieldCheck } from 'lucide-react';
import './workflow.css';
import ToolGovernanceDiagram from './ToolGovernanceDiagram';

// 演示配置对应当前 graph.py；不执行 Agent，也不读取真实客户数据。
const NODES = {
  input: {
    name: '接收用户问题',
    code: 'HTTP · Ticket · Memory',
    summary: '系统检查请求、保存工单，并读取这次会话的历史消息。',
    input: '用户的问题、会话身份和所用知识库版本。',
    output: '工单编号、请求编号、客户标识，以及后续处理需要的历史消息。',
    design: '系统先确认会话属于谁、请求是否过大，再交给 Agent。工单和消息会存入数据库。Memory（会话记忆）帮助理解前文，但订单等实时信息仍要通过工具查询。',
    tags: ['FastAPI', '检查身份与请求频率', 'Memory（会话记忆）']
  },
  analyzer: {
    name: '理解问题并检查安全',
    code: 'analyzer',
    summary: 'Agent 判断用户要做什么、问题有多紧急，以及该交给哪个部门。',
    input: '工单主题、问题描述，以及按长度和数量限制选取的历史消息。',
    output: '问题分类、优先级、处理部门、分类置信度、情绪和安全检查结果。',
    design: '系统先按规则提出分类结果。启用 Jev 时，DecisionProvider 会让 Jev 再判断一次；结果不够可靠或服务故障时，再按配置使用规则或 LLM（大模型）。如果发现恶意指令，系统直接判断是否需要人工处理，不再查询工具或生成回复。',
    tags: ['规则 / Jev / 大模型', 'Prompt Guardrails（安全检查）', '8 类用户意图']
  },
  skill_selector: {
    name: '选择处理方案与工具',
    code: 'skill_selector',
    summary: '系统按问题分类选择 Skill，规定这次能用哪些工具和知识文档。',
    input: '问题分类、客户标识和操作人员的角色。',
    output: '所选 Skill 及版本、可用和禁止的工具、知识类别，以及仍缺少的信息。',
    design: '每类问题对应预先配置的 Skill（处理能力），不是由模型临时决定工具权限。当前有 6 个 Skill，配置版本为 v1.1。系统会保存本次用到的配置，方便恢复任务和排查问题。',
    tags: ['Skill（处理能力）', '按固定规则选择', '记录所用版本']
  },
  context_enrichment: {
    name: '同时查询业务与知识',
    code: 'context_enrichment',
    summary: '系统同时查询业务资料和知识库，检查结果后再交给回复生成节点。',
    input: 'Skill 允许的工具和知识范围、问题分类、客户标识、用户问题及知识库版本。',
    output: '业务查询结果、工具调用记录、知识文档引用和安全检查结果。',
    design: 'Tooling（业务查询）和 Retriever（知识检索）在这个 LangGraph 节点内同时执行，并不是两个独立的 Graph 节点。任何一方发现恶意指令，系统都会保留风险标记，跳过回复生成和质量检查，转到人工处理判断。',
    tags: ['两类查询同时执行', '检查外部内容中的恶意指令', '合并查询结果']
  },
  tooling: {
    name: '查询业务资料',
    code: 'Tooling · ToolRegistry',
    summary: '工具查询客户、订单、历史工单、物流、保修和账务信息。',
    input: '客户标识、问题分类、操作角色，以及 Skill 的工具使用规则。',
    output: '查询到的业务资料、调用是否成功，以及可供追查的调用记录。',
    design: '系统有 9 个注册工具。调用前会检查参数、权限、风险、Skill 限制和工具是否启用；调用失败时按 Resilience（故障处理）配置重试或回退。物流、权益、支付发票和服务状态工具只能查询。主流程不会自动执行退款，当前业务数据也都来自本地演示适配器。',
    tags: ['9 个业务工具', '检查权限并记录调用', '仅使用演示数据']
  },
  retriever: {
    name: '查找相关政策和流程',
    code: 'Retriever · Hybrid RAG',
    summary: '系统按知识库版本和类别查找文档，把相关内容和出处交给 Agent。',
    input: '用户问题、历史消息中与检索有关的信息，以及知识库版本和类别。',
    output: '相关文档片段、来源、版本和检索评分。',
    design: 'Hybrid RAG（混合检索）结合向量检索和关键词匹配，再对结果排序。中文关键词按连续两个字匹配。文档交给模型前会检查是否包含恶意指令；如果指定类别没有结果，系统会扩大类别范围，但仍只查指定版本。',
    tags: ['ChromaDB', 'Hybrid RAG（混合检索）', '保留文档出处']
  },
  resolver: {
    name: '生成客服回复',
    code: 'resolver',
    summary: '模型根据必要的业务资料和最相关文档，生成一份客服回复草稿。',
    input: '用户问题、最相关的两条文档引用、必要的业务字段和历史消息。',
    output: '回复草稿，以及模型输入和输出的 Token 用量。',
    design: '系统限制输入资料的长度和模型输出的 Token 数，减少不必要的内容。回复默认使用用户当前提问的语言。这个节点只写回复，不执行退款或取消订单，也不能声称演示数据中的操作已在真实系统完成。',
    tags: ['LLM（大模型）', '只保留必要资料', '只生成草稿']
  },
  qa: {
    name: '检查回复是否可靠',
    code: 'qa',
    summary: '系统检查回复有没有依据、引用是否可用，以及是否泄露敏感信息。',
    input: '回复草稿、引用文档、业务查询结果和之前的安全检查结果。',
    output: 'QA 评分、是否可能编造内容、引用检查结果，以及采用的检查方式。',
    design: '明显的安全问题和部分补充信息的回复由规则检查。需要进一步判断时可以使用 Jev；结果不够可靠或服务故障时，按配置使用轻量大模型。QA 评分只是风险判断的一部分，不是分数高就一定自动发送。',
    tags: ['规则 / Jev / 大模型', '核对回答依据', '过滤敏感内容']
  },
  escalation: {
    name: '判断是否需要人工处理',
    code: 'escalation · Risk Engine',
    summary: '系统结合风险、紧急程度和回复质量，决定是否转人工，并说明原因。',
    input: '问题分类、优先级、分类置信度、回复质量、安全检查结果和服务故障记录。',
    output: '风险等级、具体原因，以及是否需要人工处理或审批。',
    design: '咨询政策和申请实际操作要分开处理，不能因为用户提到退款就一律要求审批。高风险操作、无法确认的业务结果、不可靠的回复或服务故障，会按现有规则交给人工。后台能看到具体原因，便于客服判断下一步怎么处理。',
    tags: ['Risk Engine（风险判断）', '说明转人工原因', '人工确认']
  },
  approval_gate: {
    name: '等待人工审批并恢复处理',
    code: 'approval_gate',
    summary: '不需要审批时直接结束；需要审批且已启用持久执行时，先保存进度并暂停。',
    input: '是否需要审批、是否启用 Durable Execution（持久执行），以及人工的决定。',
    output: '暂停时保存的 Checkpoint（检查点），或人工审批后恢复的处理结果。',
    design: '系统通过 interrupt 暂停，并保存流程状态。人工通过、修改或拒绝回复后，服务用 Command(resume) 继续同一次任务。批准回复不代表授权退款；退款等高风险工具操作仍需要单独提议、审批和执行。',
    tags: ['Checkpoint（检查点）', '暂停后继续处理', '操作审批单独进行']
  },
  end: {
    name: '保存结果并告知用户',
    code: 'END · AgentRun · Feedback',
    summary: '系统保存回复和处理状态，用户可以查看结果并提交评价。',
    input: '已完成的处理结果，或正在等待人工确认的状态。',
    output: '已保存的回复或人工处理中提示，以及对应的运行记录和 Trace ID。',
    design: '用户打开对话或工单详情时，页面读取已保存的结果，不会重新运行 Agent。需要审批时，页面会告诉用户仍在等待人工。用户评价会关联本次 AgentRun（运行记录），方便后续分析和评测。',
    tags: ['保存到数据库', 'AgentRun（运行记录）', '记录用户评价']
  }
};
const SCENARIOS = {
  normal: {
    title: '查询订单和物流',
    question: '我的订单还没有收到，能帮我查一下吗？',
    path: ['input', 'analyzer', 'skill_selector', 'context_enrichment', 'resolver', 'qa', 'escalation', 'approval_gate', 'end'],
    result: '示例：Agent 将问题归为订单查询，选择订单处理 Skill，查询订单和物流，再查找配送说明。回复通过质量与风险检查后，系统保存结果并返回给用户。'
  },
  refund: {
    title: '申请退款，等待审批',
    question: '我想申请这笔订单的退款。',
    path: ['input', 'analyzer', 'skill_selector', 'context_enrichment', 'resolver', 'qa', 'escalation', 'approval_gate', 'end'],
    result: '示例：Agent 查询订单和账务资料，生成回复草稿。实际退款申请需要人工确认；启用持久执行时，流程会先保存进度并暂停，审批后再继续。执行退款还需要单独审批对应的工具操作。'
  },
  injection: {
    title: '用户输入包含恶意指令',
    question: '示例：用户要求忽略系统规则并泄露内部信息。',
    path: ['input', 'analyzer', 'escalation', 'approval_gate', 'end'],
    result: '示例：Analyzer 发现安全威胁后，跳过能力选择、工具查询、知识检索、回复生成和 QA。系统直接判断是否需要人工处理，并向用户显示安全提示。'
  },
  indirect: {
    title: '查询结果包含恶意指令',
    question: '示例：工具返回的内容或知识文档中夹带恶意指令。',
    path: ['input', 'analyzer', 'skill_selector', 'context_enrichment', 'escalation', 'approval_gate', 'end'],
    result: '示例：系统在检查查询结果时发现恶意指令，不把这些内容交给回复模型。流程跳过回复生成和 QA，转到人工处理判断。'
  }
};
const STAGES = ['input', 'analyzer', 'skill_selector', 'context_enrichment', 'resolver', 'qa', 'escalation', 'approval_gate', 'end'];
export default function WorkflowPage({
  onBack,
  embedded = false
}) {
  const [view, setView] = useState('workflow');
  const [scenarioKey, setScenarioKey] = useState('normal');
  const [step, setStep] = useState(-1);
  const [selected, setSelected] = useState('analyzer');
  const scenario = SCENARIOS[scenarioKey];
  const detail = NODES[selected];
  const visited = scenario.path.slice(0, step + 1);
  const current = scenario.path[step];
  const finished = step === scenario.path.length - 1;
  const Heading = embedded ? 'h2' : 'h1';
  function nextStep() {
    const next = Math.min(step + 1, scenario.path.length - 1);
    setStep(next);
    setSelected(scenario.path[next]);
  }
  function renderNode(key, branch = false) {
    const node = NODES[key];
    const parentActive = branch && visited.includes('context_enrichment');
    const skipped = step >= 0 && !scenario.path.includes(branch ? 'context_enrichment' : key);
    return <button type="button" className={`wf-node ${selected === key ? 'is-selected' : ''} ${visited.includes(key) || parentActive ? 'is-visited' : ''} ${current === key ? 'is-current' : ''} ${skipped ? 'is-skipped' : ''}`} onClick={() => setSelected(key)} aria-pressed={selected === key}>
      <span className="wf-node-top"><code>{node.code}</code>{(visited.includes(key) || parentActive) && <CheckCircle2 size={15} aria-label="演示已走到这一步" />}</span>
      <strong>{node.name}</strong><span>{node.summary}</span>
      {skipped && <em className="wf-skipped-label">这个场景不执行此步骤</em>}
      {key === 'context_enrichment' && <em>以下两类查询同时执行 ↓</em>}
    </button>;
  }
  return <section className="wf-page">
    {onBack && <button className="wf-back" type="button" onClick={onBack}><ArrowLeft size={16} /> 返回用户咨询</button>}
    <div className="wf-hero"><div><span className="wf-eyebrow"><GitBranch size={16} /> SupportGPT 智能客服 Agent · 处理流程演示</span><Heading>{view === 'workflow' ? '用户提问后，Agent 怎样完成处理？' : '高风险操作怎样审批、执行和恢复？'}</Heading><p>{view === 'workflow' ? 'Agent 先理解问题，再查询业务和知识、生成并检查回复，最后决定直接返回结果还是等待人工确认。' : '通过状态机和故障时序，查看 Action、Outbox、Worker Lease 与 Review 如何共同保护一笔业务操作。'}</p></div><span className="wf-demo-label">Demo · 静态演示，不会实际运行 Agent</span></div>
    <nav className="wf-view-switch" aria-label="选择架构演示"><button type="button" aria-pressed={view === 'workflow'} onClick={() => setView('workflow')}>Agent 处理流程</button><button type="button" aria-pressed={view === 'governance'} onClick={() => setView('governance')}>高风险操作与故障恢复</button></nav>
    {view === 'governance' ? <ToolGovernanceDiagram /> : <>
    <div className="wf-overview">{[['7', 'LangGraph 流程节点'], ['6', 'Skill 处理能力'], ['9', '已注册的业务工具'], ['OTel', '记录调用过程与运行指标']].map(([value, label]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
    <section className="wf-scenarios" aria-label="演示场景">
      <div className="wf-section-heading"><div><h2>选一个场景，逐步查看系统怎样处理</h2><p>点击“下一步”查看处理顺序。演示只在当前页面运行，不向后端提交请求，不调用模型，也不展示真实客户数据或耗时。</p></div></div>
      <div className="wf-tabs">{Object.entries(SCENARIOS).map(([key, item]) => <button type="button" key={key} aria-pressed={key === scenarioKey} className={key === scenarioKey ? 'active' : ''} onClick={() => {
            setScenarioKey(key);
            setStep(-1);
            setSelected('analyzer');
          }}>{item.title}</button>)}</div>
      <div className="wf-example"><div><small>示例输入</small><p>{scenario.question}</p></div><div className="wf-controls"><button type="button" className="btn btn-secondary" onClick={() => {
              setStep(-1);
              setSelected('analyzer');
            }}><RotateCcw size={15} /> 重置</button><button type="button" className="btn btn-primary" onClick={nextStep} disabled={finished}>{step < 0 ? '开始演示' : finished ? '演示完成' : '下一步'}<ArrowRight size={15} /></button></div></div>
      <p className="wf-scenario-result" role="status">{step < 0 ? scenario.result : `当前步骤：${NODES[current].name}（${step + 1} / ${scenario.path.length}）${finished ? '。' + scenario.result : '。点击节点可查看输入、输出和设计说明。'}`}</p>
      <div className="wf-path" aria-label="当前场景路径">{scenario.path.map((key, index) => <React.Fragment key={key}>{index > 0 && <ArrowRight size={12} />}<span className={current === key ? 'current' : visited.includes(key) ? 'visited' : ''}>{NODES[key].name}</span></React.Fragment>)}</div>
    </section>
    <div className="wf-layout">
      <section className="wf-graph" aria-label="完整 Workflow 架构图"><div className="wf-section-heading"><h2><GitBranch size={18} /> 完整处理流程</h2><span>点击节点查看详情</span></div>
        <div className="wf-flow">{STAGES.map((key, index) => <React.Fragment key={key}>
          {index > 0 && <div className="wf-connector"><ArrowDown size={18} /><span>{key === 'approval_gate' ? '是否需要审批？' : key === 'end' ? '无需审批则结束；需要审批则等待人工决定后继续' : '通过 AgentState 传递处理结果'}</span></div>}
          {renderNode(key)}
          {key === 'analyzer' && <div className="wf-route-note"><ShieldCheck size={14} /> 发现输入有安全风险 → 判断是否转人工 → 审批步骤</div>}
          {key === 'context_enrichment' && <><div className="wf-parallel">{renderNode('tooling', true)}{renderNode('retriever', true)}</div><div className="wf-route-note"><ShieldCheck size={14} /> 查询结果有安全风险 → 跳过生成回复 → 判断是否转人工</div></>}
          {key === 'approval_gate' && <div className="wf-approval"><Database size={17} /><div><strong>需要审批，且已启用持久执行</strong><p>暂停任务 → 保存检查点 → 人工通过、修改或拒绝回复 → 恢复任务</p><small>回复审批和退款等高风险工具操作的审批分开进行。</small></div></div>}
        </React.Fragment>)}</div>
      </section>
      <aside className="wf-details" aria-label="节点详情"><div className="wf-detail-card"><span className="wf-eyebrow">节点说明</span><h2>{detail.name}</h2><code>{detail.code}</code><div className="wf-tags">{detail.tags.map(tag => <span key={tag}>{tag}</span>)}</div><dl><dt>接收什么</dt><dd>{detail.input}</dd><dt>产生什么</dt><dd>{detail.output}</dd><dt>为什么这样处理</dt><dd>{detail.design}</dd></dl></div>
        <div className="wf-detail-card"><h3><Layers size={17} /> 节点之间传递什么？</h3><p>各节点通过 AgentState（流程状态）共享问题分类、所用 Skill、查询资料、文档引用、回复草稿和检查结果。Checkpoint（检查点）保存流程进度；工单、AgentRun（运行记录）、审批和工具调用记录另存到数据库。</p></div>
        <div className="wf-detail-card wf-boundary"><h3>这个演示不代表什么？</h3><p>页面按当前代码展示流程，所选场景只是示例。真实请求执行哪些步骤，仍由用户问题、后端配置、风险和回复质量决定。</p><p>工具使用本地 Mock（模拟数据），模型和 Jev 是否启用取决于后端配置。仓库提供 16 篇初始化知识文档，但当前部署不一定已导入全部文档。</p></div>
      </aside>
    </div>
    <section className="wf-foundations"><h2>流程之外，系统如何可靠运行</h2><div className="wf-foundation-grid">{[['保存会话和任务进度', '数据库保存工单、会话和运行记录，Redis 缓存最近的历史消息。Checkpoint 保存暂停时的进度，执行租约防止多个进程同时恢复任务。人工审批后，系统从保存的位置继续。'], ['Tool Governance V2.2（工具操作管理）', '退款等高风险操作需要单独提出申请并审批。系统用幂等键防止重复操作，通过 Transactional Outbox（事务消息表）记录待执行任务，再由 Worker（后台执行程序）处理。超时后先标记结果未知并查询实际结果，不直接重试退款。'], ['查看执行过程并评测质量', 'OpenTelemetry 记录节点、模型、知识检索和工具调用。Collector 将 Trace（调用过程）发到 LangSmith，并提供 Prometheus 指标。系统用固定 Baseline（测试集）重新运行流程，检查行为是否符合预期；PromptOps 可以在同一测试集上比较当前版本和候选版本。'], ['服务故障时怎样处理', '模型、检索或工具调用都有超时限制和重试次数限制；连续失败时可暂停调用或使用备用方案。系统分别检查用户输入、查询结果、知识文档和回复。如果无法确认结果可靠，就向用户说明情况或交给人工。']].map(([title, text]) => <article key={title}><h3>{title}</h3><p>{text}</p></article>)}</div></section>
    </>}
  </section>;
}
