import React, { useEffect, useMemo, useState } from 'react';
import { AUTH_EXPIRED_EVENT, fetchHealth, fetchReviewQueue, login, logout } from './api/client';
import CustomerSupportPage from './components/CustomerSupportPage';
import TicketList from './components/TicketList';
import TicketDetails from './components/TicketDetails';
import ObservabilityPage from './components/ObservabilityPage';
import ResourceManagementPage from './components/ResourceManagementPage';
import WorkflowPage from './components/WorkflowPage';
import { translateRole } from './i18n';
import {
  Activity,
  ArrowLeft,
  Headphones,
  GitBranch,
  LibraryBig,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';

export default function App() {
  const staffEntryEnabled = import.meta.env.VITE_STAFF_ENTRY_ENABLED !== 'false';
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(localStorage.getItem('token')));
  const [entryMode, setEntryMode] = useState(() => (
    window.location.hash === '#workflow' ? 'workflow' : window.location.hash === '#support' ? 'customer' : localStorage.getItem('token') ? 'staff' : 'customer'
  ));
  const [userRole, setUserRole] = useState(() => localStorage.getItem('role') || '');
  const [username, setUsername] = useState(() => localStorage.getItem('username') || '');
  const [loginUser, setLoginUser] = useState('');
  const [loginPass, setLoginPass] = useState('');
  const [activeView, setActiveView] = useState('workspace');
  const [authNotice, setAuthNotice] = useState('');
  const [tickets, setTickets] = useState([]);
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [ticketsLoading, setTicketsLoading] = useState(false);
  const [ticketsError, setTicketsError] = useState('');
  const [serviceStatus, setServiceStatus] = useState('checking');

  // 架构演示使用公开 hash 入口，不加载受保护的后台数据。
  useEffect(() => {
    function handleHashChange() {
      if (window.location.hash === '#workflow') setEntryMode('workflow');
      else if (window.location.hash === '#support') setEntryMode('customer');
      else if (window.location.hash === '#staff') setEntryMode('staff');
    }
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  useEffect(() => {
    if (isAuthenticated && entryMode === 'staff') {
      setUserRole(localStorage.getItem('role') || 'agent');
      setUsername(localStorage.getItem('username') || '');
      loadTickets();
      checkServiceHealth();
    }
  }, [entryMode, isAuthenticated]);

  useEffect(() => {
    function handleAuthExpired() {
      setIsAuthenticated(false);
      setUserRole('');
      setUsername('');
      setTickets([]);
      setSelectedTicket(null);
      setActiveView('workspace');
      setEntryMode('staff');
      setAuthNotice('登录已过期，请重新登录。');
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, []);

  async function loadTickets() {
    setTicketsLoading(true);
    setTicketsError('');
    try {
      const list = await fetchReviewQueue();
      setTickets(list);
      setSelectedTicket((current) => list.find((ticket) => ticket.id === current?.id) || null);
      return list;
    } catch (error) {
      console.error('加载待人工处理队列失败：', error);
      setTicketsError(error.message || '加载待人工处理队列失败。');
      return [];
    } finally {
      setTicketsLoading(false);
    }
  }

  async function checkServiceHealth() {
    setServiceStatus('checking');
    try {
      const health = await fetchHealth();
      setServiceStatus(health.status === 'healthy' ? 'healthy' : 'degraded');
    } catch {
      setServiceStatus('offline');
    }
  }

  async function handleLogin(event) {
    event.preventDefault();
    try {
      await login(loginUser, loginPass);
      setAuthNotice('');
      setEntryMode('staff');
      setIsAuthenticated(true);
    } catch (error) {
      alert('登录失败，请检查用户名和密码。');
    }
  }

  function handleLogout() {
    logout();
    setIsAuthenticated(false);
    setTickets([]);
    setSelectedTicket(null);
    setActiveView('workspace');
    window.location.hash = 'support';
    setEntryMode('customer');
  }

  // 审批完成后刷新人工队列，已处理工单会自动移出。
  function handleActionComplete() {
    loadTickets();
    setSelectedTicket(null);
  }

  const workspaceStats = useMemo(() => ({
    total: tickets.length,
    active: tickets.filter((ticket) => ticket.status === 'pending_approval' || ticket.requires_tool_review).length,
    attention: tickets.filter((ticket) => ['urgent', 'high'].includes(ticket.priority)).length,
  }), [tickets]);

  if (entryMode === 'workflow') {
    return <main className="wf-public"><WorkflowPage onBack={() => {
      window.location.hash = 'support';
      setEntryMode('customer');
    }} /></main>;
  }

  if (entryMode === 'customer') {
    return <CustomerSupportPage onWorkflowEntry={() => {
      window.location.hash = 'workflow';
      setEntryMode('workflow');
    }} onStaffEntry={staffEntryEnabled ? () => {
      window.location.hash = 'staff';
      setEntryMode('staff');
    } : null} />;
  }

  if (!isAuthenticated) {
    return (
      <div className="staff-login-page">
        <div className="glass-card staff-login-card">
          <button type="button" className="back-to-customer" onClick={() => {
            window.location.hash = 'support';
            setEntryMode('customer');
          }}>
            <ArrowLeft size={15} /> 返回用户咨询
          </button>
          <div className="staff-login-heading">
            <h1><Sparkles color="#8b5cf6" size={24} /> SupportGPT 智能客服 Agent</h1>
            <p>客服员工后台</p>
          </div>

          {authNotice && <div className="auth-notice" role="alert">{authNotice}</div>}

          <form onSubmit={handleLogin} className="staff-login-form">
            <label><span>用户名</span><input type="text" value={loginUser} onChange={(event) => setLoginUser(event.target.value)} required /></label>
            <label><span>密码</span><input type="password" value={loginPass} onChange={(event) => setLoginPass(event.target.value)} required /></label>
            <button type="submit" className="btn btn-primary">登录客服后台</button>
          </form>

          <div className="staff-register">
            <span>后台账号由管理员统一创建，不开放公开注册。</span>
          </div>
        </div>
      </div>
    );
  }

  const canViewObservability = ['manager', 'admin'].includes(userRole);
  const canManageResources = userRole === 'admin';
  const isObservabilityView = activeView === 'observability' && canViewObservability;
  const isResourceView = activeView === 'resources' && canManageResources;
  const isWorkflowView = activeView === 'workflow';

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark"><Sparkles size={20} /></span>
          <div><strong>SupportGPT</strong><small>智能客服 Agent 后台</small></div>
        </div>

        <nav className="sidebar-nav" aria-label="主要功能">
          <span className="sidebar-nav-label">工作空间</span>
          <button className={activeView === 'workspace' ? 'active' : ''} onClick={() => setActiveView('workspace')}>
            <LayoutDashboard size={18} /><span>人工处理台</span><em>{workspaceStats.active}</em>
          </button>
          <button className={isWorkflowView ? 'active' : ''} onClick={() => setActiveView('workflow')}>
            <GitBranch size={18} /><span>Agent 处理流程</span>
          </button>
          {canViewObservability && (
            <button className={activeView === 'observability' ? 'active' : ''} onClick={() => setActiveView('observability')}>
              <Activity size={18} /><span>Agent 运行监控</span>
            </button>
          )}
          {canManageResources && (
            <button className={activeView === 'resources' ? 'active' : ''} onClick={() => setActiveView('resources')}>
              <LibraryBig size={18} /><span>资源管理</span>
            </button>
          )}
        </nav>

        <div className={`sidebar-runtime runtime-${serviceStatus}`}>
          <div className="runtime-title"><ShieldCheck size={15} /> {
            serviceStatus === 'healthy' ? 'Agent 服务正常'
              : serviceStatus === 'checking' ? '正在检查 Agent 服务'
                : serviceStatus === 'degraded' ? 'Agent 服务状态异常' : '无法连接 Agent 服务'
          }</div>
          <p>{serviceStatus === 'healthy' ? '普通问题自动处理，异常请求进入当前人工队列。' : '当前状态来自后端健康检查。'}</p>
        </div>

        <div className="sidebar-account">
          <span className="account-avatar">{(username || '客').slice(0, 1).toUpperCase()}</span>
          <div><strong>{username || '当前用户'}</strong><small>{translateRole(userRole)}</small></div>
          <button onClick={handleLogout} title="退出登录" aria-label="退出登录"><LogOut size={17} /></button>
        </div>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <div>
            <span className="topbar-eyebrow">{isWorkflowView ? '系统怎样处理用户问题' : isResourceView ? '工具与知识管理' : isObservabilityView ? '查看请求处理情况' : '需要人工处理的工单'}</span>
            <h1>{isWorkflowView ? 'Agent 处理流程' : isResourceView ? '资源管理' : isObservabilityView ? 'Agent 运行监控' : '异常与待审批工单'}</h1>
          </div>
          {!isObservabilityView && !isResourceView && !isWorkflowView && (
            <button className="icon-button" onClick={loadTickets} disabled={ticketsLoading} title="刷新工单" aria-label="刷新工单">
              <RefreshCw size={17} className={ticketsLoading ? 'spin' : ''} />
            </button>
          )}
        </header>

        <div className="app-content">
          {isWorkflowView ? (
            <WorkflowPage />
          ) : isResourceView ? (
            <ResourceManagementPage />
          ) : isObservabilityView ? (
            <ObservabilityPage />
          ) : (
            <section className="workspace-page">
              <div className="workspace-hero">
                <div>
                  <span className="workspace-eyebrow"><Headphones size={14} /> 人工处理队列</span>
                  <h2>{workspaceStats.active > 0 ? `还有 ${workspaceStats.active} 张异常工单等待确认` : '当前没有需要人工处理的工单'}</h2>
                  <p>普通问题由 Agent 自动回复。这里列出需要审批回复或核实业务操作结果的工单，两类任务分开处理。</p>
                </div>
                <div className="workspace-hero-actions">
                  {workspaceStats.attention > 0 && <span className="attention-pill">{workspaceStats.attention} 张高优工单</span>}
                  <span className="review-queue-count"><ShieldCheck size={16} /> {workspaceStats.total} 张待审核</span>
                </div>
              </div>

              <main className="grid-dashboard">
                <TicketList
                  tickets={tickets}
                  selectedId={selectedTicket?.id}
                  onSelect={setSelectedTicket}
                  loading={ticketsLoading}
                  error={ticketsError}
                  onRetry={loadTickets}
                />
                <TicketDetails ticket={selectedTicket} userRole={userRole} onActionComplete={handleActionComplete} />
              </main>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
