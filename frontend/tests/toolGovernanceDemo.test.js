import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ACTION_STATES, ACTION_TRANSITIONS, FAULT_SCENARIOS, PARTICIPANTS, diagramEdges, outgoingTransitions } from '../src/components/toolGovernanceDemo.js';

const backend = readFileSync(new URL('../../src/tools/action_state_machine.py', import.meta.url), 'utf8');

test('图中的完整状态和命令迁移与当前后端状态机一致', () => {
  const statusBlock = backend.split('class ToolActionStatus:')[1].split('class ToolActionCommand:')[0];
  const states = [...statusBlock.matchAll(/^    [A-Z_]+ = "([a-z_]+)"/gm)].map((match) => match[1]);
  assert.deepEqual(ACTION_STATES.map((item) => item.id).sort(), states.sort());
  const transitionsBlock = backend.split('    transitions = {')[1].split('    def transition')[0];
  const actual = [];
  for (const block of transitionsBlock.matchAll(/ToolActionStatus\.([A-Z_]+): \{([\s\S]*?)\n        \}/g)) {
    for (const item of block[2].matchAll(/ToolActionCommand\.([A-Z_]+): ToolActionStatus\.([A-Z_]+)/g)) {
      actual.push(`${block[1].toLowerCase()}:${item[1].toLowerCase()}:${item[2].toLowerCase()}`);
    }
  }
  assert.equal(actual.length, 25);
  assert.deepEqual(ACTION_TRANSITIONS.map((item) => `${item.from}:${item.command}:${item.to}`).sort(), actual.sort());
});

test('状态图连线不重复，所有命令都能找到对应连线', () => {
  const edges = diagramEdges();
  const keys = new Set(edges.map((item) => `${item.from}:${item.to}`));
  assert.equal(keys.size, edges.length);
  for (const item of ACTION_TRANSITIONS) assert.ok(keys.has(`${item.from}:${item.to}`));
  assert.equal(outgoingTransitions('rejected').length, 0);
  assert.equal(outgoingTransitions('succeeded')[0].to, 'compensation_pending');
  assert.deepEqual(outgoingTransitions('compensation_unknown').map((item) => item.command).sort(), ['manual_compensated', 'manual_compensation_failure']);
});

test('六套时序场景有完整快照，参与者和业务状态均有效', () => {
  assert.equal(Object.keys(FAULT_SCENARIOS).length, 6);
  const ids = new Set(ACTION_STATES.map((item) => item.id));
  for (const scenario of Object.values(FAULT_SCENARIOS)) {
    assert.ok(scenario.steps.length >= 5);
    for (const item of scenario.steps) {
      assert.ok(ids.has(item.action));
      assert.ok(PARTICIPANTS[item.from] && PARTICIPANTS[item.to]);
      for (const field of ['text', 'outbox', 'lease', 'review', 'note']) assert.ok(item[field].length > 0);
    }
  }
});

test('时序保留关键语义：投递成功不等于业务成功，DLQ 不自动变失败', () => {
  assert.ok(FAULT_SCENARIOS.timeout.steps.some((item) => item.action === 'unknown' && item.outbox.includes('execute: succeeded')));
  assert.ok(FAULT_SCENARIOS.manual.steps.some((item) => item.action === 'unknown' && item.outbox.includes('dead_letter')));
  assert.equal(FAULT_SCENARIOS.manual.steps.at(-1).review, 'resolved · 人工确认');
  assert.ok(FAULT_SCENARIOS.manual.steps.at(-1).note.includes('不会自动批准 AI 回复'));
  assert.ok(FAULT_SCENARIOS.stale.steps.some((item) => item.text.includes('被拒绝') && item.lease.includes('B / v2')));
});

test('补偿示例从直接成功且未创建 Review 的 Action 开始，不宣称自动补偿对账', () => {
  assert.equal(FAULT_SCENARIOS.compensation.steps[0].review, '无');
  assert.equal(FAULT_SCENARIOS.compensation.steps.at(-1).action, 'compensated');
  assert.ok(FAULT_SCENARIOS.compensation.summary.includes('人工核实'));
  assert.equal(ACTION_TRANSITIONS.some((item) => item.from === 'compensation_unknown' && item.to === 'reconciling'), false);
});
