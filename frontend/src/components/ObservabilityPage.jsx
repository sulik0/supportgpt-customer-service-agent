import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bot,
  Check,
  Clock3,
  Copy,
  ExternalLink,
  RefreshCw,
  Route,
  Search,
  ShieldAlert,
  Sparkles,
  X,
} from 'lucide-react';
import { fetchAgentRun, fetchAgentRuns } from '../api/client';
import { translatePriority, translateSentiment } from '../i18n';

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
  escalation: '判断是否转人工',
};

const STRATEGY_LABELS = {
  rule: '规则处理',
  jev: 'Jev 判断',
  llm: 'LLM（大模型）判断',
  not_run: '未执行',
  unknown: '未知',
};

const INTENT_LABELS = {
  billing_dispute: '账务 / 退款争议',
  outage_report: 'API / 服务故障',
  order_cancellation: '订单取消',
  order_status: '订单状态',
  account_support: '账户异常',
  warranty_claim: '保修申请',
  feedback: '用户反馈',
  information_request: '信息咨询',
};

const DEPARTMENT_LABELS = {
  billing: '账务',
  technical: '技术支持',
  shipping: '订单与物流',
  general: '综合支持',
};

function StrategyBadge({ value }) {
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
    hourCycle: 'h23',
  }).format(new Date(normalized));
}

function runStatus(run) {
  if (run.workflow_errors?.length) return { label: '异常', className: 'status-error' };
  if (run.approval_required || run.escalation_recommended) {
    return { label: '待人工', className: 'status-review' };
  }
  return { label: '已完成', className: 'status-success' };
}

function SummaryCard({ icon, label, value, hint }) {
  return (
    <div className="obs-summary-card glass-card">
      <span className="obs-summary-icon">{icon}</span>
      <div>
        <div className="obs-summary-label">{label}</div>
        <div className="obs-summary-value">{value}</div>
        <div className="obs-summary-hint">{hint}</div>
      </div>
    </div>
  );
}

