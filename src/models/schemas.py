from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field


# --- AUTH SCHEMAS ---
class UserCreate(BaseModel):
    """定义用户注册时提交的账号、密码和角色。"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(default="agent", pattern="^(admin|manager|agent)$")


class UserResponse(BaseModel):
    """定义对外返回的用户基础信息。"""

    id: int
    username: str
    role: str
    created_at: datetime

    class Config:
        """允许响应模型从 ORM 对象读取字段。"""

        from_attributes = True


class LoginRequest(BaseModel):
    """定义用户登录凭据。"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class Token(BaseModel):
    """定义登录成功后返回的访问令牌信息。"""

    access_token: str
    token_type: str
    role: str


class TokenData(BaseModel):
    """定义从 JWT 载荷解析出的用户身份信息。"""

    username: Optional[str] = None
    role: Optional[str] = None


# --- CHAT SCHEMAS ---
class ChatMessage(BaseModel):
    """定义单条标准化对话消息。"""

    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    """定义客服对话入口的会话、客户和知识库参数。"""

    session_id: str = Field(..., min_length=1, max_length=100)
    customer_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=5000)
    kb_version: str = Field(default="v1", max_length=50)


class Citation(BaseModel):
    """定义回复引用的知识来源、正文片段和相关性信息。"""

    source: str
    text: str
    score: Optional[float] = None
    version: Optional[str] = None


class CostMetadata(BaseModel):
    """定义单次 Agent 请求的 token、成本和延迟统计。"""

    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    latency_seconds: float = 0.0


class ToolCallTrace(BaseModel):
    """定义返回给调用方的 Tool 调用审计摘要。"""

    tool_name: str
    role: str
    ticket_id: Optional[int] = None
    allowed: bool
    status: str
    latency_ms: float
    mocked: bool = True
    error: Optional[str] = None


class ChatResponse(BaseModel):
    """定义客服 Agent 对话的完整业务响应。"""

    session_id: str
    response: str
    sentiment: str
    priority: str
    tool_context: Dict[str, Any] = Field(default_factory=dict)
    tool_calls: List[ToolCallTrace] = Field(default_factory=list)
    citations: List[Citation]
    escalation_recommended: bool
    escalation_reason: Optional[str] = None
    analyzer_confidence: float = 1.0
    risk_level: str = "low"
    risk_score: float = 0.0
    risk_reasons: List[str] = Field(default_factory=list)
    cost_metadata: CostMetadata
    approval_required: bool = False
    approval_id: Optional[int] = None
    agent_run_id: Optional[str] = None
    feedback_token: Optional[str] = None


# --- TICKET SCHEMAS ---
class TicketCreate(BaseModel):
    """定义创建客服工单所需的基础字段。"""

    customer_id: str
    subject: str
    description: str
    kb_version: str = Field(default="v1")


class PublicSupportRequest(BaseModel):
    """定义用户咨询页面提交的最小请求。"""

    customer_id: str = Field(..., min_length=1, max_length=100)
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    message: str = Field(..., min_length=2, max_length=5000)
    kb_version: str = Field(default="v1", max_length=50)


class PublicSupportResponse(BaseModel):
    """只向终端用户返回安全的处理状态与最终回复。"""

    ticket_id: int
    session_id: str
    status: str
    response: Optional[str] = None
    message: str
    handling_reason: Optional[str] = None
    created_at: datetime
    agent_run_id: Optional[str] = None
    feedback_token: Optional[str] = None


class PublicConversationMessageResponse(BaseModel):
    """用户端可见的历史消息，不包含待审草稿正文。"""

    id: str
    ticket_id: Optional[int] = None
    role: str
    status: str
    content: str
    created_at: datetime


class PublicConversationHistoryResponse(BaseModel):
    """返回指定演示客户最近七天的安全会话历史。"""

    customer_id: str
    window_days: int = 7
    window_start: datetime
    messages: List[PublicConversationMessageResponse]


