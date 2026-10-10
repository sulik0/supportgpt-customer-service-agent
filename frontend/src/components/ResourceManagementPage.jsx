import { friendlyError } from '../errors';
import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, BookOpen, CheckCircle2, FileCode2, Plus, RefreshCw, Save, Settings2, ShieldCheck, Trash2, Wrench } from 'lucide-react';
import { createPromptCandidate, deleteAdminRagDocument, fetchAdminPrompts, fetchAdminRagDocuments, fetchAdminTools, reindexAdminRagDocuments, saveAdminRagDocument, updateAdminTool } from '../api/client';
import { translateRisk, translateRole } from '../i18n';
import { updateRouteParams } from '../navigation';
import { useConfirm, useUnsavedChanges } from './uiHooks';
const EMPTY_DOCUMENT = {
  id: '',
  title: '',
  category: 'faq',
  version: 'v1',
  content: '',
  metadata: '{}'
};
function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
function shortHash(value) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : '未设置';
}
export default function ResourceManagementPage({
  route
}) {
  const activeTab = route?.tab || 'tools';
  const [tools, setTools] = useState([]);
  const [prompts, setPrompts] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [notice, setNotice] = useState(null);
  const [promptDraft, setPromptDraft] = useState(null);
  const [editingDocumentId, setEditingDocumentId] = useState(null);
  const [documentDraft, setDocumentDraft] = useState(EMPTY_DOCUMENT);
  const [documentOriginal, setDocumentOriginal] = useState(EMPTY_DOCUMENT);
  const [loadError, setLoadError] = useState('');
  const [loaded, setLoaded] = useState({});
  const [query, setQuery] = useState('');
  const {
    ask,
    confirmation
  } = useConfirm();
  const dirty = Boolean(promptDraft) || JSON.stringify(documentDraft) !== JSON.stringify(documentOriginal);
  useUnsavedChanges(dirty, ask);
  async function loadResources(signal) {
    setLoading(true);
    setLoadError('');
    try {
      const value = await {
        tools: fetchAdminTools,
        prompts: fetchAdminPrompts,
        rag: fetchAdminRagDocuments
      }[activeTab](signal);
      if (signal?.aborted) return;
      if (activeTab === 'tools') setTools(value);else if (activeTab === 'prompts') setPrompts(value);else setDocuments(value);
      setLoaded(current => ({
        ...current,
        [activeTab]: true
      }));
    } catch (error) {
      if (!signal?.aborted) setLoadError(friendlyError(error) || '当前资源加载失败，请重试。');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }
  useEffect(() => {
    // 浏览器前进或后退切换 Tab 时，也清理已确认放弃的编辑内容。
    setPromptDraft(null);
    setDocumentDraft(EMPTY_DOCUMENT);
    setDocumentOriginal(EMPTY_DOCUMENT);
    setEditingDocumentId(null);
    setQuery('');
    setNotice(null);
    const controller = new AbortController();
    loadResources(controller.signal);
    return () => controller.abort();
  }, [activeTab]);
  async function changeTab(tab) {
    if (busyKey || tab === activeTab) return;
    if (dirty && !(await ask({
      title: '放弃未保存的修改？',
      description: '切换资源类型后，尚未保存的内容不会保留。',
      confirmLabel: '放弃修改',
      danger: true
    }))) return;
    setPromptDraft(null);
    setDocumentDraft(EMPTY_DOCUMENT);
    setDocumentOriginal(EMPTY_DOCUMENT);
    setEditingDocumentId(null);
    setQuery('');
    setNotice(null);
    updateRouteParams({
      tab
    });
  }
  async function toggleTool(tool) {
    const enabled = !tool.enabled;
    let reason = '管理员恢复启用';
    if (!enabled) {
      reason = await ask({
        title: '停用工具',
        description: `停用 ${tool.name} 后，后续请求不能再调用它。现有权限与审批规则不变。`,
        input: '停用原因',
        initialValue: '临时维护',
        confirmLabel: '停用工具',
        danger: true
      });
      if (!reason) return;
    }
    setBusyKey(tool.name);
    try {
      const updated = await updateAdminTool(tool.name, enabled, reason);
      setTools(items => items.map(item => item.name === tool.name ? updated : item));
      setNotice({
        type: 'success',
        text: `${tool.name} 已${enabled ? '启用' : '停用'}。`
      });
    } catch (error) {
      setNotice({
        type: 'error',
        text: friendlyError(error)
      });
    } finally {
      setBusyKey('');
    }
  }
  function beginPromptCandidate() {
    const source = prompts?.effective?.production?.payload;
    if (!source) return;
    const draft = clone(source);
    draft.version = `${source.version}-candidate`;
    setPromptDraft(draft);
  }
  function updatePromptTemplate(node, role, value) {
    setPromptDraft(current => ({
      ...current,
      templates: {
        ...current.templates,
        [node]: {
          ...current.templates[node],
          [role]: value
        }
      }
    }));
  }
  async function savePrompt(event) {
    event.preventDefault();
    setBusyKey('prompt');
    try {
      const created = await createPromptCandidate(promptDraft);
      const refreshed = await fetchAdminPrompts();
      setPrompts(refreshed);
      setPromptDraft(null);
      setNotice({
        type: 'success',
        text: `已保存提示词候选版本 ${created.version}。正式发布前，还需通过 EvalOps 评测和发布检查。`
      });
    } catch (error) {
      setNotice({
        type: 'error',
        text: friendlyError(error)
      });
    } finally {
      setBusyKey('');
    }
  }
  function beginDocument(document = null) {
    if (!document) {
      setEditingDocumentId(null);
      setDocumentDraft(EMPTY_DOCUMENT);
      setDocumentOriginal(EMPTY_DOCUMENT);
      return;
    }
    setEditingDocumentId(document.id);
    const nextDraft = {
      ...document,
      metadata: JSON.stringify(document.metadata || {}, null, 2)
    };
    setDocumentDraft(nextDraft);
    setDocumentOriginal(nextDraft);
  }
  async function saveDocument(event) {
    event.preventDefault();
    let metadata;
    try {
      metadata = JSON.parse(documentDraft.metadata || '{}');
    } catch {
      setNotice({
        type: 'error',
        text: '文档附加信息必须使用有效的 JSON 格式。'
      });
      return;
    }
    setBusyKey('document');
    try {
      const payload = {
        ...documentDraft,
        metadata
      };
      await saveAdminRagDocument(payload, editingDocumentId);
      setDocuments(await fetchAdminRagDocuments());
      setEditingDocumentId(null);
      setDocumentDraft(EMPTY_DOCUMENT);
      setDocumentOriginal(EMPTY_DOCUMENT);
      setNotice({
        type: 'success',
        text: `文档 ${payload.id} 及向量索引已更新。`
      });
    } catch (error) {
      setNotice({
        type: 'error',
        text: friendlyError(error)
      });
    } finally {
      setBusyKey('');
    }
  }
  async function removeDocument(document) {
    if (!(await ask({
      title: '删除知识文档？',
      description: `将删除「${document.title}」及其检索索引片段。删除后不能从此页面恢复。`,
      confirmLabel: '删除文档',
      danger: true
    }))) return;
    setBusyKey(`delete:${document.id}`);
    try {
      await deleteAdminRagDocument(document.id);
      setDocuments(items => items.filter(item => item.id !== document.id));
      if (editingDocumentId === document.id) beginDocument();
      setNotice({
        type: 'success',
        text: `文档 ${document.id} 已删除。`
      });
    } catch (error) {
      setNotice({
        type: 'error',
        text: friendlyError(error)
      });
    } finally {
      setBusyKey('');
    }
  }
  async function reindexDocuments() {
    setBusyKey('reindex');
    try {
      const result = await reindexAdminRagDocuments();
      setNotice({
        type: 'success',
        text: `已根据数据库中保存的原文，为 ${result.indexed_documents} 篇文档重建向量索引。`
      });
    } catch (error) {
      setNotice({
        type: 'error',
        text: friendlyError(error)
      });
    } finally {
      setBusyKey('');
    }
  }
  const toolStats = useMemo(() => ({
    total: tools.length,
    enabled: tools.filter(item => item.enabled).length,
    highRisk: tools.filter(item => item.risk_level === 'high').length
  }), [tools]);
  return <section className="resource-page">
      {confirmation}
      <div className="resource-hero">
        <div>
          <span className="resource-eyebrow"><ShieldCheck size={14} /> 管理员专属</span>
          <h2>管理工具、提示词和知识文档</h2>
          <p>管理员可以启停工具、创建提示词候选版本，以及编辑知识文档。工具权限和提示词发布规则仍按现有配置执行。</p>
        </div>
        <button className="btn btn-secondary" onClick={() => loadResources()} disabled={loading || Boolean(busyKey)}>
          <RefreshCw size={15} className={loading ? 'spin' : ''} /> 刷新当前列表
        </button>
      </div>

      {notice && <div className={`resource-notice ${notice.type}`} role={notice.type === 'error' ? 'alert' : 'status'}>
          {notice.type === 'success' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          {notice.text}
        </div>}

      <div className="resource-tabs" role="tablist" aria-label="资源类型">
        {[['tools', '业务工具', Wrench, tools.length], ['prompts', '提示词版本', FileCode2, prompts?.bundles?.length], ['rag', '知识文档', BookOpen, documents.length]].map(([tab, label, Icon, count], index) => <button key={tab} id={`resource-tab-${tab}`} role="tab" aria-selected={activeTab === tab} aria-controls={`resource-panel-${tab}`} tabIndex={activeTab === tab ? 0 : -1} className={activeTab === tab ? 'active' : ''} disabled={Boolean(busyKey)} onClick={() => changeTab(tab)} onKeyDown={event => {
        const keys = ['tools', 'prompts', 'rag'];
        const next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : null;
        if (next != null) {
          event.preventDefault();
          document.getElementById(`resource-tab-${keys[next]}`)?.focus();
        }
      }}><Icon size={16} />{label}<span>{loaded[tab] ? count : '—'}</span></button>)}
      </div>

      {loading ? <div className="resource-loading"><RefreshCw className="spin" /> 正在加载资源…</div> : null}
      {loadError && <div className="ui-notice notice-error" role="alert">{loadError}<button className="btn btn-secondary" onClick={() => loadResources()}>重试</button></div>}
      {!loading && !loadError && ['tools', 'rag'].includes(activeTab) && <label className="resource-search">筛选当前列表<input value={query} onChange={event => setQuery(event.target.value)} placeholder={activeTab === 'tools' ? '工具名称或说明' : '文档标题、类别、版本或 ID'} /></label>}

      {!loading && !loadError && activeTab === 'tools' && <div className="resource-section" role="tabpanel" id="resource-panel-tools" aria-labelledby="resource-tab-tools">
          <div className="resource-stat-row">
            <div><span>已注册</span><strong>{toolStats.total}</strong></div>
            <div><span>已启用</span><strong>{toolStats.enabled}</strong></div>
            <div><span>高风险</span><strong>{toolStats.highRisk}</strong></div>
          </div>
          <div className="data-table-wrap">
            <table className="data-table tool-table"><caption className="sr-only">已注册业务工具及权限配置</caption><thead><tr>{['工具与参数', '操作类型', '风险', '最低角色', '版本', '状态', '操作'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
              <tbody>{tools.filter(tool => `${tool.name} ${tool.description}`.toLowerCase().includes(query.toLowerCase())).map(tool => <tr key={tool.name}>
                <td><code>{tool.name}</code><p>{tool.description}</p><details><summary>参数与意图范围</summary><pre>{JSON.stringify({
                      input: tool.input_schema,
                      output: tool.output_schema,
                      allowed_intents: tool.allowed_intents
                    }, null, 2)}</pre></details>{tool.disabled_reason && <p className="tool-disabled-reason">停用原因：{tool.disabled_reason}</p>}</td>
                <td>{tool.operation_type === 'write' ? '写操作' : '只读查询'}</td><td><span className={`status-badge badge-${tool.risk_level === 'high' ? 'warning' : 'neutral'}`}>{translateRisk(tool.risk_level)}</span></td><td>{translateRole(tool.min_role)}</td><td><code>{tool.version}</code></td><td><span className={`resource-status ${tool.enabled ? 'enabled' : 'disabled'}`}>{tool.enabled ? '已启用' : '已停用'}</span></td>
                <td><button className={`btn ${tool.enabled ? 'btn-danger-outline' : 'btn-secondary'}`} disabled={Boolean(busyKey)} onClick={() => toggleTool(tool)}>{busyKey === tool.name ? '保存中…' : tool.enabled ? '停用' : '启用'}</button></td>
              </tr>)}</tbody>
            </table>
            {!tools.some(tool => `${tool.name} ${tool.description}`.toLowerCase().includes(query.toLowerCase())) && <div className="resource-empty">{query ? '没有符合筛选条件的工具，请调整关键词。' : '当前没有已注册工具。'}</div>}
          </div>
        </div>}

      {!loading && !loadError && activeTab === 'prompts' && prompts && <div className="resource-section prompt-admin-layout" role="tabpanel" id="resource-panel-prompts" aria-labelledby="resource-tab-prompts">
          <div className="prompt-admin-main">
            <div className="resource-section-heading">
              <div><h3>Prompt Bundle（提示词版本包）</h3><p>每个版本包按内容 Hash（摘要）保存。保存后不能覆盖原版本，需要创建新版本。</p></div>
              <button className="btn btn-primary" disabled={Boolean(promptDraft) || Boolean(busyKey)} onClick={beginPromptCandidate}><Plus size={15} /> 创建候选版本</button>
            </div>
            <div className="prompt-environments">
              {['production', 'staging'].map(environment => {
            const bundle = prompts.effective[environment];
            return <div key={environment}><span>{environment === 'production' ? '生产环境' : '预发布环境'}</span><strong>{bundle.version}</strong><code>{shortHash(bundle.bundle_id)}</code></div>;
          })}
            </div>
            <div className="prompt-bundle-list">
              {prompts.bundles.map(bundle => <details key={bundle.bundle_id}>
                  <summary><div><strong>{bundle.version}</strong><code>{shortHash(bundle.bundle_id)}</code></div><span>{Object.keys(bundle.payload.templates).length} 个节点</span></summary>
                  <pre>{JSON.stringify(bundle.payload, null, 2)}</pre>
                </details>)}
            </div>
          </div>

          <aside className="prompt-governance-card">
            <ShieldCheck size={20} /><h3>怎样发布新版本？</h3>
            <p>这个页面只能保存候选版本，不能直接切换生产环境的提示词。发布前，需要通过 EvalOps，在同一测试集上比较当前版本和候选版本，并检查是否符合发布要求。</p>
          </aside>

          {promptDraft && <form className="prompt-editor" onSubmit={savePrompt}>
              <div className="resource-section-heading"><div><h3>新建提示词候选版本</h3><p>保存时，后端会检查模板中的占位符是否符合 PromptOps 要求。</p></div><button type="button" className="icon-button" aria-label="关闭候选编辑器" onClick={async () => {
            if (await ask({
              title: '放弃候选版本？',
              description: '尚未保存的提示词内容将丢弃。',
              confirmLabel: '放弃修改',
              danger: true
            })) setPromptDraft(null);
          }} disabled={Boolean(busyKey)}>×</button></div>
              <label><span>版本标签</span><input value={promptDraft.version} onChange={event => setPromptDraft({
            ...promptDraft,
            version: event.target.value
          })} required disabled={Boolean(busyKey)} /></label>
              {['analyzer', 'resolver', 'qa'].map(node => <fieldset key={node} disabled={Boolean(busyKey)}><legend>{{
              analyzer: 'Analyzer（问题分析）',
              resolver: 'Resolver（回复生成）',
              qa: 'QA（回复检查）'
            }[node]}</legend>
                  {['system', 'user'].map(role => <label key={role}><span>{role === 'system' ? 'System（系统提示词）' : 'User（用户输入模板）'}</span><textarea rows={role === 'system' ? 5 : 3} value={promptDraft.templates[node][role]} onChange={event => updatePromptTemplate(node, role, event.target.value)} required /></label>)}
                </fieldset>)}
              <p className="obs-helper">将保存 {promptDraft.version} 的新候选版本，包含 Analyzer、Resolver 与 QA 模板；生产和预发布指针不会改变。</p>
              <div className="resource-form-actions"><button type="button" className="btn btn-secondary" disabled={Boolean(busyKey)} onClick={async () => {
            if (await ask({
              title: '放弃候选版本？',
              description: '尚未保存的提示词内容将丢弃。',
              confirmLabel: '放弃修改',
              danger: true
            })) setPromptDraft(null);
          }}>取消</button><button className="btn btn-primary" disabled={Boolean(busyKey)}><Save size={15} /> {busyKey === 'prompt' ? '正在保存…' : '保存候选版本'}</button></div>
            </form>}
        </div>}

      {!loading && !loadError && activeTab === 'rag' && <div className="resource-section rag-admin-layout" role="tabpanel" id="resource-panel-rag" aria-labelledby="resource-tab-rag">
          <div className="rag-document-list">
            <div className="resource-section-heading"><div><h3>知识文档</h3><p>数据库保存文档原文，ChromaDB 保存用于检索的向量索引。</p></div><button className="btn btn-secondary" onClick={reindexDocuments} disabled={Boolean(busyKey)}><RefreshCw size={14} className={busyKey === 'reindex' ? 'spin' : ''} /> 重建全部文档索引</button></div>
            {documents.length === 0 && <div className="resource-empty">还没有知识文档，可以在新建表单中添加第一篇。</div>}
            {documents.length > 0 && !documents.some(document => `${document.id} ${document.title} ${document.category} ${document.version}`.toLowerCase().includes(query.toLowerCase())) && <div className="resource-empty">没有符合筛选条件的文档，请调整关键词。</div>}
            {documents.filter(document => `${document.id} ${document.title} ${document.category} ${document.version}`.toLowerCase().includes(query.toLowerCase())).map(document => <article className={editingDocumentId === document.id ? 'selected' : ''} key={document.id}>
                <button className="rag-document-main" onClick={async () => {
            if (!dirty || (await ask({
              title: '切换文档？',
              description: '当前文档的未保存修改将丢弃。',
              confirmLabel: '放弃修改',
              danger: true
            }))) beginDocument(document);
          }} disabled={Boolean(busyKey)}><strong>{document.title}</strong><span>{document.id}</span><p>{document.content.slice(0, 130)}{document.content.length > 130 ? '…' : ''}</p><div><em>{document.category}</em><em>{document.version}</em></div></button>
                <button className="rag-delete" title="删除文档" aria-label={`删除文档 ${document.title}`} onClick={() => removeDocument(document)} disabled={Boolean(busyKey)}><Trash2 size={15} /></button>
              </article>)}
          </div>

          <form className="rag-document-editor" onSubmit={saveDocument}>
            <div className="resource-section-heading"><div><h3>{editingDocumentId ? '编辑文档' : '新建文档'}</h3><p>保存后，系统会将文档分成片段，并更新检索索引。</p></div>{editingDocumentId && <button type="button" className="btn btn-secondary" disabled={Boolean(busyKey)} onClick={async () => {
            if (!dirty || (await ask({
              title: '放弃当前文档修改？',
              description: '将打开空白文档表单，当前未保存的修改不会保留。',
              confirmLabel: '放弃修改',
              danger: true
            }))) beginDocument();
          }}><Plus size={14} /> 新建</button>}</div>
            <fieldset className="resource-form-fields" disabled={Boolean(busyKey)}>
            <div className="rag-form-grid"><label><span>文档 ID</span><input value={documentDraft.id} disabled={Boolean(editingDocumentId)} onChange={event => setDocumentDraft({
                ...documentDraft,
                id: event.target.value
              })} pattern="[a-zA-Z0-9][a-zA-Z0-9._-]*" required /></label><label><span>标题</span><input value={documentDraft.title} onChange={event => setDocumentDraft({
                ...documentDraft,
                title: event.target.value
              })} required /></label><label><span>分类</span><input value={documentDraft.category} onChange={event => setDocumentDraft({
                ...documentDraft,
                category: event.target.value
              })} required /></label><label><span>版本</span><input value={documentDraft.version} onChange={event => setDocumentDraft({
                ...documentDraft,
                version: event.target.value
              })} required /></label></div>
            <label><span>文档内容</span><textarea rows="14" value={documentDraft.content} onChange={event => setDocumentDraft({
              ...documentDraft,
              content: event.target.value
            })} required /></label>
            <label><span>文档附加信息（JSON）</span><textarea rows="5" value={documentDraft.metadata} onChange={event => setDocumentDraft({
              ...documentDraft,
              metadata: event.target.value
            })} /></label>
            </fieldset>
            <div className="resource-form-actions"><button className="btn btn-primary" disabled={Boolean(busyKey)}><Save size={15} /> {busyKey === 'document' ? '正在保存…' : '保存并更新索引'}</button></div>
          </form>
        </div>}
    </section>;
}
