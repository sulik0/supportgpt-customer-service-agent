import test from 'node:test';
import assert from 'node:assert/strict';
import { consumeNavigationApproval, navigate, parseRoute } from '../src/navigation.js';
import { friendlyError } from '../src/errors.js';
import { formatLatency, summarizeRuns, totalTokens } from '../src/components/runMetrics.js';
import { createLatestRequest } from '../src/latestRequest.js';

test('切换工单后，旧详情查询取消且不能覆盖新工单', async () => {
  const requests = createLatestRequest();
  const oldRequest = requests.start();
  let releaseOld;
  let displayedTicket = null;
  const oldResponse = new Promise(resolve => { releaseOld = resolve; });
  const oldUpdate = oldResponse.then(ticket => {
    if (oldRequest.isCurrent()) displayedTicket = ticket;
  });
  const newRequest = requests.start();
  assert.equal(oldRequest.signal.aborted, true);
  if (newRequest.isCurrent()) displayedTicket = 102;
  releaseOld(101);
  await oldUpdate;
  assert.equal(displayedTicket, 102);
});

test('关闭详情后，未完成查询不能写回页面或清除新查询的加载状态', () => {
  const requests = createLatestRequest();
  const pending = requests.start();
  requests.cancel();
  assert.equal(pending.signal.aborted, true);
  assert.equal(pending.isCurrent(), false);
  const reopened = requests.start();
  assert.equal(reopened.isCurrent(), true);
  assert.equal(pending.isCurrent(), false);
});
test('旧入口保持兼容，未登录默认用户咨询', () => {
  assert.equal(parseRoute('').entry, 'customer');
  assert.equal(parseRoute('', true).entry, 'staff');
  assert.equal(parseRoute('#staff').view, 'workspace');
  assert.equal(parseRoute('#support').entry, 'customer');
  assert.equal(parseRoute('#workflow').entry, 'workflow');
});
test('后台地址恢复页面、工单、筛选、分页与资源 Tab', () => {
  const review = parseRoute('#staff/review?ticket=123&department=shipping&query=%E8%AE%A2%E5%8D%95');
  assert.equal(review.ticket, 123);
  assert.equal(review.filters.department, 'shipping');
  assert.equal(review.filters.query, '订单');
  const runs = parseRoute('#staff/runs?ticket=42&offset=20&run=run-1');
  assert.equal(runs.view, 'observability');
  assert.equal(runs.offset, 20);
  assert.equal(runs.run, 'run-1');
  assert.equal(parseRoute('#staff/resources?tab=rag').tab, 'rag');
});
test('未知页面和非法参数不能变成任意导航目标', () => {
  assert.equal(parseRoute('#staff/not-a-page').view, 'workspace');
  assert.equal(parseRoute('#staff/runs?ticket=-1&offset=Infinity').ticket, null);
  assert.equal(parseRoute('#staff/runs?ticket=9007199254740993').ticket, null);
  assert.equal(parseRoute('#staff/resources?tab=secrets').tab, 'tools');
  assert.equal(parseRoute('#staffevil').entry, 'customer');
  assert.equal(parseRoute('#staff/review?priority=invalid&sentiment=invalid').filters.priority, '');
});
test('网络错误显示自然中文，业务说明不丢失', () => {
  assert.equal(friendlyError(new TypeError('Failed to fetch')), '无法连接服务，请检查网络或稍后重试。');
  assert.equal(friendlyError(new Error('文档版本不允许覆盖')), '文档版本不允许覆盖');
});
test('未保存编辑可以阻止导航，批准后的导航只消费一次许可', async () => {
  const previous = globalThis.window;
  let allow = false;
  globalThis.window = {
    location: {
      hash: '#staff/review'
    },
    dispatchEvent(event) {
      event.detail.guards.push(async () => allow);
    }
  };
  try {
    await navigate('#staff/runs');
    assert.equal(window.location.hash, '#staff/review');
    allow = true;
    await navigate('#staff/runs');
    assert.equal(window.location.hash, '#staff/runs');
    assert.equal(consumeNavigationApproval('#staff/runs'), true);
    assert.equal(consumeNavigationApproval('#staff/runs'), false);
  } finally {
    globalThis.window = previous;
  }
});
test('缺失耗时和 Token 不显示为零，明确零值仍正常显示', () => {
  assert.equal(formatLatency(null), '—');
  assert.equal(formatLatency(0), '0.00s');
  assert.equal(totalTokens({
    tokens_input: 10
  }), null);
  assert.equal(totalTokens({
    tokens_input: 0,
    tokens_output: 0
  }), 0);
  assert.equal(summarizeRuns([]).averageLatency, null);
  assert.equal(summarizeRuns([]).totalTokens, null);
});
test('当前页汇总只计算已经记录的观测值', () => {
  const result = summarizeRuns([{
    latency_seconds: 2,
    tokens_input: 10,
    tokens_output: 5
  }, {
    latency_seconds: 4,
    approval_required: true
  }, {
    latency_seconds: null,
    tokens_input: 0,
    tokens_output: 0
  }]);
  assert.equal(result.averageLatency, 3);
  assert.equal(result.totalTokens, 15);
  assert.equal(result.latencyCount, 2);
  assert.equal(result.tokenCount, 2);
  assert.equal(result.reviewCount, 1);
});
