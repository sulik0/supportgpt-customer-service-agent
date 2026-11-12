import React, { useEffect, useId, useRef, useState } from "react";
import { fetchPublicRunInsight } from "../api/client";
import { createLatestRequest } from "../latestRequest";
import { friendlyError } from "../errors";
import { formatToolStatus } from "../formatters";
import {
  translateDepartment,
  translateEscalationReason,
  translatePriority,
  translateRisk,
} from "../i18n";
import { buildInsightSteps } from "../publicRunInsight";
import { INTENT_LABELS, NODE_LABELS } from "../workflowMetadata";
import { formatLatency } from "./runMetrics";
import { Badge, Button, Card, LoadingState, NavLink, Notice } from "./ui";

const STEP_STATES = {
  recorded: "已记录",
  skipped: "本次跳过",
  unrecorded: "未单独记录",
  group: "并行分支",
  summary: "结论摘要",
};

function StepFacts({ step, insight }) {
  if (step.state === "skipped" || step.state === "unrecorded") return null;
  if (step.id === "analyzer")
    return (
      <dl className="support-insight-facts">
        <div>
          <dt>意图</dt>
          <dd>{INTENT_LABELS[insight.intent] || "未记录"}</dd>
        </div>
        <div>
          <dt>置信度</dt>
          <dd>
            {insight.analyzer_confidence == null
              ? "未记录"
              : `${(insight.analyzer_confidence * 100).toFixed(0)}%`}
          </dd>
        </div>
        <div>
          <dt>部门</dt>
          <dd>
            {insight.department
              ? translateDepartment(insight.department)
              : "未记录"}
          </dd>
        </div>
        <div>
          <dt>优先级</dt>
          <dd>
            {insight.priority ? translatePriority(insight.priority) : "未记录"}
          </dd>
        </div>
      </dl>
    );
  if (step.id === "skill_selector")
    return (
      <span>
        {insight.skill_selection
          ? `${insight.skill_selection.skill_name} / ${insight.skill_selection.skill_version}`
          : "未记录 Skill"}
      </span>
    );
  if (step.id === "tooling")
    return insight.tool_calls.length ? (
      <ul className="support-insight-tools">
        {insight.tool_calls.map((call, index) => (
          <li key={index}>
            <code>{call.tool_name}</code>
            <span>{formatToolStatus(call.status)}</span>
            <span>
              {call.latency_ms == null ? "耗时未记录" : `${call.latency_ms} ms`}
            </span>
          </li>
        ))}
      </ul>
    ) : (
      <span>无工具调用记录</span>
    );
  if (step.id === "retriever")
    return insight.citations.length ? (
      <ul>
        {insight.citations.map((citation, index) => (
          <li key={index}>{citation.source}</li>
        ))}
      </ul>
    ) : (
      <span>无知识引用记录</span>
    );
  if (step.id === "qa")
    return (
      <span>
        QA：{insight.qa_score == null ? "未记录" : insight.qa_score.toFixed(2)}
      </span>
    );
  if (step.id === "escalation")
    return (
      <>
        <Badge
          tone={
            ["high", "critical"].includes(insight.risk_level)
              ? "warning"
              : "neutral"
          }
        >
          风险：{translateRisk(insight.risk_level)}
        </Badge>
        {insight.review_reasons.length > 0 && (
          <ul>
            {insight.review_reasons.map((reason) => (
              <li key={reason}>{translateEscalationReason(reason)}</li>
            ))}
          </ul>
        )}
      </>
    );
  if (step.id === "approval_gate")
    return (
      <span>
        {insight.approval_required ? "当次需人工审批" : "当次无需审批"}
      </span>
    );
  return null;
}

export default function SupportRunInsight({ agentRunId, feedbackToken }) {
  const [open, setOpen] = useState(false);
  const [insight, setInsight] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requests = useRef(createLatestRequest());
  const panelId = useId();
  useEffect(() => () => requests.current.cancel(), []);

  async function load() {
    const request = requests.current.start();
    setLoading(true);
    setError("");
    try {
      const value = await fetchPublicRunInsight(
        agentRunId,
        feedbackToken,
        request.signal,
      );
      if (request.isCurrent()) setInsight(value);
    } catch (err) {
      if (request.isCurrent() && err.name !== "AbortError")
        setError(friendlyError(err));
    } finally {
      if (request.isCurrent()) setLoading(false);
    }
  }
  function toggle() {
    if (open) {
      requests.current.cancel();
      setLoading(false);
    } else if (!insight) load();
    setOpen(!open);
  }

  return (
    <div className="support-run-insight">
      <Button
        tone="quiet"
        size="sm"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={toggle}
      >
        {open ? "收起处理过程" : "查看处理过程"}
      </Button>
      {open && (
        <Card
          id={panelId}
          title="Agent 处理过程"
          className="support-insight-panel"
          aria-label="Agent 处理过程"
        >
          {loading ? (
            <LoadingState>正在读取处理过程…</LoadingState>
          ) : error ? (
            <>
              <Notice>{error}</Notice>
              <Button onClick={load}>重试</Button>
            </>
          ) : (
            insight && (
              <>
                <p className="support-insight-helper">
                  已保存的路径与审批摘要。
                </p>
                <ol className="support-insight-steps">
                  {buildInsightSteps(insight.workflow_path).map((step) => (
                    <li
                      key={step.id}
                      className={`insight-${step.state} ${step.branch ? "insight-branch" : ""}`}
                    >
                      <div className="support-insight-step-heading">
                        <strong>{NODE_LABELS[step.id]}</strong>
                        <Badge>{STEP_STATES[step.state]}</Badge>
                      </div>
                      <StepFacts step={step} insight={insight} />
                    </li>
                  ))}
                </ol>
                <footer className="support-insight-footer">
                  <span>总耗时 {formatLatency(insight.latency_seconds)}</span>
                  <span>Token {insight.tokens_total ?? "未记录"}</span>
                  <NavLink href="#workflow">在流程图中查看</NavLink>
                </footer>
              </>
            )
          )}
        </Card>
      )}
    </div>
  );
}
