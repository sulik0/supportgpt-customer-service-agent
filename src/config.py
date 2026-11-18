import base64
from urllib.parse import urlsplit
from typing import ClassVar, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """集中定义应用运行配置，并支持通过环境变量覆盖默认值。

    标注为 ClassVar 的是固定值，不从环境变量或 .env 读取；需要调整时直接改代码。
    """

    # API Settings
    APP_NAME: str = Field(default="SupportGPT 智能客服 Agent")
    APP_ENV: str = Field(default="development")
    DEBUG: bool = Field(default=True)
    LOG_LEVEL: str = Field(default="INFO")

    # Security & Auth
    JWT_SECRET: str = Field(default="super-secret-jwt-key-change-in-production-123456")
    JWT_ALGORITHM: ClassVar[str] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: ClassVar[int] = 60
    STAFF_SELF_REGISTRATION_ENABLED: bool = Field(default=False)

    # Public Demo Security
    CORS_ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000"
    )
    TRUST_PROXY_HEADERS: bool = Field(default=False)
    PUBLIC_DEMO_ISOLATION_ENABLED: bool = Field(default=True)
    PUBLIC_DEMO_PROFILE_IDS: str = Field(default="cust_101,cust_102,cust_103,cust_201,cust_202,cust_203")
    PUBLIC_VISITOR_SECRET: Optional[str] = Field(default=None)
    PUBLIC_VISITOR_COOKIE_NAME: str = Field(default="supportgpt_visitor")
    PUBLIC_VISITOR_COOKIE_MAX_AGE_SECONDS: ClassVar[int] = 2592000
    # 只读的账务/故障咨询：人工复核照常进行，但先向用户展示通过 QA 的初步答复。
    PUBLIC_PRELIMINARY_ANSWER_ENABLED: bool = Field(default=True)
    PUBLIC_PRELIMINARY_ANSWER_MIN_QA_SCORE: ClassVar[float] = 0.8
    PUBLIC_RATE_LIMIT_ENABLED: bool = Field(default=True)
    PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE: int = Field(default=5, ge=1, le=1000)
    PUBLIC_CHAT_RATE_LIMIT_PER_DAY: int = Field(default=100, ge=1, le=100000)
    PUBLIC_READ_RATE_LIMIT_PER_MINUTE: int = Field(default=30, ge=1, le=5000)
    # 线上检查脚本带此密钥时使用单独额度，不占公开访客额度；为空则不启用。
    ONLINE_CHECK_KEY: str = Field(default="")
    ONLINE_CHECK_RATE_LIMIT_PER_MINUTE: int = Field(default=10, ge=1, le=1000)
    ONLINE_CHECK_RATE_LIMIT_PER_DAY: ClassVar[int] = 300
    PUBLIC_FEEDBACK_RATE_LIMIT_PER_MINUTE: ClassVar[int] = 10
    AUTH_RATE_LIMIT_PER_MINUTE: ClassVar[int] = 10
    RATE_LIMIT_REDIS_PREFIX: ClassVar[str] = "supportgpt:rate-limit"
    MAX_REQUEST_BODY_BYTES: ClassVar[int] = 65536

    # Database & Cache
    # Default to sqlite in-memory or file for easy local run without postgres, override via env
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./supportgpt.db")
    REDIS_URL: Optional[str] = Field(default=None)
    # Memory V1 仅使用有界近期对话和确定性摘要。
    MEMORY_ENABLED: bool = Field(default=True)
    MEMORY_RECENT_MESSAGES: ClassVar[int] = 12
    MEMORY_CONTEXT_MAX_CHARS: ClassVar[int] = 4000
    MEMORY_SUMMARY_MAX_CHARS: ClassVar[int] = 1200
    MEMORY_SCAN_MESSAGES: ClassVar[int] = 48
    MEMORY_TTL_SECONDS: ClassVar[int] = 86400

    # LangGraph Checkpoint 本地使用独立 SQLite，生产默认复用 PostgreSQL。
    LANGGRAPH_CHECKPOINT_ENABLED: bool = Field(default=True)
    LANGGRAPH_CHECKPOINT_DATABASE_URL: Optional[str] = Field(default=None)
    LANGGRAPH_CHECKPOINT_SQLITE_PATH: str = Field(
        default="./.runtime/langgraph-checkpoints.sqlite"
    )
    LANGGRAPH_CHECKPOINT_NAMESPACE: ClassVar[str] = "supportgpt-workflow-v1"
    LANGGRAPH_RESUME_LEASE_SECONDS: ClassVar[int] = 60

    # LLM Configuration
    LLM_PROVIDER: str = Field(default="mock")  # mock, openai, azure
    LLM_MODEL_NAME: Optional[str] = Field(default=None)
    LLM_BASE_URL: Optional[str] = Field(default=None)
    LLM_API_KEY: Optional[str] = Field(default=None)
    # Analyzer 与 QA 可共用独立的小模型服务，未配置时回退主模型。
    LLM_FAST_MODEL_NAME: Optional[str] = Field(default=None)
    LLM_FAST_BASE_URL: Optional[str] = Field(default=None)
    LLM_FAST_API_KEY: Optional[str] = Field(default=None)
    LLM_ANALYZER_MODEL_NAME: Optional[str] = Field(default=None)
    LLM_QA_MODEL_NAME: Optional[str] = Field(default=None)
    LLM_ANALYZER_MAX_TOKENS: ClassVar[int] = 120
    LLM_RESOLVER_MAX_TOKENS: int = Field(default=480, ge=64, le=2048)
    LLM_QA_MAX_TOKENS: int = Field(default=96, ge=32, le=512)
    LLM_RESOLVER_MAX_RAG_CHARS: ClassVar[int] = 5000
    LLM_RESOLVER_MAX_TOOL_CHARS: ClassVar[int] = 2500
    LLM_QA_MAX_CONTEXT_CHARS: int = Field(default=4000, ge=500, le=20000)
    # DeepSeek 等兼容服务的思考模式；留空不传。思考 Token 会占用 max_tokens 输出额度。
    LLM_THINKING: Optional[str] = Field(default=None, pattern="^(enabled|disabled)$")
    # DecisionProvider 默认关闭，Jev 只处理封闭选项的语义决策。
    # jev_replay 只供 PR 门禁和测试：读录好的 Jev 回答，生产环境禁止。
    DECISION_PROVIDER: str = Field(
        default="disabled", pattern="^(disabled|jev|jev_replay)$"
    )
    DECISION_REPLAY_PATH: str = Field(
        default="evaluation/decision_recordings/jev_baseline.json"
    )
    JEV_API_KEY: Optional[str] = Field(default=None)
    JEV_BASE_URL: Optional[str] = Field(default=None)
    JEV_MODEL: str = Field(default="jev-1.13.0", min_length=1, max_length=100)
    JEV_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0.0, le=30.0)
    JEV_MAX_RETRIES: int = Field(default=0, ge=0, le=2)
    JEV_MAX_STATE_CHARS: ClassVar[int] = 12000
    JEV_INTENT_CONFIDENCE_THRESHOLD: ClassVar[float] = 0.75
    JEV_QA_CONFIDENCE_THRESHOLD: ClassVar[float] = 0.75
    JEV_NOUL_THRESHOLD: ClassVar[float] = 0.7
    # Resilience 默认只执行一次有界 Retry，避免放大故障。
    RESILIENCE_ENABLED: bool = Field(default=True)
    RESILIENCE_LLM_TIMEOUT_SECONDS: ClassVar[float] = 20.0
    RESILIENCE_LLM_MAX_RETRIES: int = Field(default=1, ge=0, le=3)
    RESILIENCE_RAG_TIMEOUT_SECONDS: ClassVar[float] = 5.0
    RESILIENCE_RAG_MAX_RETRIES: ClassVar[int] = 1
    RESILIENCE_TOOL_READ_MAX_RETRIES: int = Field(default=1, ge=0, le=3)
    RESILIENCE_RETRY_BASE_DELAY_SECONDS: float = Field(default=0.1, ge=0.0, le=5.0)
    RESILIENCE_CIRCUIT_FAILURE_THRESHOLD: ClassVar[int] = 3
    RESILIENCE_CIRCUIT_RECOVERY_SECONDS: float = Field(default=30.0, gt=0.0)
    # 备用模型是可选的 OpenAI-compatible endpoint。
    LLM_FALLBACK_MODEL_NAME: Optional[str] = Field(default=None)
    LLM_FALLBACK_BASE_URL: Optional[str] = Field(default=None)
    LLM_FALLBACK_API_KEY: Optional[str] = Field(default=None)
    # rules 为确定性路由；react 由 LLM 在 Skill 白名单的只读 Tool 中受控选择。
    TOOL_SELECTION_MODE: str = Field(default="rules", pattern="^(rules|react)$")
    TOOL_REACT_MAX_STEPS: int = Field(default=3, ge=1, le=5)
    TOOL_REACT_MAX_TOOL_CALLS: int = Field(default=5, ge=1, le=10)
    TOOL_REACT_MAX_RESULT_CHARS: ClassVar[int] = 600
    LLM_TOOL_PLANNER_MAX_TOKENS: ClassVar[int] = 200
    LLM_TOOL_PLANNER_MODEL_NAME: Optional[str] = Field(default=None)
    # Tool Governance V2.2：Outbox Worker 统一执行、对账与补偿事件。
    TOOL_POLICY_VERSION: str = Field(default="tool-policy-v2.2")
    TOOL_ACTION_ENCRYPTION_KEY: Optional[str] = Field(default=None)
    TOOL_OUTBOX_WORKER_ENABLED: bool = Field(default=True)
    TOOL_OUTBOX_POLL_INTERVAL_SECONDS: ClassVar[float] = 1.0
    TOOL_OUTBOX_BATCH_SIZE: ClassVar[int] = 20
    TOOL_OUTBOX_LEASE_SECONDS: int = Field(default=30, ge=5, le=600)
    TOOL_OUTBOX_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=20)
    TOOL_OUTBOX_RETRY_BASE_SECONDS: float = Field(default=1.0, ge=0.0, le=60.0)
    TOOL_OUTBOX_RETRY_MAX_SECONDS: float = Field(default=60.0, ge=0.0, le=3600.0)
    TOOL_RECONCILIATION_DELAY_SECONDS: float = Field(default=2.0, ge=0.0, le=300.0)
    # 默认仍为进程内 Mock；reference_http 只连接独立演示 OMS。
    OMS_PROVIDER: str = Field(default="mock", pattern="^(mock|reference_http)$")
    OMS_BASE_URL: Optional[str] = Field(default=None)
    OMS_API_KEY: Optional[str] = Field(default=None, repr=False)
    OMS_TIMEOUT_SECONDS: float = Field(default=1.5, gt=0.0, le=1.8)
    PROMPT_VERSION: str = Field(default="support-v1")
    # PromptOps 按内容 Hash 绑定版本；旧标签仅保留兼容。
    PROMPT_REGISTRY_DIR: str = Field(default="./.runtime/promptops")
    PROMPT_ENVIRONMENT: str = Field(
        default="production", pattern="^(staging|production)$"
    )
    PROMPT_BUNDLE_ID: Optional[str] = Field(default=None)
    AGENT_WORKFLOW_VERSION: ClassVar[str] = "support-workflow-v1"
    # OPENAI_API_KEY 继续供 Embedding 和离线评测模块独立使用。
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    AZURE_OPENAI_API_KEY: Optional[str] = Field(default=None)
    AZURE_OPENAI_ENDPOINT: Optional[str] = Field(default=None)
    AZURE_OPENAI_API_VERSION: Optional[str] = Field(default="2024-02-15-preview")
    AZURE_OPENAI_DEPLOYMENT: Optional[str] = Field(default="gpt-4")

    # Vector DB
    VECTOR_DB_PERSIST_DIR: str = Field(default="./.runtime/chromadb-0.5")
    CHROMA_HOST: Optional[str] = Field(default=None)
    CHROMA_PORT: Optional[int] = Field(default=None)
    CHROMA_ANONYMIZED_TELEMETRY: ClassVar[bool] = False

    # Observability：应用仅通过 OpenTelemetry SDK 采集并使用 OTLP 导出。
    OTEL_ENABLED: bool = Field(default=True)
    OTEL_SERVICE_NAME: str = Field(default="supportgpt-backend")
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: Optional[str] = Field(default=None)
    OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: Optional[str] = Field(default=None)
    OTEL_METRIC_EXPORT_INTERVAL_MILLISECONDS: ClassVar[int] = 15000
    OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS: ClassVar[float] = 3.0
    OTEL_EXPORTER_PREFLIGHT_ENABLED: bool = Field(default=True)
    OTEL_EXPORTER_PREFLIGHT_TIMEOUT_SECONDS: ClassVar[float] = 0.25
    OTEL_CONSOLE_EXPORTER: bool = Field(default=False)
    OTEL_TRACE_SAMPLE_RATIO: ClassVar[float] = 1.0
    OTEL_EXCLUDED_URLS: ClassVar[str] = "health"
    # 只导出 Agent 链路 Span；留空时按 Endpoint 自动判断（直连 LangSmith 开，Collector 关）。
    OTEL_EXPORT_AGENT_SPANS_ONLY: Optional[bool] = Field(default=None)
    # 仅用于共享 .env 校验和 Collector 容器替换，业务代码不会使用或直连。
    OTEL_COLLECTOR_LANGSMITH_API_KEY: Optional[str] = Field(default=None)
    OTEL_COLLECTOR_LANGSMITH_PROJECT: str = Field(default="supportgpt-enterprise")
    LANGSMITH_CAPTURE_LLM_CONTENT: bool = Field(default=True)
    LANGSMITH_LLM_CONTENT_MAX_CHARS: int = Field(default=50000, ge=1000, le=200000)

    # Guardrails Settings
    PII_ANONYMIZATION_ENABLED: ClassVar[bool] = True
    PROMPT_INJECTION_PROTECTION_ENABLED: ClassVar[bool] = True
    JAILBREAK_DETECTION_ENABLED: ClassVar[bool] = True
    RESPONSE_FILTERING_ENABLED: ClassVar[bool] = True

    # Qwen3Guard 通过独立 OpenAI-compatible 服务提供语义安全分类。
    QWEN3_GUARD_ENABLED: bool = Field(default=False)
    QWEN3_GUARD_BASE_URL: str = Field(default="http://127.0.0.1:18001/v1")
    QWEN3_GUARD_API_KEY: str = Field(default="EMPTY")
    QWEN3_GUARD_MODEL_NAME: str = Field(default="Qwen/Qwen3Guard-Gen-0.6B")
    QWEN3_GUARD_TIMEOUT_SECONDS: ClassVar[float] = 5.0
    QWEN3_GUARD_MAX_RETRIES: ClassVar[int] = 0
    QWEN3_GUARD_BLOCK_CONTROVERSIAL: bool = Field(default=False)
    QWEN3_GUARD_MAX_INPUT_CHARS: ClassVar[int] = 20000

    # Risk Engine
    RISK_MEDIUM_THRESHOLD: float = Field(default=0.4, ge=0.0, le=1.0)
    RISK_HIGH_THRESHOLD: float = Field(default=0.7, ge=0.0, le=1.0)
    RISK_CRITICAL_THRESHOLD: float = Field(default=0.9, ge=0.0, le=1.0)
    RISK_LOW_CONFIDENCE_THRESHOLD: ClassVar[float] = 0.65
    RISK_QA_SCORE_THRESHOLD: ClassVar[float] = 0.8

    @model_validator(mode="after")
    def validate_risk_thresholds(self):
        """确保风险等级阈值从低到高严格递增。"""
        if not (
            self.RISK_MEDIUM_THRESHOLD
            < self.RISK_HIGH_THRESHOLD
            < self.RISK_CRITICAL_THRESHOLD
        ):
            raise ValueError("Risk thresholds must satisfy medium < high < critical.")
        return self

    @model_validator(mode="after")
    def validate_llm_fallback(self):
        """备用 LLM 三项配置必须同时出现，避免故障时才暴露配置错误。"""
        values = (
            self.LLM_FALLBACK_MODEL_NAME,
            self.LLM_FALLBACK_BASE_URL,
            self.LLM_FALLBACK_API_KEY,
        )
        if any(values) and not all(values):
            raise ValueError(
                "LLM fallback requires model name, base URL and API key together."
            )
        return self

    @model_validator(mode="after")
    def validate_decision_provider(self):
        """Jev 启用时必须显式提供密钥。"""
        if self.DECISION_PROVIDER == "jev" and not str(self.JEV_API_KEY or "").strip():
            raise ValueError("DECISION_PROVIDER=jev requires JEV_API_KEY.")
        if self.DECISION_PROVIDER == "jev_replay" and self.APP_ENV.lower() in {
            "production",
            "prod",
        }:
            raise ValueError("DECISION_PROVIDER=jev_replay is not allowed in production.")
        return self

    @model_validator(mode="after")
    def validate_tool_outbox(self):
        """避免 Retry 上限小于初始退避，导致无效配置。"""
        if self.TOOL_OUTBOX_RETRY_MAX_SECONDS < self.TOOL_OUTBOX_RETRY_BASE_SECONDS:
            raise ValueError("Tool Outbox max retry delay must be >= base delay.")
        return self

    @property
    def cors_allowed_origins(self) -> list[str]:
        """返回去重后的 CORS 白名单，不接受隐式通配符。"""
        return list(
            dict.fromkeys(
                origin.strip().rstrip("/")
                for origin in self.CORS_ALLOWED_ORIGINS.split(",")
                if origin.strip()
            )
        )

    @model_validator(mode="after")
    def validate_oms_gateway(self):
        """显式启用 HTTP 才检查凭据，并禁止 URL 中夹带密钥。"""
        if self.OMS_PROVIDER == "mock":
            return self
        url = urlsplit(self.OMS_BASE_URL or "")
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "OMS_BASE_URL must be a plain HTTP(S) URL without credentials"
            )
        if self.APP_ENV in {"production", "prod"} and url.scheme != "https":
            raise ValueError("Production OMS gateway requires HTTPS")
        if len(self.OMS_API_KEY or "") < 32:
            raise ValueError("OMS reference HTTP gateway requires a dedicated API key")
        return self

    @property
    def public_demo_profile_ids(self) -> set[str]:
        """返回公开 Demo 允许使用的虚构客户画像。"""
        return {
            profile.strip()
            for profile in self.PUBLIC_DEMO_PROFILE_IDS.split(",")
            if profile.strip()
        }

    @model_validator(mode="after")
    def validate_production_security(self):
        """生产环境拒绝默认密钥和宽松的公网配置。"""
        if self.APP_ENV.lower() not in {"production", "prod"}:
            return self

        errors: list[str] = []
        if self.DEBUG:
            errors.append("DEBUG must be false")
        if (
            len(self.JWT_SECRET) < 32
            or self.JWT_SECRET == "super-secret-jwt-key-change-in-production-123456"
        ):
            errors.append("JWT_SECRET must be a unique secret of at least 32 characters")
        if not self.PUBLIC_VISITOR_SECRET or len(self.PUBLIC_VISITOR_SECRET) < 32:
            errors.append("PUBLIC_VISITOR_SECRET must contain at least 32 characters")
        if not self.PUBLIC_DEMO_ISOLATION_ENABLED:
            errors.append("PUBLIC_DEMO_ISOLATION_ENABLED must remain enabled")
        if not self.PUBLIC_RATE_LIMIT_ENABLED:
            errors.append("PUBLIC_RATE_LIMIT_ENABLED must remain enabled")
        if self.ONLINE_CHECK_KEY and len(self.ONLINE_CHECK_KEY) < 32:
            errors.append("ONLINE_CHECK_KEY must contain at least 32 characters")
        if self.STAFF_SELF_REGISTRATION_ENABLED:
            errors.append("STAFF_SELF_REGISTRATION_ENABLED must remain disabled")
        if not self.cors_allowed_origins or "*" in self.cors_allowed_origins:
            errors.append("CORS_ALLOWED_ORIGINS must contain explicit origins")
        elif any(
            not origin.startswith("https://") for origin in self.cors_allowed_origins
        ):
            errors.append("production CORS origins must use HTTPS")
        if not self.public_demo_profile_ids:
            errors.append("PUBLIC_DEMO_PROFILE_IDS must not be empty")
        try:
            key = base64.urlsafe_b64decode(
                str(self.TOOL_ACTION_ENCRYPTION_KEY or "").encode("ascii")
            )
            if len(key) != 32:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            errors.append("TOOL_ACTION_ENCRYPTION_KEY must be a dedicated Fernet key")
        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))
        return self

    # Feedback Pipeline
    FEEDBACK_TRAINING_MIN_RATING: ClassVar[int] = 4
    FEEDBACK_TRAINING_MIN_QA_SCORE: ClassVar[float] = 0.8
    FEEDBACK_TRAINING_MIN_RAG_SCORE: ClassVar[float] = 0.75

    class Config:
        """定义 Pydantic Settings 读取 `.env` 的规则。"""

        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        # 旧 .env 里已改为固定值的变量直接忽略，不让启动失败。
        extra = "ignore"


settings = Settings()
