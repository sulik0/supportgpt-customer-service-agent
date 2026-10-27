"""Application metrics backed exclusively by the OpenTelemetry Metrics API."""

from opentelemetry import metrics

meter = metrics.get_meter("supportgpt.observability.metrics", "1.0.0")

# Counter names omit ``_total`` because the Collector Prometheus exporter adds
# the conventional suffix when translating OpenTelemetry monotonic sums.
HTTP_REQUESTS_TOTAL = meter.create_counter(
    "http_requests", description="Total HTTP requests received"
)
HTTP_REQUEST_DURATION_SECONDS = meter.create_histogram(
    "http_request_duration_seconds", unit="s", description="HTTP request duration"
)
LLM_TOKENS_TOTAL = meter.create_counter(
    "llm_tokens", unit="{token}", description="Total tokens consumed by LLM calls"
)
LLM_COST_TOTAL = meter.create_counter(
    "llm_cost", unit="USD", description="Total estimated LLM cost"
)
LLM_LATENCY_SECONDS = meter.create_histogram(
    "llm_latency_seconds", unit="s", description="LLM API latency"
)
DECISION_CALLS_TOTAL = meter.create_counter(
    "decision_calls", description="Total typed decision model calls"
)
DECISION_CALL_DURATION_SECONDS = meter.create_histogram(
    "decision_call_duration_seconds",
    unit="s",
    description="Typed decision model call duration",
)
DECISION_TOKENS_TOTAL = meter.create_counter(
    "decision_tokens", unit="{token}", description="Decision model token usage"
)
DECISION_CONFIDENCE = meter.create_histogram(
    "decision_confidence_ratio",
    description="Confidence reported by typed decision models",
)
AGENT_EXECUTION_DURATION_SECONDS = meter.create_histogram(
    "agent_execution_duration_seconds", unit="s", description="Agent execution time"
)
AGENT_EXECUTION_COUNT = meter.create_counter(
    "agent_execution", description="Total agent executions"
)
AGENT_REQUESTS_TOTAL = meter.create_counter(
    "agent_requests", description="Total end-to-end Agent workflow requests"
)
AGENT_NODE_EXECUTIONS_TOTAL = meter.create_counter(
    "agent_node_executions", description="Total Agent node executions"
)
AGENT_DECISION_STRATEGY_TOTAL = meter.create_counter(
    "agent_decision_strategy", description="Analyzer and QA strategy decisions"
)
AGENT_NODE_DURATION_SECONDS = meter.create_histogram(
    "agent_node_duration_seconds",
    unit="s",
    description="Agent node execution duration",
)
AGENT_SKILL_SELECTIONS_TOTAL = meter.create_counter(
    "agent_skill_selections", description="Total deterministic Skill selections"
)
AGENT_WORKFLOW_INTERRUPTS_TOTAL = meter.create_counter(
    "agent_workflow_interrupts", description="Total durable workflow interrupts"
)
AGENT_WORKFLOW_RESUMES_TOTAL = meter.create_counter(
    "agent_workflow_resumes", description="Total durable workflow resume attempts"
)
AGENT_WORKFLOW_RESUME_DURATION_SECONDS = meter.create_histogram(
    "agent_workflow_resume_duration_seconds",
    unit="s",
    description="Durable workflow resume duration",
)
TOOL_CALLS_TOTAL = meter.create_counter(
    "agent_tool_calls", description="Total ToolRegistry calls"
)
TOOL_CALL_DURATION_SECONDS = meter.create_histogram(
    "agent_tool_call_duration_seconds",
    unit="s",
    description="ToolRegistry call duration",
)
TOOL_ACTION_TRANSITIONS_TOTAL = meter.create_counter(
    "tool_action_transitions", description="Total governed Tool Action transitions"
)
TOOL_PLANNER_RUNS_TOTAL = meter.create_counter(
    "tool_planner_runs", description="ReAct Tool planner runs by stop reason"
)
TOOL_OUTBOX_EVENTS_TOTAL = meter.create_counter(
    "tool_outbox_events", description="Total Tool Outbox delivery outcomes"
)
TOOL_RECONCILIATIONS_TOTAL = meter.create_counter(
    "tool_reconciliations", description="Total governed Tool reconciliation outcomes"
)
HUMAN_APPROVALS_TOTAL = meter.create_counter(
    "human_approvals", description="Total human approval workflow events"
)
TICKET_SENTIMENT_TOTAL = meter.create_counter(
    "ticket_sentiment", description="Total processed tickets by sentiment"
)
TICKET_ESCALATIONS_TOTAL = meter.create_counter(
    "ticket_escalations", description="Total ticket escalations"
)
ACTIVE_SESSIONS = meter.create_up_down_counter(
    "active_sessions_count", description="Number of active user sessions in memory"
)
MEMORY_CONTEXT_LOADS_TOTAL = meter.create_counter(
    "memory_context_loads", description="Conversation memory context loads"
)
MEMORY_WRITES_TOTAL = meter.create_counter(
    "memory_writes", description="Durable conversation message writes"
)
MEMORY_CONTEXT_MESSAGES = meter.create_histogram(
    "memory_context_messages",
    unit="{message}",
    description="Messages selected for Agent context",
)
MEMORY_CONTEXT_CHARS = meter.create_histogram(
    "memory_context_chars",
    unit="{character}",
    description="Conversation context characters",
)
MEMORY_FILTERED_MESSAGES_TOTAL = meter.create_counter(
    "memory_filtered_messages",
    description="Unsafe messages excluded from Agent context",
)
QA_SCORE_HISTOGRAM = meter.create_histogram(
    "qa_score_ratio", description="Distribution of quality assurance scores"
)
GUARDRAIL_VIOLATIONS_TOTAL = meter.create_counter(
    "guardrail_violations", description="Total guardrail violations"
)
SEMANTIC_GUARD_CHECKS_TOTAL = meter.create_counter(
    "semantic_guard_checks", description="Total Qwen3Guard semantic safety checks"
)
SEMANTIC_GUARD_DURATION_SECONDS = meter.create_histogram(
    "semantic_guard_duration_seconds",
    unit="s",
    description="Qwen3Guard semantic safety check duration",
)
RISK_ASSESSMENTS_TOTAL = meter.create_counter(
    "agent_risk_assessments", description="Total final Agent risk assessments"
)
RISK_SCORE_HISTOGRAM = meter.create_histogram(
    "agent_risk_score_ratio", description="Distribution of final Agent risk scores"
)
FEEDBACK_EVENTS_TOTAL = meter.create_counter(
    "feedback_events", description="Total online feedback events"
)
TRAINING_CANDIDATES_TOTAL = meter.create_counter(
    "training_candidates", description="Total SFT or DPO candidate samples exported"
)
DEPENDENCY_CALLS_TOTAL = meter.create_counter(
    "dependency_calls", description="Total resilient dependency operations"
)
DEPENDENCY_CALL_DURATION_SECONDS = meter.create_histogram(
    "dependency_call_duration_seconds",
    unit="s",
    description="End-to-end resilient dependency operation duration",
)
DEPENDENCY_RETRIES_TOTAL = meter.create_counter(
    "dependency_retries", description="Total bounded dependency retries"
)
DEPENDENCY_FALLBACKS_TOTAL = meter.create_counter(
    "dependency_fallbacks", description="Total dependency fallback activations"
)
DEGRADED_AGENT_REQUESTS_TOTAL = meter.create_counter(
    "degraded_agent_requests", description="Total Agent requests by degradation level"
)
