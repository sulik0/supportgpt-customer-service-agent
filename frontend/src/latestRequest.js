// 新查询替换旧查询；即使适配器忽略取消信号，旧结果也不能再写回页面。
export function createLatestRequest() {
  let active = null;
  return {
    start() {
      active?.abort();
      const controller = new AbortController();
      active = controller;
      return {
        signal: controller.signal,
        isCurrent: () => active === controller && !controller.signal.aborted,
      };
    },
    cancel() {
      active?.abort();
      active = null;
    },
  };
}
