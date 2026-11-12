export function formatDate(value) {
  if (!value) return '-';
  // 数据库裸时间按 UTC 解析，统一显示北京时间。
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  }).format(new Date(normalized));
}

export function formatOrderAmount(amount, currency) {
  if (amount == null) return '—';
  if (!currency) return `${amount}（币种未记录）`;
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency
  }).format(amount);
}

const TOOL_STATUS_LABELS = {
  success: '调用成功',
  error: '调用失败',
  failed: '调用失败',
  denied: '调用被拒绝',
  blocked: '调用被阻止'
};

export function formatToolStatus(status) {
  return TOOL_STATUS_LABELS[status] || status || '未记录状态';
}