function RunDetail({ run, loading, onClose, onCopyTrace, copiedTrace }) {
  useEffect(() => {
    if (!run && !loading) return undefined;
    function handleKeyDown(event) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [loading, onClose, run]);

  if (!run && !loading) return null;
  const snapshot = run?.execution_snapshot;
  const analyzer = snapshot?.analyzer_result || {};
  return (
    <div className="obs-detail-backdrop" onClick={onClose}>
      <aside className="obs-detail-panel" role="dialog" aria-modal="true" aria-label="Agent 运行详情" onClick={(event) => event.stopPropagation()}>
        <div className="obs-detail-header">
          <div>
            <span className="obs-eyebrow">AgentRun（运行记录）</span>
            <h2>{run?.id || '正在加载…'}</h2>
          </div>
          <button className="obs-icon-button" onClick={onClose} aria-label="关闭运行详情">
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="obs-empty"><RefreshCw className="spin" size={20} /> 正在加载运行详情…</div>
        ) : (
          <div className="obs-detail-content">
            <section className="obs-detail-section">
              <h3>查看本次调用过程</h3>
              <div className="obs-trace-box">
                <code>{run.trace_id || '本次运行未采集 Trace ID'}</code>
                {run.trace_id && (
                  <button className="obs-icon-button" onClick={() => onCopyTrace(run.trace_id)} title="复制 Trace ID">
                    {copiedTrace === run.trace_id ? <Check size={16} /> : <Copy size={16} />}
                  </button>
                )}
              </div>
              <a className="btn btn-primary obs-link-button" href={LANGSMITH_PROJECT_URL} target="_blank" rel="noreferrer">
                在 LangSmith 中查看 <ExternalLink size={15} />
              </a>
              <p className="obs-helper">打开 LangSmith 项目后，用上方 Trace ID 搜索本次请求，查看各节点和服务调用的详情。</p>
            </section>

            <section className="obs-detail-section">
              <h3>本次执行了哪些步骤？</h3>
              <div className="obs-workflow">
                {(run.workflow_path || []).map((node, index) => (
                  <React.Fragment key={`${node}-${index}`}>
                    <div className="obs-node"><span>{index + 1}</span>{NODE_LABELS[node] || node}</div>
                    {index < run.workflow_path.length - 1 && <div className="obs-arrow">→</div>}
                  </React.Fragment>
                ))}
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
                <div><dt>处理耗时</dt><dd>{run.latency_seconds.toFixed(3)}s</dd></div>
                <div><dt>Token 用量</dt><dd>{run.tokens_input + run.tokens_output}</dd></div>
                <div><dt>QA 评分</dt><dd>{run.qa_score == null ? '-' : run.qa_score.toFixed(2)}</dd></div>
                <div><dt>人工审批</dt><dd>{run.approval_required ? '需要' : '不需要'}</dd></div>
              </dl>

              {snapshot ? (
                <div className="obs-analyzer-snapshot">
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
                </div>
              ) : (
                <p className="obs-snapshot-unavailable">这是一条旧记录，没有保存 Analyzer 和 QA 当时使用的处理方式。</p>
              )}
            </section>

            <section className="obs-detail-section">
              <h3>工具与引用</h3>
              <div className="obs-compact-list">
                {(run.tool_calls || []).length ? run.tool_calls.map((tool, index) => (
                  <div key={`${tool.tool_name}-${index}`}>
                    <Bot size={15} />
                    <span>{tool.tool_name || '未命名工具'}</span>
                    <em>{tool.status || '未知'}</em>
                  </div>
                )) : <p>本次运行没有记录工具调用。</p>}
                {(run.citations || []).map((citation, index) => (
                  <div key={`${citation.source}-${index}`}>
                    <Sparkles size={15} />
                    <span>{citation.source || '未命名知识来源'}</span>
                    <em>知识引用</em>
                  </div>
                ))}
              </div>
            </section>

            {(run.workflow_errors || []).length > 0 && (
              <section className="obs-detail-section obs-error-section">
                <h3><AlertTriangle size={16} /> 执行过程中出现的异常</h3>
                {run.workflow_errors.map((error, index) => <p key={index}>{error}</p>)}
              </section>
            )}

            <section className="obs-detail-section">
              <h3>脱敏输入与回复</h3>
              <div className="obs-text-snapshot"><strong>用户输入</strong><p>{run.input_text}</p></div>
              <div className="obs-text-snapshot"><strong>Agent 回复</strong><p>{run.output_text}</p></div>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

export default function ObservabilityPage() {
  const [page, setPage] = useState({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
  const [offset, setOffset] = useState(0);
  const [ticketQuery, setTicketQuery] = useState('');
  const [ticketFilter, setTicketFilter] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedRun, setSelectedRun] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [copiedTrace, setCopiedTrace] = useState('');

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setPage(await fetchAgentRuns(PAGE_SIZE, offset, ticketFilter));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, [offset, ticketFilter]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const summary = useMemo(() => {
    const runs = page.items;
    const totalTokens = runs.reduce((sum, run) => sum + run.tokens_input + run.tokens_output, 0);
    const averageLatency = runs.length
      ? runs.reduce((sum, run) => sum + run.latency_seconds, 0) / runs.length
      : 0;
    const reviewCount = runs.filter((run) => run.approval_required || run.escalation_recommended).length;
    return { totalTokens, averageLatency, reviewCount };
  }, [page.items]);

  async function openRun(runId) {
    setDetailLoading(true);
    setSelectedRun({ id: runId });
    try {
      setSelectedRun(await fetchAgentRun(runId));
    } catch (requestError) {
      setError(requestError.message);
      setSelectedRun(null);
    } finally {
      setDetailLoading(false);
    }
  }

  async function copyTrace(traceId) {
    await navigator.clipboard.writeText(traceId);
    setCopiedTrace(traceId);
    window.setTimeout(() => setCopiedTrace(''), 1600);
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
    setOffset(0);
    if (ticketFilter === nextTicketId && offset === 0) loadRuns();
    else setTicketFilter(nextTicketId);
  }

  function clearTicketSearch() {
    setTicketQuery('');
    setError('');
    setOffset(0);
    if (ticketFilter == null && offset === 0) loadRuns();
    else setTicketFilter(null);
  }

  const canGoNext = offset + PAGE_SIZE < page.total;

  return (
    <section className="observability-page">
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

      {!HAS_PROJECT_URL && (
        <div className="obs-config-notice">
          <ShieldAlert size={17} />
          尚未配置 LangSmith 项目快捷入口，当前按钮会打开 LangSmith 首页。若本次 Trace 已上报，可复制 Trace ID 并在 LangSmith 中搜索。
        </div>
      )}

      <div className="obs-summary-grid">
        <SummaryCard icon={<Route size={20} />} label="运行记录数" value={page.total} hint="符合当前查询条件的记录" />
        <SummaryCard icon={<Clock3 size={20} />} label="本页平均耗时" value={`${summary.averageLatency.toFixed(2)}s`} hint={`按本页 ${page.items.length} 条记录计算`} />
        <SummaryCard icon={<Bot size={20} />} label="本页 Token 用量" value={summary.totalTokens.toLocaleString()} hint="模型输入与输出合计" />
        <SummaryCard icon={<ShieldAlert size={20} />} label="本页建议人工处理" value={summary.reviewCount} hint="需转人工或审批的运行记录" />
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
              <input
                inputMode="numeric"
                value={ticketQuery}
                onChange={(event) => setTicketQuery(event.target.value)}
                placeholder="按工单编号查询"
                aria-label="按工单编号查询运行记录"
              />
              <button type="submit" className="btn btn-secondary" disabled={loading}>查询</button>
              {ticketFilter && <button type="button" className="btn btn-quiet" onClick={clearTicketSearch}>清除</button>}
            </form>
            <span>{page.total ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, page.total)} / {page.total}</span>
          </div>
        </div>

        {error && <div className="obs-error-banner"><AlertTriangle size={17} /> {error}</div>}
        {loading ? (
          <div className="obs-empty"><RefreshCw className="spin" size={20} /> 正在加载运行记录…</div>
        ) : page.items.length === 0 ? (
          <div className="obs-empty">{ticketFilter ? `工单 #${ticketFilter} 还没有运行记录。` : '还没有运行记录。请先在用户咨询页提交一个问题，或创建一张工单。'}</div>
        ) : (
          <div className="obs-table-wrap">
            <table className="obs-table">
              <thead><tr><th>北京时间</th><th>工单</th><th>运行 / Trace 编号</th><th>执行步骤</th><th>回复质量</th><th>耗时与用量</th><th>状态</th></tr></thead>
              <tbody>
                {page.items.map((run) => {
                  const status = runStatus(run);
                  return (
                    <tr key={run.id} onClick={() => openRun(run.id)} tabIndex={0} onKeyDown={(event) => ['Enter', ' '].includes(event.key) && openRun(run.id)}>
                      <td>{formatDate(run.created_at)}</td>
                      <td>{run.ticket_id ? <strong>#{run.ticket_id}</strong> : <span>-</span>}</td>
                      <td><code>{run.id.slice(0, 8)}</code><small>{run.trace_id ? `Trace ${run.trace_id.slice(0, 10)}…` : '无 Trace ID'}</small></td>
                      <td><strong>{run.workflow_path?.length || 0} 个步骤</strong><small>{run.model_name}</small></td>
                      <td><strong>{run.qa_score == null ? '-' : run.qa_score.toFixed(2)}</strong><small>{run.hallucination_detected ? '可能缺少依据' : '未发现编造内容'}</small></td>
                      <td><strong>{run.latency_seconds.toFixed(2)}s</strong><small>{(run.tokens_input + run.tokens_output).toLocaleString()} Token</small></td>
                      <td><span className={`obs-status ${status.className}`}>{status.label}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="obs-pagination">
          <button className="btn btn-secondary" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
          <button className="btn btn-secondary" disabled={!canGoNext || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>下一页</button>
        </div>
      </div>

      <RunDetail run={selectedRun} loading={detailLoading} onClose={() => setSelectedRun(null)} onCopyTrace={copyTrace} copiedTrace={copiedTrace} />
    </section>
  );
}
