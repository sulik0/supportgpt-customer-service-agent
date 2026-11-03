import '../styles/pages/workspace.css';
import React from 'react';
import { Clock3, Coins, Cpu, ShieldCheck } from 'lucide-react';

export default function MetricsGrid({ metrics = {} }) {
  const items = [
    { label: '本次预估成本', value: `$${(metrics.cost || 0).toFixed(4)}`, hint: '按模型 Token 用量估算', icon: Coins, tone: 'green' },
    { label: '模型 Token 用量', value: (metrics.tokens || 0).toLocaleString(), hint: '模型输入与输出合计', icon: Cpu, tone: 'blue' },
    { label: '本次处理耗时', value: `${(metrics.latency || 0).toFixed(2)}s`, hint: '本次 Agent 流程的总耗时', icon: Clock3, tone: 'amber' },
    { label: '安全检查', value: metrics.violations ? `${metrics.violations} 次拦截` : '未记录拦截', hint: '检查恶意指令、越权和敏感内容', icon: ShieldCheck, tone: metrics.violations ? 'red' : 'purple' },
  ];

  return (
    <div className="metrics-strip" aria-label="Agent 运行概览">
      {items.map((item) => (
        <div className="metric-item" key={item.label}>
          <span className={`metric-icon tone-${item.tone}`}><item.icon size={17} /></span>
          <div><span>{item.label}</span><strong>{item.value}</strong><small>{item.hint}</small></div>
        </div>
      ))}
    </div>
  );
}
