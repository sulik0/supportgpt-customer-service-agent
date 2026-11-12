import React from "react";
import { Badge, Card, NavLink } from "./ui";
import { WORKFLOW_EXAMPLES } from "./workflowExamples";

export default function ToolLoopGuide() {
  return (
    <Card
      className="wf-tool-loop"
      title="工具查询循环"
      actions={<Badge tone="accent">受控 ReAct</Badge>}
    >
      <p>Tooling 按需补查，再进入回复生成与质量检查。</p>
      <ol className="wf-loop-steps">
        <li>
          <strong>限定范围</strong>
          <p>绑定客户，只开放 Skill、角色与意图允许的只读工具。</p>
        </li>
        <li>
          <strong>选择工具</strong>
          <p>校验白名单、重复调用和预算，支持同轮并行。</p>
        </li>
        <li>
          <strong>读取结果</strong>
          <p>安全结果脱敏裁剪后回传，信息不足再补查。</p>
        </li>
        <li>
          <strong>有界停止</strong>
          <p>注入即隔离，规划异常回退规则查询。</p>
        </li>
      </ol>
      <details className="wf-loop-setup">
        <summary>模式与预算</summary>
        <dl className="wf-loop-facts">
          <div>
            <dt>默认</dt>
            <dd>
              <code>TOOL_SELECTION_MODE=rules</code>
            </dd>
          </div>
          <div>
            <dt>循环</dt>
            <dd>
              <code>TOOL_SELECTION_MODE=react</code>，配置后重启后端
            </dd>
          </div>
          <div>
            <dt>默认预算</dt>
            <dd>3 轮 / 5 次规划工具调用 / 每次回传 600 字符</dd>
          </div>
          <div>
            <dt>系统必查</dt>
            <dd>画像与历史工单并行获取，不计入 5 次预算</dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>
              <code>LLM_PROVIDER=mock</code> 使用确定性规划
            </dd>
          </div>
        </dl>
        <p>实际配置以后端为准；真实模型调用可能产生费用。</p>
      </details>
      <div className="wf-loop-test-heading">
        <h3>验证工具选择</h3>
        <NavLink className="btn btn-secondary" href="#support">
          选择查询场景
        </NavLink>
      </div>
      <div className="wf-loop-examples">
        {WORKFLOW_EXAMPLES.filter((example) => example.tools.length > 0).map(
          (example) => (
            <article key={example.id}>
              <h3>{example.title}</h3>
              <Badge>
                {example.customerName} · {example.customerId}
              </Badge>
              <ul>
                {example.tools.map((tool) => (
                  <li key={tool}>
                    <code>{tool}</code>
                  </li>
                ))}
              </ul>
            </article>
          ),
        )}
      </div>
      <details className="wf-loop-setup">
        <summary>核对执行记录</summary>
        <ol>
          <li>主管或管理员按工单号查运行记录。</li>
          <li>
            用 Trace ID 核对 <code>agent.tooling</code> 与{" "}
            <code>supportgpt.llm.plan_tool_calls</code>。
          </li>
          <li>
            日志查看 <code>tool_planner_steps</code> 与规则回退记录。
          </li>
        </ol>
        <p>多次工具调用不等于多轮循环，以运行记录为准。</p>
      </details>
    </Card>
  );
}
