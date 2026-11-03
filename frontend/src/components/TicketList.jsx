import '../styles/pages/workspace.css';
import React, { useMemo } from 'react';
import { AlertCircle, Inbox, MessageSquare, Search } from 'lucide-react';
import { DEPARTMENT_LABELS, translateDepartment, translatePriority, translateSentiment, translateStatus, translateSubject } from '../i18n';
import { Badge, Button, EmptyState, Skeleton } from './ui';
import { filterTickets } from './ticketFilters';
import { updateRouteParams } from '../navigation';
export default function TicketList({
  tickets = [],
  selectedId,
  onSelect,
  loading = false,
  error = '',
  onRetry,
  route
}) {
  const query = route?.filters.query || '';
  const priorityFilter = route?.filters.priority || 'all';
  const departmentFilter = route?.filters.department || 'all';
  const sentimentFilter = route?.filters.sentiment || 'all';
  const setQuery = value => updateRouteParams({
    query: value
  });
  const setPriorityFilter = value => updateRouteParams({
    priority: value
  });
  const setDepartmentFilter = value => updateRouteParams({
    department: value
  });
  const setSentimentFilter = value => updateRouteParams({
    sentiment: value
  });
  const hasFilters = Boolean(query.trim() || priorityFilter !== 'all' || departmentFilter !== 'all' || sentimentFilter !== 'all');
  const filteredTickets = useMemo(() => filterTickets(tickets, {
    query,
    department: departmentFilter,
    priority: priorityFilter,
    sentiment: sentimentFilter
  }), [departmentFilter, priorityFilter, query, sentimentFilter, tickets]);
  const departments = useMemo(() => [...new Set([...Object.keys(DEPARTMENT_LABELS), ...tickets.map(ticket => ticket.department || 'unassigned')])], [tickets]);
  function clearFilters() {
    updateRouteParams({
      query: null,
      priority: null,
      department: null,
      sentiment: null
    });
  }
  return <aside className="ticket-queue">
      <div className="queue-heading">
        <div>
          <span className="section-label">处理队列</span>
          <h2>待人工处理 <em>{loading || error ? '—' : tickets.length}</em></h2>
        </div>
      </div>

      <div className="queue-filters">
        <label className="queue-search">
          <Search size={15} />
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索客户、主题或编号" aria-label="搜索工单" />
        </label>
        <select value={departmentFilter} onChange={event => setDepartmentFilter(event.target.value)} aria-label="筛选工单部门">
          <option value="all">全部部门</option>
          {departments.map(department => <option key={department} value={department}>{translateDepartment(department === 'unassigned' ? null : department)}</option>)}
        </select>
        <select value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)} aria-label="筛选工单优先级">
          <option value="all">全部优先级</option>
          <option value="urgent">紧急</option>
          <option value="high">高</option>
          <option value="medium">中</option>
          <option value="low">低</option>
        </select>
        <select value={sentimentFilter} onChange={event => setSentimentFilter(event.target.value)} aria-label="筛选客户情绪">
          <option value="all">全部情绪</option>
          <option value="negative">负面</option>
          <option value="neutral">中性</option>
          <option value="positive">正面</option>
        </select>
        <div className="queue-filter-summary">
          <span>{loading ? '正在加载工单…' : error ? '工单数量暂不可用' : `符合条件 ${filteredTickets.length} 张`}</span>
          <button type="button" onClick={clearFilters} disabled={!hasFilters}>清除筛选</button>
        </div>
      </div>

      <div className="ticket-list" aria-live="polite">
        {loading && tickets.length === 0 ? <div className="workspace-skeleton" role="status">
            <span className="sr-only">正在加载待处理工单，请稍候</span>
            {[0, 1, 2].map(index => <div className="queue-skeleton-row" key={index}><Skeleton width="45%" /><Skeleton height={20} /><Skeleton /><Skeleton width="65%" /></div>)}
          </div> : error ? <EmptyState className="queue-request-state" role="alert" icon={<AlertCircle size={28} />} title="工单队列加载失败" description={error} actions={<Button onClick={onRetry}>重试</Button>} />
          : filteredTickets.length === 0 ? <EmptyState icon={<Inbox size={28} />} title={tickets.length === 0 ? '暂无待处理工单' : '没有匹配的工单'} description={tickets.length === 0 ? 'Agent 发现异常或需要审批时会自动加入这里' : '请调整搜索词、部门、优先级或情绪筛选'} /> : filteredTickets.map(ticket => {
        const isSelected = ticket.id === selectedId;
        const priority = ticket.priority || 'medium';
        const status = ticket.status || 'open';
        return <button type="button" key={ticket.id} className={`ticket-row ${isSelected ? 'selected' : ''}`} onClick={() => onSelect(ticket)} aria-pressed={isSelected}>
              <span className="ticket-row-body">
                <span className="ticket-row-meta">
                  <span className="workspace-mono">#{ticket.id}</span>
                  <span>{ticket.customer_id}</span>
                </span>
                <strong>{translateSubject(ticket.subject)}</strong>
                <span className="ticket-row-preview">{ticket.description || '未填写问题描述'}</span>
                <span className="ticket-row-footer">
                  <Badge tone={status === 'resolved' ? 'success' : status === 'pending_approval' ? 'accent' : 'neutral'}>{translateStatus(status)}</Badge>
                  <Badge tone={['urgent', 'high'].includes(priority) ? 'warning' : 'neutral'}>{translatePriority(priority)}优先级</Badge>
                  {ticket.requires_tool_review && <Badge tone="accent">业务待核实</Badge>}
                  <Badge>{translateDepartment(ticket.department)}</Badge>
                  <Badge><MessageSquare size={12} />{translateSentiment(ticket.sentiment)}</Badge>
                </span>
              </span>
            </button>;
      })}
      </div>

      <div className="queue-footer">{loading ? '正在更新列表…' : error ? '连接恢复后可重新加载' : `当前显示 ${filteredTickets.length} / ${tickets.length} 张工单`}</div>
    </aside>;
}
