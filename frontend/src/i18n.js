const PRIORITY_LABELS = {
  urgent: '紧急',
  high: '高',
  medium: '中',
  low: '低',
};

const SENTIMENT_LABELS = {
  positive: '正面',
  neutral: '中性',
  negative: '负面',
};

const STATUS_LABELS = {
  open: '处理中',
  in_progress: '处理中',
  pending: '待处理',
  pending_approval: '待审批',
  approved: '已批准',
  modified: '已修改',
  rejected: '已拒绝',
  closed: '已关闭',
  resolved: '已解决',
  shipped: '已发货',
  delivered: '已送达',
  cancelled: '已取消',
};

const ROLE_LABELS = {
  agent: '客服',
  manager: '主管',
  admin: '管理员',
};

const RISK_LABELS = { low: '低', medium: '中', high: '高', critical: '严重' };
export const DEPARTMENT_LABELS = {
  billing: '账务',
  technical: '技术支持',
  shipping: '订单与物流',
  general: '综合支持',
};

const TIER_LABELS = {
  VIP: 'VIP 客户',
  Standard: '标准客户',
  Enterprise: '企业客户',
};

const SUBJECT_LABELS = {
  'Active Chat Conversation': '在线客服对话',
};

const ORDER_ITEM_LABELS = {
  'Enterprise SaaS User Pack (10)': '企业版 SaaS 用户包（10 个账号）',
  'Developer API Key Pack': '开发者 API Key 套餐',
  'Dedicated AWS Gateway Cluster': 'AWS 专属网关集群',
  'Enterprise Premium support SLA addon': '企业高级支持 SLA 附加服务',
};

// 将后端枚举值转换为中文，未知值保持原样，便于兼容后续扩展。
function translateValue(labels, value, fallback = '') {
  if (!value) return fallback;
  return labels[value] || value;
}

export const translatePriority = (value) => translateValue(PRIORITY_LABELS, value, '中');
export const translateSentiment = (value) => translateValue(SENTIMENT_LABELS, value, '中性');
export const translateStatus = (value) => translateValue(STATUS_LABELS, value, '未知');
export const translateRole = (value) => translateValue(ROLE_LABELS, value, '客服');
export const translateRisk = (value) => translateValue(RISK_LABELS, value, '未知');
export const translateDepartment = (value) => translateValue(DEPARTMENT_LABELS, value, '未分配部门');
export const translateTier = (value) => translateValue(TIER_LABELS, value, value);
export const translateSubject = (value) => translateValue(SUBJECT_LABELS, value, value);
export const translateOrderItem = (value) => translateValue(ORDER_ITEM_LABELS, value, value);

export function translateEscalationReason(value) {
  if (!value) return '';
  const reasonLabels = {
    security_threat_detected: '安全检测发现可疑提示词注入或越权指令。',
    semantic_safety_unsafe: '语义安全模型将请求判定为不安全。',
    semantic_safety_controversial: '语义安全模型识别到争议性内容，需要人工确认。',
    semantic_guard_degraded: '语义安全检测服务暂时不可用，系统无法确认请求是否安全。',
    untrusted_context_guard_unavailable: '知识文档或工具返回内容的安全检查未完成。',
    dependency_partially_degraded: '部分服务未能正常工作，需要人工检查处理结果。',
    dependency_requires_human: '调用的服务出现故障，系统已转交人工处理。',
    dependency_failed: '关键服务调用失败，系统无法生成可靠回复。',
    urgent_priority: '工单被判定为紧急优先级。',
    high_priority: '工单优先级较高，需要人工关注。',
    negative_high_priority: '客户表达了负面情绪，且该工单优先级较高。',
    high_risk_business_intent: '请求涉及退款争议或订单取消等高风险业务操作。',
    low_analyzer_confidence: '系统对问题分类的把握不足，需要人工确认用户想做什么。',
    medium_analyzer_confidence: '系统对问题分类的把握不够高，需要进一步确认。',
    authoritative_answer_unavailable: '系统缺少足够的业务资料或知识文档，无法确认应该怎样回答。',
    hallucination_detected: 'QA 检查发现，回复可能包含没有依据的内容。',
    qa_score_below_threshold: '回复质量评分未达到自动发送的要求。',
    workflow_error: 'Agent 执行过程中出现异常，需要人工确认结果。',
    manual_review_policy: '按照当前处理规则，这张工单需要人工审批。',
  };
  if (reasonLabels[value]) return reasonLabels[value];
  if (value === 'Security guardrails violation block.' || value === 'Security violation block') {
    return '安全检查发现了高风险请求。';
  }
  if (value === 'Ticket designated as Urgent priority.') return '工单被判定为紧急优先级。';
  if (value === 'Negative customer sentiment combined with high priority.') {
    return '客户表达了负面情绪，且该工单优先级较高。';
  }
  if (value.startsWith('AI quality assurance score')) {
    const score = value.match(/\(([^)]+)\)/)?.[1];
    return `回复质量评分${score ? `（${score}）` : ''}未达到要求，或回复可能包含没有依据的内容。`;
  }
  if (value.startsWith('Authoritative business guidance is unavailable')) {
    return reasonLabels.authoritative_answer_unavailable;
  }
  if (value.startsWith('Risk Engine classified ticket as')) {
    const level = value.match(/as ([a-z]+)/i)?.[1]?.toLowerCase();
    const levelLabel = level === 'critical' ? '严重' : level === 'high' ? '高' : level === 'medium' ? '中' : '低';
    return `Risk Engine（风险判断模块）将这张工单判定为${levelLabel}风险。`;
  }
  if (/^[a-z0-9_.-]+:[a-z0-9_.-]+$/i.test(value)) {
    return `服务调用 ${value} 未能正常处理，请检查对应环节。`;
  }
  return value;
}
