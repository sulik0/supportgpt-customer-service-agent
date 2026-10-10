import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Button, Overlay } from './ui';

// Hook 与组件分开导出，避免编辑组件时破坏 Fast Refresh。
export function useConfirm() {
  const [request, setRequest] = useState(null);
  const [value, setValue] = useState('');
  const resolver = useRef(null);
  const inputId = useId();
  useEffect(() => () => resolver.current?.(null), []);
  const ask = useCallback(options => {
    resolver.current?.(null);
    setValue(options.initialValue || '');
    setRequest(options);
    return new Promise(resolve => {
      resolver.current = resolve;
    });
  }, []);
  function finish(result) {
    resolver.current?.(result);
    resolver.current = null;
    setRequest(null);
  }
  const confirmation = request ? <Overlay title={request.title} onClose={() => finish(null)}><form className="ui-dialog-body" onSubmit={event => {
      event.preventDefault();
      finish(request.input ? value.trim() : true);
    }}><p>{request.description}</p>{request.input && <label htmlFor={inputId}>{request.input}<input id={inputId} value={value} onChange={event => setValue(event.target.value)} required maxLength={500} data-autofocus /></label>}<div className="ui-dialog-actions"><Button onClick={() => finish(null)}>取消</Button><Button type="submit" tone={request.danger ? 'danger' : 'primary'} disabled={request.input && !value.trim()}>{request.confirmLabel || '确认'}</Button></div></form></Overlay> : null;
  return { ask, confirmation };
}

// 编辑期间提示离开，页面内导航与浏览器关闭分别处理。
export function useUnsavedChanges(dirty, ask) {
  useEffect(() => {
    if (!dirty) return undefined;
    const beforeUnload = event => {
      event.preventDefault();
      event.returnValue = '';
    };
    const guard = event => event.detail.guards.push(async () => Boolean(await ask({
      title: '放弃未保存的修改？',
      description: '离开后，本页尚未保存的编辑内容不会保留。',
      confirmLabel: '放弃修改',
      danger: true
    })));
    window.addEventListener('beforeunload', beforeUnload);
    window.addEventListener('supportgpt:before-navigate', guard);
    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('supportgpt:before-navigate', guard);
    };
  }, [dirty, ask]);
}
