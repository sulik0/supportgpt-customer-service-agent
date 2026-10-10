import { friendlyError } from '../errors';
import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowRight, Bot, CheckCircle2, Clock3, Headphones, GitBranch, RefreshCw, Send, ShieldCheck, Sparkles, Star } from 'lucide-react';
import { fetchSupportHistory, submitSupportRequest, submitUserFeedback } from '../api/client';
const EXAMPLE_QUESTIONS = ['我的订单还没有收到，能帮我查一下吗？', '我想了解退款需要满足什么条件？', 'API 一直超时，应该如何排查？'];
const DEMO_CUSTOMER_HINTS = {
  cust_201: '演示订单 ORD-12001：699 元，已发货，物流延迟。可以询问配送进度或取消订单。',
  cust_202: '演示订单 ORD-12002：1299 元，网关设备已签收，有模拟保修权益。可以询问维修申请。',
  cust_203: '演示订单 ORD-12003：3999 元，企业 API 套餐已付款，发票处理中。可以询问发票或 API 429 故障。'
};
const WELCOME_MESSAGE = {
  role: 'assistant',
  content: '您好，我是 SupportGPT 智能客服。请告诉我您遇到的问题，我会直接回复处理结果；需要人工确认时也会明确告知您。'
};
function createSessionId() {
  return globalThis.crypto?.randomUUID?.() || `support-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
function rememberSession(customerId, sessionId) {
  const key = `supportgpt:sessions:${customerId}`;
  let sessions = [];
  try {
    const stored = JSON.parse(localStorage.getItem(key) || '[]');
    if (Array.isArray(stored)) sessions = stored.filter(item => typeof item === 'string');
  } catch {
    sessions = [];
  }
  const next = [...sessions.filter(item => item !== sessionId), sessionId].slice(-50);
  localStorage.setItem(key, JSON.stringify(next));
  return next;
}
function knownSessions(customerId) {
  try {
    const stored = JSON.parse(localStorage.getItem(`supportgpt:sessions:${customerId}`) || '[]');
    return Array.isArray(stored) ? stored.filter(item => typeof item === 'string').slice(-50) : [];
  } catch {
    return [];
  }
}
function sessionForCustomer(customerId) {
  const key = `supportgpt:session:${customerId}`;
  const existing = localStorage.getItem(key);
  if (existing) {
    rememberSession(customerId, existing);
    return existing;
  }
  const created = createSessionId();
  localStorage.setItem(key, created);
  rememberSession(customerId, created);
  return created;
}
function normalizeUtc(value) {
  if (!value) return null;
  return /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
}
function historyDay(value) {
  const normalized = normalizeUtc(value);
  if (!normalized) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: 'long',
    day: 'numeric'
  }).format(new Date(normalized));
}
function historyTime(value) {
  const normalized = normalizeUtc(value);
  if (!normalized) return '';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  }).format(new Date(normalized));
}
export default function CustomerSupportPage({
  onStaffEntry,
  onWorkflowEntry
}) {
  const [customerId, setCustomerId] = useState('cust_101');
  const [sessionId, setSessionId] = useState(() => sessionForCustomer('cust_101'));
  const [message, setMessage] = useState('');
  const [conversation, setConversation] = useState([WELCOME_MESSAGE]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [feedbackRating, setFeedbackRating] = useState(0);
  const [feedbackComment, setFeedbackComment] = useState('');
  const [feedbackState, setFeedbackState] = useState('idle');
  const [feedbackError, setFeedbackError] = useState('');
  const conversationRef = useRef(null);
  const historyRequest = useRef(0);

  // 新消息出现时滚到末尾，不改变会话和历史数据。
  useEffect(() => {
    const element = conversationRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [conversation, submitting, historyLoading]);
  useEffect(() => {
    const controller = new AbortController();
    loadHistory(customerId, controller.signal);
    return () => {
      controller.abort();
      historyRequest.current += 1;
    };
  }, [customerId]);
  async function loadHistory(currentCustomerId, signal) {
    const requestId = ++historyRequest.current;
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const history = await fetchSupportHistory(currentCustomerId, knownSessions(currentCustomerId), signal);
      if (signal?.aborted || requestId !== historyRequest.current) return;
      const messages = history.messages.map(item => ({
        id: item.id,
        role: item.role,
        content: item.content,
        ticketId: item.ticket_id,
        createdAt: item.created_at
      }));
      setConversation(messages.length ? messages : [WELCOME_MESSAGE]);
    } catch (requestError) {
      if (requestError.name === 'AbortError' || requestId !== historyRequest.current) return;
      setHistoryError('暂时无法加载最近七天的对话，请检查服务连接后重试。');
      setConversation([WELCOME_MESSAGE]);
    } finally {
      if (!signal?.aborted && requestId === historyRequest.current) setHistoryLoading(false);
    }
  }
  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting || historyLoading || !message.trim()) return;
    setSubmitting(true);
    setResult(null);
    setError('');
    resetFeedback();
    const userMessage = message.trim();
    const submittedAt = new Date().toISOString();
    setMessage('');
    setConversation(current => [...current, {
      role: 'user',
      content: userMessage,
      createdAt: submittedAt
    }]);
    try {
      const nextResult = await submitSupportRequest(customerId, userMessage, sessionId);
      setResult(nextResult);
      setConversation(current => [...current, {
        role: nextResult.status === 'answered' ? 'assistant' : 'status',
        content: nextResult.response || nextResult.message || '您的问题已收到，我们正在继续处理。',
        ticketId: nextResult.ticket_id,
        createdAt: new Date().toISOString()
      }]);
    } catch (requestError) {
      const errorMessage = friendlyError(requestError) || '问题提交失败，请稍后重试。';
      setError(errorMessage);
      setConversation(current => [...current, {
        role: 'error',
        content: '抱歉，本次请求暂时未能完成。您的问题仍保留在当前对话中，请稍后重试；如持续失败，请联系人工客服。',
        createdAt: new Date().toISOString()
      }]);
    } finally {
      setSubmitting(false);
    }
  }
  function resetFeedback() {
    setFeedbackRating(0);
    setFeedbackComment('');
    setFeedbackState('idle');
    setFeedbackError('');
  }
  async function handleFeedback(event) {
    event.preventDefault();
    if (!feedbackRating || !result?.agent_run_id || !result?.feedback_token) return;
    setFeedbackState('submitting');
    setFeedbackError('');
    try {
      const randomPart = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      await submitUserFeedback(result.agent_run_id, result.feedback_token, feedbackRating, feedbackComment, `feedback-${result.ticket_id}-${randomPart}`);
      setFeedbackState('submitted');
    } catch (requestError) {
      setFeedbackError(friendlyError(requestError) || '提交评价失败，请稍后重试。');
      setFeedbackState('idle');
    }
  }
  function startNewConversation(nextCustomerId = customerId) {
    const nextSessionId = createSessionId();
    localStorage.setItem(`supportgpt:session:${nextCustomerId}`, nextSessionId);
    rememberSession(nextCustomerId, nextSessionId);
    setCustomerId(nextCustomerId);
    setSessionId(nextSessionId);
    setConversation(current => [...current, {
      id: `new-session-${nextSessionId}`,
      role: 'status',
      content: '已开始新对话。最近七天的历史记录仍保留在当前窗口中。',
      createdAt: new Date().toISOString()
    }]);
    setMessage('');
    setResult(null);
    setError('');
    resetFeedback();
  }
  function switchCustomer(nextCustomerId) {
    setCustomerId(nextCustomerId);
    setSessionId(sessionForCustomer(nextCustomerId));
    setConversation([WELCOME_MESSAGE]);
    setMessage('');
    setResult(null);
    setError('');
    resetFeedback();
  }
  return <main className="customer-portal" id="main-content" tabIndex={-1}>
      <header className="customer-header">
        <div className="customer-brand">
          <span><Sparkles size={20} /></span>
          <div><strong>SupportGPT</strong><small>智能客户服务</small></div>
        </div>
        <div className="customer-header-actions">
        {onWorkflowEntry && <button type="button" className="staff-entry" onClick={onWorkflowEntry}><GitBranch size={16} /> 项目架构</button>}
        {onStaffEntry && <button type="button" className="staff-entry" onClick={onStaffEntry}>
            <Headphones size={16} /> 客服员工入口 <ArrowRight size={15} />
          </button>}
        </div>
      </header>

      <section className="customer-hero">
        <div className="customer-hero-copy">
          <span className="customer-eyebrow"><ShieldCheck size={15} /> 智能客服演示</span>
          <h1>您好，需要什么帮助？</h1>
          <p>我可以协助查询订单、退款、账户和售后问题。需要人工确认时，页面会说明原因与处理状态。</p>
          <div className="customer-capabilities">
            <span><CheckCircle2 size={15} /> 订单与物流</span>
            <span><CheckCircle2 size={15} /> 退款与售后</span>
            <span><CheckCircle2 size={15} /> 账户与技术支持</span>
          </div>
        </div>

        <div className="support-card">
          <div className="support-chat-toolbar">
            <div className="support-form-heading">
              <span className="support-bot"><Bot size={21} /></span>
              <div><strong>SupportGPT 智能客服</strong><small>{historyLoading ? '正在加载最近七天对话' : historyError ? '历史对话暂不可用' : '显示最近七天的对话'}</small></div>
            </div>
            <button type="button" className="support-new-session" onClick={() => startNewConversation()} disabled={submitting || historyLoading}>
              <RefreshCw size={14} /> 新对话
            </button>
          </div>

          <label className="customer-selector support-customer-selector">
            <span>Demo · 演示客户</span>
            <select value={customerId} onChange={event => switchCustomer(event.target.value)} disabled={submitting || historyLoading}>
              <option value="cust_101">简·多伊（VIP 客户）</option>
              <option value="cust_102">约翰·史密斯（标准客户）</option>
              <option value="cust_103">艾克米公司（企业客户）</option>
              <option value="cust_201">张晓雨（标准客户 · 物流延迟）</option>
              <option value="cust_202">李明（VIP 客户 · 设备保修）</option>
              <option value="cust_203">星河科技 / 陈晨（企业客户 · API 与发票）</option>
            </select>
          </label>

          {DEMO_CUSTOMER_HINTS[customerId] && <p className="support-privacy">{DEMO_CUSTOMER_HINTS[customerId]}以上客户和记录均为虚构演示数据。</p>}

          <div className="support-conversation" ref={conversationRef} role="log" aria-label="当前对话记录" aria-live="polite" aria-relevant="additions" aria-busy={historyLoading}>
            {historyLoading ? <div className="support-history-loading"><RefreshCw className="spin" size={16} /> 正在加载最近 7 天的对话记录……</div> : conversation.map((item, index) => {
            const day = historyDay(item.createdAt);
            const previousDay = index > 0 ? historyDay(conversation[index - 1].createdAt) : '';
            return <React.Fragment key={item.id || `${item.role}-${item.ticketId || 'local'}-${index}`}>
                  {day && day !== previousDay && <div className="support-history-day"><span>{day}</span></div>}
                  <div className={`support-message ${item.role}`}>
                    <span>{item.role === 'user' ? '您' : item.role === 'assistant' ? 'AI' : item.role === 'error' ? '异常' : '状态'}</span>
                    <div>
                      <p>{item.content}</p>
                      {(item.ticketId || item.createdAt) && <small>{item.ticketId ? `工单 #${item.ticketId}` : ''}{item.ticketId && item.createdAt ? ' · ' : ''}{historyTime(item.createdAt)}</small>}
                    </div>
                  </div>
                </React.Fragment>;
          })}
            {submitting && <div className="support-message status support-typing">
                <span>AI</span><p><RefreshCw className="spin" size={13} /> 正在处理您的问题……</p>
              </div>}
          </div>

          {historyError && <div className="support-history-error" role="alert">
              <AlertTriangle size={16} />
              <span>{historyError}</span>
              <button type="button" onClick={() => loadHistory(customerId)} disabled={submitting || historyLoading}>重试</button>
            </div>}

          {result?.status === 'pending_human' && <div className={`support-status-panel ${result.handling_reason || 'manual_review'}`} role="status">
              {result.handling_reason === 'processing_exception' ? <AlertTriangle size={18} /> : <Clock3 size={18} />}
              <div>
                <strong>{result.handling_reason === 'risk_review' ? '需要人工确认请求是否安全' : result.handling_reason === 'processing_exception' ? '处理过程中出现了异常' : result.handling_reason === 'quality_review' ? '需要人工检查回复是否可靠' : '已转交人工客服'}</strong>
                <span>{result.message}</span>
              </div>
            </div>}

          {result?.status === 'answered' && result.agent_run_id && result.feedback_token && (feedbackState === 'submitted' ? <div className="feedback-success" role="status"><CheckCircle2 size={17} /> 感谢您的评价，将用于改进服务质量。</div> : <form className="support-feedback support-feedback-compact" onSubmit={handleFeedback}>
                <strong>这次回答对您有帮助吗？</strong>
                <div className="feedback-rating" aria-label="回答评分">
                  {[1, 2, 3, 4, 5].map(rating => <button type="button" key={rating} className={feedbackRating >= rating ? 'active' : ''} onClick={() => setFeedbackRating(rating)} aria-label={`${rating} 分`} aria-pressed={feedbackRating === rating} disabled={feedbackState === 'submitting'}>
                      <Star size={18} fill={feedbackRating >= rating ? 'currentColor' : 'none'} />
                    </button>)}
                </div>
                <textarea aria-label="评价补充说明（可选）" value={feedbackComment} onChange={event => setFeedbackComment(event.target.value)} placeholder="可选：告诉我们哪里做得好或需要改进" maxLength={2000} disabled={feedbackState === 'submitting'} />
                {feedbackError && <span className="feedback-error" role="alert">{feedbackError}</span>}
                <button type="submit" className="support-secondary" disabled={!feedbackRating || feedbackState === 'submitting'}>
                  {feedbackState === 'submitting' ? '提交中……' : '提交评价'}
                </button>
              </form>)}

          <form onSubmit={handleSubmit} className="support-form support-composer">
            {conversation.length === 1 && <div className="question-examples">
                <span>您可以这样问</span>
                <div>{EXAMPLE_QUESTIONS.map(question => <button type="button" key={question} onClick={() => setMessage(question)} disabled={submitting || historyLoading}>{question}</button>)}</div>
              </div>}

            <label className="support-message-field">
              <span>输入您的问题</span>
              <textarea value={message} onChange={event => setMessage(event.target.value)} placeholder="继续描述问题或补充订单号等信息……" maxLength={5000} disabled={submitting || historyLoading} required />
              <small>{message.length} / 5000</small>
            </label>

            {error && <div className="support-error" role="alert">{error}</div>}

            <button className="support-submit" type="submit" disabled={submitting || historyLoading || !message.trim()}>
              {submitting ? <><RefreshCw className="spin" size={17} /> 正在处理…</> : <><Send size={17} /> 发送问题</>}
            </button>
            <p className="support-privacy"><ShieldCheck size={13} /> 系统会隐藏识别出的敏感信息。请勿发送密码、验证码或密钥；需要人工处理时，页面会说明情况。</p>
          </form>
        </div>
      </section>

      <footer className="customer-footer">SupportGPT 企业智能客服 · AI 回复可能需要人工复核</footer>
    </main>;
}
