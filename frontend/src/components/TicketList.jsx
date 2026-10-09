import React, { useMemo, useState } from 'react';
import { AlertCircle, ChevronRight, Inbox, MessageSquare, RefreshCw, Search } from 'lucide-react';
import { DEPARTMENT_LABELS, translateDepartment, translatePriority, translateSentiment, translateStatus, translateSubject } from '../i18n';
import { filterTickets } from './ticketFilters';

export default function TicketList({ tickets = [], selectedId, onSelect, loading = false, error = '', onRetry }) {
  const [query, setQuery] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [departmentFilter, setDepartmentFilter] = useState('all');
  const [sentimentFilter, setSentimentFilter] = useState('all');
  const hasFilters = Boolean(query.trim() || priorityFilter !== 'all' || departmentFilter !== 'all' || sentimentFilter !== 'all');

  const filteredTickets = useMemo(() => filterTickets(tickets, {
    query, department: departmentFilter, priority: priorityFilter, sentiment: sentimentFilter,
  }), [departmentFilter, priorityFilter, query, sentimentFilter, tickets]);
  const departments = useMemo(() => [...new Set([
    ...Object.keys(DEPARTMENT_LABELS), ...tickets.map((ticket) => ticket.department || 'unassigned'),
  ])], [tickets]);

  function clearFilters() {
    setQuery('');
    setPriorityFilter('all');
    setDepartmentFilter('all');
    setSentimentFilter('all');
  }

  return (
    <aside className="ticket-queue">
      <div className="queue-heading">
        <div>
          <span className="section-label">处理队列</span>
          <h2>待人工处理 <em>{tickets.length}</em></h2>
        </div>
      </div>

      <div className="queue-filters">
        <label className="queue-search">
          <Search size={15} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索客户、主题或编号" aria-label="搜索工单" />
        </label>
        <select value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value)} aria-label="筛选工单部门">
          <option value="all">全部部门</option>
          {departments.map((department) => <option key={department} value={department}>{translateDepartment(department === 'unassigned' ? null : department)}</option>)}
        </select>
        <select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value)} aria-label="筛选工单优先级">
          <option value="all">全部优先级</option>
          <option value="urgent">紧急</option>
          <option value="high">高</option>
          <option value="medium">中</option>
          <option value="low">低</option>
        </select>
        <select value={sentimentFilter} onChange={(event) => setSentimentFilter(event.target.value)} aria-label="筛选客户情绪">
          <option value="all">全部情绪</option>
          <option value="negative">负面</option>
          <option value="neutral">中性</option>
          <option value="positive">正面</option>
        </select>
        <div className="queue-filter-summary">
          <span>符合条件 {filteredTickets.length} 张</span>
          <button type="button" onClick={clearFilters} disabled={!hasFilters}>清除筛选</button>
        </div>
      </div>

      <div className="ticket-list" aria-live="polite">
        {loading && tickets.length === 0 ? (
          <div className="queue-empty queue-request-state">
            <RefreshCw className="spin" size={28} />
            <strong>正在加载待处理工单</strong>
            <span>请稍候……</span>
          </div>
        ) : error ? (
          <div className="queue-empty queue-request-state error" role="alert">
            <AlertCircle size={28} />
            <strong>工单队列加载失败</strong>
            <span>{error}</span>
            <button type="button" className="btn btn-secondary" onClick={onRetry}>重试</button>
          </div>
        ) : filteredTickets.length === 0 ? (
          <div className="queue-empty">
            <Inbox size={28} />
            <strong>{tickets.length === 0 ? '暂无待处理工单' : '没有匹配的工单'}</strong>
            <span>{tickets.length === 0 ? 'Agent 发现异常或需要审批时会自动加入这里' : '请调整搜索词、部门、优先级或情绪筛选'}</span>
          </div>
        ) : filteredTickets.map((ticket) => {
          const isSelected = ticket.id === selectedId;
          const priority = ticket.priority || 'medium';
          const status = ticket.status || 'open';
          return (
            <button
              type="button"
              key={ticket.id}
              className={`ticket-row ${isSelected ? 'selected' : ''}`}
              onClick={() => onSelect(ticket)}
              aria-pressed={isSelected}
            >
              <span className={`priority-rail priority-${priority}`} />
              <span className="ticket-row-body">
                <span className="ticket-row-meta">
                  <span>#{ticket.id}</span>
                  <span>{ticket.customer_id}</span>
                  <span className={`status-dot status-${status}`} />
                  <span>{translateStatus(status)}</span>
                  {ticket.requires_tool_review && <span>业务待核实</span>}
                </span>
                <strong>{translateSubject(ticket.subject)}</strong>
                <span className="ticket-row-preview">{ticket.description || '未填写问题描述'}</span>
                <span className="ticket-row-footer">
                  <span>{translateDepartment(ticket.department)}</span>
                  <span className={`mini-priority priority-text-${priority}`}>
                    {['urgent', 'high'].includes(priority) && <AlertCircle size={12} />}
                    {translatePriority(priority)}优先级
                  </span>
                  <span><MessageSquare size={12} /> {translateSentiment(ticket.sentiment)}</span>
                </span>
              </span>
              <ChevronRight className="ticket-row-arrow" size={17} />
            </button>
          );
        })}
      </div>

      <div className="queue-footer">当前显示 {filteredTickets.length} / {tickets.length} 张工单</div>
    </aside>
  );
}
