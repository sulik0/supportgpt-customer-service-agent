import { friendlyError } from '../errors';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, Bot, Check, Clock3, Copy, ExternalLink, RefreshCw, Route, Search, ShieldAlert, Sparkles, X } from 'lucide-react';
import { fetchAgentRun, fetchAgentRuns } from '../api/client';
import { translatePriority, translateSentiment } from '../i18n';
import { Overlay } from './ui';
import { updateRouteParams } from '../navigation';
import { formatLatency, summarizeRuns, totalTokens } from './runMetrics';
const PAGE_SIZE = 20;
const LANGSMITH_PROJECT_URL = import.meta.env.VITE_LANGSMITH_PROJECT_URL || 'https://smith.langchain.com/';
const HAS_PROJECT_URL = Boolean(import.meta.env.VITE_LANGSMITH_PROJECT_URL);
const NODE_LABELS = {
  analyzer: '分析问题',
  skill_selector: '选择处理能力',
  context_enrichment: '查询业务和知识',
  resolver: '生成回复',
  approval_gate: '检查是否需要审批',
  human_approval: '人工审批后继续',
  ticket_analyzer: '工单分析',
  tool_call: '工具调用',
  retriever: '知识检索',
  llm_generation: '回复生成',
  qa: '检查回复质量',
  escalation: '判断是否转人工'
};
const STRATEGY_LABELS = {
  rule: '规则处理',
  jev: 'Jev 判断',
  llm: 'LLM（大模型）判断',
  not_run: '未执行',
  unknown: '未知'
};
const INTENT_LABELS = {
  billing_dispute: '账务 / 退款争议',
  outage_report: 'API / 服务故障',
  order_cancellation: '订单取消',
  order_status: '订单状态',
  account_support: '账户异常',
  warranty_claim: '保修申请',
  feedback: '用户反馈',
  information_request: '信息咨询'
};
const DEPARTMENT_LABELS = {
  billing: '账务',
  technical: '技术支持',
  shipping: '订单与物流',
  general: '综合支持'
};
function StrategyBadge({
  value
}) {
  const normalized = value || 'not_run';
  return <span className={`obs-strategy strategy-${normalized}`}>{STRATEGY_LABELS[normalized] || normalized}</span>;
}
function formatDate(value) {
  if (!value) return '-';
  // 数据库存储 UTC 裸时间；无时区后缀时按 UTC 解析，再统一显示北京时间。
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  }).format(new Date(normalized));
}
function runStatus(run) {
  if (run.workflow_errors?.length) return {
    label: '异常',
    className: 'status-error'
  };
  if (run.approval_required || run.escalation_recommended) {
    return {
      label: '本次需人工',
      className: 'status-review'
    };
  }
  return {
    label: '已完成',
    className: 'status-success'
  };
}
function SummaryCard({
  icon,
  label,
  value,
  hint
}) {
  return <div className="obs-summary-card glass-card">
      <span className="obs-summary-icon">{icon}</span>
      <div>
        <div className="obs-summary-label">{label}</div>
        <div className="obs-summary-value">{value}</div>
        <div className="obs-summary-hint">{hint}</div>
      </div>
    </div>;
}
function RunDetail({
  run,
  loading,
  error,
  onRetry,
  onClose,
  onCopyTrace,
  copiedTrace
}) {
  if (!run && !loading) return null;
  const snapshot = run?.execution_snapshot;
  const analyzer = snapshot?.analyzer_result || {};
  return <Overlay title="Agent 运行详情" onClose={onClose} drawer>
        <div className="obs-detail-header">
          <div>
            <span className="obs-eyebrow">AgentRun（运行记录）</span>
            <h2>{run?.id || '正在加载…'}</h2>
          </div>
        </div>

        {error ? <div className="ui-dialog-body"><div role="alert">{error}</div><button className="btn btn-secondary" onClick={onRetry}>重新加载</button></div> : loading ? <div className="obs-empty"><RefreshCw className="spin" size={20} /> 正在加载运行详情…</div> : <div className="obs-detail-content">
            <section className="obs-detail-section">
              <h3>查看本次调用过程</h3>
              <div className="obs-trace-box">
                <code>{run.trace_id || '本次运行未采集 Trace ID'}</code>
                {run.trace_id && <button className="obs-icon-button" onClick={() => onCopyTrace(run.trace_id)} title="复制 Trace ID" aria-label="复制 Trace ID">
                    {copiedTrace === run.trace_id ? <Check size={16} /> : <Copy size={16} />}
                  </button>}
              </div>
              <a className="btn btn-primary obs-link-button" href={LANGSMITH_PROJECT_URL} target="_blank" rel="noreferrer">
                在 LangSmith 中查看 <ExternalLink size={15} />
              </a>
              <p className="obs-helper">打开 LangSmith 项目后，用上方 Trace ID 搜索本次请求，查看各节点和服务调用的详情。</p>
            </section>

            <section className="obs-detail-section">
              <h3>本次执行了哪些步骤？</h3>
              <p className="obs-helper">这是保存的执行路径，不表示实时进度或精确节点耗时。完整调用过程请查看 LangSmith。</p>
              <div className="obs-workflow">
                {(run.workflow_path || []).map((node, index) => <React.Fragment key={`${node}-${index}`}>
                    <div className="obs-node"><span>{index + 1}</span>{NODE_LABELS[node] || node}</div>
                    {index < run.workflow_path.length - 1 && <div className="obs-arrow">→</div>}
                  </React.Fragment>)}
              </div>
            </section>

            <section className="obs-detail-section">
              <h3>本次运行的配置与结果</h3>
              <dl className="obs-kv-grid">
                <div><dt>模型</dt><dd>{run.model_provider} / {run.model_name}</dd></div>
                <div><dt>提示词版本</dt><dd>{run.prompt_version}</dd></div>
                <div><dt>流程版本</dt><dd>{run.workflow_version}</dd></div>
                <div><dt>所用 Skill</dt><dd>{run.skill_selection ? `${run.skill_selection.skill_name} / ${run.skill_selection.skill_version}` : '-'}</dd></div>
                <div><dt>Skill 选择方式</dt><dd>{run.skill_selection?.selection_strategy === 'intent_rule' ? '按问题分类选择' : run.skill_selection?.selection_strategy || '-'}</dd></div>
                <div><dt>问题分析方式</dt><dd>{snapshot ? <StrategyBadge value={snapshot.analyzer_strategy} /> : '-'}</dd></div>
                <div><dt>回复检查方式</dt><dd>{snapshot ? <StrategyBadge value={snapshot.qa_strategy} /> : '-'}</dd></div>
                <div><dt>知识库</dt><dd>{run.kb_version}</dd></div>
                <div><dt>处理耗时</dt><dd>{formatLatency(run.latency_seconds, 3)}</dd></div>
                <div><dt>Token 用量</dt><dd>{totalTokens(run) ?? '未记录'}</dd></div>
                <div><dt>QA 评分</dt><dd>{run.qa_score == null ? '-' : run.qa_score.toFixed(2)}</dd></div>
                <div><dt>本次是否要求审批</dt><dd>{run.approval_required ? '需要' : '不需要'}</dd></div>
              </dl>

              {snapshot ? <div className="obs-analyzer-snapshot">
                  <div className="obs-snapshot-heading">
                    <div><strong>Analyzer（问题分析）结果</strong><span>系统根据这些结果选择后续 Skill 和工具</span></div>
                    <StrategyBadge value={snapshot.analyzer_strategy} />
                  </div>
                  <dl className="obs-analyzer-grid">
                    <div><dt>意图</dt><dd>{INTENT_LABELS[analyzer.intent] || analyzer.intent || '-'}</dd></div>
                    <div><dt>部门</dt><dd>{DEPARTMENT_LABELS[analyzer.department] || analyzer.department || '-'}</dd></div>
                    <div><dt>优先级</dt><dd>{translatePriority(analyzer.priority)}</dd></div>
                    <div><dt>情绪</dt><dd>{translateSentiment(analyzer.sentiment)}</dd></div>
                    <div><dt>置信度</dt><dd>{typeof analyzer.confidence === 'number' ? analyzer.confidence.toFixed(2) : '-'}</dd></div>
                  </dl>
                </div> : <p className="obs-snapshot-unavailable">这是一条旧记录，没有保存 Analyzer 和 QA 当时使用的处理方式。</p>}
            </section>

            <section className="obs-detail-section">
              <h3>工具与引用</h3>
              <div className="obs-compact-list">
                {(run.tool_calls || []).length ? run.tool_calls.map((tool, index) => <div key={`${tool.tool_name}-${index}`}>
                    <Bot size={15} />
                    <span>{tool.tool_name || '未命名工具'}</span>
                    <em>{tool.status || '未知'}</em>
                  </div>) : <p>本次运行没有记录工具调用。</p>}
                {(run.citations || []).map((citation, index) => <div key={`${citation.source}-${index}`}>
                    <Sparkles size={15} />
                    <span>{citation.source || '未命名知识来源'}</span>
                    <em>知识引用</em>
                  </div>)}
              </div>
            </section>

            {(run.workflow_errors || []).length > 0 && <section className="obs-detail-section obs-error-section">
                <h3><AlertTriangle size={16} /> 执行过程中出现的异常</h3>
                {run.workflow_errors.map((error, index) => <p key={index}>{error}</p>)}
              </section>}

            <section className="obs-detail-section">
              <h3>脱敏输入与回复</h3>
              <div className="obs-text-snapshot"><strong>用户输入</strong><p>{run.input_text}</p></div>
              <div className="obs-text-snapshot"><strong>Agent 回复</strong><p>{run.output_text}</p></div>
            </section>
          </div>}
    </Overlay>;
}
export default function ObservabilityPage({
  route
}) {
  const [page, setPage] = useState({
    items: [],
    total: 0,
    limit: PAGE_SIZE,
    offset: 0
  });
  const offset = Math.floor((route?.offset || 0) / PAGE_SIZE) * PAGE_SIZE;
  const ticketFilter = route?.ticket || null;
  const [ticketQuery, setTicketQuery] = useState(ticketFilter ? String(ticketFilter) : '');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedRun, setSelectedRun] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [copiedTrace, setCopiedTrace] = useState('');
  const [detailError, setDetailError] = useState('');
  const listController = useRef(null);
  const detailController = useRef(null);
  useEffect(() => {
    setTicketQuery(ticketFilter ? String(ticketFilter) : '');
  }, [ticketFilter]);
  const loadRuns = useCallback(async () => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    setLoading(true);
    setError('');
    try {
      const nextPage = await fetchAgentRuns(PAGE_SIZE, offset, ticketFilter, controller.signal);
      if (!controller.signal.aborted) setPage(nextPage);
    } catch (requestError) {
      if (!controller.signal.aborted) setError(friendlyError(requestError));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [offset, ticketFilter]);
  useEffect(() => {
    loadRuns();
    return () => listController.current?.abort();
  }, [loadRuns]);
  const summary = useMemo(() => {
    return summarizeRuns(page.items);
  }, [page.items]);
  async function openRun(runId) {
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setDetailLoading(true);
    setDetailError('');
    setSelectedRun({
      id: runId
    });
    try {
      const nextRun = await fetchAgentRun(runId, controller.signal);
      if (!controller.signal.aborted) setSelectedRun(nextRun);
    } catch (requestError) {
      if (!controller.signal.aborted) setDetailError(friendlyError(requestError));
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  }
  async function copyTrace(traceId) {
    try {
      await navigator.clipboard.writeText(traceId);
      setCopiedTrace(traceId);
      window.setTimeout(() => setCopiedTrace(''), 1600);
    } catch {
      setError('复制失败，请在详情中手动选择 Trace ID。');
    }
  }
  function handleTicketSearch(event) {
    event.preventDefault();
    const normalized = ticketQuery.trim().replace(/^#/, '');
    const nextTicketId = Number(normalized);
    if (!/^\d+$/.test(normalized) || !Number.isSafeInteger(nextTicketId) || nextTicketId < 1) {
      setError('请输入大于 0 的工单编号，例如 123。');
      return;
    }
    setError('');
    if (ticketFilter === nextTicketId && offset === 0) loadRuns();else updateRouteParams({
      ticket: nextTicketId,
      offset: 0,
      run: null
    }, false);
  }
  function clearTicketSearch() {
    setTicketQuery('');
    setError('');
    if (ticketFilter == null && offset === 0) loadRuns();else updateRouteParams({
      ticket: null,
      offset: null,
      run: null
    }, false);
  }
  const canGoNext = offset + PAGE_SIZE < page.total;
  useEffect(() => {
    if (route?.run) openRun(route.run);else {
      detailController.current?.abort();
      setSelectedRun(null);
      setDetailLoading(false);
    }
    return () => detailController.current?.abort();
  }, [route?.run]);
  return <section className="observability-page">
      <div className="obs-hero glass-card">
        <div>
          <span className="obs-eyebrow"><Activity size={14} /> Agent 运行监控</span>
          <h2>查看 Agent 怎样处理每次请求</h2>
          <p>先查找运行记录，再用 Trace ID 到 LangSmith 查看流程步骤、模型调用、知识检索和工具调用。</p>
        </div>
        <div className="obs-hero-actions">
          <button className="btn btn-secondary" onClick={loadRuns} disabled={loading}>
            <RefreshCw size={15} className={loading ? 'spin' : ''} /> 刷新
          </button>
          <a className="btn btn-primary" href={LANGSMITH_PROJECT_URL} target="_blank" rel="noreferrer">
            打开 LangSmith <ExternalLink size={15} />
          </a>
        </div>
      </div>

      {!HAS_PROJECT_URL && <div className="obs-config-notice">
          <ShieldAlert size={17} />
          尚未配置 LangSmith 项目快捷入口，当前按钮会打开 LangSmith 首页。若本次 Trace 已上报，可复制 Trace ID 并在 LangSmith 中搜索。
        </div>}

      <div className="obs-summary-grid">
        <SummaryCard icon={<Route size={20} />} label="运行记录数" value={loading || error ? '—' : page.total} hint="符合当前查询条件的记录" />
        <SummaryCard icon={<Clock3 size={20} />} label="本页平均耗时" value={loading || error ? '—' : formatLatency(summary.averageLatency)} hint={`按本页 ${summary.latencyCount} 条已记录耗时计算`} />
        <SummaryCard icon={<Bot size={20} />} label="本页 Token 用量" value={loading || error ? '—' : summary.totalTokens?.toLocaleString() ?? '—'} hint="本页已记录的模型输入与输出合计" />
        <SummaryCard icon={<ShieldAlert size={20} />} label="本页建议人工处理" value={loading || error ? '—' : summary.reviewCount} hint="运行时的判断，不代表工单当前状态" />
      </div>

      <div className="obs-runs-card glass-card">
        <div className="obs-table-heading">
          <div>
            <h3>Agent 运行记录</h3>
            <p>{ticketFilter ? `正在查看工单 #${ticketFilter} 的运行记录` : '仅主管和管理员可查看'}</p>
          </div>
          <div className="obs-table-controls">
            <form className="obs-ticket-search" onSubmit={handleTicketSearch}>
              <Search size={15} />
              <input inputMode="numeric" value={ticketQuery} onChange={event => setTicketQuery(event.target.value)} placeholder="按工单编号查询" aria-label="按工单编号查询运行记录" />
              <button type="submit" className="btn btn-secondary" disabled={loading}>查询</button>
              {ticketFilter && <button type="button" className="btn btn-quiet" onClick={clearTicketSearch}>清除</button>}
            </form>
            <span>{page.total ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, page.total)} / {page.total}</span>
          </div>
        </div>

        {error && <div className="obs-error-banner" role="alert"><AlertTriangle size={17} /> {error}<button className="btn btn-secondary" onClick={loadRuns}>重试</button></div>}
        {loading ? <div className="obs-empty"><RefreshCw className="spin" size={20} /> 正在加载运行记录…</div> : error ? null : page.items.length === 0 ? <div className="obs-empty">{ticketFilter ? `工单 #${ticketFilter} 还没有运行记录。` : '还没有运行记录。请先在用户咨询页提交一个问题，或创建一张工单。'}</div> : <div className="obs-table-wrap">
            <table className="obs-table">
              <thead><tr><th scope="col">北京时间（UTC+8）</th><th scope="col">工单</th><th scope="col">运行 / Trace 编号</th><th scope="col">执行步骤</th><th scope="col">回复质量</th><th scope="col">耗时与用量</th><th scope="col">本次结果</th></tr></thead>
              <tbody>
                {page.items.map(run => {
              const status = runStatus(run);
              return <tr key={run.id} onClick={() => updateRouteParams({
                run: run.id
              }, false)} tabIndex={0} aria-label={`查看运行 ${run.id}`} onKeyDown={event => {
                if (['Enter', ' '].includes(event.key)) {
                  event.preventDefault();
                  updateRouteParams({
                    run: run.id
                  }, false);
                }
              }}>
                      <td>{formatDate(run.created_at)}</td>
                      <td>{run.ticket_id ? <strong>#{run.ticket_id}</strong> : <span>-</span>}</td>
                      <td><code>{run.id.slice(0, 8)}</code><small>{run.trace_id ? `Trace ${run.trace_id.slice(0, 10)}…` : '无 Trace ID'}</small></td>
                      <td><strong>{run.workflow_path?.length || 0} 个步骤</strong><small>{run.model_name}</small></td>
                      <td><strong>{run.qa_score == null ? '-' : run.qa_score.toFixed(2)}</strong><small>{run.hallucination_detected == null ? '未记录质量判断' : run.hallucination_detected ? '可能缺少依据' : '未发现编造内容'}</small></td>
                      <td><strong>{formatLatency(run.latency_seconds)}</strong><small>{totalTokens(run)?.toLocaleString() ?? '未记录'} Token</small></td>
                      <td><span className={`obs-status ${status.className}`}>{status.label}</span></td>
                    </tr>;
            })}
              </tbody>
            </table>
          </div>}

        <div className="obs-pagination">
          <button className="btn btn-secondary" disabled={offset === 0 || loading} onClick={() => updateRouteParams({
          offset: Math.max(0, offset - PAGE_SIZE)
        }, false)}>上一页</button>
          <button className="btn btn-secondary" disabled={!canGoNext || loading} onClick={() => updateRouteParams({
          offset: offset + PAGE_SIZE
        }, false)}>下一页</button>
        </div>
      </div>

      <RunDetail run={selectedRun} loading={detailLoading} error={detailError} onRetry={() => openRun(route.run)} onClose={() => updateRouteParams({
      run: null
    })} onCopyTrace={copyTrace} copiedTrace={copiedTrace} />
    </section>;
}
