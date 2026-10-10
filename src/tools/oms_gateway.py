"""将参考 OMS 的 HTTP 契约适配到现有 Tool/Outbox 接口。"""

import httpx
from pydantic import ValidationError

from src.oms.contracts import (
    CompensationReceipt,
    KeyInput,
    ReconciliationReceipt,
    RefundInput,
    RefundReceipt,
)


class OMSRejectedError(RuntimeError):
    """只有已确认未执行的拒绝才作为确定性失败返回。"""

    def __init__(self, status_code: int, code: str = "request_rejected"):
        self.status_code = status_code
        self.code = code
        super().__init__(f"OMS rejected the request before execution: {code}")


class HTTPRefundGateway:
    """禁止隐式写入重试；超时、异常回执均交给 unknown 对账。"""

    def __init__(
        self, base_url: str, api_key: str, timeout: float = 1.5, transport=None
    ):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def _request(self, path, payload, schema, key=None):
        """错误中不带响应体或 Authorization，避免进入 Tool 审计和 Trace。"""
        headers = {"Authorization": "Bearer " + self._api_key}
        if key:
            headers["Idempotency-Key"] = key
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = client.post(
                    self.base_url + path, json=payload, headers=headers
                )
        except httpx.TimeoutException:
            raise TimeoutError(
                "OMS response timed out; execution result is unknown"
            ) from None
        except httpx.TransportError:
            raise ConnectionError(
                "OMS connection failed; execution result is unknown"
            ) from None
        if response.status_code in {401, 403}:
            raise OMSRejectedError(response.status_code)
        if response.status_code in {409, 422}:
            try:
                body = response.json()
                rejected = body.get("execution_rejected") is True
            except (ValueError, AttributeError):
                rejected = False
            if rejected:
                # 仅透出已知错误码，不把外部响应文字写入 Trace。
                known_codes = {
                    "idempotency_parameter_conflict",
                    "order_refund_already_requested",
                    "order_not_available",
                    "order_not_refundable",
                    "refund_not_found",
                    "refund_already_compensated",
                    "invalid_request",
                }
                code = body.get("code")
                raise OMSRejectedError(
                    response.status_code,
                    (
                        code
                        if isinstance(code, str) and code in known_codes
                        else "request_rejected"
                    ),
                )
        if response.status_code != 200:
            raise ConnectionError(
                "OMS result cannot be verified; reconciliation required"
            )
        try:
            return schema.model_validate(response.json()).model_dump()
        except (ValueError, ValidationError):
            # 写入可能已提交，不能把坏回执当作未执行的参数错误。
            raise ConnectionError(
                "OMS receipt is invalid; reconciliation required"
            ) from None

    def create_refund_request(self, *, customer_id, order_id, reason, idempotency_key):
        payload = RefundInput(customer_id=customer_id, order_id=order_id, reason=reason)
        KeyInput(idempotency_key=idempotency_key)
        return self._request(
            "/v1/refund-requests", payload.model_dump(), RefundReceipt, idempotency_key
        )

    def reconcile(self, *, idempotency_key):
        payload = KeyInput(idempotency_key=idempotency_key)
        return self._request(
            "/v1/refund-requests/reconcile", payload.model_dump(), ReconciliationReceipt
        )

    def compensate(self, *, idempotency_key, compensation_key):
        payload = KeyInput(idempotency_key=idempotency_key)
        KeyInput(idempotency_key=compensation_key)
        return self._request(
            "/v1/refund-requests/compensate",
            payload.model_dump(),
            CompensationReceipt,
            compensation_key,
        )
