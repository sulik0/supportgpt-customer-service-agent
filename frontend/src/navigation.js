const VIEWS = {
  review: 'workspace',
  runs: 'observability',
  resources: 'resources',
  workflow: 'workflow'
};
let approvedHash = null;

// 只接受已知页面与参数，旧入口仍然可用。
export function parseRoute(hash, authenticated = false) {
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  const params = new URLSearchParams(query);
  const entry = path === 'workflow' ? 'workflow' : path === 'support' ? 'customer' : /^staff(?:\/|$)/.test(path) ? 'staff' : authenticated ? 'staff' : 'customer';
  const view = VIEWS[path?.split('/')[1]] || 'workspace';
  const ticket = /^[1-9]\d*$/.test(params.get('ticket') || '') && Number.isSafeInteger(Number(params.get('ticket'))) ? Number(params.get('ticket')) : null;
  const offset = /^\d+$/.test(params.get('offset') || '') && Number.isSafeInteger(Number(params.get('offset'))) ? Number(params.get('offset')) : 0;
  const tab = ['tools', 'prompts', 'rag'].includes(params.get('tab')) ? params.get('tab') : 'tools';
  const filters = Object.fromEntries(['department', 'priority', 'sentiment', 'query'].map(key => [key, (params.get(key) || '').slice(0, 200)]));
  if (!['urgent', 'high', 'medium', 'low'].includes(filters.priority)) filters.priority = '';
  if (!['negative', 'neutral', 'positive'].includes(filters.sentiment)) filters.sentiment = '';
  return {
    entry,
    view,
    ticket,
    offset,
    tab,
    run: (params.get('run') || '').slice(0, 128),
    filters
  };
}
export async function requestNavigationPermission() {
  const guards = [];
  window.dispatchEvent(new CustomEvent('supportgpt:before-navigate', {
    detail: {
      guards
    }
  }));
  for (const guard of guards) if (!(await guard())) return false;
  return true;
}
export function consumeNavigationApproval(hash) {
  const allowed = approvedHash === hash;
  approvedHash = null;
  return allowed;
}
export async function navigate(hash) {
  if (window.location.hash === hash) return;
  if (!(await requestNavigationPermission())) return;
  approvedHash = hash;
  window.location.hash = hash;
}
export function updateRouteParams(updates, replace = true) {
  const [path, query = ''] = window.location.hash.split('?');
  const params = new URLSearchParams(query);
  Object.entries(updates).forEach(([key, value]) => {
    if (value == null || value === '' || value === 'all') params.delete(key);else params.set(key, String(value));
  });
  const hash = `${path}${params.size ? `?${params}` : ''}`;
  if (replace) {
    approvedHash = hash;
    window.history.replaceState(null, '', hash);
    window.dispatchEvent(new Event('hashchange'));
  } else return navigate(hash);
}
