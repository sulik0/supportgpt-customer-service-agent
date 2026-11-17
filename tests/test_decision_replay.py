"""Jev 录制回放：录音键跨运行稳定，缺录音必须被登记。"""

import json

import pytest

import src.observability.sanitization as sanitization
from src.decision.models import DecisionAnswer, DecisionResult
from src.decision.questions import INTENT_QUESTION_SET_VERSION, intent_questions
from src.decision.replay import (
    MISSING_ERROR_CODE,
    RecordingDecisionProvider,
    ReplayDecisionProvider,
    recording_key,
)


def _key(state):
    return recording_key(
        operation="intent_classification",
        question_set_version=INTENT_QUESTION_SET_VERSION,
        questions=intent_questions(),
        state=state,
    )


def test_key_ignores_process_alias_and_timestamps(monkeypatch):
    state = {
        "customer_question": "Where is order ORD-7001?",
        "evidence": ['{"order_id": "ORD-7001", "order_date": "2026-10-28T09:15:02"}'],
    }
    first = _key(state)
    # 换一个进程内别名密钥和日期，相当于另一次运行。
    monkeypatch.setattr(sanitization, "_ALIAS_KEY", b"x" * 32)
    shifted = {
        **state,
        "evidence": ['{"order_id": "ORD-7001", "order_date": "2026-10-29T11:00:00"}'],
    }
    assert _key(shifted) == first


def test_key_changes_with_text_or_questions():
    base = _key({"current_ticket": "退款多久到账？"})
    assert _key({"current_ticket": "请帮我退款。"}) != base
    changed = intent_questions()
    changed["intent"] = {**changed["intent"], "instructions": "changed"}
    assert (
        recording_key(
            operation="intent_classification",
            question_set_version=INTENT_QUESTION_SET_VERSION,
            questions=changed,
            state={"current_ticket": "退款多久到账？"},
        )
        != base
    )


def _result():
    return DecisionResult(
        enabled=True,
        available=True,
        provider="jev",
        operation="intent_classification",
        question_set_version=INTENT_QUESTION_SET_VERSION,
        model="jev-test",
        answers={
            "intent": DecisionAnswer(
                kind="choice",
                value="billing_dispute",
                probabilities={"billing_dispute": 1.0},
                confidence=0.9,
            )
        },
    )


class _Live:
    calls = 0

    async def evaluate(self, **kwargs):
        self.calls += 1
        return _result()

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_record_then_replay_round_trip(tmp_path):
    live = _Live()
    recorder = RecordingDecisionProvider(live, {})
    args = dict(
        state={"current_ticket": "退款多久到账？"},
        questions=intent_questions(),
        operation="intent_classification",
        question_set_version=INTENT_QUESTION_SET_VERSION,
    )
    await recorder.evaluate(**args)
    await recorder.evaluate(**args)
    assert live.calls == 1 and recorder.stats.recorded == 1

    path = tmp_path / "rec.json"
    path.write_text(
        json.dumps({"schema_version": 1, "entries": recorder.used}), encoding="utf-8"
    )
    replay = ReplayDecisionProvider(path)
    hit = await replay.evaluate(**args)
    assert hit.available and hit.answers["intent"].value == "billing_dispute"
    assert hit.provider == "jev_replay"

    miss = await replay.evaluate(**{**args, "state": {"current_ticket": "别的问题"}})
    assert not miss.available and miss.error_code == MISSING_ERROR_CODE
    assert len(replay.missing) == 1


@pytest.mark.asyncio
async def test_qa_judgment_is_not_replayed(tmp_path):
    replay = ReplayDecisionProvider(tmp_path / "missing.json")
    result = await replay.evaluate(
        state={"customer_question": "q", "evidence": [], "answer": "a"},
        questions={},
        operation="response_judgment",
        question_set_version="qa",
    )
    assert result.enabled is False and not replay.missing


def test_replay_provider_rejected_in_production():
    from src.config import Settings

    with pytest.raises(ValueError, match="jev_replay"):
        Settings(APP_ENV="production", DECISION_PROVIDER="jev_replay")
