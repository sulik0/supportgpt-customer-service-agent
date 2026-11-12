// 页面共用节点标签与主流程顺序。
export const NODE_LABELS = {
  input: "接收用户问题",
  end: "保存处理结果",
  tooling: "业务工具查询",
  analyzer: "分析问题",
  skill_selector: "选择能力",
  context_enrichment: "查询依据",
  resolver: "生成回复",
  approval_gate: "审批门控",
  human_approval: "审批续跑",
  ticket_analyzer: "工单分析",
  tool_call: "工具调用",
  retriever: "知识检索",
  llm_generation: "回复生成",
  qa: "质量检查",
  escalation: "风险判断",
};

export const INTENT_LABELS = {
  billing_dispute: "账务 / 退款争议",
  outage_report: "API / 服务故障",
  order_cancellation: "订单取消",
  order_status: "订单状态",
  account_support: "账户异常",
  warranty_claim: "保修申请",
  feedback: "用户反馈",
  information_request: "信息咨询",
};

export const WORKFLOW_STAGES = [
  "input",
  "analyzer",
  "skill_selector",
  "context_enrichment",
  "resolver",
  "qa",
  "escalation",
  "approval_gate",
  "end",
];