class TicketResponse(BaseModel):
    """定义包含状态、分析结果和时间信息的工单响应。"""

    id: int
    customer_id: str
    subject: str
    description: str
    status: str
    priority: str
    sentiment: Optional[str]
    department: Optional[str]
    sla_hours: Optional[float]
    requires_tool_review: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        """允许响应模型从 ORM 工单对象读取字段。"""

        from_attributes = True


class TicketAgentResultResponse(BaseModel):
    """返回工单已持久化的 Agent 处理结果，不触发新的 Workflow。"""

    ticket_id: int
    agent_run_id: str
    kb_version: str
    response: str
    citations: List[Citation] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    qa_score: Optional[float] = None
    hallucination_detected: bool = False
    escalation_recommended: bool = False
    escalation_reason: Optional[str] = None
    review_reasons: List[str] = Field(default_factory=list)
    risk_level: Optional[str] = None
    risk_score: Optional[float] = None
    analyzer_confidence: Optional[float] = None
    approval_required: bool = False
    approval_id: Optional[int] = None
    approval_status: Optional[str] = None
    workflow_execution_id: Optional[str] = None
    workflow_execution_status: Optional[str] = None
    cost_metadata: CostMetadata
    created_at: datetime


class TicketSummaryResponse(BaseModel):
    """定义工单摘要、关键问题和紧急程度结果。"""

    ticket_id: int
    summary: str
    key_issues: List[str]
    sentiment: str
    priority: str
    urgency_score: float


class TicketSentimentResponse(BaseModel):
    """定义工单情绪、置信度和优先级分析结果。"""

    ticket_id: int
    sentiment: str
    confidence_score: float
    detected_emotions: List[str]
    priority: str


class TicketEscalationResponse(BaseModel):
    """定义工单升级建议、目标部门和 SLA 信息。"""

    ticket_id: int
    escalation_recommended: bool
    escalation_reason: str
    suggested_department: str
    sla_hours: float


# --- RESOLUTION SCHEMAS ---
class SuggestResponseRequest(BaseModel):
    """定义生成工单建议回复所需的请求参数。"""

    ticket_id: int
    kb_version: str = Field(default="v1")


class SuggestResponseResponse(BaseModel):
    """定义建议回复、引用、工具上下文和 QA 结果。"""

    ticket_id: int
    suggested_response: str
    tool_context: Dict[str, Any] = Field(default_factory=dict)
    tool_calls: List[ToolCallTrace] = Field(default_factory=list)
    citations: List[Citation]
    qa_score: float
    hallucination_detected: bool
    analyzer_confidence: float = 1.0
    risk_level: str = "low"
    risk_score: float = 0.0
    risk_reasons: List[str] = Field(default_factory=list)
    cost_metadata: CostMetadata
    agent_run_id: Optional[str] = None
    feedback_token: Optional[str] = None


# --- CUSTOMER CONTEXT SCHEMAS ---
class CustomerContextRequest(BaseModel):
    """定义查询客户业务上下文的请求。"""

    customer_id: str


class OrderInfo(BaseModel):
    """定义客户近期订单的结构化摘要。"""

    order_id: str
    status: str
    items: List[str]
    total_amount: float
    order_date: datetime


class CustomerContextResponse(BaseModel):
    """定义客户画像、工单数量和近期订单上下文。"""

    customer_id: str
    name: str
    tier: str  # VIP, Standard, Enterprise
    open_tickets_count: int
    recent_orders: List[OrderInfo]
    last_interaction: Optional[datetime] = None


# --- EVALUATION SCHEMAS ---
class EvaluateResponseRequest(BaseModel):
    """定义回复质量评测所需的问题、上下文和答案。"""

    query: str
    context: List[str]
    response: str
    agent_run_id: Optional[str] = None
    external_ref: Optional[str] = Field(default=None, max_length=160)


class EvaluateResponseResponse(BaseModel):
    """定义回复评测的各项分数、结论和报告摘要。"""

    faithfulness_score: float
    context_precision: float
    context_recall: float
    hallucination_rate: float
    answer_relevance: float
    overall_quality_score: float
    passed_evaluation: bool
    report_summary: str


# --- HUMAN IN THE LOOP APPROVAL SCHEMAS ---
class ResponseApprovalRequest(BaseModel):
    """定义人工审批动作及可选的修改后回复。"""

    approval_id: int
    modified_response: Optional[str] = None
    status: str = Field(..., pattern="^(approved|modified|rejected)$")


