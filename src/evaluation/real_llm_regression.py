"""真实 LLM 回归测试的配置校验、用例选择与成本保护。"""

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


REAL_LLM_PROVIDERS = frozenset({"openai", "azure"})
SMOKE_CASE_IDS = (
    "baseline-refund-v1-within-window",
    "baseline-refund-v2-fee",
    "baseline-account-email-validation",
    "baseline-api-504-first-check",
    "baseline-order-payment-total-7001",
    "baseline-info-unsupported-warranty",
    "baseline-refund-v1-chinese-window",
    "baseline-api-chinese-504",
    "baseline-security-ignore-all-reveal",
    "baseline-security-bypass-safety-chain-of-thought",
    "baseline-security-base64-payload",
    "baseline-security-benign-definition",
)

# 线上真实模型测试发现问题的补充用例，放在独立数据集中，不改固定 SHA256 的 Baseline。
ONLINE_REGRESSION_CASE_IDS = (
    "online-complaint-sla-chinese",
    "online-account-login-reset-chinese",
    "online-security-leak-orders-chinese",
    "online-order-status-english-language",
    "online-outage-aws-gateway-cluster-chinese",
)


@dataclass(frozen=True)
class RealLLMRunPlan:
    """保存真实模型回归的可审计运行计划。"""

    provider: str
    model: str
    endpoint_host: str
    suite: str
    case_ids: tuple[str, ...]
    estimated_workflow_llm_calls: int

    def report_metadata(self) -> dict[str, Any]:
        """输出不含密钥和 Prompt 原文的报告来源信息。"""
        return {
            "mode": "real_llm_regression",
            "llm_provider": self.provider,
            "llm_model": self.model,
            "endpoint_host": self.endpoint_host,
            "suite": self.suite,
            "selected_case_count": len(self.case_ids),
            "estimated_workflow_llm_calls": self.estimated_workflow_llm_calls,
        }


def build_real_llm_run_plan(
    *,
    settings: Any,
    cases: Sequence[Any],
    suite: str,
    explicit_case_ids: Sequence[str] | None,
    max_workflow_calls: int,
) -> RealLLMRunPlan:
    """校验真实 Provider，选择用例并阻止超出预期的付费调用。"""
    provider, model, endpoint_host = validate_real_llm_settings(settings)
    case_ids = select_real_llm_cases(
        cases,
        suite=suite,
        explicit_case_ids=explicit_case_ids,
    )
    estimated_calls = estimate_workflow_llm_calls(cases, case_ids)
    if max_workflow_calls < 1:
        raise ValueError("max_workflow_calls must be greater than zero.")
    if estimated_calls > max_workflow_calls:
        raise ValueError(
            f"Estimated Workflow LLM calls ({estimated_calls}) exceed "
            f"the configured maximum ({max_workflow_calls}). Increase "
            "--max-workflow-calls explicitly if this cost is intended."
        )
    return RealLLMRunPlan(
        provider=provider,
        model=model,
        endpoint_host=endpoint_host,
        suite=suite if not explicit_case_ids else "explicit",
        case_ids=tuple(case_ids),
        estimated_workflow_llm_calls=estimated_calls,
    )


def merge_dataset_files(paths: Sequence[Any], output_path: Any) -> Any:
    """把 Baseline 与补充数据集合并成临时文件，Baseline 本身不被修改。"""
    import json
    from pathlib import Path

    merged: list[Any] = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        merged.extend(payload.get("cases", payload) if isinstance(payload, dict) else payload)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"name": "merged_real_llm_regression", "cases": merged}, ensure_ascii=False),
        encoding="utf-8",
    )
    return output


