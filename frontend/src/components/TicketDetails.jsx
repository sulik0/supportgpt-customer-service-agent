import '../styles/pages/workspace.css';
import { friendlyError } from '../errors';
import { formatOrderAmount, formatToolStatus } from '../formatters';
import React, { useEffect, useRef, useState } from 'react';
import { createLatestRequest } from '../latestRequest';
import { useConfirm, useUnsavedChanges } from './uiHooks';
import { Badge, Button, Card, EmptyState, Skeleton } from './ui';
import ToolReviewPanel from './ToolReviewPanel';
import { AlertTriangle, BookOpen, CheckCircle2, ChevronDown, ClipboardCheck, RefreshCw, ShieldAlert, ShoppingBag, Sparkles, User, XCircle } from 'lucide-react';
import { evaluateResponse, fetchCustomerContext, fetchTicketAgentResult, submitApproval } from '../api/client';
import { translateEscalationReason, translateOrderItem, translatePriority, translateStatus, translateSubject, translateTier } from '../i18n';
export default function TicketDetails({
  ticket,
  userRole,
  onActionComplete
}) {
  const [customer, setCustomer] = useState(null);
  const [chatOutput, setChatOutput] = useState(null);
  const [editedResponse, setEditedResponse] = useState('');
  const [evaluation, setEvaluation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [evalLoading, setEvalLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [actionNotice, setActionNotice] = useState('');
  const [actionError, setActionError] = useState('');
  const detailsRequests = useRef(null);
  const evaluationRequests = useRef(null);
  if (!detailsRequests.current) detailsRequests.current = createLatestRequest();
  if (!evaluationRequests.current) evaluationRequests.current = createLatestRequest();
  const {
    ask,
    confirmation
  } = useConfirm();
  const responseChanged = Boolean(chatOutput && editedResponse.trim() !== chatOutput.response.trim());
  useUnsavedChanges(responseChanged, ask);

  // 切换工单时只读取已经持久化的 Agent 结果。
  useEffect(() => {
    if (!ticket) return;
    setCustomer(null);
    setChatOutput(null);
    setEditedResponse('');
    setEvaluation(null);
    setEvalLoading(false);
    setLoadError('');
    setActionNotice('');
    setActionError('');
    loadDetails(ticket);
    return () => {
      detailsRequests.current.cancel();
      evaluationRequests.current.cancel();
    };
  }, [ticket?.id]);
  async function loadDetails(currentTicket = ticket) {
    if (!currentTicket) return;
    const request = detailsRequests.current.start();
    setLoading(true);
    setLoadError('');
    try {
      const [crmProfile, chatRes] = await Promise.all([fetchCustomerContext(currentTicket.customer_id, request.signal), fetchTicketAgentResult(currentTicket.id, request.signal)]);
      if (!request.isCurrent()) return;
      setCustomer(crmProfile);
      setChatOutput(chatRes);
      setEditedResponse(chatRes.response);
    } catch (err) {
      if (err.name === 'AbortError' || !request.isCurrent()) return;
      console.error('加载工单详情失败：', err);
      setLoadError(friendlyError(err) || 'Agent 处理失败，请稍后重试。');
    } finally {
      if (request.isCurrent()) setLoading(false);
    }
  }
  async function refreshDetails() {
    // 重新读取结果前确认，避免覆盖员工正在编辑的回复。
    if (responseChanged && !(await ask({
      title: '放弃修改并重新加载？',
      description: '重新加载会用已保存的回复替换当前尚未提交的修改。',
      confirmLabel: '重新加载'
    }))) return;
    await loadDetails(ticket);
  }
  async function handleApproval(status) {
    if (!chatOutput?.approval_id) return;
    const trimmedResponse = editedResponse.trim();
    const responseChanged = trimmedResponse !== chatOutput.response.trim();
    if (status === 'modified' && !responseChanged) {
      setActionError('回复内容没有变化，可直接批准 AI 回复。');
      return;
    }
    if (status === 'modified' && !trimmedResponse) {
      setActionError('人工修改后的回复不能为空。');
      return;
    }
    if (status === 'rejected' && !(await ask({
      title: '拒绝这条回复？',
      description: '拒绝后，当前回复审批将按拒绝结果结束。此操作不代表取消或执行业务操作。',
      confirmLabel: '拒绝回复',
      danger: true
    }))) return;
    setLoading(true);
    setActionNotice('');
    setActionError('');
    try {
      await submitApproval(chatOutput.approval_id, status, status === 'modified' ? trimmedResponse : null);
      setActionNotice(status === 'approved' ? 'AI 回复已批准。' : status === 'modified' ? '人工修改已提交。' : 'AI 回复已拒绝。');
      onActionComplete?.();
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setLoading(false);
    }
  }
  async function triggerEvaluation() {
    if (!chatOutput || evalLoading) return;
    const request = evaluationRequests.current.start();
    setEvalLoading(true);
    try {
      const contexts = (chatOutput.citations || []).map(citation => citation.text);
      const result = await evaluateResponse(ticket.description, contexts, editedResponse, chatOutput.agent_run_id);
      if (request.isCurrent()) setEvaluation(result);
    } catch (err) {
      if (request.isCurrent()) setActionError(`回复评测失败：${friendlyError(err)}`);
    } finally {
      if (request.isCurrent()) setEvalLoading(false);
    }
  }
  if (!ticket) {
    return <Card className="ticket-empty-state"><EmptyState icon={<ClipboardCheck size={34} />} title="从人工队列中选择一张工单" description="普通问题由 Agent 自动回复。这里显示需要人工确认或审批的工单；选择一张即可查看已保存的回复和处理原因。"><div className="empty-workflow"><span>1. 理解问题</span><span>2. 查询相关资料</span><span>3. 生成回复</span><span>4. 人工确认</span></div></EmptyState></Card>;
  }
  const citations = chatOutput?.citations || [];
  const recentOrders = customer?.recent_orders || [];
  const canRunEvaluation = ['manager', 'admin'].includes(userRole);
  const reviewReasons = Array.from(new Set((chatOutput?.review_reasons?.length ? chatOutput.review_reasons : [chatOutput?.escalation_reason]).map(translateEscalationReason).filter(Boolean)));
  return <section className="ticket-detail">
      {confirmation}
      <div className="workspace-detail-main">
      <Card className="ticket-overview">
      <header className="ticket-detail-header">
        <div>
          <div className="ticket-detail-meta">
            <span className="workspace-mono">工单 #{ticket.id}</span>
            <Badge tone={ticket.status === 'resolved' ? 'success' : ticket.status === 'pending_approval' ? 'accent' : 'neutral'}>{translateStatus(ticket.status)}</Badge>
            <Badge tone={['urgent', 'high'].includes(ticket.priority) ? 'warning' : 'neutral'}>{translatePriority(ticket.priority)}优先级</Badge>
          </div>
          <h2>{translateSubject(ticket.subject)}</h2>
          <p>客户 {ticket.customer_id} · 本次使用的知识库版本：{chatOutput?.kb_version || (loading ? '加载中' : '暂不可用')}</p>
        </div>
        <button className="btn btn-secondary compact-button" onClick={refreshDetails} disabled={loading}>
          <RefreshCw size={15} className={loading ? 'spin' : ''} /> 刷新已保存的结果
        </button>
      </header>

      <blockquote className="workspace-customer-quote"><span className="workspace-customer-avatar" aria-hidden="true"><User size={16} /></span><div><span>客户 {ticket.customer_id} · 客户提交的原始内容</span><p>{ticket.description}</p></div></blockquote>
      </Card>
      <p className="saved-result-notice">此页读取已保存的处理结果，不会重新运行 Agent。</p>

      {loading && <Card className="agent-loading-card"><div className="workspace-skeleton" role="status"><span>正在读取已保存的处理结果</span><Skeleton width="50%" height={20} /><Skeleton height={100} /><Skeleton width="75%" /><span className="sr-only">正在加载客户资料、引用文档和回复草稿，请稍候</span></div></Card>}

      {!loading && loadError && <div className="detail-error"><AlertTriangle size={19} /><div><strong>处理结果加载失败</strong><span>{loadError}</span></div><button className="btn btn-secondary" onClick={refreshDetails}>重新加载</button></div>}

      {!loading && chatOutput && <Card className="assistant-panel">
          <header className="assistant-heading">
            <div className="assistant-title">
              <span className="assistant-logo"><Sparkles size={19} /></span>
              <div><span>SupportGPT 回复建议</span><small>已保存的回复和检查结果</small></div>
            </div>
            <div className="assistant-badges">
              <Badge><BookOpen size={13} /> {citations.length} 条知识依据</Badge>
              {chatOutput.qa_score != null && <Badge>QA {chatOutput.qa_score.toFixed(2)}</Badge>}
              {chatOutput.hallucination_detected && <Badge tone="danger"><ShieldAlert size={13} /> 回复可能缺少依据</Badge>}
              {chatOutput.approval_required ? <Badge tone="warning"><ShieldAlert size={13} /> 待人工审批</Badge> : <Badge tone="success"><CheckCircle2 size={13} /> 自动校验通过</Badge>}
            </div>
          </header>

          {chatOutput.escalation_recommended && <div className="escalation-banner">
              <ShieldAlert size={19} />
              <div>
                <strong>建议交给人工处理</strong>
                <span>{reviewReasons[0] || '该工单需要人工复核。'}</span>
                {reviewReasons.length > 1 && <ul className="escalation-reasons">
                    {reviewReasons.slice(1).map(reason => <li key={reason}>{reason}</li>)}
                  </ul>}
                {chatOutput.risk_level && chatOutput.risk_score != null && <small>
                    风险等级：{chatOutput.risk_level.toUpperCase()}
                    · 风险分：{chatOutput.risk_score.toFixed(2)}
                    {chatOutput.analyzer_confidence != null && ` · 意图置信度：${chatOutput.analyzer_confidence.toFixed(2)}`}
                  </small>}
              </div>
            </div>}

          <div className="draft-section">
            <div className="draft-label"><div><strong>回复草稿</strong><span>发送前可直接编辑</span></div><span>{editedResponse.length} 字</span></div>
            <textarea aria-label="回复草稿" value={editedResponse} onChange={event => setEditedResponse(event.target.value)} placeholder="已保存的 Agent 回复草稿会显示在这里……" />
          </div>

          {citations.length > 0 && <div className="workspace-citation-tags" aria-label="引用知识"><span>引用知识</span>{citations.map((citation, index) => <Badge key={`${citation.source}-${index}`}><BookOpen size={12} />{citation.source}</Badge>)}</div>}
          <details className="reference-disclosure">
            <summary>
              <span><BookOpen size={16} /> 查看知识引用与检索依据</span>
              <span>{citations.length} 条 <ChevronDown size={15} /></span>
            </summary>
            <div className="reference-list">
              {citations.length === 0 ? <p className="no-reference">本次未检索到可引用的知识文档。</p> : citations.map((citation, index) => <div className="reference-item" key={`${citation.source}-${index}`}>
                  <div><Badge><BookOpen size={12} />{citation.source}</Badge><em>相关度 {citation.score}</em></div>
                  <p>{citation.text}</p>
                </div>)}
            </div>
          </details>

          <footer className="assistant-actions">
            {canRunEvaluation && <button onClick={triggerEvaluation} className="btn btn-quiet" disabled={evalLoading}>
                <Sparkles size={14} className={evalLoading ? 'spin' : ''} /> {evalLoading ? '评测中…' : '运行质量评测'}
              </button>}
            <div>
              {chatOutput.approval_required ? <>
                  <Button onClick={() => handleApproval('rejected')} tone="danger" size="lg"><XCircle size={16} /> 拒绝回复</Button>
                  <Button onClick={() => handleApproval('approved')} tone="primary" size="lg" disabled={responseChanged}><CheckCircle2 size={16} /> 批准原回复</Button>
                  <Button onClick={() => handleApproval('modified')} tone={responseChanged ? 'primary' : 'secondary'} size="lg" disabled={!responseChanged}>批准修改后的回复</Button>
                </> : <span className="no-review-needed"><CheckCircle2 size={16} /> 此回复无需人工审批</span>}
            </div>
          </footer>

          {actionNotice && <div className="action-notice success" role="status">{actionNotice}</div>}
          {actionError && <div className="action-notice error" role="alert">{actionError}</div>}

          {evaluation && <section className="evaluation-panel">
              <div className="evaluation-heading"><div><span>本次回复的质量评测</span><small>Ragas + DeepEval</small></div><CheckCircle2 size={19} /></div>
              <div className="evaluation-grid">
                <div><span>与依据一致程度</span><strong>{evaluation.faithfulness_score}</strong></div>
                <div><span>无依据内容比例</span><strong className={evaluation.hallucination_rate > 0.3 ? 'score-risk' : ''}>{evaluation.hallucination_rate}</strong></div>
                <div><span>相关资料召回率</span><strong>{evaluation.context_recall}</strong></div>
                <div><span>回答相关性</span><strong>{evaluation.answer_relevance}</strong></div>
              </div>
            </section>}
        </Card>}
      <div className="case-context-grid">
        <article className="detail-card customer-card">
          <div className="card-heading"><span className="card-icon"><User size={17} /></span><div><span>客户资料</span><small>来自本地 CRM 演示适配器</small></div></div>
          {customer ? <div className="customer-facts">
              <div><span>客户</span><strong>{customer.name}</strong></div>
              <div><span>等级</span><strong>{translateTier(customer.tier)}</strong></div>
              <div><span>未结工单</span><strong>{customer.open_tickets_count}</strong></div>
              <div><span>最近订单</span><strong>{recentOrders.length}</strong></div>
            </div> : <div className="workspace-skeleton" role="status"><span className="sr-only">{loadError ? '客户资料暂不可用' : '正在读取客户资料…'}</span>{loadError ? <span>客户资料暂不可用</span> : <><Skeleton /><Skeleton width="70%" /></>}</div>}
        </article>
      </div>

      {customer && recentOrders.length > 0 && <details className="orders-disclosure">
          <summary><span><ShoppingBag size={16} /> 最近订单</span><span>{recentOrders.length} 笔 <ChevronDown size={15} /></span></summary>
          <div className="orders-grid">
            {recentOrders.map(order => <div className="order-item" key={order.order_id}>
                <div><strong>{order.order_id}</strong><span>{translateStatus(order.status)}</span></div>
                <p>{order.items.map(translateOrderItem).join('、')}</p>
                <b>{formatOrderAmount(order.total_amount, order.currency)}</b>
              </div>)}
          </div>
        </details>}

      </div>
      <aside className="workspace-review-column" aria-label="业务操作核实与处理轨迹">
        <ToolReviewPanel ticketId={ticket.id} userRole={userRole} onActionComplete={onActionComplete} />
        {!loading && chatOutput?.tool_calls?.length > 0 && <Card title="工具调用轨迹" className="workspace-run-trace">
          <div className="workspace-tool-trace"><ol>{chatOutput.tool_calls.map((call, index) => <li key={`${call.tool_name}-${index}`}>
            <span className="workspace-trace-dot" aria-hidden="true" /><div><code>{call.tool_name}</code><span>{formatToolStatus(call.status)}</span>{call.latency_ms != null && <span className="workspace-mono">{call.latency_ms} ms</span>}{call.error && <span>{call.error}</span>}</div>
          </li>)}</ol></div>
        </Card>}
      </aside>
    </section>;
}
