import "../styles/pages/workspace.css";
import React from "react";
import { MetricCard } from "./ui";

export default function MetricsGrid({ metrics = {} }) {
  const items = [
    {
      label: "预估成本",
      value: `$${(metrics.cost || 0).toFixed(4)}`,
      hint: "按 Token 估算",
    },
    {
      label: "Token 用量",
      value: (metrics.tokens || 0).toLocaleString(),
      hint: "输入输出合计",
    },
    {
      label: "处理耗时",
      value: `${(metrics.latency || 0).toFixed(2)}s`,
      hint: "流程总耗时",
    },
    {
      label: "安全检查",
      value: metrics.violations ? `${metrics.violations} 次拦截` : "未记录拦截",
      hint: "注入、越权、敏感信息",
    },
  ];

  return (
    <div className="metrics-strip" aria-label="Agent 运行概览">
      {items.map((item) => (
        <MetricCard
          key={item.label}
          label={item.label}
          value={item.value}
          hint={item.hint}
        />
      ))}
    </div>
  );
}
