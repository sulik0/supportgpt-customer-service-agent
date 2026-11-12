import logging
from typing import Dict, Set

logger = logging.getLogger("supportgpt.observability.cost")
_warned_models: Set[str] = set()

# Pricing per 1,000 tokens in USD
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4": {
        "input": 0.03,
        "output": 0.06
    },
    "gpt-4-turbo": {
        "input": 0.01,
        "output": 0.03
    },
    "gpt-3.5-turbo": {
        "input": 0.0015,
        "output": 0.002
    },
    # DeepSeek 官方定价，查询日期 2026-11-12，来源 api-docs.deepseek.com/quick_start/pricing。
    # 官方单位为 USD / 1M tokens，这里已换算为 / 1K tokens。
    # 按高峰价（peak）且 cache miss 计算（偏保守）；错峰价为一半，缓存命中更低，未区分。
    # deepseek-flash: 输入 $0.30 / 输出 $1.20 每 1M；deepseek-v4-pro: 输入 $1.32 / 输出 $3.96 每 1M。
    "deepseek-flash": {"input": 0.0003, "output": 0.0012},
    "deepseek-v4-flash": {"input": 0.0003, "output": 0.0012},  # 旧别名，按 Flash 计费
    "deepseek-v4-pro": {"input": 0.00132, "output": 0.00396},
    "mock": {
        "input": 0.0,
        "output": 0.0
    }
}

def calculate_llm_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate estimated cost in USD for LLM usage.
    """
    normalized_name = model_name.lower()
    
    # Matching logic
    matching_model = None
    for key in sorted(MODEL_PRICING.keys(), key=len, reverse=True):
        if key in normalized_name:
            matching_model = key
            break
    if matching_model is None:
        # 未知模型不静默按 0 计算：每个模型名只告警一次。
        if normalized_name not in _warned_models:
            _warned_models.add(normalized_name)
            logger.warning(
                "No pricing for model %r; cost is reported as 0. "
                "Add it to MODEL_PRICING.",
                model_name,
            )
        return 0.0


    rates = MODEL_PRICING[matching_model]
    input_cost = (input_tokens / 1000.0) * rates["input"]
    output_cost = (output_tokens / 1000.0) * rates["output"]
    
    return round(input_cost + output_cost, 6)
