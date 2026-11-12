import "../styles/pages/observability.css";
import { friendlyError } from "../errors";
import { formatDate, formatToolStatus } from "../formatters";
import { NODE_LABELS, INTENT_LABELS } from "../workflowMetadata";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Bot,
  Check,
  Copy,
  ExternalLink,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import { fetchAgentRun, fetchAgentRuns } from "../api/client";
import { translatePriority, translateSentiment } from "../i18n";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  MetricCard,
  Notice,
  Overlay,
  Skeleton,
} from "./ui";
import { updateRouteParams } from "../navigation";
import { formatLatency, summarizeRuns, totalTokens } from "./runMetrics";
const PAGE_SIZE = 20;
const LANGSMITH_PROJECT_URL =
  import.meta.env.VITE_LANGSMITH_PROJECT_URL?.trim();
const STRATEGY_LABELS = {
  rule: "规则",
  jev: "Jev",
  llm: "大模型",
  not_run: "未执行",
  unknown: "未知",
};
const DEPARTMENT_LABELS = {
  billing: "账务",
  technical: "技术支持",
  shipping: "订单与物流",
  general: "综合支持",
};
function StrategyBadge({ value }) {
  const normalized = value || "not_run";
  return (
    <Badge className="obs-strategy">
      {STRATEGY_LABELS[normalized] || normalized}
    </Badge>
  );
}
function runStatus(run) {
  if (run.workflow_errors?.length)
    return {
      label: "异常",
      className: "status-error",
    };
  if (run.approval_required || run.escalation_recommended) {
    return {
      label: "本次需人工",
      className: "status-review",
    };
  }
  return {
    label: "已完成",
    className: "status-success",
  };
}
function RunSkeleton({ label }) {
  return (
    <div className="obs-skeleton" role="status">
      <span className="sr-only">{label}</span>
      {[0, 1, 2].map((index) => (
        <div className="obs-skeleton-row" key={index}>
          <Skeleton width="35%" />
          <Skeleton height={24} />
          <Skeleton width="70%" />
        </div>
      ))}
    </div>
  );
}
function RunDetail({
  run,
  loading,
  error,
  onRetry,
  onClose,
  onCopyTrace,
  copiedTrace,
}) {
  if (!run && !loading) return null;
  const snapshot = run?.execution_snapshot;
  const analyzer = snapshot?.analyzer_result || {};
  return (
    <Overlay
      title="Agent 运行详情"
      onClose={onClose}
      className="obs-detail-drawer"
      drawer
    >
      <div className="obs-detail-header">
        <div>
          <span className="obs-eyebrow">AgentRun（运行记录）</span>
          <h2>{run?.id || "正在加载…"}</h2>
        </div>
      </div>

      {error ? (
        <div className="ui-dialog-body">
          <Notice>{error}</Notice>
          <Button onClick={onRetry}>重新加载</Button>
        </div>
      ) : loading ? (
        <RunSkeleton label="正在加载运行详情…" />
      ) : (
        <div className="obs-detail-content">
          <Card className="obs-detail-section" title="Trace">
            <div className="obs-trace-box">
              <code>{run.trace_id || "未采集 Trace ID"}</code>
              {run.trace_id && (
                <button
                  className="obs-icon-button"
                  onClick={() => onCopyTrace(run.trace_id)}
                  title="复制 Trace ID"
                  aria-label="复制 Trace ID"
                >
                  {copiedTrace === run.trace_id ? (
                    <Check size={16} />
                  ) : (
                    <Copy size={16} />
                  )}
                </button>
              )}
            </div>
            {LANGSMITH_PROJECT_URL && (
              <>
                <a
                  className="btn btn-secondary obs-link-button"
                  href={LANGSMITH_PROJECT_URL}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开 LangSmith <ExternalLink size={15} />
                </a>
                <p className="obs-helper">复制 Trace ID 后到项目搜索。</p>
              </>
            )}
          </Card>

          <Card className="obs-detail-section" title="执行路径">
            <p className="obs-helper">
              保存的步骤顺序，不含实时进度与精确耗时。
            </p>
            {(run.workflow_path || []).length ? (
              <ol className="obs-workflow">
                {run.workflow_path.map((node, index) => (
                  <li className="obs-node" key={`${node}-${index}`}>
                    <span className="obs-node-index" aria-hidden="true">
                      {index + 1}
                    </span>
                    <div>
                      <strong>{NODE_LABELS[node] || node}</strong>
                      <code>{node}</code>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <EmptyState title="未记录执行步骤" />
            )}
          </Card>

          <Card className="obs-detail-section" title="配置与结果">
            <dl className="obs-kv-grid">
              <div>
                <dt>模型</dt>
                <dd>
                  {run.model_provider} / {run.model_name}
                </dd>
              </div>
              <div>
                <dt>提示词版本</dt>
                <dd>{run.prompt_version}</dd>
              </div>
              <div>
                <dt>流程版本</dt>
                <dd>{run.workflow_version}</dd>
              </div>
              <div>
                <dt>所用 Skill</dt>
                <dd>
                  {run.skill_selection
                    ? `${run.skill_selection.skill_name} / ${run.skill_selection.skill_version}`
                    : "-"}
                </dd>
              </div>
              <div>
                <dt>Skill 选择方式</dt>
                <dd>
                  {run.skill_selection?.selection_strategy === "intent_rule"
                    ? "意图规则"
                    : run.skill_selection?.selection_strategy || "-"}
                </dd>
              </div>
              <div>
                <dt>分析策略</dt>
                <dd>
                  {snapshot ? (
                    <StrategyBadge value={snapshot.analyzer_strategy} />
                  ) : (
                    "-"
                  )}
                </dd>
              </div>
              <div>
                <dt>QA 策略</dt>
                <dd>
                  {snapshot ? (
                    <StrategyBadge value={snapshot.qa_strategy} />
                  ) : (
                    "-"
                  )}
                </dd>
              </div>
              <div>
                <dt>知识库</dt>
                <dd>{run.kb_version}</dd>
              </div>
              <div>
                <dt>处理耗时</dt>
                <dd>{formatLatency(run.latency_seconds, 3)}</dd>
              </div>
              <div>
                <dt>Token 用量</dt>
                <dd>{totalTokens(run) ?? "未记录"}</dd>
              </div>
              <div>
                <dt>QA 评分</dt>
                <dd>{run.qa_score == null ? "-" : run.qa_score.toFixed(2)}</dd>
              </div>
              <div>
                <dt>依据检查</dt>
                <dd>
                  {run.hallucination_detected == null
                    ? "未记录"
                    : run.hallucination_detected
                      ? "可能缺少依据"
                      : "未发现编造内容"}
                </dd>
              </div>
              <div>
                <dt>审批要求</dt>
                <dd>{run.approval_required ? "需要" : "不需要"}</dd>
              </div>
            </dl>

            {snapshot ? (
              <div className="obs-analyzer-snapshot">
                <div className="obs-snapshot-heading">
                  <div>
                    <strong>Analyzer（问题分析）</strong>
                  </div>
                  <StrategyBadge value={snapshot.analyzer_strategy} />
                </div>
                <dl className="obs-analyzer-grid">
                  <div>
                    <dt>意图</dt>
                    <dd>
                      {INTENT_LABELS[analyzer.intent] || analyzer.intent || "-"}
                    </dd>
                  </div>
                  <div>
                    <dt>部门</dt>
                    <dd>
                      {DEPARTMENT_LABELS[analyzer.department] ||
                        analyzer.department ||
                        "-"}
                    </dd>
                  </div>
                  <div>
                    <dt>优先级</dt>
                    <dd>{translatePriority(analyzer.priority)}</dd>
                  </div>
                  <div>
                    <dt>情绪</dt>
                    <dd>{translateSentiment(analyzer.sentiment)}</dd>
                  </div>
                  <div>
                    <dt>置信度</dt>
                    <dd>
                      {typeof analyzer.confidence === "number"
                        ? analyzer.confidence.toFixed(2)
                        : "-"}
                    </dd>
                  </div>
                </dl>
              </div>
            ) : (
              <p className="obs-snapshot-unavailable">
                旧记录未保存分析与 QA 策略。
              </p>
            )}
          </Card>

          <Card className="obs-detail-section" title="工具与引用">
            <div className="obs-compact-list">
              {(run.tool_calls || []).length ? (
                run.tool_calls.map((tool, index) => (
                  <div key={`${tool.tool_name}-${index}`}>
                    <Bot size={15} />
                    <code>{tool.tool_name || "未命名工具"}</code>
                    <em>{formatToolStatus(tool.status)}</em>
                  </div>
                ))
              ) : (
                <EmptyState className="obs-tools-empty" title="暂无工具调用" />
              )}
              {(run.citations || []).map((citation, index) => (
                <div key={`${citation.source}-${index}`}>
                  <Sparkles size={15} />
                  <span>{citation.source || "未命名知识来源"}</span>
                  <em>知识引用</em>
                </div>
              ))}
            </div>
          </Card>

          {(run.workflow_errors || []).length > 0 && (
            <Notice>
              <strong>执行异常</strong>
              {run.workflow_errors.map((error, index) => (
                <p key={index}>{error}</p>
              ))}
            </Notice>
          )}

          <Card className="obs-detail-section" title="脱敏输入与回复">
            <div className="obs-text-snapshot">
              <strong>用户输入</strong>
              <pre>{run.input_text}</pre>
            </div>
            <div className="obs-text-snapshot">
              <strong>Agent 回复</strong>
              <pre>{run.output_text}</pre>
            </div>
          </Card>
        </div>
      )}
    </Overlay>
  );
}
export default function ObservabilityPage({ route }) {
  const [page, setPage] = useState({
    items: [],
    total: 0,
    limit: PAGE_SIZE,
    offset: 0,
  });
  const offset = Math.floor((route?.offset || 0) / PAGE_SIZE) * PAGE_SIZE;
  const ticketFilter = route?.ticket || null;
  const [ticketQuery, setTicketQuery] = useState(
    ticketFilter ? String(ticketFilter) : "",
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedRun, setSelectedRun] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [copiedTrace, setCopiedTrace] = useState("");
  const [detailError, setDetailError] = useState("");
  const listController = useRef(null);
  const detailController = useRef(null);
  useEffect(() => {
    setTicketQuery(ticketFilter ? String(ticketFilter) : "");
  }, [ticketFilter]);
  const loadRuns = useCallback(async () => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    setLoading(true);
    setError("");
    try {
      const nextPage = await fetchAgentRuns(
        PAGE_SIZE,
        offset,
        ticketFilter,
        controller.signal,
      );
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
    setDetailError("");
    setSelectedRun({
      id: runId,
    });
    try {
      const nextRun = await fetchAgentRun(runId, controller.signal);
      if (!controller.signal.aborted) setSelectedRun(nextRun);
    } catch (requestError) {
      if (!controller.signal.aborted)
        setDetailError(friendlyError(requestError));
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  }
  async function copyTrace(traceId) {
    try {
      await navigator.clipboard.writeText(traceId);
      setCopiedTrace(traceId);
      window.setTimeout(() => setCopiedTrace(""), 1600);
    } catch {
      setError("复制失败，请在详情中手动选择 Trace ID。");
    }
  }
  function handleTicketSearch(event) {
    event.preventDefault();
    const normalized = ticketQuery.trim().replace(/^#/, "");
    const nextTicketId = Number(normalized);
    if (
      !/^\d+$/.test(normalized) ||
      !Number.isSafeInteger(nextTicketId) ||
      nextTicketId < 1
    ) {
      setError("请输入大于 0 的工单编号，例如 123。");
      return;
    }
    setError("");
    if (ticketFilter === nextTicketId && offset === 0) loadRuns();
    else
      updateRouteParams(
        {
          ticket: nextTicketId,
          offset: 0,
          run: null,
        },
        false,
      );
  }
  function clearTicketSearch() {
    setTicketQuery("");
    setError("");
    if (ticketFilter == null && offset === 0) loadRuns();
    else
      updateRouteParams(
        {
          ticket: null,
          offset: null,
          run: null,
        },
        false,
      );
  }
  const canGoNext = offset + PAGE_SIZE < page.total;
  useEffect(() => {
    if (route?.run) openRun(route.run);
    else {
      detailController.current?.abort();
      setSelectedRun(null);
      setDetailLoading(false);
    }
    return () => detailController.current?.abort();
  }, [route?.run]);
  return (
    <section className="observability-page">
      <div className="obs-hero">
        <div className="obs-hero-actions">
          <button
            className="btn btn-secondary"
            onClick={loadRuns}
            disabled={loading}
          >
            <RefreshCw size={15} className={loading ? "spin" : ""} /> 刷新
          </button>
          {LANGSMITH_PROJECT_URL && (
            <a
              className="btn btn-secondary"
              href={LANGSMITH_PROJECT_URL}
              target="_blank"
              rel="noreferrer"
            >
              打开 LangSmith <ExternalLink size={15} />
            </a>
          )}
        </div>
      </div>

      <div className="obs-summary-grid">
        <MetricCard
          label="运行记录数"
          value={
            loading ? (
              <Skeleton height={35} width="70%" />
            ) : error ? (
              "—"
            ) : (
              page.total
            )
          }
          hint="当前查询条件"
        />
        <MetricCard
          label="本页平均耗时"
          value={
            loading ? (
              <Skeleton height={35} width="70%" />
            ) : error ? (
              "—"
            ) : (
              formatLatency(summary.averageLatency)
            )
          }
          hint={
            loading || error
              ? "仅已记录耗时"
              : `本页 ${summary.latencyCount} 条耗时`
          }
        />
        <MetricCard
          label="本页 Token 用量"
          value={
            loading ? (
              <Skeleton height={35} width="70%" />
            ) : error ? (
              "—"
            ) : (
              (summary.totalTokens?.toLocaleString() ?? "—")
            )
          }
          hint="已记录输入输出"
        />
        <MetricCard
          label="本页建议人工处理"
          value={
            loading ? (
              <Skeleton height={35} width="70%" />
            ) : error ? (
              "—"
            ) : (
              summary.reviewCount
            )
          }
          hint="当次建议，非当前状态"
        />
      </div>

      <div className="obs-runs-card">
        <div className="obs-table-heading">
          <div>
            <h3>Agent 运行记录</h3>
            <p>
              {ticketFilter ? `工单 #${ticketFilter}` : "仅主管和管理员可查看"}
            </p>
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
              <button
                type="submit"
                className="btn btn-secondary"
                disabled={loading}
              >
                查询
              </button>
              {ticketFilter && (
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={clearTicketSearch}
                >
                  清除
                </button>
              )}
            </form>
            <span>
              {page.total ? offset + 1 : 0}–
              {Math.min(offset + PAGE_SIZE, page.total)} / {page.total}
            </span>
          </div>
        </div>

        {error && (
          <div className="obs-list-error">
            <Notice>
              <span>{error}</span>
              <Button onClick={loadRuns}>重试</Button>
            </Notice>
          </div>
        )}
        {loading ? (
          <RunSkeleton label="正在加载运行记录…" />
        ) : error ? null : page.items.length === 0 ? (
          <EmptyState
            title="暂无运行记录"
            description={
              ticketFilter
                ? `工单 #${ticketFilter} 还没有运行记录。`
                : "请先提交咨询或创建工单。"
            }
          />
        ) : (
          <div
            className="obs-table-wrap"
            tabIndex={0}
            role="region"
            aria-label="Agent 运行记录，可横向滚动"
          >
            <table className="obs-table">
              <thead>
                <tr>
                  <th scope="col">北京时间（UTC+8）</th>
                  <th scope="col" className="obs-number">
                    工单
                  </th>
                  <th scope="col">运行编号</th>
                  <th scope="col" className="obs-number">
                    执行步骤
                  </th>
                  <th scope="col" className="obs-number">
                    回复质量
                  </th>
                  <th scope="col" className="obs-number">
                    耗时
                  </th>
                  <th scope="col" className="obs-number">
                    Token
                  </th>
                  <th scope="col">本次结果</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((run) => {
                  const status = runStatus(run);
                  return (
                    <tr
                      key={run.id}
                      className={route?.run === run.id ? "is-selected" : ""}
                      onClick={() =>
                        updateRouteParams(
                          {
                            run: run.id,
                          },
                          false,
                        )
                      }
                      tabIndex={0}
                      aria-label={`查看运行 ${run.id}`}
                      onKeyDown={(event) => {
                        if (["Enter", " "].includes(event.key)) {
                          event.preventDefault();
                          updateRouteParams(
                            {
                              run: run.id,
                            },
                            false,
                          );
                        }
                      }}
                    >
                      <td>{formatDate(run.created_at)}</td>
                      <td className="obs-number obs-mono">
                        {run.ticket_id ? (
                          <strong>#{run.ticket_id}</strong>
                        ) : (
                          <span>-</span>
                        )}
                      </td>
                      <td className="obs-mono">
                        <code>{run.id.slice(0, 8)}</code>
                      </td>
                      <td className="obs-number">
                        <strong>{run.workflow_path?.length || 0} 个步骤</strong>
                      </td>
                      <td className="obs-number">
                        <strong className="obs-mono">
                          {run.qa_score == null ? "-" : run.qa_score.toFixed(2)}
                        </strong>
                      </td>
                      <td className="obs-number obs-mono">
                        {formatLatency(run.latency_seconds)}
                      </td>
                      <td className="obs-number obs-mono">
                        {totalTokens(run)?.toLocaleString() ?? "未记录"}
                      </td>
                      <td>
                        <Badge
                          tone={
                            status.className === "status-error"
                              ? "danger"
                              : status.className === "status-review"
                                ? "accent"
                                : "success"
                          }
                        >
                          {status.label}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="obs-pagination">
          <button
            className="btn btn-secondary"
            disabled={offset === 0 || loading}
            onClick={() =>
              updateRouteParams(
                {
                  offset: Math.max(0, offset - PAGE_SIZE),
                },
                false,
              )
            }
          >
            上一页
          </button>
          <button
            className="btn btn-secondary"
            disabled={!canGoNext || loading}
            onClick={() =>
              updateRouteParams(
                {
                  offset: offset + PAGE_SIZE,
                },
                false,
              )
            }
          >
            下一页
          </button>
        </div>
      </div>

      <RunDetail
        run={selectedRun}
        loading={detailLoading}
        error={detailError}
        onRetry={() => openRun(route.run)}
        onClose={() =>
          updateRouteParams({
            run: null,
          })
        }
        onCopyTrace={copyTrace}
        copiedTrace={copiedTrace}
      />
    </section>
  );
}