class ResponseApprovalResponse(BaseModel):
    """定义人工审批完成后的最终回复和处理信息。"""

    id: int
    ticket_id: int
    status: str
    final_response: str
    latency_seconds: float
    approved_at: datetime
    workflow_execution_status: Optional[str] = None


class AgentExecutionResponse(BaseModel):
    """返回 Durable Execution 的脱敏状态与 Trace 关联。"""

    id: str
    ticket_id: int
    approval_id: Optional[int]
    agent_run_id: Optional[str]
    status: str
    checkpoint_backend: str
    workflow_version: str
    lock_version: int
    resume_attempts: int
    initial_trace_id: Optional[str]
    resume_trace_id: Optional[str]
    last_error_type: Optional[str]
    created_at: datetime
    updated_at: datetime
    interrupted_at: Optional[datetime]
    resumed_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        """允许直接从 SQLAlchemy 对象返回安全字段。"""

        from_attributes = True


# --- TOOL GOVERNANCE SCHEMAS ---
class ToolActionCreateRequest(BaseModel):
    """提交高风险 Tool Action，参数将在服务端加密。"""

    ticket_id: int = Field(..., ge=1)
    tool_name: str = Field(..., min_length=1, max_length=160)
    payload: Dict[str, Any]
    intent: str = Field(..., min_length=1, max_length=80)


class ToolActionDecisionRequest(BaseModel):
    """定义审批人对高风险动作的决策。"""

    decision: str = Field(..., pattern="^(approved|rejected)$")
    expected_version: int = Field(..., ge=1)
    comment: Optional[str] = Field(default=None, max_length=1000)


class ToolActionExecuteRequest(BaseModel):
    """通过版本号防止并发重复执行。"""

    expected_version: int = Field(..., ge=1)


class ToolActionResolutionRequest(BaseModel):
    """主管提交外部核实结果，不触发任何业务写入。"""

    expected_version: int = Field(..., ge=1)
    outcome: str = Field(
        ..., pattern="^(succeeded|failed|compensated|compensation_failed)$"
    )
    evidence_reference: str = Field(..., min_length=3, max_length=500)
    note: str = Field(..., min_length=5, max_length=1000)


class ToolActionReviewResponse(BaseModel):
    """向工单详情返回待核实操作及脱敏后的确认记录。"""

    tool_action_id: str
    ticket_id: int
    status: str
    reason: str
    outcome: Optional[str]
    evidence_summary: Optional[Dict[str, Any]]
    resolved_by_user_id: Optional[int]
    created_at: datetime
    resolved_at: Optional[datetime]
    action: "ToolActionResponse"


class ToolActionCompensationRequest(BaseModel):
    """由主管发起带原因和乐观版本的补偿请求。"""

    expected_version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=3, max_length=500)


class ToolActionEventResponse(BaseModel):
    """返回 Append-only 状态迁移事件。"""

    id: str
    sequence: int
    action: str
    from_status: Optional[str]
    to_status: str
    actor_user_id: Optional[int]
    actor_role: Optional[str]
    request_id: str
    trace_id: Optional[str]
    details: Dict[str, Any]
    created_at: datetime

    class Config:
        """允许从 ORM 事件读取字段。"""

        from_attributes = True


class ToolActionResponse(BaseModel):
    """返回不含密文和完整 HMAC 的高风险动作视图。"""

    id: str
    ticket_id: int
    tool_name: str
    tool_version: str
    intent: str
    risk_level: str
    operation_type: str
    status: str
    version: int
    policy_version: str
    idempotency_key: Optional[str]
    policy_hash: Optional[str]
    payload_summary: Dict[str, Any]
    result_summary: Optional[Dict[str, Any]]
    error_type: Optional[str]
    failure_reason: Optional[str]
    request_id: str
    trace_id: Optional[str]
    proposed_by_user_id: int
    proposed_by_role: str
    reviewed_by_user_id: Optional[int]
    reviewed_by_role: Optional[str]
    executed_by_user_id: Optional[int]
    review_comment: Optional[str]
    created_at: datetime
    updated_at: datetime
    reviewed_at: Optional[datetime]
    execution_started_at: Optional[datetime]
    completed_at: Optional[datetime]
    events: List[ToolActionEventResponse] = Field(default_factory=list)

    class Config:
        """允许从 ORM Action 读取字段。"""

        from_attributes = True


