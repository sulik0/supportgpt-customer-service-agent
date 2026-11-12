import { WORKFLOW_STAGES } from "./workflowMetadata.js";

const NODE_ALIASES = {
  ticket_analyzer: "analyzer",
  tool_call: "tooling",
  llm_generation: "resolver",
};

export function buildInsightSteps(path = []) {
  const recorded = new Set(
    (Array.isArray(path) ? path : []).map((node) => NODE_ALIASES[node] || node),
  );
  const missing = recorded.size ? "skipped" : "unrecorded";
  const stateFor = (id) => (recorded.has(id) ? "recorded" : missing);
  const steps = [];
  for (const id of WORKFLOW_STAGES.filter(
    (node) => !["input", "end"].includes(node),
  )) {
    if (id === "context_enrichment") {
      const hasBranches = recorded.has("tooling") || recorded.has("retriever");
      steps.push({
        id,
        state: recorded.has(id) ? "recorded" : hasBranches ? "group" : missing,
      });
      for (const branch of ["tooling", "retriever"]) {
        steps.push({
          id: branch,
          branch: true,
          state: recorded.has(branch)
            ? "recorded"
            : recorded.has(id)
              ? "unrecorded"
              : missing,
        });
      }
    } else {
      steps.push({
        id,
        state:
          id === "approval_gate" && !recorded.has(id)
            ? "summary"
            : stateFor(id),
      });
    }
  }
  if (recorded.has("human_approval"))
    steps.push({ id: "human_approval", state: "recorded" });
  return steps;
}

export async function requestPublicRunInsight(
  baseUrl,
  agentRunId,
  feedbackToken,
  signal,
) {
  const response = await fetch(
    `${baseUrl}/support/runs/${encodeURIComponent(agentRunId)}/insight`,
    {
      credentials: "include",
      headers: { "X-Feedback-Token": feedbackToken },
      signal,
    },
  );
  if (!response.ok) {
    const messages = {
      403: "无权查看本次处理过程，请重新提交咨询。",
      404: "处理过程未开放或记录已不可用。",
      429: "查看过于频繁，请稍后重试。",
    };
    throw new Error(messages[response.status] || "处理过程加载失败，请重试。");
  }
  return response.json();
}
