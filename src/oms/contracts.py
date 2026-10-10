"""Agent Gateway 与参考 OMS 共享的最小 HTTP 契约。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RefundInput(BaseModel):
    """金额不由调用方传入，必须从 OMS 订单中取得。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: str = Field(min_length=1, max_length=80)
    order_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=2, max_length=500)


class KeyInput(BaseModel):
    """查询和补偿只按原申请幂等键定位，不接受 Agent 推测结果。"""

    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(
        min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:\-]+$"
    )


class RefundReceipt(BaseModel):
    """submitted 只表示申请已持久化，不表示退款到账。"""

    refund_request_id: str = Field(min_length=1)
    status: Literal["submitted", "compensated"]
    message: str
    amount_minor: int = Field(gt=0)
    currency: str
    reference_implementation: Literal[True]
    funds_moved: Literal[False]


class ReconciliationReceipt(BaseModel):
    """未查到记录仍为 pending，不能据此认定远端写入失败。"""

    status: Literal["succeeded", "pending"]
    found: bool
    authoritative: Literal[True]
    result: RefundReceipt | None = None

    @model_validator(mode="after")
    def validate_result(self):
        if self.found != (self.result is not None) or self.found != (
            self.status == "succeeded"
        ):
            raise ValueError("Inconsistent reconciliation receipt")
        return self


class CompensationReceipt(BaseModel):
    """补偿仅撤销参考服务中的申请，不模拟实际资金冲正。"""

    status: Literal["compensated"]
    refund_request_id: str = Field(min_length=1)
    reference_implementation: Literal[True]
    funds_moved: Literal[False]
