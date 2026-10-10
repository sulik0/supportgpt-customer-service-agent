// 缺失观测数据不是零，汇总只计算确实记录的值。
export function totalTokens(run) {
  return Number.isFinite(run.tokens_input) && Number.isFinite(run.tokens_output) ? run.tokens_input + run.tokens_output : null;
}
export function formatLatency(value, digits = 2) {
  return Number.isFinite(value) ? `${value.toFixed(digits)}s` : '—';
}
export function summarizeRuns(runs) {
  const latencies = runs.map(run => run.latency_seconds).filter(Number.isFinite);
  const tokens = runs.map(totalTokens).filter(Number.isFinite);
  return {
    averageLatency: latencies.length ? latencies.reduce((sum, value) => sum + value, 0) / latencies.length : null,
    totalTokens: tokens.length ? tokens.reduce((sum, value) => sum + value, 0) : null,
    reviewCount: runs.filter(run => run.approval_required || run.escalation_recommended).length,
    latencyCount: latencies.length,
    tokenCount: tokens.length
  };
}
