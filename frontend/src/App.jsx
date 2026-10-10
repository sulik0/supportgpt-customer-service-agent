import { friendlyError } from './errors';
import React, { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AUTH_EXPIRED_EVENT, fetchHealth, fetchReviewQueue, login, logout } from './api/client';
import { LoadingState, NavLink, Button, Notice } from './components/ui';
import { consumeNavigationApproval, navigate, parseRoute, requestNavigationPermission, updateRouteParams } from './navigation';
import { translateRole } from './i18n';
import { Activity, ArrowLeft, GitBranch, LibraryBig, Inbox, LogOut, Menu, RefreshCw, ShieldCheck, MessagesSquare } from 'lucide-react';
const CustomerSupportPage = lazy(() => import('./components/CustomerSupportPage'));
const TicketList = lazy(() => import('./components/TicketList'));
const TicketDetails = lazy(() => import('./components/TicketDetails'));
const ObservabilityPage = lazy(() => import('./components/ObservabilityPage'));
const ResourceManagementPage = lazy(() => import('./components/ResourceManagementPage'));
const WorkflowPage = lazy(() => import('./components/WorkflowPage'));
const VIEW_TITLES = {
  workspace: '人工处理台',
  observability: 'Agent 运行监控',
  resources: '资源管理',
  workflow: 'Agent 处理流程'
};
export default function App() {
  const staffEntryEnabled = import.meta.env.VITE_STAFF_ENTRY_ENABLED !== 'false';
  const [authenticated, setAuthenticated] = useState(() => Boolean(localStorage.getItem('token')));
  const [route, setRoute] = useState(() => parseRoute(window.location.hash, Boolean(localStorage.getItem('token'))));
  const [role, setRole] = useState(() => localStorage.getItem('role') || '');
  const [username, setUsername] = useState(() => localStorage.getItem('username') || '');
  const [loginUser, setLoginUser] = useState('');
  const [loginPass, setLoginPass] = useState('');
  const [loginBusy, setLoginBusy] = useState(false);
  const [authNotice, setAuthNotice] = useState('');
  const [tickets, setTickets] = useState([]);
  const [ticketsLoading, setTicketsLoading] = useState(true);
  const [ticketsError, setTicketsError] = useState('');
  const [serviceStatus, setServiceStatus] = useState('checking');
  const [mobileNav, setMobileNav] = useState(false);
  const queueController = useRef(null);
  const routeRef = useRef(route);
  routeRef.current = route;
  const lastHash = useRef(window.location.hash || (authenticated ? '#staff/review' : '#support'));
  const navigationSequence = useRef(0);
  const selectedTicket = tickets.find(ticket => ticket.id === route.ticket) || null;
  const allowed = route.view === 'observability' ? ['manager', 'admin'].includes(role) : route.view === 'resources' ? role === 'admin' : true;
  useEffect(() => {
    const changed = async () => {
      const sequence = ++navigationSequence.current;
      const hash = window.location.hash;
      const next = parseRoute(hash, authenticated);
      const current = routeRef.current;
      const leavesEditor = next.entry !== current.entry || next.view !== current.view || next.view === 'workspace' && next.ticket !== current.ticket || next.view === 'resources' && next.tab !== current.tab;
      if (!consumeNavigationApproval(hash) && leavesEditor && !(await requestNavigationPermission())) {
        if (sequence === navigationSequence.current) window.history.replaceState(null, '', lastHash.current);
        return;
      }
      if (sequence !== navigationSequence.current) return;
      lastHash.current = hash;
      setRoute(next);
      setMobileNav(false);
    };
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, [authenticated]);
  const loadTickets = useCallback(async () => {
    queueController.current?.abort();
    const controller = new AbortController();
    queueController.current = controller;
    setTicketsLoading(true);
    setTicketsError('');
    try {
      const list = await fetchReviewQueue(controller.signal);
      if (!controller.signal.aborted) setTickets(list);
    } catch (error) {
      if (!controller.signal.aborted) setTicketsError(friendlyError(error) || '工单加载失败，请重试。');
    } finally {
      if (!controller.signal.aborted) setTicketsLoading(false);
    }
  }, []);
  useEffect(() => {
    if (!authenticated || route.entry !== 'staff' || route.view !== 'workspace') return undefined;
    loadTickets();
    return () => queueController.current?.abort();
  }, [authenticated, route.entry, route.view, loadTickets]);
  useEffect(() => {
    if (!authenticated || route.entry !== 'staff') return undefined;
    const controller = new AbortController();
    setServiceStatus('checking');
    fetchHealth(controller.signal).then(health => {
      if (!controller.signal.aborted) setServiceStatus(health.status === 'healthy' ? 'healthy' : 'degraded');
    }).catch(() => {
      if (!controller.signal.aborted) setServiceStatus('offline');
    });
    return () => controller.abort();
  }, [authenticated, route.entry]);
  useEffect(() => {
    function expired() {
      setAuthenticated(false);
      setRole('');
      setUsername('');
      setTickets([]);
      setAuthNotice('登录已过期，请重新登录。');
      window.location.hash = 'staff';
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  }, []);
  async function handleLogin(event) {
    event.preventDefault();
    setLoginBusy(true);
    setAuthNotice('');
    try {
      const data = await login(loginUser, loginPass);
      setRole(data.role);
      setUsername(loginUser);
      setAuthenticated(true);
      setLoginPass('');
    } catch {
      setAuthNotice('登录失败，请检查账号、密码和服务连接。');
    } finally {
      setLoginBusy(false);
    }
  }
  async function handleLogout() {
    await navigate('#support');
    if (window.location.hash !== '#support') return;
    logout();
    setAuthenticated(false);
    setRole('');
    setUsername('');
    setTickets([]);
  }
  const stats = useMemo(() => ({
    total: tickets.length,
    high: tickets.filter(ticket => ['urgent', 'high'].includes(ticket.priority)).length
  }), [tickets]);
  const content = route.entry === 'workflow' ? <main className="wf-public" id="main-content" tabIndex={-1}><WorkflowPage onBack={() => navigate('#support')} /></main> : route.entry === 'customer' ? <CustomerSupportPage onWorkflowEntry={() => navigate('#workflow')} onStaffEntry={staffEntryEnabled ? () => navigate('#staff') : null} /> : !authenticated ? <main className="staff-login-page" id="main-content" tabIndex={-1}><section className="staff-login-card">
        <NavLink href="#support" className="back-to-customer"><ArrowLeft size={16} />返回用户咨询</NavLink>
        <div className="staff-login-heading"><span className="brand-mark"><MessagesSquare size={24} /></span><h1>登录员工后台</h1><p>SupportGPT 智能客服</p></div>
        {authNotice && <Notice>{authNotice}</Notice>}
        <form onSubmit={handleLogin} className="staff-login-form"><label htmlFor="username">用户名<input id="username" name="username" autoComplete="username" value={loginUser} onChange={event => setLoginUser(event.target.value)} required disabled={loginBusy} /></label><label htmlFor="password">密码<input id="password" name="password" type="password" autoComplete="current-password" value={loginPass} onChange={event => setLoginPass(event.target.value)} required disabled={loginBusy} /></label><Button type="submit" tone="primary" busy={loginBusy}>登录客服后台</Button></form><p className="staff-register">后台账号由管理员创建，不开放公开注册。</p>
      </section></main> : <div className={`app-shell ${mobileNav ? 'nav-open' : ''}`}>
          <aside className="app-sidebar" id="staff-navigation"><NavLink href="#staff/review" className="sidebar-brand"><span className="brand-mark"><MessagesSquare size={20} /></span><div><strong>SupportGPT</strong><small>智能客服工作空间</small></div></NavLink>
            <nav className="sidebar-nav" aria-label="主要功能">
              {[['workspace', '#staff/review', '人工处理台', Inbox, true], ['observability', '#staff/runs', 'Agent 运行监控', Activity, ['manager', 'admin'].includes(role)], ['resources', '#staff/resources', '资源管理', LibraryBig, role === 'admin'], ['workflow', '#staff/workflow', 'Agent 处理流程', GitBranch, true]].filter(item => item[4]).map(([view, href, label, Icon]) => <NavLink key={view} href={href} className={route.view === view ? 'active' : ''} aria-current={route.view === view ? 'page' : undefined}><Icon size={18} /><span>{label}</span></NavLink>)}
            </nav>
            <NavLink href="#support" className="sidebar-support"><MessagesSquare size={16} />用户咨询</NavLink>
            <div className={`sidebar-runtime runtime-${serviceStatus}`}><ShieldCheck size={16} /><span>{{
            healthy: '服务连接正常',
            checking: '正在检查服务',
            degraded: '部分服务不可用',
            offline: '无法连接服务'
          }[serviceStatus]}</span><small>最近一次健康检查结果</small></div>
            <div className="sidebar-account"><span className="account-avatar">{(username || '客').slice(0, 1)}</span><div><strong>{username || '当前员工'}</strong><small>{translateRole(role)}</small></div><Button className="icon-button" onClick={handleLogout} aria-label="退出登录"><LogOut size={17} /></Button></div>
          </aside>
          <div className="app-main"><header className="app-topbar"><Button className="icon-button mobile-menu" aria-label="切换导航" aria-expanded={mobileNav} aria-controls="staff-navigation" onClick={() => setMobileNav(!mobileNav)}><Menu size={20} /></Button><div><h1>{VIEW_TITLES[route.view]}</h1><p>{{
              workspace: '处理需要人工确认的回复和业务结果',
              observability: '查看运行记录并追查调用过程',
              resources: '管理工具、提示词和知识文档',
              workflow: '静态演示：了解系统怎样处理请求'
            }[route.view]}</p></div><NavLink href="#support" className="topbar-support">用户咨询</NavLink>{route.view === 'workspace' && <Button onClick={loadTickets} busy={ticketsLoading} aria-label="刷新工单"><RefreshCw size={16} /><span>刷新</span></Button>}</header>
            <main className="app-content" id="main-content" tabIndex={-1}>{!allowed ? <Notice>您的角色无权查看此页面。<NavLink href="#staff/review">返回人工处理台</NavLink></Notice> : route.view === 'workflow' ? <WorkflowPage embedded /> : route.view === 'resources' ? <ResourceManagementPage route={route} /> : route.view === 'observability' ? <ObservabilityPage route={route} /> : <section className={`workspace-page ${selectedTicket ? 'has-selection' : ''}`}>
              <div className="workspace-summary"><p>当前人工处理队列</p><span>{ticketsLoading ? '加载中…' : ticketsError ? '暂不可用' : `${stats.total} 张工单`}</span>{!ticketsLoading && !ticketsError && stats.high > 0 && <span className="status-badge badge-warning">{stats.high} 张高优先级</span>}</div>
              <div className="grid-dashboard"><TicketList tickets={tickets} selectedId={selectedTicket?.id} onSelect={ticket => updateRouteParams({
              ticket: ticket.id
            }, false)} loading={ticketsLoading} error={ticketsError} onRetry={loadTickets} route={route} /><div className="workspace-detail"><Button className="mobile-detail-back" onClick={() => updateRouteParams({
                ticket: null
              }, false)}><ArrowLeft size={16} />返回工单列表</Button><TicketDetails ticket={selectedTicket} userRole={role} onActionComplete={() => {
                updateRouteParams({
                  ticket: null
                });
                loadTickets();
              }} /></div></div>
            </section>}</main>
          </div>
        </div>;
  return <><a className="skip-link" href="#main-content" onClick={event => {
      event.preventDefault();
      document.getElementById('main-content')?.focus();
    }}>跳到主要内容</a><Suspense fallback={<LoadingState>正在加载页面…</LoadingState>}>{content}</Suspense></>;
}
