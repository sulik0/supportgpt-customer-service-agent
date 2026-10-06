import { translateDepartment, translateSubject } from '../i18n.js';

// 队列接口返回完整列表，组合条件只筛选已有数据，不触发 Agent。
export function filterTickets(tickets, { query = '', department = 'all', priority = 'all', sentiment = 'all' } = {}) {
  const text = query.trim().toLowerCase();
  return tickets.filter((ticket) => {
    if (department !== 'all' && (ticket.department || 'unassigned') !== department) return false;
    if (priority !== 'all' && ticket.priority !== priority) return false;
    if (sentiment !== 'all' && ticket.sentiment !== sentiment) return false;
    const searchable = [ticket.id, ticket.customer_id, ticket.subject, translateSubject(ticket.subject), ticket.description, translateDepartment(ticket.department)].join(' ').toLowerCase();
    return !text || searchable.includes(text);
  });
}
