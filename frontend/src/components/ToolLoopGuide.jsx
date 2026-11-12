import React from 'react';
import { Badge, Card, NavLink } from './ui';
import { WORKFLOW_EXAMPLES } from './workflowExamples';

export default function ToolLoopGuide() {
  return <Card className="wf-tool-loop" title="工具调用循环：查到足够的信息再回复" actions={<Badge tone="accent">Tooling 内的受控 ReAct</Badge>}>
    <p>固定工作流走到 context_enrichment 时，业务工具查询与知识检索并行。工具查询内部可让模型读取工具结果，再决定是否补查；之后仍要经过回复生成、质量检查和风险判断。</p>
    <ol className="wf-loop-steps">
      <li><strong>限定可查范围</strong><p>系统绑定当前客户身份，只提供 Skill、角色和意图允许的低风险只读工具。客户画像与历史工单由系统获取，与第一轮规划并行。</p></li>
      <li><strong>选择并执行</strong><p>模型选择本轮需要的工具，系统检查白名单、重复调用和预算。同一轮可以并行查多个工具，模型不能指定其他客户的参数。</p></li>
      <li><strong>读取结果，继续或结束</strong><p>安全检查通过的结果经脱敏、截断后回传模型。信息足够时结束，否则进入下一轮；多次工具调用不一定代表多轮循环。</p></li>
      <li><strong>有界停止与回退</strong><p>默认最多 3 轮规划、5 次规划器工具调用，每次回传结果最多 600 字符；系统必查的画像与历史工单不计入这 5 次。命中注入检测会停止且不回传污染结果；规划异常回退规则查询。</p></li>
    </ol>
    <details className="wf-loop-setup">
      <summary>测试前：确认后端启用了哪种模式</summary>
      <p>仓库默认 <code>TOOL_SELECTION_MODE=rules</code>，走确定性工具路由。要验证循环，由运行后端的人员设置 <code>TOOL_SELECTION_MODE=react</code> 并重启服务；前端不会替您切换后端配置，也无法从当前接口确认是否已启用。</p>
      <p><code>LLM_PROVIDER=mock</code> 也会执行后端循环代码，但使用确定性模拟规划。验证真实模型决策时，需要已配置且支持工具调用的模型服务（openai / azure）；实际调用可能产生费用。上述预算是默认值，以部署配置为准。</p>
      <p>循环只查询数据，不执行退款或取消订单，也不会绕过独立的操作审批。以下例子使用仓库现有的虚构客户和模拟业务记录，真实执行工作流不等于访问真实订单系统。</p>
    </details>
    <div className="wf-loop-test-heading"><h3>通过客户咨询页实际发送测试</h3><NavLink className="btn btn-secondary" href="#support">去客户咨询页选择例子</NavLink></div>
    <p>在空对话中选择查询场景，再确认发送。</p>
    <div className="wf-loop-examples">
      {WORKFLOW_EXAMPLES.filter(example => example.tools.length > 0).map(example => <article key={example.id}>
        <h3>{example.title}</h3><Badge>{example.customerName} · {example.customerId}</Badge>
        <pre>{example.question}</pre>
        <p>重点检查的工具（实际选择由运行结果决定）：</p>
        <ul>{example.tools.map(tool => <li key={tool}><code>{tool}</code></li>)}</ul>
        <p>{example.inspect}</p>
      </article>)}
    </div>
    <details className="wf-loop-setup">
      <summary>怎样确认走了真实工作流和工具循环？</summary>
      <ol>
        <li>发送后记录返回的工单号；主管或管理员在运行监控中按工单筛选，检查保存的执行步骤和工具调用。</li>
        <li>复制该运行的 Trace ID，在已配置的 LangSmith 项目中查找 <code>agent.tooling</code> 和 <code>supportgpt.llm.plan_tool_calls</code>，检查规划调用及其间的工具结果。未采集 Trace 时，不能仅凭回复文字断言发生了循环。</li>
        <li>后端日志的 <code>tool_selection_mode</code> 和 <code>tool_planner_steps</code> 可辅助确认；同时排查“Tool planner failed, using rules”回退日志。react 模式标记本身不能证明规划成功。一次取齐后结束是合法结果，不保证每个问题都会多轮补查。</li>
      </ol>
    </details>
  </Card>;
}
