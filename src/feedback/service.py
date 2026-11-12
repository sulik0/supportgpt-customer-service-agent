"""Feedback Pipeline 的持久化、关联、质量门控与导出服务。"""

import hashlib
import hmac
import json
import secrets
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models.db_models import (
    AgentReviewContext,
    AgentRun,
    AgentRunLink,
    AgentRunSnapshot,
    AgentSkillSelection,
    FeedbackEvent,
)
from src.observability.metrics import (
    FEEDBACK_EVENTS_TOTAL,
    TRAINING_CANDIDATES_TOTAL,
)
from src.observability.sanitization import redact_text, sanitize_value
from src.observability.tracing import get_request_id, get_tracer, observed_span

tracer = get_tracer(__name__)


class FeedbackService:
    """统一管理 Agent Run、反馈事件与训练样本候选。

    所有正文在写入反馈域前脱敏，业务流程故障不会反向影响 Agent 回复。
    """

    async def record_agent_run(
        self,
        db: AsyncSession,
        *,
        agent_output: Dict[str, Any],
        input_text: str,
        endpoint: str,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> AgentRun:
        """保存可被反馈、评测和审批引用的一次 Agent 执行快照。"""
        feedback_token = secrets.token_urlsafe(32)
        run = AgentRun(
            id=str(uuid.uuid4()),
            ticket_id=agent_output.get("ticket_id"),
            session_id_hash=self._identifier_hash(session_id) if session_id else None,
            request_id=self._sanitize_training_text(
                str(agent_output.get("request_id") or get_request_id() or "background")
            )[:128],
            trace_id=trace_id,
            feedback_token_hash=self._token_hash(feedback_token),
            endpoint=self._bounded(endpoint, 100, "/unknown"),
            workflow_version=self._bounded(
                settings.AGENT_WORKFLOW_VERSION, 100, "unknown"
            ),
            prompt_version=(
                str(agent_output["prompt_bundle_id"])
                if re.fullmatch(r"[0-9a-f]{64}", str(agent_output.get("prompt_bundle_id", "")))
                else self._bounded(settings.PROMPT_VERSION, 100, "unknown")
            ),
            model_provider=self._bounded(settings.LLM_PROVIDER, 50, "unknown"),
            model_name=self._model_name(),
            kb_version=self._bounded(agent_output.get("kb_version"), 50, "v1"),
            input_text=self._sanitize_training_text(input_text),
            output_text=self._sanitize_training_text(
                agent_output.get("suggested_response", "")
            ),
            workflow_path=sanitize_value(agent_output.get("workflow_path", [])),
            tool_calls=self._safe_tool_calls(agent_output.get("tool_calls", [])),
            citations=self._safe_citations(agent_output.get("context_citations", [])),
            qa_score=agent_output.get("qa_score"),
            hallucination_detected=bool(
                agent_output.get("hallucination_detected", False)
            ),
            escalation_recommended=bool(
                agent_output.get("escalation_recommended", False)
            ),
            approval_required=bool(agent_output.get("approval_required", False)),
            workflow_errors=sanitize_value(agent_output.get("errors", [])),
            tokens_input=int(agent_output.get("tokens_input", 0)),
            tokens_output=int(agent_output.get("tokens_output", 0)),
            latency_seconds=float(agent_output.get("latency_seconds", 0.0)),
        )
        db.add(run)
        await db.flush()
        db.add(
            AgentReviewContext(
                agent_run_id=run.id,
                escalation_reason=self._optional_text(
                    agent_output.get("escalation_reason"), 1000
                ),
                risk_level=self._bounded(
                    agent_output.get("risk_level"), 30, "low"
                ),
                risk_score=self._score(agent_output.get("risk_score"), 0.0),
                risk_reasons=self._safe_reason_list(
                    agent_output.get("risk_reasons", [])
                ),
                analyzer_confidence=self._score(
                    agent_output.get("analyzer_confidence"), 1.0
                ),
                response_requires_human=bool(
                    agent_output.get("response_requires_human", False)
                ),
                degradation_reasons=self._safe_reason_list(
                    agent_output.get("degradation_reasons", [])
                ),
            )
        )
        db.add(
            AgentRunSnapshot(
                agent_run_id=run.id,
                analyzer_strategy=self._node_strategy(
                    agent_output.get("analyzer_strategy")
                ),
                analyzer_result=sanitize_value(
                    {
                        "intent": str(agent_output.get("intent") or "unknown"),
                        "department": agent_output.get("department", "general"),
                        "priority": agent_output.get("priority", "medium"),
                        "sentiment": agent_output.get("sentiment", "neutral"),
                        "confidence": self._score(
                            agent_output.get("analyzer_confidence"), 0.0
                        ),
                    }
                ),
                qa_strategy=self._node_strategy(agent_output.get("qa_strategy")),
                qa_result=sanitize_value(
                    {
                        "score": self._score(agent_output.get("qa_score"), 0.0),
                        "hallucination_detected": bool(
                            agent_output.get("hallucination_detected", False)
                        ),
                        "citation_verified": bool(
                            agent_output.get("citation_verified", False)
                        ),
                        "response_grounded": bool(
                            agent_output.get("response_grounded", False)
                        ),
                        "response_requires_human": bool(
                            agent_output.get("response_requires_human", False)
                        ),
                    }
                ),
                decision_records=sanitize_value(
                    agent_output.get("decision_records", [])
                ),
            )
        )
        if agent_output.get("skill_name") not in {None, "", "unselected"}:
            db.add(
                AgentSkillSelection(
                    agent_run_id=run.id,
                    skill_name=self._bounded(
                        agent_output.get("skill_name"), 100, "unknown"
                    ),
                    skill_version=self._bounded(
                        agent_output.get("skill_version"), 50, "unknown"
                    ),
                    selection_strategy=self._bounded(
                        agent_output.get("selection_strategy"), 50, "unknown"
                    ),
                    registry_id=self._bounded(
                        agent_output.get("skill_registry_id"), 64, "unknown"
                    ),
                )
            )
            await db.flush()
        run._feedback_token = feedback_token
        return run

    async def link_entity(
        self,
        db: AsyncSession,
        *,
        agent_run_id: str,
        entity_type: str,
        entity_id: str | int,
    ) -> AgentRunLink:
        """幂等关联 Agent Run 与审批或其他外部业务记录。"""
        existing = await self._find_link(db, entity_type, str(entity_id))
        if existing:
            return existing
        link = AgentRunLink(
            agent_run_id=agent_run_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
        )
        try:
            async with db.begin_nested():
                db.add(link)
                await db.flush()
        except IntegrityError:
            existing = await self._find_link(db, entity_type, str(entity_id))
            if existing:
                return existing
            raise
        return link

    async def record_user_feedback(
        self,
        db: AsyncSession,
        *,
        agent_run_id: str,
        feedback_token: str,
        rating: int,
        comment: Optional[str],
        idempotency_key: str,
    ) -> FeedbackEvent:
        """采集用户评分，并通过幂等键避免客户端重试产生重复事件。"""
        run = await self.get_token_authorized_run(db, agent_run_id, feedback_token)
        # 一个 Run 只接收一条不可变用户反馈，服务端不信任客户端换键重放。
        external_ref = self._external_ref("user", agent_run_id)
        existing = await self._find_event(db, "user", external_ref)
        if existing:
            return existing

        eligible, reason = self._user_feedback_eligibility(run, rating)
        return await self._create_event(
            db,
            run=run,
            source="user",
            feedback_type="rating",
            external_ref=external_ref,
            rating=rating,
            comment=self._sanitize_training_text(comment) if comment else None,
            corrected_response=None,
            evaluation_metrics=None,
            evaluation_passed=None,
            training_eligible=eligible,
            exclusion_reason=reason,
        )

    async def record_human_correction(
        self,
        db: AsyncSession,
        *,
        approval_id: int,
        status_value: str,
        corrected_response: Optional[str],
        user_id: int,
    ) -> Optional[FeedbackEvent]:
        """把审批结果转换为可审计的人类偏好或修正反馈。"""
        link = await self._find_link(db, "approval", str(approval_id))
        if not link:
            return None
        run = await self.get_agent_run(db, link.agent_run_id)
        external_ref = f"approval:{approval_id}"
        existing = await self._find_event(db, "human_review", external_ref)
        if existing:
            return existing

        correction = (
            self._sanitize_training_text(corrected_response)
            if corrected_response
            else None
        )
        changed = bool(correction and correction.strip() != run.output_text.strip())
        safe_run = not run.hallucination_detected and not run.workflow_errors
        if status_value == "modified":
            eligible = bool(correction)
        elif status_value == "approved":
            eligible = bool(correction) and safe_run
        else:
            eligible = False
        reason = None
        if not eligible:
            if status_value == "approved" and not safe_run:
                reason = "unsafe_agent_run"
            else:
                reason = f"approval_status={status_value}"
        return await self._create_event(
            db,
            run=run,
            source="human_review",
            feedback_type="correction" if changed else status_value,
            external_ref=external_ref,
            rating=None,
            comment=None,
            corrected_response=correction,
            evaluation_metrics=None,
            evaluation_passed=None,
            training_eligible=eligible,
            exclusion_reason=reason,
            created_by_user_id=user_id,
        )

    async def record_evaluation(
        self,
        db: AsyncSession,
        *,
        agent_run_id: str,
        metrics: Dict[str, Any],
        passed: bool,
        external_ref: Optional[str] = None,
    ) -> FeedbackEvent:
        """关联在线或离线评测结果，为训练数据质量门控提供依据。"""
        run = await self.get_agent_run(db, agent_run_id)
        normalized_metrics = sanitize_value(metrics)
        external_key = (
            self._external_ref("evaluation", f"{agent_run_id}:{external_ref}")
            if external_ref
            else self._evaluation_ref(agent_run_id, normalized_metrics)
        )
        existing = await self._find_event_for_run(db, "evaluation", agent_run_id)
        if existing:
            history = []
            if isinstance(existing.evaluation_metrics, dict):
                history = list(existing.evaluation_metrics.get("_history", []))
                previous_metrics = {
                    key: value
                    for key, value in existing.evaluation_metrics.items()
                    if key != "_history"
                }
                history.append(
                    {
                        "sequence": existing.sequence or 1,
                        "passed": existing.evaluation_passed,
                        "metrics": previous_metrics,
                    }
                )
            normalized_metrics["_history"] = history[-19:]
            existing.external_ref = external_key
            existing.evaluation_metrics = normalized_metrics
            existing.evaluation_passed = passed
            existing.training_eligible = passed
            existing.exclusion_reason = None if passed else "evaluation_failed"
            existing.sequence = (existing.sequence or 0) + 1
            await db.flush()
            self._record_feedback_metric("evaluation", "quality_score", passed)
            return existing
        return await self._create_event(
            db,
            run=run,
            source="evaluation",
            feedback_type="quality_score",
            external_ref=external_key,
            rating=None,
            comment=None,
            corrected_response=None,
            evaluation_metrics=normalized_metrics,
            evaluation_passed=passed,
            training_eligible=passed,
            exclusion_reason=None if passed else "evaluation_failed",
            sequence=1,
        )

    async def get_token_authorized_run(
        self, db: AsyncSession, run_id: str, feedback_token: str
    ) -> AgentRun:
        """评分与公开摘要复用同一令牌校验。"""
        run = await self.get_agent_run(db, run_id)
        if not secrets.compare_digest(
            run.feedback_token_hash, self._token_hash(feedback_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid feedback token for this Agent run.",
            )
        return run

    async def get_agent_run(self, db: AsyncSession, run_id: str) -> AgentRun:
        """查询 Agent Run 及全部 Feedback Event。"""
        result = await db.execute(
            select(AgentRun)
            .options(
                selectinload(AgentRun.feedback_events),
                selectinload(AgentRun.skill_selection),
                selectinload(AgentRun.execution_snapshot),
                selectinload(AgentRun.review_context),
            )
            .where(AgentRun.id == run_id)
        )
        run = result.scalars().first()
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent run {run_id} not found.",
            )
        return run

    async def list_agent_runs(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        ticket_id: int | None = None,
    ) -> tuple[List[AgentRun], int]:
        """按工单精确筛选，并按最新优先分页返回 Agent Run。"""
        filters = [AgentRun.ticket_id == ticket_id] if ticket_id is not None else []
        total_result = await db.execute(
            select(func.count()).select_from(AgentRun).where(*filters)
        )
        total = int(total_result.scalar_one())
        result = await db.execute(
            select(AgentRun)
            .options(selectinload(AgentRun.skill_selection))
            .where(*filters)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), total

    async def export_training_candidates(
        self, db: AsyncSession, output_dir: Path
    ) -> Dict[str, Any]:
        """导出通过质量门控的 SFT 与 DPO JSONL 候选集。"""
        await db.flush()
        db.expire_all()
        result = await db.execute(
            select(AgentRun)
            .options(
                selectinload(AgentRun.feedback_events),
                selectinload(AgentRun.skill_selection),
            )
            .order_by(AgentRun.created_at.asc())
        )
        runs = list(result.scalars().unique().all())
        sft_rows: List[Dict[str, Any]] = []
        dpo_rows: List[Dict[str, Any]] = []
        seen_sft = set()
        seen_dpo = set()

        for run in runs:
            sft, dpo = self._build_training_rows(run)
            if sft:
                key = self._row_hash(sft)
                if key not in seen_sft:
                    seen_sft.add(key)
                    sft_rows.append(sft)
            if dpo:
                key = self._row_hash(dpo)
                if key not in seen_dpo:
                    seen_dpo.add(key)
                    dpo_rows.append(dpo)

        output_dir.mkdir(parents=True, exist_ok=True)
        sft_path = output_dir / "sft_candidates.jsonl"
        dpo_path = output_dir / "dpo_candidates.jsonl"
        manifest_path = output_dir / "manifest.json"
        self._write_jsonl(sft_path, sft_rows)
        self._write_jsonl(dpo_path, dpo_rows)
        self._write_json(
            manifest_path,
            {
                "schema_version": "1.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "quality_gate": {
                    "minimum_user_rating": settings.FEEDBACK_TRAINING_MIN_RATING,
                    "minimum_qa_score": settings.FEEDBACK_TRAINING_MIN_QA_SCORE,
                    "minimum_rag_score": settings.FEEDBACK_TRAINING_MIN_RAG_SCORE,
                    "evaluation_required_for_user_feedback": True,
                },
                "datasets": {
                    "sft": {"path": sft_path.name, "count": len(sft_rows)},
                    "dpo": {"path": dpo_path.name, "count": len(dpo_rows)},
                },
            },
        )
        try:
            TRAINING_CANDIDATES_TOTAL.add(len(sft_rows), {"dataset_type": "sft"})
            TRAINING_CANDIDATES_TOTAL.add(len(dpo_rows), {"dataset_type": "dpo"})
        except Exception:
            pass
        return {
            "sft_path": str(sft_path),
            "dpo_path": str(dpo_path),
            "manifest_path": str(manifest_path),
            "sft_count": len(sft_rows),
            "dpo_count": len(dpo_rows),
        }

    async def _create_event(
        self,
        db: AsyncSession,
        *,
        run: AgentRun,
        source: str,
        feedback_type: str,
        external_ref: str,
        rating: Optional[int],
        comment: Optional[str],
        corrected_response: Optional[str],
        evaluation_metrics: Optional[Dict[str, Any]],
        evaluation_passed: Optional[bool],
        training_eligible: bool,
        exclusion_reason: Optional[str],
        created_by_user_id: Optional[int] = None,
        sequence: Optional[int] = None,
    ) -> FeedbackEvent:
        with observed_span(
            tracer,
            "feedback.capture",
            {
                "feedback.source": source,
                "feedback.type": feedback_type,
                "agent.run_id": run.id,
                "trace.id": run.trace_id,
            },
        ):
            event = FeedbackEvent(
                id=str(uuid.uuid4()),
                agent_run_id=run.id,
                ticket_id=run.ticket_id,
                trace_id=run.trace_id,
                sequence=sequence,
                source=source,
                feedback_type=feedback_type,
                external_ref=external_ref,
                rating=rating,
                comment=comment,
                original_response=run.output_text,
                corrected_response=corrected_response,
                evaluation_metrics=evaluation_metrics,
                evaluation_passed=evaluation_passed,
                training_eligible=training_eligible,
                exclusion_reason=exclusion_reason,
                created_by_user_id=created_by_user_id,
            )
            try:
                async with db.begin_nested():
                    db.add(event)
                    await db.flush()
            except IntegrityError:
                existing = await self._find_event(db, source, external_ref)
                if not existing:
                    existing = await self._find_event_for_run(db, source, run.id)
                if existing:
                    return existing
                raise
            self._record_feedback_metric(source, feedback_type, training_eligible)
            return event

    def _record_feedback_metric(
        self, source: str, feedback_type: str, training_eligible: bool
    ) -> None:
        try:
            FEEDBACK_EVENTS_TOTAL.add(
                1,
                {
                    "source": source,
                    "type": feedback_type,
                    "training_eligible": str(training_eligible).lower(),
                },
            )
        except Exception:
            pass

    async def _find_event(
        self, db: AsyncSession, source: str, external_ref: str
    ) -> Optional[FeedbackEvent]:
        result = await db.execute(
            select(FeedbackEvent).where(
                FeedbackEvent.source == source,
                FeedbackEvent.external_ref == external_ref,
            )
        )
        return result.scalars().first()

    async def _find_event_for_run(
        self, db: AsyncSession, source: str, agent_run_id: str
    ) -> Optional[FeedbackEvent]:
        result = await db.execute(
            select(FeedbackEvent).where(
                FeedbackEvent.source == source,
                FeedbackEvent.agent_run_id == agent_run_id,
            )
        )
        return result.scalars().first()

    async def _find_link(
        self, db: AsyncSession, entity_type: str, entity_id: str
    ) -> Optional[AgentRunLink]:
        result = await db.execute(
            select(AgentRunLink).where(
                AgentRunLink.entity_type == entity_type,
                AgentRunLink.entity_id == entity_id,
            )
        )
        return result.scalars().first()

    def _build_training_rows(
        self, run: AgentRun
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        feedback_events = list(run.feedback_events or [])
        human_events = [e for e in feedback_events if e.source == "human_review"]
        user_events = [e for e in feedback_events if e.source == "user"]
        eval_events = [e for e in feedback_events if e.source == "evaluation"]
        best_human = next(
            (
                event
                for event in sorted(
                    human_events,
                    key=lambda row: (row.created_at, row.id),
                    reverse=True,
                )
                if event.training_eligible and event.corrected_response
            ),
            None,
        )
        positive_user = any(
            event.training_eligible
            and (event.rating or 0) >= settings.FEEDBACK_TRAINING_MIN_RATING
            for event in user_events
        )
        latest_evaluation = max(
            eval_events, key=lambda row: (row.created_at, row.id), default=None
        )
        evaluation_passed = bool(
            latest_evaluation
            and latest_evaluation.training_eligible
            and latest_evaluation.evaluation_passed
        )
        safe_run = not run.hallucination_detected and not run.workflow_errors
        accepted_response = (
            best_human.corrected_response
            if best_human
            else (
                run.output_text
                if positive_user and evaluation_passed and safe_run
                else None
            )
        )
        metadata = {
            "agent_run_id": run.id,
            "trace_id": run.trace_id,
            "prompt_version": run.prompt_version,
            "workflow_version": run.workflow_version,
            "model_provider": run.model_provider,
            "model_name": run.model_name,
            "kb_version": run.kb_version,
            "skill_name": (
                run.skill_selection.skill_name if run.skill_selection else None
            ),
            "skill_version": (
                run.skill_selection.skill_version if run.skill_selection else None
            ),
        }
        sft = (
            {
                "instruction": run.input_text,
                "response": accepted_response,
                "metadata": metadata,
            }
            if accepted_response
            else None
        )
        dpo = (
            {
                "prompt": run.input_text,
                "chosen": best_human.corrected_response,
                "rejected": run.output_text,
                "metadata": metadata,
            }
            if best_human
            and best_human.corrected_response.strip() != run.output_text.strip()
            else None
        )
        return sft, dpo

    def _user_feedback_eligibility(
        self, run: AgentRun, rating: int
    ) -> tuple[bool, Optional[str]]:
        if rating < settings.FEEDBACK_TRAINING_MIN_RATING:
            return False, "rating_below_threshold"
        if run.hallucination_detected:
            return False, "hallucination_detected"
        if run.workflow_errors:
            return False, "workflow_errors"
        if (run.qa_score or 0.0) < settings.FEEDBACK_TRAINING_MIN_QA_SCORE:
            return False, "qa_score_below_threshold"
        return True, None

    def _model_name(self) -> str:
        if settings.LLM_MODEL_NAME:
            return self._bounded(settings.LLM_MODEL_NAME, 100, "unknown")
        if settings.LLM_PROVIDER == "azure":
            return self._bounded(settings.AZURE_OPENAI_DEPLOYMENT, 100, "azure-openai")
        if settings.LLM_PROVIDER == "openai":
            return "gpt-4-turbo"
        return "mock-support-v1"

    def _bounded(self, value: Any, limit: int, fallback: str) -> str:
        text = self._sanitize_training_text(str(value or fallback)).strip()
        return (text or fallback)[:limit]

    def _optional_text(self, value: Any, limit: int) -> Optional[str]:
        """仅保存有内容的脱敏文本，避免空理由污染审批快照。"""
        text = self._sanitize_training_text(str(value or "")).strip()
        return text[:limit] if text else None

    def _safe_reason_list(self, values: Any) -> List[str]:
        """将风险与降级原因限长、去重后持久化。"""
        if not isinstance(values, (list, tuple, set)):
            return []
        reasons: List[str] = []
        for value in values:
            reason = self._optional_text(value, 300)
            if reason and reason not in reasons:
                reasons.append(reason)
            if len(reasons) >= 20:
                break
        return reasons

    @staticmethod
    def _score(value: Any, default: float) -> float:
        try:
            return round(min(max(float(value), 0.0), 1.0), 4)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _node_strategy(value: Any) -> str:
        """节点策略只允许固定枚举，防止外部文本进入运行快照。"""
        strategy = str(value or "not_run").strip().lower()
        return strategy if strategy in {"rule", "jev", "llm", "not_run"} else "unknown"

    def _sanitize_training_text(self, text: Optional[str]) -> str:
        """对训练候选文本执行稳定占位符脱敏。"""
        return redact_text(text or "")

    def _safe_tool_calls(self, calls: List[Any]) -> List[Dict[str, Any]]:
        allowed = {"tool_name", "role", "allowed", "status", "latency_ms", "mocked"}
        return [
            sanitize_value(
                {key: value for key, value in call.items() if key in allowed}
            )
            for call in calls
            if isinstance(call, dict)
        ]

    def _safe_citations(self, citations: List[Any]) -> List[Dict[str, Any]]:
        allowed = {"source", "text", "score", "version", "category", "doc_id"}
        rows = []
        for citation in citations:
            if isinstance(citation, dict):
                payload = {
                    key: value for key, value in citation.items() if key in allowed
                }
            else:
                payload = {
                    "source": getattr(citation, "source", ""),
                    "text": getattr(citation, "text", ""),
                    "score": getattr(citation, "score", None),
                    "version": getattr(citation, "version", None),
                }
            rows.append(sanitize_value(payload))
        return rows

    def _external_ref(self, source: str, key: str) -> str:
        digest = hashlib.sha256(f"{source}:{key}".encode()).hexdigest()
        return f"{source}:{digest}"

    def _token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _identifier_hash(self, value: str) -> str:
        return hmac.new(
            settings.JWT_SECRET.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _evaluation_ref(self, run_id: str, metrics: Dict[str, Any]) -> str:
        digest = hashlib.sha256(
            json.dumps(metrics, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"evaluation:{run_id}:{digest}"

    def _row_hash(self, row: Dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def _write_jsonl(self, path: Path, rows: List[Dict[str, Any]]) -> None:
        text = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
        self._atomic_write(path, text)

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self._atomic_write(path, text)

    def _atomic_write(self, path: Path, text: str) -> None:
        """原子替换导出文件，避免中断留下半成品。"""
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)


feedback_service = FeedbackService()
