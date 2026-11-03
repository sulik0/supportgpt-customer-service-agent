"""识别具体业务记录查询；政策咨询不能被当作实际查询结果。"""
import re

_ENTITY = re.compile(r"\bord-[\w-]+\b|我的.{0,12}(?:订单|设备)|\bmy (?:order|device|hardware)\b", re.I)
_BILLING = re.compile(r"付款|支付|发票|账单|\b(?:payment|invoice|billing)\b", re.I)
_WARRANTY = re.compile(r"保修|维修|\b(?:warranty|entitlement|repair)\b", re.I)
_LOOKUP = re.compile(r"查|核实|确认|进度|记录|权益|是否|\b(?:check|look up|verify|status|coverage|entitlements?)\b", re.I)


def requested_record_tool(text: str) -> str | None:
    """仅返回有具体业务对象、明确查询要求且无跨领域歧义的只读工具。"""
    if not _ENTITY.search(text) or not _LOOKUP.search(text):
        return None
    billing = bool(_BILLING.search(text))
    warranty = bool(_WARRANTY.search(text))
    if billing == warranty:
        return None
    return 'billing.get_payment_invoices' if billing else 'warranty.get_entitlements'