def validate_real_llm_settings(settings: Any) -> tuple[str, str, str]:
    """拒绝 Mock、缺失密钥或仍为示例值的真实回归配置。"""
    provider = str(getattr(settings, "LLM_PROVIDER", "")).strip().lower()
    if provider not in REAL_LLM_PROVIDERS:
        raise ValueError(
            "Real LLM regression requires LLM_PROVIDER=openai or azure; "
            "mock and unknown providers are rejected."
        )

    if provider == "openai":
        api_key = getattr(settings, "LLM_API_KEY", None)
        model = str(getattr(settings, "LLM_MODEL_NAME", "") or "").strip()
        if not _is_configured_secret(api_key):
            raise ValueError("Real openai regression requires a valid LLM_API_KEY.")
        if not model or model.lower().startswith("mock"):
            raise ValueError(
                "Real openai regression requires a non-Mock LLM_MODEL_NAME."
            )
        endpoint = getattr(settings, "LLM_BASE_URL", None)
        return provider, model, _endpoint_host(endpoint, default="api.openai.com")

    api_key = getattr(settings, "AZURE_OPENAI_API_KEY", None)
    endpoint = getattr(settings, "AZURE_OPENAI_ENDPOINT", None)
    deployment = str(getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or "").strip()
    if not _is_configured_secret(api_key):
        raise ValueError("Real azure regression requires AZURE_OPENAI_API_KEY.")
    if not endpoint or "your-resource" in str(endpoint):
        raise ValueError("Real azure regression requires AZURE_OPENAI_ENDPOINT.")
    if not deployment or deployment.lower().startswith("mock"):
        raise ValueError("Real azure regression requires AZURE_OPENAI_DEPLOYMENT.")
    return provider, deployment, _endpoint_host(endpoint, default="azure-openai")


def select_real_llm_cases(
    cases: Sequence[Any],
    *,
    suite: str,
    explicit_case_ids: Sequence[str] | None = None,
) -> list[str]:
    """按 smoke、full 或显式 ID 生成稳定的回归用例清单。"""
    case_index = {str(case.id): case for case in cases}
    if len(case_index) != len(cases):
        raise ValueError("Real LLM regression case ids must be unique.")
    if explicit_case_ids:
        selected = list(dict.fromkeys(str(case_id) for case_id in explicit_case_ids))
    elif suite == "smoke":
        # 补充数据集已合并进来时一并选入。
        selected = list(SMOKE_CASE_IDS) + [
            case_id for case_id in ONLINE_REGRESSION_CASE_IDS if case_id in case_index
        ]
    elif suite == "full":
        selected = [str(case.id) for case in cases]
    else:
        raise ValueError("suite must be either 'smoke' or 'full'.")

    missing = [case_id for case_id in selected if case_id not in case_index]
    if missing:
        raise ValueError("Unknown real LLM regression case ids: " + ", ".join(missing))
    if not selected:
        raise ValueError("Real LLM regression must select at least one case.")
    return selected


def estimate_workflow_llm_calls(
    cases: Sequence[Any], selected_case_ids: Sequence[str]
) -> int:
    """估算 Analyzer、Resolver 和 QA 调用数，安全短路样本不调用 LLM。"""
    index: Mapping[str, Any] = {str(case.id): case for case in cases}
    calls = 0
    for case_id in selected_case_ids:
        case = index[case_id]
        configured = dict(getattr(case, "security_expectations", {}) or {})
        tags = {str(tag).lower() for tag in getattr(case, "tags", [])}
        expected_attack = bool(
            configured.get(
                "expected_attack",
                tags & {"prompt_injection", "jailbreak", "security_attack"},
            )
        )
        calls += 0 if expected_attack else 3
    return calls


def require_live_confirmation(confirmed: bool) -> None:
    """必须显式确认才允许产生外部模型调用和费用。"""
    if not confirmed:
        raise ValueError(
            "Live model calls are disabled. Re-run with --confirm-live after "
            "reviewing the provider, model, selected cases, and call budget."
        )


def prepare_judge_environment(
    settings: Any, *, rag_engine: str, agent_engine: str
) -> None:
    """仅在启用 Ragas / DeepEval 真实评委时注入独立评委密钥。"""
    requires_judge = rag_engine == "ragas" or agent_engine == "deepeval"
    if not requires_judge:
        return
    judge_key = getattr(settings, "OPENAI_API_KEY", None)
    if not _is_configured_secret(judge_key):
        raise ValueError(
            "Ragas/DeepEval judge mode requires a valid OPENAI_API_KEY. "
            "This key is separate from the Workflow LLM_API_KEY."
        )
    os.environ.setdefault("OPENAI_API_KEY", str(judge_key))


def _is_configured_secret(value: Any) -> bool:
    candidate = str(value or "").strip()
    return bool(candidate) and not candidate.lower().startswith("your-")


def _endpoint_host(value: Any, *, default: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return default
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    return parsed.hostname or default
