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
        "version": "support-v1.1",
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
                    "an irreversible action. If evidence is insufficient, say human review "
                    f"is needed. {RESOLUTION_LANGUAGE_POLICY} {RESOLUTION_OUTPUT_POLICY}"
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
                    'Return only JSON: {"score":0.0,"hallucination_detected":false,'
                    '"citation_verified":false}. Judge whether the answer is supported by evidence.'
                ),
            },
        },
    }
