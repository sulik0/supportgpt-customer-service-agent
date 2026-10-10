// 网络异常使用中文提示，业务校验信息保留后端的具体说明。
export function friendlyError(error) {
  const message = error?.message || '';
  if (/Failed to fetch|NetworkError|Load failed|fetch failed/i.test(message)) return '无法连接服务，请检查网络或稍后重试。';
  return message || '请求未完成，请稍后重试。';
}
