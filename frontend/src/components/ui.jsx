import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';
import { navigate } from '../navigation';
export function Button({
  children,
  tone = 'secondary',
  size = 'md',
  busy = false,
  className = '',
  disabled,
  ...props
}) {
  return <button type="button" className={`btn btn-${tone} btn-${size} ${className}`} disabled={disabled || busy} aria-busy={busy || undefined} {...props}>{busy && <Loader2 size={16} className="spin" />}{children}</button>;
}
export function StatusBadge({
  children,
  tone = 'neutral'
}) {
  return <span className={`status-badge badge-${tone}`}>{children}</span>;
}
export function Notice({
  children,
  tone = 'error'
}) {
  return <div className={`ui-notice notice-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{tone === 'success' ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}<div>{children}</div></div>;
}
export function LoadingState({
  children = '正在加载…'
}) {
  return <div className="ui-loading" role="status"><Loader2 size={20} className="spin" />{children}</div>;
}
export function NavLink({
  href,
  children,
  onClick,
  ...props
}) {
  return <a href={href} {...props} onClick={event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href).then(() => onClick?.());
  }}>{children}</a>;
}

// 原生 Dialog 管理焦点与背景隔离，关闭后恢复到触发元素。
export function Overlay({
  children,
  title,
  onClose,
  drawer = false,
  className = '',
  id
}) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const element = ref.current;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    element.showModal();
    element.querySelector('[data-autofocus]')?.focus();
    document.body.style.overflow = 'hidden';
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return createPortal(<dialog ref={ref} id={id} className={`ui-dialog ${drawer ? 'ui-drawer' : ''} ${className}`} aria-label={title} onCancel={event => {
    event.preventDefault();
    closeRef.current?.();
  }} onClick={event => {
    if (event.target === event.currentTarget) {
      const bounds = ref.current.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeRef.current?.();
    }
  }}><header className="ui-dialog-header"><h2>{title}</h2><Button aria-label={`关闭${title}`} onClick={onClose} className="icon-button"><X size={18} /></Button></header>{children}</dialog>, document.body);
}


/** Shared primitives. Native HTML attributes and className pass through. */
export function Card({ title, actions, children, className = '', ...props }) {
  return <section className={`ui-card ${className}`} {...props}>
    {(title || actions) && <header className="ui-card-header">{title && <h2>{title}</h2>}{actions}</header>}
    <div className="ui-card-body">{children}</div>
  </section>;
}

export function Badge({ tone = 'neutral', children, className = '', ...props }) {
  return <span className={`ui-badge badge-${tone} ${className}`} {...props}>{children}</span>;
}

export function MetricCard({ label, value, hint, className = '', ...props }) {
  return <Card className={`ui-metric-card ${className}`} {...props}>
    <div className="ui-metric-label">{label}</div>
    <div className="ui-metric-value">{value ?? '—'}</div>
    {hint != null && <div className="ui-metric-hint">{hint}</div>}
  </Card>;
}

export function Chip({ selected = false, children, className = '', ...props }) {
  return <button type="button" aria-pressed={selected} className={`ui-chip ${selected ? 'is-selected' : ''} ${className}`} {...props}>{children}</button>;
}

export function EmptyState({ icon, title = '暂无内容', description, actions, children, className = '', ...props }) {
  return <div className={`ui-empty-state ${className}`} {...props}>
    {icon && <span className="ui-empty-icon" aria-hidden="true">{icon}</span>}
    <h3>{title}</h3>{description && <p>{description}</p>}{children}{actions}
  </div>;
}

export function Skeleton({ width = '100%', height = 16, className = '', style, ...props }) {
  return <span aria-hidden="true" className={`ui-skeleton ${className}`} style={{ width, height, ...style }} {...props} />;
}
