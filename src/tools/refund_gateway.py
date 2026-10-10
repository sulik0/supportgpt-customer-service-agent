"""模拟支持幂等、结果查询和补偿的 OMS 退款网关。"""

import threading
from typing import Any

from src.tools.payload_security import tool_payload_security


class MockRefundGateway:
    """用进程内账本模拟真实 OMS 的幂等写入与对账接口。"""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_refund_request(
        self,
        *,
        customer_id: str,
        order_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        # 相同幂等键只返回首次写入结果，不产生第二笔退款。
        with self._lock:
            existing = self._records.get(idempotency_key)
            if existing:
                return dict(existing)
            reference = tool_payload_security.payload_hash(
                {"idempotency_key": idempotency_key, "order_id": order_id}
            )[:12]
            result = {
                "refund_request_id": f"REF-{reference.upper()}",
                "status": "submitted",
                "message": "Mock refund request submitted for downstream processing.",
            }
            self._records[idempotency_key] = dict(result)
            return result

    def reconcile(self, *, idempotency_key: str) -> dict[str, Any]:
        """按幂等键查询 OMS 的权威结果，不重复执行写操作。"""
        with self._lock:
            result = self._records.get(idempotency_key)
            if result is None:
                return {"status": "pending", "found": False}
            return {"status": "succeeded", "found": True, "result": dict(result)}

    def compensate(
        self, *, idempotency_key: str, compensation_key: str
    ) -> dict[str, Any]:
        """模拟撤销已提交退款；补偿本身也使用独立幂等键。"""
        with self._lock:
            record = self._records.get(idempotency_key)
            if record is None:
                return {"status": "failed", "reason": "refund_not_found"}
            record = dict(record)
            record["status"] = "compensated"
            record["compensation_key"] = compensation_key
            self._records[idempotency_key] = record
            return {
                "status": "compensated",
                "refund_request_id": record["refund_request_id"],
            }

    def reset(self) -> None:
        """仅供隔离测试清空模拟账本。"""
        with self._lock:
            self._records.clear()


def create_refund_gateway():
    """保持现有 Tool handler 协议，默认不连接任何外部 OMS。"""
    from src.config import settings

    if settings.OMS_PROVIDER == "reference_http":
        from src.tools.oms_gateway import HTTPRefundGateway

        return HTTPRefundGateway(
            settings.OMS_BASE_URL, settings.OMS_API_KEY, settings.OMS_TIMEOUT_SECONDS
        )
    return MockRefundGateway()


refund_gateway = create_refund_gateway()
