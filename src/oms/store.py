"""以数据库事务保存幂等结果，并在多进程之间校验参数冲突。"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.oms.contracts import RefundInput


class OMSBase(DeclarativeBase):
    """与客服数据库模型隔离，避免参考服务创建业务后台表。"""


class OMSOrder(OMSBase):
    """订单金额及归属来自 OMS 自身，调用方不能覆盖。"""

    __tablename__ = "reference_oms_orders"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="oms_positive_amount"),)
    order_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    refundable: Mapped[bool] = mapped_column(Boolean, default=True)


class OMSRefund(OMSBase):
    """一个订单只允许一个全额申请；重复幂等键返回首次持久化结果。"""

    __tablename__ = "reference_oms_refunds"
    refund_request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    order_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("reference_oms_orders.order_id"), unique=True
    )
    receipt: Mapped[dict] = mapped_column(JSON)
    compensated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class OMSCompensation(OMSBase):
    """补偿也有独立幂等键及不可变回执，防止重复撤销。"""

    __tablename__ = "reference_oms_compensations"
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    refund_request_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("reference_oms_refunds.refund_request_id"), unique=True
    )
    payload_hash: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSON)


class OMSConflict(Exception):
    """确定性业务拒绝；异常消息不带客户参数或凭据。"""

    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class OMSStore:
    """数据库提交先于 HTTP 返回，响应丢失后可从主库查询回执。"""

    def __init__(self, sessions, encryption_key: str):
        self.sessions = sessions
        self._cipher = Fernet(encryption_key.encode())
        self._hash_key = encryption_key.encode()

    @staticmethod
    def digest(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def payload_hash(self, payload: dict) -> str:
        data = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        return hmac.new(self._hash_key, data, hashlib.sha256).hexdigest()

    @staticmethod
    def _match(record, payload_hash):
        if not hmac.compare_digest(record.payload_hash, payload_hash):
            raise OMSConflict("idempotency_parameter_conflict")
        return dict(record.receipt)

    async def create(self, payload: RefundInput, key: str) -> dict:
        """订单行锁串行化同订单申请，唯一约束兜底跨订单的幂等键竞争。"""
        digest, fingerprint = self.digest(key), self.payload_hash(payload.model_dump())
        async with self.sessions() as db:
            try:
                async with db.begin():
                    existing = await db.scalar(
                        select(OMSRefund).where(OMSRefund.key_digest == digest)
                    )
                    if existing:
                        return self._match(existing, fingerprint)
                    order = await db.scalar(
                        select(OMSOrder)
                        .where(OMSOrder.order_id == payload.order_id)
                        .with_for_update()
                    )
                    if order is None or order.customer_id != payload.customer_id:
                        raise OMSConflict("order_not_available", 422)
                    existing = await db.scalar(
                        select(OMSRefund).where(OMSRefund.key_digest == digest)
                    )
                    if existing:
                        return self._match(existing, fingerprint)
                    if not order.refundable or order.amount_minor <= 0:
                        raise OMSConflict("order_not_refundable", 422)
                    if await db.scalar(
                        select(OMSRefund).where(OMSRefund.order_id == order.order_id)
                    ):
                        raise OMSConflict("order_refund_already_requested")
                    receipt = {
                        "refund_request_id": "REF-" + uuid.uuid4().hex,
                        "status": "submitted",
                        "message": "退款申请已保存到 OMS 参考服务；未执行真实资金退款。",
                        "amount_minor": order.amount_minor,
                        "currency": order.currency,
                        "reference_implementation": True,
                        "funds_moved": False,
                    }
                    encrypted = self._cipher.encrypt(
                        json.dumps(payload.model_dump(), ensure_ascii=False).encode()
                    ).decode()
                    db.add(
                        OMSRefund(
                            refund_request_id=receipt["refund_request_id"],
                            key_digest=digest,
                            payload_hash=fingerprint,
                            encrypted_payload=encrypted,
                            order_id=order.order_id,
                            receipt=receipt,
                        )
                    )
                return receipt
            except IntegrityError:
                await db.rollback()
                winner = await db.scalar(
                    select(OMSRefund).where(OMSRefund.key_digest == digest)
                )
                if winner:
                    return self._match(winner, fingerprint)
                duplicate = await db.scalar(
                    select(OMSRefund).where(OMSRefund.order_id == payload.order_id)
                )
                if duplicate:
                    raise OMSConflict("order_refund_already_requested")
                raise

    async def reconcile(self, key: str) -> dict:
        """只读取主库的已提交申请；没有记录不等于确定未执行。"""
        async with self.sessions() as db:
            record = await db.scalar(
                select(OMSRefund).where(OMSRefund.key_digest == self.digest(key))
            )
            if record is None:
                return {
                    "status": "pending",
                    "found": False,
                    "authoritative": True,
                    "result": None,
                }
            result = dict(record.receipt)
            if record.compensated:
                result["status"] = "compensated"
            return {
                "status": "succeeded",
                "found": True,
                "authoritative": True,
                "result": result,
            }

    async def compensate(self, original_key: str, compensation_key: str) -> dict:
        """补偿回执和申请状态在同一个事务中提交。"""
        digest = self.digest(compensation_key)
        fingerprint = self.payload_hash(
            {"original_key_digest": self.digest(original_key)}
        )
        async with self.sessions() as db:
            try:
                async with db.begin():
                    prior = await db.get(OMSCompensation, digest)
                    if prior:
                        return self._match(prior, fingerprint)
                    refund = await db.scalar(
                        select(OMSRefund)
                        .where(OMSRefund.key_digest == self.digest(original_key))
                        .with_for_update()
                    )
                    if refund is None:
                        raise OMSConflict("refund_not_found", 422)
                    prior = await db.get(OMSCompensation, digest)
                    if prior:
                        return self._match(prior, fingerprint)
                    if refund.compensated:
                        raise OMSConflict("refund_already_compensated")
                    receipt = {
                        "status": "compensated",
                        "refund_request_id": refund.refund_request_id,
                        "reference_implementation": True,
                        "funds_moved": False,
                    }
                    refund.compensated = True
                    db.add(
                        OMSCompensation(
                            key_digest=digest,
                            refund_request_id=refund.refund_request_id,
                            payload_hash=fingerprint,
                            receipt=receipt,
                        )
                    )
                return receipt
            except IntegrityError:
                await db.rollback()
                prior = await db.get(OMSCompensation, digest)
                if prior:
                    return self._match(prior, fingerprint)
                committed = await db.scalar(
                    select(OMSRefund).where(
                        OMSRefund.key_digest == self.digest(original_key)
                    )
                )
                if committed and committed.compensated:
                    raise OMSConflict("refund_already_compensated")
                raise