ToolActionReviewResponse.model_rebuild()


class ToolActionPageResponse(BaseModel):
    """返回高风险 Tool Action 分页。"""

    items: List[ToolActionResponse]
    total: int
    limit: int
    offset: int


class ToolPolicyReplayResponse(BaseModel):
    """返回历史 Policy 快照的确定性审计重放结果。"""

    passed: bool
    policy_version: Optional[str]
    policy_hash: Optional[str] = None
    checks: Dict[str, bool]
    violations: List[str]


class ToolOutboxEventResponse(BaseModel):
    """返回不含业务原始参数的 Outbox 运维视图。"""

    id: str
    tool_action_id: str
    event_type: str
    status: str
    attempts: int
    max_attempts: int
    version: int
    actor_role: str
    request_id: str
    trace_id: Optional[str]
    available_at: datetime
    lease_owner: Optional[str]
    lease_expires_at: Optional[datetime]
    last_error_type: Optional[str]
    last_error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    class Config:
        """允许从 ORM Outbox Event 读取字段。"""

        from_attributes = True


class ToolOutboxPageResponse(BaseModel):
    """返回 Transactional Outbox、Retry Queue 与 DLQ 分页。"""

    items: List[ToolOutboxEventResponse]
    total: int
    limit: int
    offset: int


class ToolInvocationAuditResponse(BaseModel):
    """返回不包含原始参数和原始异常的审计摘要。"""

    id: str
    tool_action_id: Optional[str]
    ticket_id: Optional[int]
    request_id: str
    trace_id: Optional[str]
    tool_name: str
    tool_version: str
    operation_type: str
    risk_level: str
    actor_user_id: Optional[int]
    actor_role: str
    allowed: bool
    status: str
    attempts: int
    latency_ms: float
    mocked: bool
    error_type: Optional[str]
    payload_keys: List[str]
    result_summary: Optional[Any]
    policy_version: str
    created_at: datetime

    class Config:
        """允许从 ORM 审计对象读取字段。"""

        from_attributes = True


class ToolInvocationAuditPageResponse(BaseModel):
    """返回 Tool 审计分页。"""

    items: List[ToolInvocationAuditResponse]
    total: int
    limit: int
    offset: int


# --- FEEDBACK PIPELINE SCHEMAS ---
class UserFeedbackRequest(BaseModel):
    """定义用户针对一次 Agent Run 提交的评分和文字评价。"""

    agent_run_id: str = Field(..., min_length=1, max_length=36)
    feedback_token: str = Field(..., min_length=32, max_length=128)
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=2000)
    idempotency_key: str = Field(..., min_length=8, max_length=120)


class FeedbackEventResponse(BaseModel):
    """返回反馈事件与 Agent Run、Trace 的关联信息。"""

    id: str
    agent_run_id: str
    ticket_id: Optional[int]
    trace_id: Optional[str]
    sequence: Optional[int]
    source: str
    feedback_type: str
    rating: Optional[int]
    comment: Optional[str]
    corrected_response: Optional[str]
    evaluation_metrics: Optional[Dict[str, Any]]
    evaluation_passed: Optional[bool]
    training_eligible: bool
    exclusion_reason: Optional[str]
    created_at: datetime

    class Config:
        """允许响应模型从 ORM 对象读取字段。"""

        from_attributes = True


class AgentSkillSelectionResponse(BaseModel):
    """返回一次 Agent Run 的 Skill 归因快照。"""

    skill_name: str
    skill_version: str
    selection_strategy: str
    registry_id: str

    class Config:
        """允许从 ORM 关联对象读取字段。"""

        from_attributes = True


