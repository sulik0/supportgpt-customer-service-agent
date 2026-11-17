"""Jev 录制回放：PR 门禁读录音，不调用 Jev、不需要 Key、结果固定。

录音键 = operation + 问题集版本 + 问题集内容哈希 + 脱敏后 State 的哈希。
State 里每次运行都会变的部分（进程内随机的业务 ID 别名、日期时间、UUID）
先换成固定占位符再算哈希，其余内容一变就算缺录音，需要重录。

只回放分类（intent_classification）。门禁的回复是 Mock 固定文案，让真实 Jev
评判它没有意义，所以 QA 评判不录不放，门禁里 QA 照旧走 Mock；QA 的真实质量
由真实模型回归检查。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from src.decision.models import DecisionAnswer, DecisionResult
from src.decision.provider import DecisionProvider, _sanitize_decision_state


RECORDING_SCHEMA_VERSION = 1
REPLAY_PROVIDER_NAME = "jev_replay"
MISSING_ERROR_CODE = "replay_missing"
REPLAYED_OPERATIONS = frozenset({"intent_classification"})


def _not_replayed(operation: str, question_set_version: str) -> DecisionResult:
    """不回放的操作按 Jev 关闭处理，业务层走原有回退路径。"""
    return DecisionResult(
        enabled=False,
        available=False,
        provider=REPLAY_PROVIDER_NAME,
        operation=operation,
        question_set_version=question_set_version,
        model="disabled",
        error_code="disabled",
    )

_ALIAS = re.compile(r"\[BUSINESS_ID_[0-9a-f]{16}\]")
_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")


def _stable_text(encoded: str) -> str:
    """把每次运行都不同的片段换成固定占位符。"""
    aliases: dict[str, str] = {}

    def alias(match: re.Match[str]) -> str:
        return aliases.setdefault(match.group(0), f"[BUSINESS_ID_{len(aliases) + 1}]")

    encoded = _ALIAS.sub(alias, encoded)
    encoded = _UUID.sub("<UUID>", encoded)
    return _DATETIME.sub("<DATETIME>", encoded)


def stable_state_text(state: Any) -> str:
    """与 Jev 实际收到的脱敏 State 对应、跨运行稳定的文本。"""
    safe_state = _sanitize_decision_state(state)
    encoded = json.dumps(safe_state, ensure_ascii=False, sort_keys=True, default=str)
    return _stable_text(encoded)


def recording_key(
    *,
    operation: str,
    question_set_version: str,
    questions: Mapping[str, Mapping[str, Any]],
    state: Any,
) -> str:
    """问题集内容和 State 任一变化都会得到新键。"""
    questions_text = json.dumps(questions, ensure_ascii=False, sort_keys=True)
    payload = "\n".join(
        [
            operation,
            question_set_version,
            hashlib.sha256(questions_text.encode()).hexdigest(),
            stable_state_text(state),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _preview(state: Any) -> str:
    """录音文件里留一小段输入，便于人工核对；只含脱敏文本。"""
    if isinstance(state, Mapping):
        text = state.get("current_ticket") or state.get("customer_question") or ""
    else:
        text = ""
    text = " ".join(_stable_text(str(text)).split())
    return text[-160:]


def answer_to_record(answer: DecisionAnswer) -> dict[str, Any]:
    return {
        "kind": answer.kind,
        "value": answer.value,
        "probabilities": dict(sorted(answer.probabilities.items())),
        "confidence": answer.confidence,
    }


def result_to_record(result: DecisionResult, state: Any) -> dict[str, Any]:
    return {
        "operation": result.operation,
        "question_set_version": result.question_set_version,
        "model": result.model,
        "preview": _preview(state),
        "answers": {
            name: answer_to_record(answer)
            for name, answer in sorted(result.answers.items())
        },
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def record_to_result(record: Mapping[str, Any]) -> DecisionResult:
    answers = {
        str(name): DecisionAnswer(
            kind=str(raw["kind"]),
            value=raw["value"],
            probabilities=dict(raw.get("probabilities") or {}),
            confidence=raw.get("confidence"),
        )
        for name, raw in dict(record.get("answers") or {}).items()
    }
    return DecisionResult(
        enabled=True,
        available=True,
        provider=REPLAY_PROVIDER_NAME,
        operation=str(record["operation"]),
        question_set_version=str(record["question_set_version"]),
        model=str(record.get("model", "unknown")),
        answers=answers,
        input_tokens=int(record.get("input_tokens", 0) or 0),
        output_tokens=int(record.get("output_tokens", 0) or 0),
    )


def load_recording(path: Path) -> dict[str, Any]:
    """文件不存在时返回空录音；格式不对直接报错。"""
    if not path.exists():
        return {"schema_version": RECORDING_SCHEMA_VERSION, "entries": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != RECORDING_SCHEMA_VERSION:
        raise ValueError(f"Unsupported decision recording schema: {path}")
    if not isinstance(payload.get("entries"), dict):
        raise ValueError(f"Decision recording has no entries: {path}")
    return payload


def recording_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@dataclass
class MissingRecording:
    operation: str
    question_set_version: str
    key: str
    preview: str


class ReplayDecisionProvider(DecisionProvider):
    """按录音返回 Jev 结果；缺录音时返回不可用并登记，由门禁判失败。"""

    name = REPLAY_PROVIDER_NAME

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.entries: dict[str, Any] = dict(load_recording(self.path)["entries"])
        self.missing: list[MissingRecording] = []
        self.hits: set[str] = set()

    async def evaluate(
        self,
        *,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        operation: str,
        question_set_version: str,
    ) -> DecisionResult:
        if operation not in REPLAYED_OPERATIONS:
            return _not_replayed(operation, question_set_version)
        key = recording_key(
            operation=operation,
            question_set_version=question_set_version,
            questions=questions,
            state=state,
        )
        record = self.entries.get(key)
        if record is None:
            self.missing.append(
                MissingRecording(
                    operation=operation,
                    question_set_version=question_set_version,
                    key=key,
                    preview=_preview(state),
                )
            )
            return DecisionResult(
                enabled=True,
                available=False,
                provider=self.name,
                operation=operation,
                question_set_version=question_set_version,
                model="replay",
                error_code=MISSING_ERROR_CODE,
            )
        self.hits.add(key)
        return record_to_result(record)


@dataclass
class RecordingStats:
    replayed: int = 0
    recorded: int = 0
    failed: list[MissingRecording] = field(default_factory=list)


class RecordingDecisionProvider(DecisionProvider):
    """录制用：已有录音直接复用，缺的才真实调用 Jev 并保存。"""

    name = "jev"

    def __init__(
        self,
        live: DecisionProvider | None,
        existing: Mapping[str, Any],
        *,
        refresh: bool = False,
    ) -> None:
        self.live = live
        self.existing = dict(existing)
        self.refresh = refresh
        self.used: dict[str, Any] = {}
        self.stats = RecordingStats()

    async def evaluate(
        self,
        *,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        operation: str,
        question_set_version: str,
    ) -> DecisionResult:
        if operation not in REPLAYED_OPERATIONS:
            return _not_replayed(operation, question_set_version)
        key = recording_key(
            operation=operation,
            question_set_version=question_set_version,
            questions=questions,
            state=state,
        )
        if key in self.used:
            return record_to_result(self.used[key])
        if not self.refresh and key in self.existing:
            self.used[key] = self.existing[key]
            self.stats.replayed += 1
            return record_to_result(self.existing[key])
        missing = MissingRecording(
            operation=operation,
            question_set_version=question_set_version,
            key=key,
            preview=_preview(state),
        )
        if self.live is None:
            # dry-run：只统计需要真实调用的次数。
            self.stats.failed.append(missing)
            return DecisionResult(
                enabled=True,
                available=False,
                provider="dry_run",
                operation=operation,
                question_set_version=question_set_version,
                model="dry_run",
                error_code=MISSING_ERROR_CODE,
            )
        result = await self.live.evaluate(
            state=state,
            questions=questions,
            operation=operation,
            question_set_version=question_set_version,
        )
        if not result.available:
            self.stats.failed.append(missing)
            return result
        self.used[key] = result_to_record(result, state)
        self.stats.recorded += 1
        return result

    async def aclose(self) -> None:
        if self.live is not None:
            await self.live.aclose()
