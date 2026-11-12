import test from 'node:test';
import assert from 'node:assert/strict';
import { buildInsightSteps, requestPublicRunInsight } from '../src/publicRunInsight.js';
import { NODE_LABELS, WORKFLOW_STAGES } from '../src/workflowMetadata.js';

const state = (steps, id) => steps.find(step => step.id === id)?.state;

test('真实路径别名映射到主流程，并保留并行分支与审批摘要', () => {
  const steps = buildInsightSteps(['ticket_analyzer', 'skill_selector', 'tool_call', 'retriever', 'llm_generation', 'qa', 'escalation']);
  for (const id of ['analyzer', 'skill_selector', 'tooling', 'retriever', 'resolver', 'qa', 'escalation']) assert.equal(state(steps, id), 'recorded');
  assert.equal(state(steps, 'context_enrichment'), 'group');
  assert.equal(state(steps, 'approval_gate'), 'summary');
  assert.deepEqual(steps.filter(step => !step.branch).map(step => step.id), WORKFLOW_STAGES.filter(id => !['input', 'end'].includes(id)));
  for (const step of steps) assert.ok(NODE_LABELS[step.id]);
});

test('输入安全短路明确标记未运行的 Skill、Tool、RAG、生成与 QA', () => {
  const steps = buildInsightSteps(['ticket_analyzer', 'escalation']);
  for (const id of ['skill_selector', 'context_enrichment', 'tooling', 'retriever', 'resolver', 'qa']) assert.equal(state(steps, id), 'skipped');
  assert.equal(state(steps, 'analyzer'), 'recorded');
  assert.equal(state(steps, 'escalation'), 'recorded');
});

test('上下文安全短路保留已查工具与检索，未生成回复或执行 QA', () => {
  const steps = buildInsightSteps(['analyzer', 'skill_selector', 'tool_call', 'retriever', 'escalation']);
  assert.equal(state(steps, 'tooling'), 'recorded');
  assert.equal(state(steps, 'retriever'), 'recorded');
  assert.equal(state(steps, 'resolver'), 'skipped');
  assert.equal(state(steps, 'qa'), 'skipped');
});

test('旧记录与未单记分支不冒充跳过，审批恢复单独显示', () => {
  assert.equal(state(buildInsightSteps([]), 'analyzer'), 'unrecorded');
  assert.equal(state(buildInsightSteps(['context_enrichment']), 'tooling'), 'unrecorded');
  const steps = buildInsightSteps(['analyzer', 'human_approval']);
  assert.deepEqual(steps.at(-1), { id: 'human_approval', state: 'recorded' });
});

test('公开读取只在请求头发送凭据，支持取消且不附加员工 JWT', async context => {
  const controller = new AbortController();
  let request;
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ tokens_total: 0 }) };
  });
  const token = 'private-token';
  const response = await requestPublicRunInsight('https://api.example', 'run/one', token, controller.signal);
  assert.equal(request.url, 'https://api.example/support/runs/run%2Fone/insight');
  assert.ok(!request.url.includes(token));
  assert.deepEqual(request.options.headers, { 'X-Feedback-Token': token });
  assert.equal(request.options.credentials, 'include');
  assert.equal(request.options.signal, controller.signal);
  assert.equal(response.tokens_total, 0);
});

test('鉴权、功能关闭与限流显示可操作提示，不透传异常正文', async context => {
  for (const status of [403, 404, 429, 500]) {
    const mock = context.mock.method(globalThis, 'fetch', async () => ({ ok: false, status, json: async () => ({ detail: 'PRIVATE_EXCEPTION' }) }));
    await assert.rejects(requestPublicRunInsight('', 'run', 'token'), error => !error.message.includes('PRIVATE_EXCEPTION') && /处理过程|频繁/.test(error.message));
    mock.mock.restore();
  }
});
