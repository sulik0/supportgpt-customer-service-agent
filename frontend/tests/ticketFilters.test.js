import test from 'node:test';
import assert from 'node:assert/strict';
import { filterTickets } from '../src/components/ticketFilters.js';

const tickets = [
  { id: 101, customer_id: 'C001', subject: '退款申请', description: '订单退款', department: 'billing', priority: 'high', sentiment: 'negative' },
  { id: 102, customer_id: 'C002', subject: '物流查询', description: '包裹未到', department: 'shipping', priority: 'medium', sentiment: 'neutral' },
  { id: 103, customer_id: 'C003', subject: 'API 故障', department: 'technical', priority: 'high', sentiment: 'negative' },
  { id: 104, customer_id: 'C004', subject: '其他问题', department: null, priority: 'low', sentiment: null },
];

test('默认显示完整队列，部门筛选只显示对应工单', () => {
  assert.deepEqual(filterTickets(tickets), tickets);
  assert.deepEqual(filterTickets(tickets, { department: 'shipping' }).map((ticket) => ticket.id), [102]);
});

test('部门、优先级、情绪和搜索条件同时生效', () => {
  assert.deepEqual(filterTickets(tickets, { department: 'billing', priority: 'high', sentiment: 'negative', query: ' C001 ' }).map((ticket) => ticket.id), [101]);
  assert.deepEqual(filterTickets(tickets, { department: 'billing', priority: 'low' }), []);
});

test('可以按工单编号、中文部门和问题内容搜索', () => {
  for (const query of ['102', '订单与物流', '包裹未到']) {
    assert.deepEqual(filterTickets(tickets, { query }).map((ticket) => ticket.id), [102]);
  }
});

test('未分配部门可单独筛选，字段缺失不报错', () => {
  assert.deepEqual(filterTickets(tickets, { department: 'unassigned' }).map((ticket) => ticket.id), [104]);
  assert.deepEqual(filterTickets([{ id: 1 }], { query: '不存在' }), []);
});

test('筛选不修改原队列，清除条件后恢复全部工单', () => {
  const before = structuredClone(tickets);
  filterTickets(tickets, { department: 'technical' });
  assert.deepEqual(tickets, before);
  assert.equal(filterTickets(tickets, { query: '', department: 'all', priority: 'all', sentiment: 'all' }).length, 4);
});
