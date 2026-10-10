const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export const AUTH_EXPIRED_EVENT = 'supportgpt:auth-expired';

function getHeaders() {
  const token = localStorage.getItem('token');
  const headers = {
    'Content-Type': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function authenticatedFetch(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...getHeaders(), ...(options.headers || {}) },
  });
  if (response.status === 401) {
    logout();
    window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
  }
  return response;
}

async function apiError(response, fallbackMessage) {
  try {
    const payload = await response.json();
    return new Error(payload.detail || payload.message || fallbackMessage);
  } catch {
    return new Error(fallbackMessage);
  }
}

export async function fetchHealth(signal) {
  const response = await fetch(`${BASE_URL}/health`, { signal });
  if (!response.ok) throw await apiError(response, '服务健康检查失败');
  return response.json();
}

export async function login(username, password) {
  const response = await fetch(`${BASE_URL}/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error('身份验证失败');
  }
  const data = await response.json();
  localStorage.setItem('token', data.access_token);
  localStorage.setItem('role', data.role);
  localStorage.setItem('username', username);
  return data;
}

export function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('role');
  localStorage.removeItem('username');
}

export async function fetchTickets() {
  const response = await authenticatedFetch(`${BASE_URL}/tickets`, {
    headers: getHeaders(),
  });
  if (!response.ok) throw new Error('加载工单失败');
  return response.json();
}

export async function fetchReviewQueue(signal) {
  const response = await authenticatedFetch(`${BASE_URL}/staff/review-queue`, {
    headers: getHeaders(), signal,
  });
  if (!response.ok) throw new Error('加载待人工处理队列失败');
  return response.json();
}

export async function submitSupportRequest(customerId, message, sessionId, kbVersion = 'v1') {
  const response = await fetch(`${BASE_URL}/support/requests`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId, message, session_id: sessionId, kb_version: kbVersion }),
  });
  if (!response.ok) throw new Error('问题提交失败，请稍后重试');
  return response.json();
}

export async function fetchSupportHistory(customerId, sessionIds, signal) {
  const params = new URLSearchParams({ customer_id: customerId });
  sessionIds.forEach((sessionId) => params.append('session_id', sessionId));
  const response = await fetch(`${BASE_URL}/support/history?${params}`, {
    credentials: 'include',
    signal,
  });
  if (!response.ok) throw await apiError(response, '加载最近对话失败');
  return response.json();
}

export async function createTicket(customerId, subject, description, kbVersion = 'v1') {
  const response = await authenticatedFetch(`${BASE_URL}/tickets`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ customer_id: customerId, subject, description, kb_version: kbVersion }),
  });
  if (!response.ok) throw new Error('创建工单失败');
  return response.json();
}

export async function fetchTicketAgentResult(ticketId, signal) {
  const response = await authenticatedFetch(`${BASE_URL}/tickets/${encodeURIComponent(ticketId)}/agent-result`, {
    headers: getHeaders(),
    signal,
  });
  if (!response.ok) throw new Error(response.status === 404 ? '该工单暂无已保存的 Agent 处理结果' : '加载 Agent 处理结果失败');
  return response.json();
}

export async function fetchPendingApprovals() {
  const response = await authenticatedFetch(`${BASE_URL}/approvals/pending`, {
    headers: getHeaders(),
  });
  if (!response.ok) throw new Error('加载审批记录失败');
  return response.json();
}

export async function fetchToolReviews(ticketId, signal) {
  const response = await authenticatedFetch(`${BASE_URL}/tickets/${encodeURIComponent(ticketId)}/tool-reviews`, { headers: getHeaders(), signal });
  if (!response.ok) throw await apiError(response, '加载业务核实任务失败');
  return response.json();
}

export async function resolveToolAction(actionId, payload) {
  const response = await authenticatedFetch(`${BASE_URL}/tool-actions/${encodeURIComponent(actionId)}/resolve`, {
    method: 'POST', headers: getHeaders(), body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, '提交外部核实结果失败');
  return response.json();
}

export async function submitApproval(approvalId, status, modifiedResponse) {
  const response = await authenticatedFetch(`${BASE_URL}/approvals/${approvalId}`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      approval_id: approvalId,
      status,
      modified_response: status === 'modified' ? modifiedResponse : null,
    }),
  });
  if (!response.ok) throw new Error('处理审批请求失败');
  return response.json();
}

export async function submitChat(message, customerId, sessionId, kbVersion = 'v1') {
  const response = await authenticatedFetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ message, customer_id: customerId, session_id: sessionId, kb_version: kbVersion }),
  });
  if (!response.ok) throw new Error('对话请求失败');
  return response.json();
}

export async function fetchCustomerContext(customerId, signal) {
  const response = await authenticatedFetch(`${BASE_URL}/customer-context`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ customer_id: customerId }),
    signal,
  });
  if (!response.ok) throw new Error('加载客户资料失败');
  return response.json();
}

export async function submitUserFeedback(agentRunId, feedbackToken, rating, comment, idempotencyKey) {
  const response = await fetch(`${BASE_URL}/feedback/user`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      agent_run_id: agentRunId,
      feedback_token: feedbackToken,
      rating,
      comment: comment.trim() || null,
      idempotency_key: idempotencyKey,
    }),
  });
  if (!response.ok) throw await apiError(response, '提交评价失败');
  return response.json();
}

export async function evaluateResponse(query, context, responseText, agentRunId) {
  const response = await authenticatedFetch(`${BASE_URL}/evaluate-response`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ query, context, response: responseText, agent_run_id: agentRunId }),
  });
  if (!response.ok) throw new Error('评测请求失败');
  return response.json();
}

export async function fetchAgentRuns(limit = 30, offset = 0, ticketId = null, signal) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (ticketId != null) params.set('ticket_id', String(ticketId));
  const response = await authenticatedFetch(`${BASE_URL}/observability/runs?${params}`, {
    headers: getHeaders(), signal,
  });
  if (!response.ok) throw new Error('加载 Agent 运行记录失败');
  return response.json();
}

export async function fetchAgentRun(agentRunId, signal) {
  const response = await authenticatedFetch(`${BASE_URL}/feedback/runs/${encodeURIComponent(agentRunId)}`, {
    headers: getHeaders(), signal,
  });
  if (!response.ok) throw new Error('加载 Agent 运行详情失败');
  return response.json();
}

export async function fetchAdminTools(signal) {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/tools`, { signal });
  if (!response.ok) throw await apiError(response, '加载工具列表失败');
  return response.json();
}

