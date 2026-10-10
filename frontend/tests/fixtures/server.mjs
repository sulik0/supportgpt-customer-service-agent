// 仅用于本地界面验收：内存中的 Demo 数据，不调用模型或真实业务系统。
import http from 'node:http';
const requests = [];
const templates = Object.fromEntries(['analyzer', 'resolver', 'qa'].map(node => [node, {
  system: `${node} 的演示系统模板，不用于真实请求。`,
  user: '{query}'
}]));
const payload = {
  version: 'demo-v1',
  templates
};
const bundle = {
  version: 'demo-v1',
  bundle_id: 'demo-bundle-000001',
  payload
};
const bundles = [bundle];
const tickets = [{
  id: 101,
  customer_id: 'demo-customer',
  subject: '物流延迟，需要核实',
  description: 'Demo：我的订单还没有收到，能帮我查一下吗？',
  department: 'shipping',
  priority: 'high',
  sentiment: 'neutral',
  status: 'pending_approval',
  requires_tool_review: true
}, {
  id: 102,
  customer_id: 'demo-customer',
  subject: '退款申请等待确认',
  description: 'Demo：请确认订单退款是否符合政策。',
  department: 'billing',
  priority: 'medium',
  sentiment: 'neutral',
  status: 'pending_approval'
}];
const runs = [{
  id: 'demo-run-101',
  ticket_id: 101,
  created_at: '2026-10-11T02:30:00',
  trace_id: 'demo-trace-101',
  model_provider: 'mock',
  model_name: 'Demo / 未调用真实模型',
  prompt_version: 'demo-v1',
  workflow_version: 'v1',
  kb_version: 'v1',
  latency_seconds: 1.2,
  tokens_input: 110,
  tokens_output: 80,
  qa_score: .9,
  approval_required: true,
  escalation_recommended: true,
  hallucination_detected: false,
  workflow_errors: [],
  workflow_path: ['analyzer', 'skill_selector', 'context_enrichment', 'resolver', 'qa', 'escalation', 'approval_gate'],
  tool_calls: [{
    tool_name: 'shipping.get_shipments',
    status: 'success'
  }],
  citations: [{
    source: 'Demo 配送说明'
  }],
  input_text: 'Demo：查询物流',
  output_text: 'Demo：订单运输中，发生配送延迟，需要核实承运方处理结果。',
  execution_snapshot: {
    analyzer_strategy: 'rule',
    qa_strategy: 'jev',
    analyzer_result: {
      intent: 'order_status',
      department: 'shipping',
      priority: 'high',
      sentiment: 'neutral',
      confidence: .99
    }
  }
}, {
  id: 'demo-run-102',
  ticket_id: 102,
  created_at: '2026-10-11T03:00:00Z',
  trace_id: null,
  model_name: 'Demo / 未记录',
  qa_score: null,
  latency_seconds: null,
  tokens_input: null,
  tokens_output: null,
  workflow_path: [],
  workflow_errors: [],
  approval_required: false
}];
const tools = ['customer.get_profile', 'shipping.get_shipments', 'order.create_refund'].map((name, i) => ({
  name,
  description: 'Demo 工具配置，仅用于界面测试。',
  enabled: true,
  operation_type: i === 2 ? 'write' : 'read',
  risk_level: i === 2 ? 'high' : 'low',
  min_role: i === 2 ? 'manager' : 'agent',
  version: 'demo-v1',
  input_schema: {
    type: 'object'
  },
  output_schema: {
    type: 'object'
  },
  allowed_intents: ['order_status']
}));
let documents = [{
  id: 'demo-delivery',
  title: 'Demo 配送说明',
  category: 'shipping',
  version: 'v1',
  content: '这是一条本地测试文档。运输延迟时，需要查询承运方的调查结果，不承诺具体到达时间。',
  metadata: {
    demo: true
  }
}];
let reviews = [{
  tool_action_id: 'demo-action-101',
  reason: 'unknown',
  status: 'pending',
  action: {
    status: 'unknown',
    version: 1,
    tool_name: 'order.create_refund'
  }
}];
let messages = [];
http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const origin = req.headers.origin;
  if (origin && /^http:\/\/(?:127\.0\.0\.1|localhost):(?:5174|5180)$/.test(origin)) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Content-Type', 'application/json');
  const send = (value, status = 200) => {
    res.statusCode = status;
    res.end(JSON.stringify(value));
  };
  if (req.method === 'OPTIONS') return send({});
  let body = '';
  try {
    for await (const chunk of req) body += chunk;
  } catch {
    if (!res.destroyed) send({ detail: 'Demo：请求已中断' }, 400);
    return;
  }
  let data = {};
  try {
    data = body ? JSON.parse(body) : {};
  } catch {
    return send({
      detail: 'Demo：JSON 无效'
    }, 400);
  }
  requests.push({
    method: req.method,
    path: url.pathname,
    query: url.search
  });
  if (url.pathname === '/__test__/requests') return send(requests);
  if (url.pathname === '/health') return send({
    status: 'healthy',
    demo: true
  });
  if (url.pathname === '/auth/token') return data.username === 'demo-admin' && data.password === 'demo-only' ? send({
    access_token: 'demo-not-a-real-jwt',
    role: 'admin'
  }) : send({
    detail: 'Demo：账号密码不匹配'
  }, 401);
  if (url.pathname === '/support/history') return send({
    messages
  });
  if (url.pathname === '/support/requests') {
    messages.push({
      id: `demo-${messages.length}`,
      role: 'user',
      content: data.message,
      created_at: new Date().toISOString(),
      ticket_id: 103
    });
    const response = '这是本地 Demo 回复。界面已提交问题；没有调用真实模型、查询真实订单或执行退款。';
    messages.push({
      id: `demo-${messages.length}`,
      role: 'assistant',
      content: response,
      created_at: new Date().toISOString(),
      ticket_id: 103
    });
    return send({
      status: 'answered',
      response,
      ticket_id: 103,
      agent_run_id: 'demo-run-103',
      feedback_token: 'demo-feedback'
    });
  }
  if (url.pathname === '/feedback/user') return send({
    status: 'recorded',
    demo: true
  });
  if (req.headers.authorization !== 'Bearer demo-not-a-real-jwt') return send({
    detail: 'Demo：请先登录本地测试账号'
  }, 401);
  if (url.pathname === '/staff/review-queue') return send(tickets);
  if (url.pathname === '/customer-context') return send({
    name: 'Demo 测试客户',
    tier: 'vip',
    open_tickets_count: 2,
    recent_orders: []
  });
  if (/\/tickets\/\d+\/agent-result$/.test(url.pathname)) {
    const ticketId = Number(url.pathname.split('/')[2]);
    if (!tickets.some(item => item.id === ticketId)) return send({ detail: 'Demo：工单不存在' }, 404);
    return send({
    response: ticketId === 101 ? 'Demo：目前物流延迟，需要人工核实承运方调查结果。' : 'Demo：退款申请需要人工确认政策与订单情况，尚未执行退款。',
    approval_id: `demo-approval-${ticketId}`,
    agent_run_id: `demo-run-${ticketId}`,
    approval_required: true,
    escalation_recommended: true,
    escalation_reason: '需要核实外部业务结果',
    review_reasons: ['需要核实外部业务结果'],
    risk_level: 'high',
    risk_score: .75,
    analyzer_confidence: .99,
    qa_score: .9,
    kb_version: 'v1',
    citations: [{
      source: 'Demo 配送说明',
      text: documents[0]?.content || 'Demo',
      score: .9
    }]
    });
  }
  if (/\/tickets\/\d+\/tool-reviews$/.test(url.pathname)) return send(url.pathname.includes('/101/') ? reviews : []);
  if (/\/tool-actions\/.+\/resolve$/.test(url.pathname)) {
    reviews = [];
    return send({
      demo: true
    });
  }
  if (url.pathname.startsWith('/approvals/')) {
    const ticketId = Number(url.pathname.split('/').at(-1).replace(/^demo-approval-/, ''));
    const index = tickets.findIndex(item => item.id === ticketId);
    if (index !== -1) tickets.splice(index, 1);
    return send({
      status: data.status
    });
  }
  if (url.pathname === '/observability/runs') {
    const items = runs.filter(run => !url.searchParams.has('ticket_id') || run.ticket_id === Number(url.searchParams.get('ticket_id')));
    return send({
      items,
      total: items.length,
      limit: 20,
      offset: 0
    });
  }
  if (url.pathname.startsWith('/feedback/runs/')) return send(runs.find(run => run.id === decodeURIComponent(url.pathname.split('/').at(-1))) || {}, 200);
  if (url.pathname === '/admin/resources/tools') return send(tools);
  if (url.pathname.startsWith('/admin/resources/tools/')) {
    const tool = tools.find(item => item.name === decodeURIComponent(url.pathname.split('/').at(-1)));
    if (!tool) return send({ detail: 'Demo：工具不存在' }, 404);
    Object.assign(tool, data, {
      disabled_reason: data.enabled ? null : data.reason
    });
    return send(tool);
  }
  if (url.pathname === '/admin/resources/prompts') {
    if (req.method === 'POST') {
      if (bundles.some(item => item.version === data.version)) return send({ detail: 'Demo：版本已存在，请使用新版本号' }, 409);
      const candidate = {
        version: data.version,
        bundle_id: `demo-candidate-${bundles.length}`,
        payload: structuredClone(data),
      };
      bundles.push(candidate);
      return send(candidate);
    }
    return send({ effective: { production: bundle, staging: bundle }, bundles });
  }
  if (url.pathname === '/admin/resources/rag-documents/reindex') return send({
    indexed_documents: documents.length
  });
  if (url.pathname === '/admin/resources/rag-documents') {
    if (req.method === 'POST') documents.push(data);
    return send(req.method === 'POST' ? data : documents);
  }
  if (url.pathname.startsWith('/admin/resources/rag-documents/')) {
    const id = decodeURIComponent(url.pathname.split('/').at(-1));
    documents = req.method === 'DELETE' ? documents.filter(doc => doc.id !== id) : documents.map(doc => doc.id === id ? data : doc);
    return send(data);
  }
  if (url.pathname === '/evaluate-response') return send({
    faithfulness_score: .9,
    hallucination_rate: 0,
    context_recall: .9,
    answer_relevance: .9,
    demo: true
  });
  return send({
    detail: 'Demo：未定义的测试接口'
  }, 404);
}).listen(18181, '127.0.0.1', () => console.log('Demo UI fixture API: http://127.0.0.1:18181 (local only)'));
