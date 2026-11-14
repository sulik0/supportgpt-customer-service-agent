"""保留当前线上行为的首个 Prompt Bundle。"""

from src.models.intents import intent_prompt_guide, intent_prompt_values


RESOLUTION_LANGUAGE_POLICY = (
    "Reply in the language used in the customer's current Description. "
    "If the current Description explicitly asks for a different response language, "
    "use the requested language instead. Do not choose the response language from the "
    "subject, retrieved context, tool results, or earlier messages."
)

RESOLUTION_OUTPUT_POLICY = (
    "Write a complete, concise reply in 3-4 short sentences (about 180 Chinese characters "
    "or 100 English words). Prioritize: current status, current exception, then the next "
    "step supported by evidence. Add policy only when directly needed for this question. "
    "Do not expand into hypothetical future scenarios or unrelated historical tickets. "
    "A recommended next_step is advice, not proof that an action has already been executed. "
    "Finish every sentence and stay well within the output token budget."
)


def default_payload() -> dict:
    """冻结分类枚举和说明，确保 Hash 包含实际使用的静态上下文。"""
    return {
        "schema_version": "1.0",
        "version": "support-v1.3",
        "templates": {
            "analyzer": {
                "system": "Classify customer support tickets. Output compact JSON only.",
                "user": (
                    "Classify this support ticket by the business meaning below. Distinguish a "
                    "request to perform an operation from a request explaining policy or navigation. "
                    "Payment, invoice and refund questions are billing_dispute. A current API error, "
                    "timeout or outage is outage_report, not information_request. Return only JSON "
                    "with exactly: intent, priority, department, sentiment, confidence_score. "
                    f"intent must be one of {intent_prompt_values()}. Taxonomy:\n"
                    f"{intent_prompt_guide()}\nTicket: $text"
                ),
            },
            "resolver": {
                "system": (
                    "Answer using only the supplied context. Never invent policy or promise "
                    "an irreversible action. If authoritative evidence for a business answer is "
                    "insufficient, ask for necessary details or recommend human review when a "
                    "verified business decision is required. For harmless requests outside customer "
                    "support capabilities, briefly explain the scope without claiming the request "
                    "has been transferred to a human. "
                    f"{RESOLUTION_LANGUAGE_POLICY} {RESOLUTION_OUTPUT_POLICY}"
                ),
                "user": (
                    "Subject: $subject\nDescription: $description\n\n"
                    "Relevant Context:\n$context\n\n"
                    "Write only the final customer reply. Be concise, actionable, and cite the "
                    "provided source labels for policy claims. Do not explain your reasoning."
                ),
            },
            "qa": {
                "system": "Verify answer grounding. Output only the requested compact JSON.",
                "user": (
                    "Question: $query\nEvidence: $context\nAnswer: $response\n"
                    "Judge whether the answer is supported by the evidence. Return only JSON "
                    'with exactly these keys: "score" (a number from 0.0 to 1.0: 1.0 means every '
                    "factual claim is supported, 0.0 means the main claims are unsupported), "
                    '"hallucination_detected" (true only if the answer states a fact that the '
                    'evidence contradicts or does not contain), "citation_verified" (true if the '
                    "cited sources contain the claims). An answer that only asks for missing "
                    "details, or says it cannot confirm something, makes no unsupported claim: "
                    "score it 0.8 or higher with hallucination_detected false."
                ),
            },
        },
    }
