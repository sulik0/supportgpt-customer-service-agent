import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';
import { navigate } from '../navigation';
export function Button({
  children,
  tone = 'secondary',
  busy = false,
  className = '',
  disabled,
  ...props
}) {
  return <button type="button" className={`btn btn-${tone} ${className}`} disabled={disabled || busy} aria-busy={busy || undefined} {...props}>{busy && <Loader2 size={16} className="spin" />}{children}</button>;
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
  drawer = false
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
  return createPortal(<dialog ref={ref} className={drawer ? 'ui-dialog ui-drawer' : 'ui-dialog'} aria-label={title} onCancel={event => {
    event.preventDefault();
    closeRef.current?.();
  }} onClick={event => {
    if (event.target === event.currentTarget) {
      const bounds = ref.current.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeRef.current?.();
    }
  }}><header className="ui-dialog-header"><h2>{title}</h2><Button aria-label={`关闭${title}`} onClick={onClose} className="icon-button"><X size={18} /></Button></header>{children}</dialog>, document.body);
}
