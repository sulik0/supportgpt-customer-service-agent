import "../styles/pages/workspace.css";
import { friendlyError } from "../errors";
import { formatDate } from "../formatters";
import React, { useEffect, useState } from "react";
import { fetchToolReviews, resolveToolAction } from "../api/client";
import { Badge, Button, Card, Skeleton } from "./ui";
import { useConfirm } from "./uiHooks";
const REASONS = {
  unknown: "业务结果未知",
  dead_letter: "自动处理失败，待人工核实",
  compensation_unknown: "补偿结果未知",
};
const OUTCOMES = {
  succeeded: "申请提交成功",
  failed: "申请失败",
  compensated: "补偿成功",
  compensation_failed: "补偿失败",
};
const STATES = {
  queued: "等待执行",
  executing: "正在执行",
  unknown: "执行结果未知",
  reconciling: "正在对账",
  succeeded: "申请已提交",
  failed: "执行失败",
  compensation_pending: "等待补偿",
  compensating: "正在补偿",
  compensation_unknown: "补偿结果未知",
  compensated: "补偿成功",
  compensation_failed: "补偿失败",
};
function ReviewItem({ review, canConfirm, onComplete }) {
  const compensation = [
    "compensation_unknown",
    "compensating",
    "compensation_pending",
  ].includes(review.action.status);
  const outcomes = compensation
    ? review.action.status === "compensation_pending"
      ? ["compensation_failed"]
      : ["compensated", "compensation_failed"]
    : review.action.status === "queued"
      ? ["failed"]
      : ["succeeded", "failed"];
  const [outcome, setOutcome] = useState(outcomes[0]);
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { ask, confirmation } = useConfirm();
  async function submit(event) {
    event.preventDefault();
    if (
      !(await ask({
        title: "保存业务核实结果？",
        description:
          "请确认已在业务系统中核实结果。这一步只记录确认结果，不会再次发起退款。",
        confirmLabel: "保存核实结果",
      }))
    )
      return;
    setBusy(true);
    setError("");
    try {
      await resolveToolAction(review.tool_action_id, {
        expected_version: review.action.version,
        outcome,
        evidence_reference: reference.trim(),
        note: note.trim(),
      });
      onComplete?.();
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="tool-review-item">
      {confirmation}
      <strong>{REASONS[review.reason] || review.reason}</strong>
      <div className="workspace-tool-heading">
        <code>{review.action.tool_name}</code>
        <Badge tone="accent">
          {STATES[review.action.status] || review.action.status}
        </Badge>
      </div>
      {review.action.payload_summary != null && (
        <div className="workspace-tool-parameters">
          <span>参数摘要（脱敏）</span>
          <pre>{JSON.stringify(review.action.payload_summary, null, 2)}</pre>
        </div>
      )}
      <small className="workspace-mono">Action：{review.tool_action_id}</small>
      {review.status === "resolved" ? (
        <p>{OUTCOMES[review.outcome] || review.outcome}；已记录。</p>
      ) : canConfirm ? (
        <form onSubmit={submit} className="tool-review-form">
          <label>
            核实结果
            <select
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
            >
              {outcomes.map((value) => (
                <option key={value} value={value}>
                  {OUTCOMES[value]}
                </option>
              ))}
            </select>
          </label>
          <label>
            外部凭证编号
            <input
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              required
              minLength={3}
              maxLength={500}
              placeholder="外部核实凭证编号"
            />
          </label>
          <label>
            核实说明
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              required
              minLength={5}
              maxLength={1000}
              placeholder="核实渠道、时间、结果；勿填密钥或客户敏感信息"
            />
          </label>
          <Button
            type="submit"
            tone="primary"
            size="lg"
            disabled={
              busy || reference.trim().length < 3 || note.trim().length < 5
            }
          >
            {busy ? "正在保存…" : "保存结果"}
          </Button>
          {error && (
            <p role="alert" className="tool-review-error">
              {error}
            </p>
          )}
        </form>
      ) : (
        <p>仅主管或管理员可确认，普通客服无权修改业务结果。</p>
      )}
      {review.action.events?.length > 0 && (
        <div className="workspace-tool-trace">
          <h4>操作轨迹</h4>
          <ol>
            {review.action.events.map((event) => (
              <li key={event.id}>
                <span className="workspace-trace-dot" aria-hidden="true" />
                <div>
                  <strong>{STATES[event.to_status] || event.to_status}</strong>
                  {event.from_status && (
                    <span>
                      从{STATES[event.from_status] || event.from_status}转入
                    </span>
                  )}
                  <time dateTime={event.created_at}>
                    {formatDate(event.created_at)}
                  </time>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
export default function ToolReviewPanel({
  ticketId,
  userRole,
  onActionComplete,
}) {
  const [reviews, setReviews] = useState([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setReviews([]);
    setError("");
    setLoading(true);
    fetchToolReviews(ticketId, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setReviews(value);
      })
      .catch((err) => {
        if (err.name !== "AbortError") setError(friendlyError(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [ticketId, revision]);
  if (loading)
    return (
      <Card className="tool-review-panel">
        <div className="workspace-skeleton" role="status">
          <span>正在查询核实任务…</span>
          <Skeleton height={20} />
          <Skeleton height={80} />
          <Skeleton width="65%" />
        </div>
      </Card>
    );
  if (!reviews.length && !error) return null;
  return (
    <Card className="tool-review-panel" title="业务操作待核实">
      <p>核实独立于回复审批，结果未知不能判失败，也不能重复提交退款。</p>
      {error && (
        <p role="alert">
          {error}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setRevision((value) => value + 1)}
          >
            重试
          </button>
        </p>
      )}
      {reviews.map((review) => (
        <ReviewItem
          key={`${review.tool_action_id}:${review.action.version}`}
          review={review}
          canConfirm={["manager", "admin"].includes(userRole)}
          onComplete={() => {
            setRevision((value) => value + 1);
            onActionComplete?.();
          }}
        />
      ))}
    </Card>
  );
}
