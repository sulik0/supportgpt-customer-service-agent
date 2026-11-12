import test from 'node:test';
import assert from 'node:assert/strict';
import { parseDemoAccounts } from '../src/demoAccounts.js';
import { formatDate, formatOrderAmount, formatToolStatus } from '../src/formatters.js';

test('演示账号未配置或 JSON 无效时安全隐藏入口', () => {
  for (const value of [undefined, '', ' ', '{', 'null', '{}', '1', '[]']) {
    assert.deepEqual(parseDemoAccounts(value), []);
  }
});

test('演示账号只接受完整字符串字段，保留密码原始空格', () => {
  const account = { label: '客服', username: 'example-user', password: ' example-value ' };
  assert.deepEqual(parseDemoAccounts(JSON.stringify([
    account, null, {}, { ...account, label: ' ' },
    { ...account, username: 123 }, { ...account, password: '' }
  ])), [account]);
});

test('裸 UTC 时间与显式时区显示一致，跨日按北京时间换算', () => {
  const expected = '2026/11/13 00:30:00';
  assert.equal(formatDate('2026-11-12T16:30:00'), expected);
  assert.equal(formatDate('2026-11-12T16:30:00Z'), expected);
  assert.equal(formatDate('2026-11-12T18:30:00+02:00'), expected);
  assert.equal(formatDate(null), '-');
});

test('订单金额遵循币种，缺失币种时不冒用美元符号', () => {
  assert.equal(formatOrderAmount(699, 'CNY'), '¥699.00');
  assert.equal(formatOrderAmount(150, 'USD'), 'US$150.00');
  assert.equal(formatOrderAmount(0, 'CNY'), '¥0.00');
  assert.equal(formatOrderAmount(10), '10（币种未记录）');
  assert.equal(formatOrderAmount(null, 'CNY'), '—');
});

test('工具状态中文映射覆盖成功、失败、拒绝、拦截及未知值', () => {
  for (const [status, label] of Object.entries({ success: '调用成功', error: '调用失败', failed: '调用失败', denied: '调用被拒绝', blocked: '调用被阻止' })) {
    assert.equal(formatToolStatus(status), label);
  }
  assert.equal(formatToolStatus('future_status'), 'future_status');
  assert.equal(formatToolStatus(null), '未记录状态');
});
