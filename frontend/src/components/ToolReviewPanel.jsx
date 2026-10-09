import React, { useEffect, useState } from 'react';
import { fetchToolReviews, resolveToolAction } from '../api/client';

const REASONS = { unknown: '业务系统是否执行成功尚未确定', dead_letter: '自动处理多次失败，需要人工核实', compensation_unknown: '补偿操作是否成功尚未确定' };
const OUTCOMES = { succeeded: '已确认申请提交成功', failed: '已确认申请失败', compensated: '已确认补偿成功', compensation_failed: '已确认补偿失败' };
const STATES = { queued: '等待执行', executing: '正在执行', unknown: '执行结果不确定', reconciling: '正在对账', succeeded: '申请已提交', failed: '执行失败', compensation_pending: '等待补偿', compensating: '正在补偿', compensation_unknown: '补偿结果不确定', compensated: '补偿成功', compensation_failed: '补偿失败' };

function ReviewItem({ review, canConfirm, onComplete }) {
  const compensation = ['compensation_unknown', 'compensating', 'compensation_pending'].includes(review.action.status);
  const outcomes = compensation ? review.action.status === 'compensation_pending' ? ['compensation_failed'] : ['compensated', 'compensation_failed'] : review.action.status === 'queued' ? ['failed'] : ['succeeded', 'failed'];
  const [outcome, setOutcome] = useState(outcomes[0]);
  const [reference, setReference] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    if (!window.confirm('请确认已在业务系统中核实结果。这一步只记录确认结果，不会再次发起退款。')) return;
    setBusy(true);
    setError('');
    try {
      await resolveToolAction(review.tool_action_id, { expected_version: review.action.version, outcome, evidence_reference: reference.trim(), note: note.trim() });
      onComplete?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return <div className="tool-review-item">
    <strong>{REASONS[review.reason] || review.reason}</strong>
    <p>操作：{review.action.tool_name} · 状态：{STATES[review.action.status] || review.action.status}</p>
    <small>Action：{review.tool_action_id}</small>
    {review.status === 'resolved' ? <p>{OUTCOMES[review.outcome] || review.outcome}；确认记录已保存。</p> : canConfirm ? <form onSubmit={submit} className="tool-review-form">
      <label>核实结果<select value={outcome} onChange={(event) => setOutcome(event.target.value)}>{outcomes.map((value) => <option key={value} value={value}>{OUTCOMES[value]}</option>)}</select></label>
      <label>外部凭证编号<input value={reference} onChange={(event) => setReference(event.target.value)} required minLength={3} maxLength={500} placeholder="业务系统的处理记录或核实凭证编号" /></label>
      <label>核实说明<textarea value={note} onChange={(event) => setNote(event.target.value)} required minLength={5} maxLength={1000} placeholder="说明核实渠道、时间和实际结果；请勿填写密钥或客户敏感信息" /></label>
      <button type="submit" className="btn btn-primary" disabled={busy || reference.trim().length < 3 || note.trim().length < 5}>{busy ? '正在保存…' : '保存已核实结果'}</button>
      {error && <p role="alert" className="tool-review-error">{error}</p>}
    </form> : <p>请主管或管理员核实并确认。普通客服不能修改业务操作结果。</p>}
  </div>;
}

export default function ToolReviewPanel({ ticketId, userRole, onActionComplete }) {
  const [reviews, setReviews] = useState([]);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setReviews([]);
    setError('');
    fetchToolReviews(ticketId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setReviews(value);
    }).catch((err) => { if (err.name !== 'AbortError') setError(err.message); });
    return () => controller.abort();
  }, [ticketId, revision]);
  if (!reviews.length && !error) return null;
  return <article className="detail-card tool-review-panel">
    <h3>业务操作待核实</h3>
    <p>这里确认退款等操作的实际结果，与下方 AI 回复审批分开。查询不到结果不能直接认定为失败，也不能重复提交退款。</p>
    {error && <p role="alert">{error}<button type="button" className="btn btn-secondary" onClick={() => setRevision((value) => value + 1)}>重试</button></p>}
    {reviews.map((review) => <ReviewItem key={`${review.tool_action_id}:${review.action.version}`} review={review} canConfirm={['manager', 'admin'].includes(userRole)} onComplete={() => { setRevision((value) => value + 1); onActionComplete?.(); }} />)}
  </article>;
}
