'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (v) =>
    String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString());
  const fmtBytes = (b) => {
    if (!b) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
    return `${(b / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
  };
  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
  const fmtDur = (ms) => {
    if (ms == null || ms < 0) return '';
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
  };
  const since = (start, end) => (start ? fmtDur((end ? Date.parse(end) : Date.now()) - Date.parse(start)) : '');

  const store = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem(key);
        return v === null ? fallback : JSON.parse(v);
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {
        /* storage unavailable */
      }
    },
  };

  // ---------------------------------------------------------------- icons
  // Codicon-style 16px line icons.
  const PATHS = {
    database: '<ellipse cx="8" cy="3.6" rx="5" ry="2"/><path d="M3 3.6v8.8c0 1.1 2.2 2 5 2s5-.9 5-2V3.6"/><path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
    download: '<path d="M8 2v8.5M4.5 7 8 10.5 11.5 7"/><path d="M2.5 11.5v2h11v-2"/>',
    search: '<circle cx="6.8" cy="6.8" r="4.3"/><path d="M10 10l4 4"/>',
    chevronDown: '<path d="M4 6l4 4 4-4"/>',
    chevronRight: '<path d="M6 4l4 4-4 4"/>',
    close: '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>',
    check: '<path d="M3.5 8.5l3 3 6-7"/>',
    pass: '<circle cx="8" cy="8" r="6"/><path d="M5.5 8.2l1.8 1.8 3.3-3.6"/>',
    error: '<circle cx="8" cy="8" r="6"/><path d="M6 6l4 4M10 6l-4 4"/>',
    slash: '<circle cx="8" cy="8" r="6"/><path d="M3.8 12.2l8.4-8.4"/>',
    pending: '<circle cx="8" cy="8" r="6"/>',
    skipped: '<circle cx="8" cy="8" r="6" stroke-dasharray="2 2.4"/>',
    loading: '<path d="M8 2a6 6 0 1 1-6 6"/>',
    warning: '<path d="M8 2l6.5 11.5h-13z"/><path d="M8 6.5v3.2M8 11.7v.1"/>',
    info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.3v3.9M8 4.9v.1"/>',
    broadcast: '<circle cx="8" cy="8" r="1.4"/><path d="M5.2 5.2a4 4 0 0 0 0 5.6M10.8 5.2a4 4 0 0 1 0 5.6M3.1 3.1a7 7 0 0 0 0 9.8M12.9 3.1a7 7 0 0 1 0 9.8"/>',
    disconnect: '<path d="M2.5 13.5l11-11"/><path d="M5.3 8.6 4 9.9a2.3 2.3 0 0 0 3.2 3.2l1.3-1.3M10.7 7.4 12 6.1a2.3 2.3 0 0 0-3.2-3.2L7.5 4.2"/>',
    linkExternal: '<path d="M9 2.5h4.5V7M13.5 2.5l-6 6"/><path d="M11.5 9.5v4h-9v-9h4"/>',
    follow: '<path d="M8 2.5v8.5M4.5 7.5 8 11l3.5-3.5"/><path d="M3 13.8h10"/>',
    rollback: '<path d="M3.5 6.5h6a3.5 3.5 0 0 1 0 7H6"/><path d="M6 3.5l-3 3 3 3"/>',
    file: '<path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3"/>',
    table: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 6.5h12M6 6.5V13"/>',
    stop: '<rect x="4" y="4" width="8" height="8" rx="1"/>',
  };
  const icon = (name, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 16 16" aria-hidden="true">${PATHS[name] || ''}</svg>`;
  const hydrateIcons = (root = document) =>
    root.querySelectorAll('[data-icon]').forEach((el) => {
      el.outerHTML = icon(el.dataset.icon, el.className); // keep layout classes such as pane-chevron
    });

  const STATUS = {
    running: ['Running', 'loading', 'c-info spin'],
    succeeded: ['Succeeded', 'pass', 'c-ok'],
    up_to_date: ['Up to date', 'pass', 'c-ok'],
    failed: ['Failed', 'error', 'c-err'],
    cancelled: ['Cancelled', 'slash', 'c-warn'],
    interrupted: ['Interrupted', 'slash', 'c-warn'],
    rolled_back: ['Rolled back', 'rollback', 'c-info'],
  };
  const statusIcon = (status) => {
    const [, ic, cls] = STATUS[status] || ['', 'pending', 'c-dim'];
    return icon(ic, cls);
  };
  const badge = (status) => `<span class="badge">${statusIcon(status)}${esc((STATUS[status] || [status])[0])}</span>`;

  const STAGE_ICON = {
    pending: ['pending', 'c-dim'],
    running: ['loading', 'c-info spin'],
    done: ['pass', 'c-ok'],
    failed: ['error', 'c-err'],
    cancelled: ['slash', 'c-warn'],
    skipped: ['skipped', 'c-dim'],
  };

  const classCss = (c) => {
    const k = String(c || '').toLowerCase();
    if (k.startsWith('non')) return 'cls cls-non-restricted';
    if (k.startsWith('restricted')) return 'cls cls-restricted';
    if (k.startsWith('prohibited')) return 'cls cls-prohibited';
    return 'cls';
  };

  let snap = null;
  let database = null;
  let wasBusy = null;

  // ------------------------------------------------------------------ API

  async function api(url, options = {}) {
    const res = await fetch(url, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
    return body;
  }
  const post = (url, data = {}) => api(url, { method: 'POST', body: JSON.stringify(data) });

  async function refreshStatus() {
    try {
      const body = await api('/api/status');
      database = body.database;
      snap = body;
      render();
    } catch (err) {
      $('sb-server').innerHTML = `${icon('error')}Status unavailable`;
    }
  }

  // ------------------------------------------- notifications and dialogs

  function notify(message, type = 'error') {
    const el = document.createElement('div');
    el.className = 'toast';
    const ic = { error: ['error', 'c-err'], warning: ['warning', 'c-warn'], info: ['info', 'c-info'] }[type];
    el.innerHTML = `${icon(ic[0], ic[1])}<div class="toast-msg">${esc(message)}</div><button class="icon-btn" aria-label="Close">${icon('close')}</button>`;
    el.querySelector('button').onclick = () => el.remove();
    $('notifications').appendChild(el);
    if (type !== 'error') setTimeout(() => el.remove(), 8000);
  }

  function confirmDialog({ title, detail = '', ok = 'OK', type = 'warning' }) {
    const dlg = $('modal');
    $('modal-icon').innerHTML = icon(type === 'warning' ? 'warning' : 'info', type === 'warning' ? 'c-warn' : 'c-info');
    $('modal-title').textContent = title;
    $('modal-detail').textContent = detail;
    $('modal-ok').textContent = ok;
    return new Promise((resolve) => {
      const done = (value) => {
        dlg.removeEventListener('close', onClose);
        if (dlg.open) dlg.close();
        resolve(value);
      };
      const onClose = () => done(false);
      $('modal-ok').onclick = () => done(true);
      $('modal-cancel').onclick = () => done(false);
      dlg.addEventListener('close', onClose);
      dlg.showModal();
      $('modal-ok').focus();
    });
  }

  // ----------------------------------------------------------------- tabs

  const FIXED_TABS = [
    { id: 'ingest', title: 'Ingest', icon: 'download' },
    { id: 'lookup', title: 'Look up', icon: 'search' },
  ];
  const MAX_FRN_TABS = 8;
  let frnTabs = []; // { id, frn, title, icon, data, error }
  let activeTab = store.get('frt.tab', 'ingest');
  if (!FIXED_TABS.some((t) => t.id === activeTab)) activeTab = 'ingest';

  function renderTabs() {
    $('tabs').innerHTML = [...FIXED_TABS, ...frnTabs]
      .map(
        (t) => `<div class="tab${t.id === activeTab ? ' active' : ''}" role="tab" tabindex="0" aria-selected="${t.id === activeTab}" data-tab="${esc(t.id)}" title="${esc(t.title)}">
          ${icon(t.icon, 'tab-icon')}<span class="tab-label">${esc(t.title)}</span>
          ${t.frn ? `<button class="tab-close" data-close="${esc(t.id)}" aria-label="Close ${esc(t.title)}">${icon('close')}</button>` : '<span class="tab-close-spacer"></span>'}
        </div>`
      )
      .join('');
  }

  function renderBreadcrumbs() {
    const parts = activeTab.startsWith('frn:')
      ? [['database', 'FRT'], ['search', 'Look up'], ['file', activeTab.slice(4)]]
      : activeTab === 'lookup'
        ? [['database', 'FRT'], ['search', 'Look up']]
        : [['database', 'FRT'], ['download', 'Ingest']];
    $('breadcrumbs').innerHTML = parts
      .map(([ic, label], i) => `${i ? `<span class="crumb-sep">${icon('chevronRight')}</span>` : ''}${icon(ic)}<span>${esc(label)}</span>`)
      .join(' ');
  }

  function activate(id) {
    activeTab = id;
    if (FIXED_TABS.some((t) => t.id === id)) store.set('frt.tab', id);
    const isFrn = id.startsWith('frn:');
    $('view-ingest').hidden = id !== 'ingest';
    $('view-lookup').hidden = id !== 'lookup';
    $('view-frn').hidden = !isFrn;
    if (isFrn) renderFrn(frnTabs.find((t) => t.id === id));
    document.querySelectorAll('.ab-item').forEach((b) => {
      b.classList.toggle('active', b.dataset.view === (id === 'ingest' ? 'ingest' : 'lookup'));
    });
    renderTabs();
    renderBreadcrumbs();
    $('editor').scrollTop = 0;
    if (id === 'lookup') $('search').focus();
  }

  function openFrn(frn) {
    const id = `frn:${frn}`;
    let tab = frnTabs.find((t) => t.id === id);
    if (!tab) {
      tab = { id, frn, title: `FRN ${frn}`, icon: 'file', data: null, error: null };
      frnTabs.push(tab);
      if (frnTabs.length > MAX_FRN_TABS) frnTabs.shift();
      api(`/api/frn/${encodeURIComponent(frn)}`)
        .then((data) => (tab.data = data))
        .catch((err) => (tab.error = err.message))
        .finally(() => activeTab === id && renderFrn(tab));
    }
    activate(id);
  }

  function closeTab(id) {
    const i = frnTabs.findIndex((t) => t.id === id);
    if (i < 0) return;
    frnTabs.splice(i, 1);
    if (activeTab === id) activate(frnTabs[i] ? frnTabs[i].id : frnTabs[i - 1] ? frnTabs[i - 1].id : 'lookup');
    else renderTabs();
  }

  $('tabs').addEventListener('click', (e) => {
    const close = e.target.closest('[data-close]');
    if (close) return closeTab(close.dataset.close);
    const tab = e.target.closest('[data-tab]');
    if (tab) activate(tab.dataset.tab);
  });
  $('tabs').addEventListener('auxclick', (e) => {
    const tab = e.target.closest('[data-tab]');
    if (e.button === 1 && tab) closeTab(tab.dataset.tab);
  });
  $('tabs').addEventListener('keydown', (e) => {
    const tab = e.target.closest('[data-tab]');
    if (tab && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      activate(tab.dataset.tab);
    }
  });
  document.querySelectorAll('.ab-item').forEach((b) => (b.onclick = () => activate(b.dataset.view)));

  // --------------------------------------------------------------- render

  function render() {
    if (!snap) return;
    renderIngest();
    renderSidebar();
    renderStatusBar();
  }

  function overallMeta(job) {
    const parts = [];
    if (job.status === 'running') parts.push(`${Math.round((job.progress || 0) * 100)}%`);
    parts.push(since(job.startedAt, job.finishedAt));
    if (job.pdf) parts.push(job.pdf);
    return parts.join(' · ');
  }

  function renderIngest() {
    const job = snap.job;
    const running = job && job.status === 'running';
    const busy = Boolean(snap.busy);

    $('btn-ingest').disabled = busy;
    $('btn-ingest').querySelector('.btn-label').textContent = running
      ? 'Ingest Running…'
      : snap.busy === 'rollback'
        ? 'Rollback Running…'
        : 'Download & Ingest Latest FRT';
    $('chk-force').disabled = busy;
    $('btn-cancel').hidden = !running;
    $('btn-cancel').disabled = !(running && job.canCancel) || (job && job.cancelRequested);
    $('btn-cancel').title = running && !job.canCancel ? 'The swap into live cannot be interrupted' : '';
    $('job-badge').innerHTML = job ? badge(job.status) : '';
    $('job-empty').hidden = Boolean(job);

    if (!job) {
      $('job').hidden = true;
      return;
    }
    $('job').hidden = false;

    const finishedOk = job.status === 'succeeded' || job.status === 'up_to_date';
    const fill = $('overall-fill');
    fill.style.width = `${finishedOk ? 100 : Math.round((job.progress || 0) * 100)}%`;
    fill.className = `progress-fill${job.status === 'failed' ? ' err' : finishedOk ? ' ok' : ''}`;

    const runningStages = job.stages.filter((s) => s.status === 'running');
    const labels = {
      running: job.cancelRequested ? 'Cancelling…' : runningStages.length ? runningStages.map((s) => s.label).join(' + ') : 'Working…',
      succeeded: `${job.pdf} is live`,
      up_to_date: `${job.pdf} is already live`,
      failed: 'Ingest failed',
      cancelled: 'Ingest cancelled',
    };
    $('overall-label').textContent = labels[job.status] || job.status;
    $('overall-meta').textContent = overallMeta(job);

    const alert = $('job-alert');
    let callout = null;
    if (job.status === 'failed' || job.status === 'cancelled') {
      callout = [job.status === 'failed' ? 'error' : 'warning', `${job.error || 'Stopped.'} The live database was not modified unless the output says otherwise.`];
    } else if (job.status === 'up_to_date') {
      callout = ['success', `${job.pdf} is already the live edition, so nothing was changed. Tick "Re-ingest" to load it again anyway.`];
    } else if (job.warnings && job.warnings.length) {
      callout = ['warning', job.warnings.join(' · ')];
    }
    alert.hidden = !callout;
    if (callout) {
      const ic = { error: 'error', warning: 'warning', success: 'pass' }[callout[0]];
      alert.className = `callout ${callout[0]}`;
      alert.innerHTML = `${icon(ic)}<span>${esc(callout[1])}</span>`;
    }

    $('stages').innerHTML = job.stages
      .map((s) => {
        const [ic, icCls] = STAGE_ICON[s.status] || STAGE_ICON.pending;
        const showBar = s.status === 'running' || s.status === 'failed' || s.status === 'cancelled';
        const indeterminate = s.status === 'running' && s.progress == null;
        const width = Math.round((s.progress || 0) * 100);
        const pct = s.status === 'running' && s.progress != null ? ` · <span class="pct">${width}%</span>` : '';
        return `<li class="tree-item ${s.status}">
          ${icon(ic, icCls)}
          <span class="ti-label">${esc(s.label)}</span>
          <span class="ti-desc" title="${esc(s.detail)}">${esc(s.detail)}</span>
          <span class="ti-time"><span class="elapsed" data-start="${esc(s.startedAt || '')}">${since(s.startedAt, s.finishedAt)}</span>${pct}</span>
          ${showBar ? `<div class="ti-progress"><div class="progress-fill${s.status === 'failed' ? ' err' : s.status === 'cancelled' ? '' : ''}${indeterminate ? ' indeterminate' : ''}" style="width:${width}%"></div></div>` : ''}
        </li>`;
      })
      .join('');

    $('log-channel').textContent = `FRT Ingest · ${job.id}`;
    const dl = $('log-download');
    dl.hidden = false;
    dl.href = `/api/jobs/${encodeURIComponent(job.id)}/log`;
  }

  function renderSidebar() {
    const cfg = snap.config || {};
    $('live-name').textContent = cfg.liveDb || '';
    $('prev-name').textContent = cfg.previousDb || '';

    // History
    const items = snap.history || [];
    $('history').innerHTML = items.length
      ? items
          .slice(0, 15)
          .map((h) => {
            const what = h.type === 'rollback' ? `Rollback → ${h.pdf || 'previous'}` : h.pdf || 'Ingest';
            const log = h.type === 'rollback' ? '' : ` · <a href="/api/jobs/${encodeURIComponent(h.id)}/log" target="_blank" rel="noopener">log</a>`;
            return `<div class="history-item" title="${esc((STATUS[h.status] || [h.status])[0])}">
              ${statusIcon(h.status)}
              <div class="history-main"><span class="history-title mono">${esc(what)}${h.force ? ' <span class="dim">(forced)</span>' : ''}</span></div>
              <div class="history-sub">${fmtDate(h.startedAt)} · ${since(h.startedAt, h.finishedAt)}${h.frns ? ` · ${fmtNum(h.frns)} FRNs` : ''}${log}</div>
              ${h.error ? `<div class="history-err">${esc(h.error)}</div>` : ''}
            </div>`;
          })
          .join('')
      : '<p class="side-empty">No ingests yet.</p>';

    if (!database) return;
    if (database.error) {
      $('live').innerHTML = `<p class="side-empty">${icon('error', 'c-err')} ${esc(database.error)}</p>`;
      return;
    }

    // Live database
    const cur = snap.current;
    const live = database.live;
    if (!live.exists) {
      $('live').innerHTML = '<p class="side-empty">No FRT data loaded yet. Run an ingest to populate the database.</p>';
    } else {
      $('live').innerHTML = `
        <div class="metrics">
          <div><div class="metric-value">${fmtNum(live.frnCount)}</div><div class="metric-label">FRNs</div></div>
          <div><div class="metric-value">${fmtBytes(live.bytes)}</div><div class="metric-label">On disk</div></div>
        </div>
        <dl class="props">
          <dt>Edition</dt><dd class="mono">${esc(cur && cur.pdf ? cur.pdf : 'unknown')}</dd>
          <dt>Loaded</dt><dd>${fmtDate(cur && cur.ingestedAt)}</dd>
        </dl>
        ${live.tables
          .map((t) => {
            // Exact counts recorded at swap time beat InnoDB's row estimates.
            const exact = t.name === 'dbo_frn' ? t.rows : cur && cur.counts ? cur.counts[t.name] : undefined;
            return `<div class="list-row" title="${esc(t.name)}">${icon('table')}<span class="label mono">${esc(t.name)}</span><span class="desc">${exact != null ? fmtNum(exact) : `≈ ${fmtNum(t.rows)}`} · ${fmtBytes(t.bytes)}</span></div>`;
          })
          .join('')}`;
    }

    // Previous version
    const prev = database.previous;
    const p = snap.previous;
    if (!prev.exists) {
      $('previous').innerHTML = `<p class="side-empty">${
        cfg.keepPrevious
          ? 'Nothing kept yet. After the next successful ingest, the version it replaces is kept here for rollback.'
          : 'KEEP_PREVIOUS is off, so replaced versions are not kept.'
      }</p>`;
    } else {
      $('previous').innerHTML = `
        <dl class="props">
          <dt>Edition</dt><dd class="mono">${esc(p && p.pdf ? p.pdf : 'unknown')}</dd>
          <dt>Loaded</dt><dd>${fmtDate(p && p.ingestedAt)}</dd>
          <dt>FRNs</dt><dd>${fmtNum(prev.frnCount)}</dd>
          <dt>Size</dt><dd>${fmtBytes(prev.bytes)}</dd>
        </dl>
        <div class="side-actions">
          <button class="btn btn-secondary" id="btn-rollback" ${snap.busy ? 'disabled' : ''}>${icon('rollback')}Roll Back to This Version</button>
        </div>`;
      $('btn-rollback').onclick = rollback;
    }
  }

  function renderStatusBar() {
    const job = snap.job;
    const running = snap.busy === 'ingest' || snap.busy === 'rollback';
    $('statusbar').classList.toggle('running', running);

    const cur = snap.current;
    const live = database && database.live;
    $('sb-edition').innerHTML =
      live && live.exists
        ? `${icon('file')}${esc(cur && cur.pdf ? cur.pdf : 'unknown edition')} · ${fmtNum(live.frnCount)} FRNs`
        : live
          ? `${icon('file')}No data loaded`
          : '';
    $('sb-server').textContent = database && database.server ? `MySQL ${database.server}` : '';

    let jobText = '';
    if (snap.busy === 'rollback') {
      jobText = `${icon('loading', 'spin')}Rolling back…`;
    } else if (job && job.status === 'running') {
      const stage = job.stages.find((s) => s.status === 'running');
      jobText = `${icon('loading', 'spin')}${esc(stage ? stage.label : 'Ingest')} · ${Math.round((job.progress || 0) * 100)}%`;
    } else if (job) {
      jobText = `${statusIcon(job.status)}Last ingest: ${esc((STATUS[job.status] || [job.status])[0])}`;
    }
    $('sb-job').innerHTML = jobText;
  }

  function renderFrn(tab) {
    const view = $('view-frn');
    if (!tab) return;
    if (tab.error) {
      view.innerHTML = `<div class="page"><div class="page-head"><h1>FRN ${esc(tab.frn)}</h1></div><div class="callout error">${icon('error')}<span>${esc(tab.error)}</span></div></div>`;
      return;
    }
    if (!tab.data) {
      view.innerHTML = `<div class="page"><div class="page-head"><h1>FRN ${esc(tab.frn)}</h1><p class="desc">Loading…</p></div></div>`;
      return;
    }
    const r = tab.data;
    const fields = [
      ['make', r.make], ['model', r.model], ['manufacturer', r.manufacturer], ['type', r.type],
      ['action', r.action], ['country', r.country], ['legal_classification', r.legal_classification],
      ['level', r.level], ['serial_numbering', r.serial_numbering], ['year_dates', r.year_dates], ['importer', r.importer],
    ].filter(([, v]) => v);
    const notes = {};
    for (const n of r.notes) (notes[n.note_key] = notes[n.note_key] || []).push(n.note_value);

    view.innerHTML = `<div class="page">
      <div class="page-head">
        <h1>FRN ${esc(r.frn)} <span class="${classCss(r.legal_classification)}">${esc(r.legal_classification)}</span></h1>
        <p class="desc">${esc([r.make, r.model].filter(Boolean).join(' '))}${r.type ? ` · ${esc(r.type)}` : ''}${r.action ? ` · ${esc(r.action)}` : ''}</p>
      </div>
      ${r.hasPdf ? `<div class="actions"><a class="btn btn-primary" href="/api/frn/${encodeURIComponent(r.frn)}/pdf" target="_blank" rel="noopener">${icon('linkExternal')}Open FRT Pages (PDF)</a></div>` : ''}
      <div class="section-title">Properties</div>
      <div class="kv-grid">${fields.map(([k, v]) => `<div class="k">${esc(k)}</div><div>${esc(v)}</div>`).join('')}</div>
      ${r.subEntries.length ? `<div class="section-title">Calibres</div><div class="table-wrap"><table><thead><tr><th>Sub-FRN</th><th>Calibre</th><th class="num">Shots</th><th class="num">Barrel (mm)</th><th>Class</th></tr></thead><tbody>${r.subEntries
        .map((s) => `<tr><td class="mono">${esc(s.sub_frn)}</td><td>${esc(s.calibre)}</td><td class="num">${esc(s.shots)}</td><td class="num">${esc(s.barrel_length)}</td><td><span class="${classCss(s.legal_classification)}">${esc(s.legal_classification)}</span></td></tr>`)
        .join('')}</tbody></table></div>` : ''}
      ${Object.keys(notes).length ? `<div class="section-title">Notes</div><div class="notes">${Object.entries(notes)
        .map(([k, vs]) => `<h3>${esc(k)}</h3><ul>${vs.map((v) => `<li>${esc(v)}</li>`).join('')}</ul>`)
        .join('')}</div>` : ''}
      ${r.crossReferences.length ? `<div class="section-title">Cross-references</div>${r.crossReferences
        .map((x) => `<div class="result" data-frn="${esc(x.ref_frn)}" tabindex="0"><span class="r-frn">${esc(x.ref_frn)}</span><span class="r-title">${esc(x.description)}</span><span></span></div>`)
        .join('')}` : ''}
    </div>`;
  }

  // Delegated: any result row (search results or cross-references) opens an FRN tab.
  $('editor').addEventListener('click', (e) => {
    const row = e.target.closest('.result[data-frn]');
    if (row) openFrn(row.dataset.frn);
  });
  $('editor').addEventListener('keydown', (e) => {
    const row = e.target.closest('.result[data-frn]');
    if (row && e.key === 'Enter') openFrn(row.dataset.frn);
  });

  // Tick elapsed timers without waiting for a server event.
  setInterval(() => {
    if (!snap || !snap.job || snap.job.status !== 'running') return;
    document.querySelectorAll('.tree-item.running .elapsed').forEach((el) => {
      el.textContent = since(el.dataset.start);
    });
    $('overall-meta').textContent = overallMeta(snap.job);
  }, 1000);

  // -------------------------------------------------------------- side bar

  const collapsed = store.get('frt.collapsed', {});
  document.querySelectorAll('.pane').forEach((pane) => {
    const header = pane.querySelector('.pane-header');
    const apply = () => {
      const isCollapsed = Boolean(collapsed[pane.dataset.pane]);
      pane.classList.toggle('collapsed', isCollapsed);
      header.setAttribute('aria-expanded', String(!isCollapsed));
    };
    header.onclick = () => {
      collapsed[pane.dataset.pane] = !collapsed[pane.dataset.pane];
      store.set('frt.collapsed', collapsed);
      apply();
    };
    apply();
  });

  // ---------------------------------------------------------------- panel

  const setPanelHeight = (h) => {
    const max = window.innerHeight - 200;
    const clamped = Math.max(80, Math.min(h, max));
    document.documentElement.style.setProperty('--panel-h', `${clamped}px`);
    return clamped;
  };
  setPanelHeight(store.get('frt.panelHeight', 260));
  $('sash').addEventListener('pointerdown', (e) => {
    const sash = $('sash');
    const startY = e.clientY;
    const startH = $('panel').getBoundingClientRect().height;
    sash.setPointerCapture(e.pointerId);
    sash.classList.add('dragging');
    const move = (ev) => setPanelHeight(startH + (startY - ev.clientY));
    const up = () => {
      sash.classList.remove('dragging');
      sash.removeEventListener('pointermove', move);
      sash.removeEventListener('pointerup', up);
      store.set('frt.panelHeight', $('panel').getBoundingClientRect().height);
    };
    sash.addEventListener('pointermove', move);
    sash.addEventListener('pointerup', up);
  });

  // ------------------------------------------------------------------ log

  const logEl = $('log');
  let logHasContent = false;
  let follow = true;

  $('btn-follow').onclick = () => {
    follow = !follow;
    $('btn-follow').classList.toggle('active', follow);
    $('btn-follow').setAttribute('aria-pressed', String(follow));
    if (follow) logEl.scrollTop = logEl.scrollHeight;
  };

  function formatLogText(text) {
    if (/^▶/.test(text)) return `<span class="l-mark">${esc(text)}</span>`;
    if (/^✔/.test(text)) return `<span class="l-done">${esc(text)}</span>`;
    if (/error|failed|traceback|exception/i.test(text)) return `<span class="l-err">${esc(text)}</span>`;
    // Highlight numbers like the editor does, escaping each piece separately.
    return text
      .split(/(\d[\d,.:]*%?)/)
      .map((part, i) => (i % 2 ? `<span class="l-num">${esc(part)}</span>` : esc(part)))
      .join('');
  }

  function appendLog(lines, reset) {
    if (reset) {
      logEl.textContent = '';
      logHasContent = false;
    }
    if (!lines.length) {
      if (!logHasContent) logEl.innerHTML = '<div class="log-empty">No output yet.</div>';
      return;
    }
    if (!logHasContent) logEl.textContent = '';
    logHasContent = true;
    const frag = document.createDocumentFragment();
    for (const l of lines) {
      const div = document.createElement('div');
      const t = new Date(l.t).toLocaleTimeString([], { hour12: false });
      div.innerHTML = `<span class="l-time">${t}</span> <span class="l-stage">[${esc(l.stage)}]</span> ${formatLogText(l.text)}`;
      frag.appendChild(div);
    }
    logEl.appendChild(frag);
    while (logEl.childElementCount > 1500) logEl.removeChild(logEl.firstChild);
    if (follow) logEl.scrollTop = logEl.scrollHeight;
  }

  // --------------------------------------------------------------- events

  function connect() {
    const es = new EventSource('/api/events');
    const conn = $('sb-conn');
    conn.innerHTML = `${icon('loading', 'spin')}Connecting`;
    es.onopen = () => {
      conn.innerHTML = `${icon('broadcast')}Live`;
      conn.title = 'Receiving live updates';
    };
    es.onerror = () => {
      conn.innerHTML = `${icon('disconnect')}Reconnecting`;
      conn.title = 'Lost the live update connection; retrying';
    };
    es.addEventListener('state', (e) => {
      const next = JSON.parse(e.data);
      snap = { ...snap, ...next };
      // Refresh DB stats when a job or rollback finishes.
      if (wasBusy && !next.busy) refreshStatus();
      wasBusy = next.busy;
      render();
    });
    es.addEventListener('log', (e) => {
      const { lines, reset } = JSON.parse(e.data);
      appendLog(lines, reset);
    });
  }

  // -------------------------------------------------------------- actions

  $('btn-ingest').onclick = async () => {
    try {
      await post('/api/ingest', { force: $('chk-force').checked });
      $('chk-force').checked = false;
      if (activeTab !== 'ingest') activate('ingest');
    } catch (err) {
      notify(err.message);
    }
  };

  $('btn-cancel').onclick = async () => {
    const ok = await confirmDialog({
      title: 'Cancel the running ingest?',
      detail: 'Work done so far is discarded. The live database will not be changed.',
      ok: 'Cancel Ingest',
    });
    if (!ok) return;
    try {
      await post('/api/ingest/cancel');
    } catch (err) {
      notify(err.message);
    }
  };

  async function rollback() {
    const p = snap.previous;
    const ok = await confirmDialog({
      title: `Roll back to ${p && p.pdf ? p.pdf : 'the previous version'}?`,
      detail: 'The previous version and the live database are swapped atomically, so you can switch back again afterwards.',
      ok: 'Roll Back',
    });
    if (!ok) return;
    try {
      await post('/api/rollback');
      notify('Rolled back to the previous version.', 'info');
      refreshStatus();
    } catch (err) {
      notify(err.message);
    }
  }

  // --------------------------------------------------------------- search

  let searchTimer = null;
  let searchSeq = 0;
  $('search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  });

  async function runSearch() {
    const q = $('search').value.trim();
    const out = $('search-results');
    const summary = $('search-summary');
    if (!q) {
      out.innerHTML = '';
      summary.textContent = '';
      return;
    }
    const seq = ++searchSeq;
    try {
      const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq) return;
      summary.textContent = results.length
        ? `${results.length === 100 ? 'First 100' : results.length} result${results.length === 1 ? '' : 's'}`
        : 'No results found.';
      out.innerHTML = results
        .map(
          (r) => `<div class="result" role="listitem" tabindex="0" data-frn="${esc(r.frn)}">
            <span class="r-frn">${esc(r.frn)}</span>
            <span class="r-title">${esc(r.make)} ${esc(r.model)}<span class="r-meta">${esc([r.type, r.action].filter(Boolean).join(' · '))}</span></span>
            <span class="${classCss(r.legal_classification)}">${esc(r.legal_classification)}</span>
          </div>`
        )
        .join('');
    } catch (err) {
      if (seq === searchSeq) {
        summary.textContent = '';
        out.innerHTML = `<div class="callout error">${icon('error')}<span>${esc(err.message)}</span></div>`;
      }
    }
  }

  // ------------------------------------------------------------------ boot

  hydrateIcons();
  activate(activeTab);
  refreshStatus();
  connect();
  setInterval(refreshStatus, 30000);
})();