class AgentRunSnapshotResponse(BaseModel):
    """返回 Analyzer / QA 的执行策略和结构化结果。"""

    analyzer_strategy: str
    analyzer_result: Dict[str, Any] = Field(default_factory=dict)
    qa_strategy: str
    qa_result: Dict[str, Any] = Field(default_factory=dict)
    decision_records: List[Dict[str, Any]] = Field(default_factory=list)

    class Config:
        """允许从 ORM 快照对象读取字段。"""

        from_attributes = True


class AgentRunResponse(BaseModel):
    """返回一次 Agent 执行快照及其全部反馈事件。"""

    id: str
    ticket_id: Optional[int]
    request_id: str
    trace_id: Optional[str]
    endpoint: str
    workflow_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    kb_version: str
    input_text: str
    output_text: str
    workflow_path: List[str]
    tool_calls: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]
    qa_score: Optional[float]
    hallucination_detected: bool
    escalation_recommended: bool
    approval_required: bool
    workflow_errors: List[str]
    tokens_input: int
    tokens_output: int
    latency_seconds: float
    created_at: datetime
    skill_selection: Optional[AgentSkillSelectionResponse] = None
    execution_snapshot: Optional[AgentRunSnapshotResponse] = None
    feedback_events: List[FeedbackEventResponse] = Field(default_factory=list)

    class Config:
        """允许响应模型从 ORM 对象读取字段。"""

        from_attributes = True


class AgentRunSummaryResponse(BaseModel):
    """定义可观测页面列表所需的低敏 Agent Run 摘要。"""

    id: str
    ticket_id: Optional[int]
    request_id: str
    trace_id: Optional[str]
    endpoint: str
    workflow_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    kb_version: str
    workflow_path: List[str]
    qa_score: Optional[float]
    hallucination_detected: bool
    escalation_recommended: bool
    approval_required: bool
    workflow_errors: List[str]
    tokens_input: int
    tokens_output: int
    latency_seconds: float
    created_at: datetime
    skill_selection: Optional[AgentSkillSelectionResponse] = None

    class Config:
        """允许从 AgentRun ORM 实例读取摘要字段。"""

        from_attributes = True


class AgentRunPageResponse(BaseModel):
    """返回 Agent Run 分页结果与总数。"""

    items: List[AgentRunSummaryResponse]
    total: int
    limit: int
    offset: int


# --- ADMIN RESOURCE MANAGEMENT SCHEMAS ---
class AdminToolResponse(BaseModel):
    """返回 Tool Registry 定义与持久化运行开关。"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    min_role: str
    timeout_seconds: float
    mocked: bool
    risk_level: str
    operation_type: str
    allowed_intents: Optional[List[str]] = None
    version: str
    enabled: bool
    disabled_reason: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class AdminToolUpdateRequest(BaseModel):
    """定义管理员对 Tool 运行开关的修改。"""

    enabled: bool
    reason: Optional[str] = Field(default=None, max_length=500)


class AdminPromptBundleRequest(BaseModel):
    """接收待发布的不可变 Prompt Bundle。"""

    schema_version: str
    version: str
    templates: Dict[str, Dict[str, str]]


class AdminPromptBundleResponse(BaseModel):
    """返回 Prompt Bundle 内容与稳定标识。"""

    bundle_id: str
    version: str
    node_hashes: Dict[str, str]
    payload: Dict[str, Any]


class AdminPromptRegistryResponse(BaseModel):
    """返回 Prompt 环境指针、生效版本与候选列表。"""

    state: Dict[str, Any]
    effective: Dict[str, AdminPromptBundleResponse]
    bundles: List[AdminPromptBundleResponse]


class AdminKnowledgeDocumentRequest(BaseModel):
    """定义知识文档和向量索引的统一写入输入。"""

    id: str = Field(
        ..., min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"
    )
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=30000)
    version: str = Field(default="v1", min_length=1, max_length=50)
    category: str = Field(..., min_length=1, max_length=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AdminKnowledgeDocumentResponse(BaseModel):
    """返回知识文档的权威 SQL 快照。"""

    id: str
    title: str
    content: str
    version: str
    category: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AdminRagReindexResponse(BaseModel):
    """返回全量 RAG 重建结果。"""

    indexed_documents: int