export async function updateAdminTool(toolName, enabled, reason) {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/tools/${encodeURIComponent(toolName)}`, {
    method: 'PUT',
    body: JSON.stringify({ enabled, reason: reason || null }),
  });
  if (!response.ok) throw await apiError(response, '更新工具状态失败');
  return response.json();
}

export async function fetchAdminPrompts(signal) {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/prompts`, { signal });
  if (!response.ok) throw await apiError(response, '加载提示词版本失败');
  return response.json();
}

export async function createPromptCandidate(payload) {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/prompts`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, '创建提示词候选版本失败');
  return response.json();
}

export async function fetchAdminRagDocuments(signal) {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/rag-documents`, { signal });
  if (!response.ok) throw await apiError(response, '加载 RAG 文档失败');
  return response.json();
}

export async function saveAdminRagDocument(payload, existingId = null) {
  const url = existingId
    ? `${BASE_URL}/admin/resources/rag-documents/${encodeURIComponent(existingId)}`
    : `${BASE_URL}/admin/resources/rag-documents`;
  const response = await authenticatedFetch(url, {
    method: existingId ? 'PUT' : 'POST',
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await apiError(response, '保存 RAG 文档失败');
  return response.json();
}

export async function deleteAdminRagDocument(docId) {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/rag-documents/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw await apiError(response, '删除 RAG 文档失败');
}

export async function reindexAdminRagDocuments() {
  const response = await authenticatedFetch(`${BASE_URL}/admin/resources/rag-documents/reindex`, {
    method: 'POST',
  });
  if (!response.ok) throw await apiError(response, '重建 RAG 索引失败');
  return response.json();
}
