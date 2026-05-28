// Master Resume Builder — LOCAL workflow (no auth, no quota).
// Uses plain fetch against the local_server.py endpoints.

const API = {
  master: '/api/master',
  masterSave: '/api/master',
  tailor: '/api/tailor',
  render: '/api/render',
  bulkParse: '/api/bulk/parse',
  bulkZip: '/api/bulk/zip',
  generations: '/api/generations',
  applied: '/api/applied',
  outreachDiscover: '/api/outreach/discover',
  outreachReveal: '/api/outreach/reveal',
  outreach: '/api/outreach',
  preferences: '/api/preferences',
  discoveryRun: '/api/discovery/run',
  discoveryStatus: '/api/discovery/status',
  discoveryJobs: '/api/discovery/jobs',
};

const APPLIED_STATUSES = [
  'applied', 'assessment', 'interview', 'offer', 'rejected', 'withdrew', 'ghosted'
];

const LI_COOKIE_KEY = 'mrb.li_at';
const BULK_CONCURRENCY = 5;

let masterCV = null;
const tabs = new Map();
let activeTabId = null;
let tabCounter = 0;

// Sidebar jobs. Keyed by `url` if present, else `gen:<id>`.
// Status: queued | scraped | running | done | error
const bulkJobs = new Map();
function jobKey(job) { return job.url || (job.genId ? `gen:${job.genId}` : ''); }

(async function init() {
  const statusEl = document.getElementById('masterStatus');
  try {
    const r = await fetch(API.master);
    if (!r.ok) throw new Error(`master CV: ${r.status}`);
    masterCV = await r.json();
    statusEl.textContent = `master CV: ${masterCV.personal_info.name}`;
    statusEl.classList.add('ok');
  } catch (e) {
    statusEl.textContent = `master CV failed: ${e.message}`;
    statusEl.classList.add('err');
  }
  document.getElementById('newTabBtn').addEventListener('click', () => createTab());
  createTab();
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 't') {
      e.preventDefault();
      createTab();
    }
  });
  wireBulkUI();
  wireMasterDrawer();
  wireAppliedDrawer();
  wirePrefsDrawer();
  wireDiscoveredDrawer();
  wireRail();
  wireSettingsTabs();
  wireCuratedView();
  await hydrateHistory();
  refreshAppliedCount();
  refreshDiscoveredCount();
  refreshDiscoveryRunStatusPill();
  loadSheetsStatus();
  loadDiscoveryBackendStatus();
})();

// ─── Rail / view router ─────────────────────────────────────────────
const VIEWS = ['compose', 'discovered', 'curated', 'applied', 'master', 'settings'];
let activeView = 'compose';

function wireRail() {
  document.querySelectorAll('.rail-btn').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });
  document.addEventListener('keydown', (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
    const idx = parseInt(e.key, 10);
    if (idx >= 1 && idx <= VIEWS.length) {
      e.preventDefault();
      switchView(VIEWS[idx - 1]);
    }
  });
}

function switchView(viewId) {
  if (!VIEWS.includes(viewId) || viewId === activeView) return;
  if (activeView === 'master') leaveMasterView();

  document.querySelectorAll('.rail-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === viewId));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${viewId}`));
  activeView = viewId;

  if (viewId === 'master') enterMasterView();
  if (viewId === 'discovered') enterDiscoveredView();
  if (viewId === 'curated') enterCuratedView();
  if (viewId === 'applied') enterAppliedView();
  if (viewId === 'settings') enterSettingsView();
}

function wireSettingsTabs() {
  document.querySelectorAll('.settings-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const section = tab.dataset.settingsSection;
      document.querySelectorAll('.settings-tab').forEach((t) => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.settings-section').forEach((s) => s.classList.toggle('active', s.id === `settings-${section}`));
    });
  });
}

function refreshDiscoveryRunStatusPill() {
  const el = document.getElementById('discoveryRunStatusPill');
  if (!el) return;
  fetch(API.discoveryStatus)
    .then((r) => (r.ok ? r.text() : ''))
    .then((txt) => {
      if (!txt || txt === 'null') { el.textContent = ''; return; }
      try {
        const rec = JSON.parse(txt);
        if (rec.finished_at) {
          el.textContent = `last run: ${shortDate(rec.finished_at)}`;
          el.className = 'status-pill ok';
        } else {
          el.textContent = 'discovery running…';
          el.className = 'status-pill';
        }
      } catch { el.textContent = ''; }
    })
    .catch(() => {});
}

async function loadSheetsStatus() {
  const el = document.getElementById('sheetsStatusBox');
  if (!el) return;
  try {
    const r = await fetch('/api/sheets/status');
    if (!r.ok) { el.textContent = 'not configured'; return; }
    const d = await r.json();
    if (d.configured) {
      el.innerHTML = `✓ configured · mode: <code>${d.auth_mode}</code> · sheet: <a target="_blank" rel="noopener" href="https://docs.google.com/spreadsheets/d/${d.spreadsheet_id}">open</a>`;
    } else {
      el.textContent = 'not configured — set SHEETS_SPREADSHEET_ID in .env.local and run scripts/sheets_oauth.py';
    }
  } catch { el.textContent = 'check failed'; }
}

async function loadDiscoveryBackendStatus() {
  const el = document.getElementById('discoveryBackendBox');
  if (!el) return;
  el.innerHTML = `Backend selected by <code>DISCOVERY_BACKEND</code> env (<code>jobspy</code> default, <code>apify</code> opt-in). Free + reliable: Jobspy. Paid: Apify actor like <code>curious_coder~linkedin-jobs-scraper</code>.`;
}

// View-switch enter/leave helpers — these just trigger the existing render funcs
// that previously fired on drawer-open. No new logic.
function enterDiscoveredView() {
  refreshDiscoveredCount().then(renderDiscoveredList);
  refreshDiscoveryRunStatus();
}
function enterAppliedView() { refreshAppliedCount().then(renderAppliedDrawer); }
function enterCuratedView() { refreshDiscoveredCount().then(renderCuratedList); }
function enterMasterView() {
  if (!masterCV) return;
  masterDraft = JSON.parse(JSON.stringify(masterCV));
  renderMasterDrawer();
}
function leaveMasterView() {
  masterDraft = null;
  setMasterDrawerStatus('');
}
async function enterSettingsView() {
  try {
    const r = await fetch(API.preferences);
    prefsDraft = r.ok ? await r.json() : {};
  } catch { prefsDraft = {}; }
  renderPrefsDrawer();
  loadSheetsStatus();
  loadDiscoveryBackendStatus();
}

function createTab(initial = {}) {
  tabCounter += 1;
  const id = `tab-${tabCounter}`;
  const state = {
    id, title: initial.title || `Tab ${tabCounter}`,
    company: initial.company || '', jd: initial.jd || '',
    cv: initial.cv || null,
    pdfUrl: null,
    pdfFilename: initial.pdfFilename || null,
    report: initial.report || null,
    status: 'idle',
    bulkUrl: initial.bulkUrl || null,
    generationId: initial.generationId || null,
    appliedId: null,
    useRac: !!initial.useRac,
  };
  tabs.set(id, state);

  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.tabId = id;
  tab.innerHTML = `
    <span class="status-dot"></span>
    <span class="tab-title">${escapeHtml(state.title)}</span>
    <span class="close" title="Close">×</span>
  `;
  tab.addEventListener('click', (e) => {
    if (e.target.classList.contains('close')) {
      e.stopPropagation(); closeTab(id); return;
    }
    activateTab(id);
  });
  document.getElementById('tabs').appendChild(tab);

  const tpl = document.getElementById('tabPanelTemplate');
  const panel = tpl.content.firstElementChild.cloneNode(true);
  panel.dataset.tabId = id;
  document.getElementById('panels').appendChild(panel);

  wirePanel(panel, state);
  activateTab(id);
  return id;
}

function closeTab(id) {
  const state = tabs.get(id);
  if (!state) return;
  if (state.pdfUrl) URL.revokeObjectURL(state.pdfUrl);
  tabs.delete(id);
  document.querySelector(`.tab[data-tab-id="${id}"]`)?.remove();
  document.querySelector(`.panel[data-tab-id="${id}"]`)?.remove();
  if (activeTabId === id) {
    const next = tabs.keys().next().value;
    if (next) activateTab(next);
    else createTab();
  }
}

function activateTab(id) {
  activeTabId = id;
  document.querySelectorAll('.tab').forEach((el) => el.classList.toggle('active', el.dataset.tabId === id));
  document.querySelectorAll('.panel').forEach((el) => el.classList.toggle('active', el.dataset.tabId === id));
}

function setTabStatus(id, status) {
  const state = tabs.get(id);
  if (!state) return;
  state.status = status;
  const tabEl = document.querySelector(`.tab[data-tab-id="${id}"]`);
  if (!tabEl) return;
  tabEl.classList.remove('is-loading', 'is-ready', 'is-error');
  if (status === 'loading') tabEl.classList.add('is-loading');
  else if (status === 'ready') tabEl.classList.add('is-ready');
  else if (status === 'error') tabEl.classList.add('is-error');
}

function setTabTitle(id, title) {
  const state = tabs.get(id);
  if (!state) return;
  state.title = title;
  const titleEl = document.querySelector(`.tab[data-tab-id="${id}"] .tab-title`);
  if (titleEl) titleEl.textContent = title;
}

function wirePanel(panel, state) {
  const $ = (sel) => panel.querySelector(sel);
  const companyEl = $('[data-company]');
  const jdEl = $('[data-jd]');
  const generateBtn = $('[data-generate]');
  const applyBtn = $('[data-apply]');
  const saveBtn = $('[data-save]');
  const editorEl = $('[data-editor]');
  const previewEl = $('[data-preview]');
  const previewEmpty = $('[data-preview-empty]');
  const logEl = $('[data-log]');

  companyEl.value = state.company;
  jdEl.value = state.jd;

  companyEl.addEventListener('input', () => {
    state.company = companyEl.value;
    if (state.company.trim()) setTabTitle(state.id, state.company.trim());
    syncTabMetaToBulk(state);
  });
  jdEl.addEventListener('input', () => { state.jd = jdEl.value; syncTabMetaToBulk(state); });

  const racToggle = panel.querySelector('[data-use-rac]');
  if (racToggle) {
    racToggle.checked = !!state.useRac;
    racToggle.addEventListener('change', () => { state.useRac = racToggle.checked; });
  }

  generateBtn.addEventListener('click', async () => {
    const company = state.company.trim();
    const jd = state.jd.trim();
    if (!company) return logErr(logEl, 'Company is required.');
    if (!jd) return logErr(logEl, 'JD is required.');
    setTabStatus(state.id, 'loading');
    setBusy([generateBtn, applyBtn, saveBtn], true);
    logInfo(logEl, 'Analyzing JD, selecting bullets, rewriting summary, enriching skills...');
    try {
      // rewrite_summary is intentionally false — summary text is locked in master_cv_bank.json.
      const source_url = state.bulkUrl || null;
      const data = await postJSON(API.tailor, { company, jd, source_url, rewrite_summary: false, enrich_skills: true, use_rac: !!state.useRac });
      state.cv = data.cv;
      state.report = data.report;
      state.pdfFilename = data.download_filename;
      state.generationId = data.generation_id || null;
      setPdf(state, previewEl, previewEmpty, data.pdf_base64);
      syncTabToBulk(state, data);
      refreshTabAppliedUI(panel, state);
      refreshTabOutreachUI(panel, state);
      loadCachedOutreach(panel, state);
      renderReport(panel, state);
      renderEditor(panel, state);
      editorEl.classList.remove('hidden');
      panel.querySelector('[data-report]').classList.remove('hidden');
      applyBtn.disabled = false;
      saveBtn.disabled = false;
      setTabStatus(state.id, 'ready');
      logOk(logEl, `Done — ${data.download_filename}`);
    } catch (e) {
      setTabStatus(state.id, 'error');
      logErr(logEl, e.message);
    } finally {
      setBusy([generateBtn, applyBtn, saveBtn], false);
    }
  });

  applyBtn.addEventListener('click', async () => {
    if (!state.cv) return;
    collectEdits(panel, state);
    setTabStatus(state.id, 'loading');
    setBusy([generateBtn, applyBtn, saveBtn], true);
    logInfo(logEl, 'Re-rendering PDF with your edits...');
    try {
      const data = await postJSON(API.render, { cv: state.cv, company: state.company });
      state.pdfFilename = data.download_filename;
      setPdf(state, previewEl, previewEmpty, data.pdf_base64);
      syncTabToBulk(state, data);
      setTabStatus(state.id, 'ready');
      logOk(logEl, 'Preview updated.');
    } catch (e) {
      setTabStatus(state.id, 'error');
      logErr(logEl, e.message);
    } finally {
      setBusy([generateBtn, applyBtn, saveBtn], false);
    }
  });

  saveBtn.addEventListener('click', () => {
    if (!state.pdfUrl) return;
    const a = document.createElement('a');
    a.href = state.pdfUrl;
    a.download = state.pdfFilename || 'Resume.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    logOk(logEl, `Downloaded ${a.download}`);
  });

  const appliedBtn = panel.querySelector('[data-applied-btn]');
  appliedBtn.addEventListener('click', () => {
    if (state.appliedId) {
      openAppliedDrawer();
      return;
    }
    markApplied(state, panel);
  });

  // Outreach
  const outreachBlock = panel.querySelector('[data-outreach]');
  const findBtn = panel.querySelector('[data-outreach-find]');
  const refreshBtn = panel.querySelector('[data-outreach-refresh]');
  findBtn.addEventListener('click', () => runOutreachDiscover(panel, state, false));
  refreshBtn.addEventListener('click', () => runOutreachDiscover(panel, state, true));
}

const BUCKET_TO_CLASS = { primary_tech: 'tech', core_impact: 'impact', must_have: 'musthave', domain: 'domain' };
const BUCKET_LABELS   = { primary_tech: 'Primary tech', core_impact: 'Core impact', must_have: 'Must-have', domain: 'Domain' };

const SPONSORSHIP_BADGE = {
  available:     { label: 'Available',     klass: 'ok'  },
  not_available: { label: 'Not available', klass: 'err' },
  mentioned:     { label: 'Ambiguous',     klass: 'warn' },
  unspecified:   { label: 'Not mentioned', klass: 'muted' },
};

function renderReport(panel, state) {
  const report = state.report;
  if (!report) return;
  const $ = (sel) => panel.querySelector(sel);
  const a = report.analysis || {};
  $('[data-report-meta]').textContent = a.role_title ? `· ${a.role_title}${a.domain ? ' · ' + a.domain : ''}` : '';

  renderSponsorship(panel, report.sponsorship);
  renderLanguages(panel, report.languages);

  const placedKeys = new Set(
    (report.placements || []).filter((p) => p.locations.length > 0).map((p) => p.keyword.toLowerCase())
  );
  renderChips(panel, '[data-chips-tech]', 'primary_tech', a.primary_tech_stack || [], placedKeys);
  renderChips(panel, '[data-chips-impact]', 'core_impact', a.core_impact_areas || [], placedKeys);
  renderChips(panel, '[data-chips-musthave]', 'must_have', a.must_have_keywords || [], placedKeys);
  renderChips(panel, '[data-chips-domain]', 'domain', a.domain ? [a.domain] : [], placedKeys);

  const placementsEl = $('[data-placements]');
  placementsEl.innerHTML = '';
  const showUnmatched = $('[data-unmatched-toggle]').checked;
  for (const p of report.placements) {
    if (!showUnmatched && p.locations.length === 0) continue;
    const row = document.createElement('div');
    row.className = 'placement-row' + (p.locations.length === 0 ? ' unplaced' : '');
    const key = document.createElement('div');
    key.className = 'placement-key';
    const klass = BUCKET_TO_CLASS[p.bucket] || '';
    key.innerHTML = `<span class="chip ${klass}">${escapeHtml(p.keyword)}</span>`;
    row.appendChild(key);

    const locs = document.createElement('div');
    locs.className = 'placement-locs';
    if (p.locations.length === 0) {
      locs.innerHTML = `<span class="muted">no placement</span>`;
    } else {
      for (const loc of p.locations) {
        const line = document.createElement('div');
        line.className = 'placement-loc';
        line.innerHTML = `
          <span class="where ${loc.is_new ? 'new' : ''}">${escapeHtml(loc.label)}</span>
          ${loc.snippet ? `<span class="snippet">${escapeHtml(loc.snippet)}</span>` : ''}
        `;
        locs.appendChild(line);
      }
    }
    row.appendChild(locs);
    placementsEl.appendChild(row);
  }

  const skillAddBlock = $('[data-skill-additions-block]');
  const skillAddEl = $('[data-skill-additions]');
  skillAddEl.innerHTML = '';
  if (report.skill_additions && report.skill_additions.length > 0) {
    skillAddBlock.classList.remove('hidden');
    for (const s of report.skill_additions) {
      const d = document.createElement('div');
      d.className = 'skill-addition-row';
      d.textContent = s;
      skillAddEl.appendChild(d);
    }
  } else {
    skillAddBlock.classList.add('hidden');
  }

  const toggle = $('[data-unmatched-toggle]');
  if (!toggle.dataset.wired) {
    toggle.dataset.wired = '1';
    toggle.addEventListener('change', () => renderReport(panel, state));
  }
}

function renderLanguages(panel, list) {
  const block = panel.querySelector('[data-languages]');
  if (!block) return;
  const chipsEl = panel.querySelector('[data-language-chips]');
  const evidenceEl = panel.querySelector('[data-language-evidence]');
  chipsEl.innerHTML = '';
  evidenceEl.innerHTML = '';
  const items = list || [];
  if (!items.length) {
    block.classList.add('hidden');
    return;
  }
  block.classList.remove('hidden');
  for (const c of items) {
    const chip = document.createElement('span');
    chip.className = 'language-chip ' + (c.required ? 'req' : 'pref');
    const levelTxt = c.level ? ` · ${c.level}` : '';
    chip.textContent = `${c.language}${levelTxt} · ${c.required ? 'required' : 'preferred'}`;
    chipsEl.appendChild(chip);
  }
  const seen = new Set();
  for (const c of items) {
    if (!c.evidence || seen.has(c.evidence)) continue;
    seen.add(c.evidence);
    const li = document.createElement('li');
    li.textContent = c.evidence;
    evidenceEl.appendChild(li);
  }
}

function renderSponsorship(panel, info) {
  const block = panel.querySelector('[data-sponsorship]');
  const badge = panel.querySelector('[data-sponsorship-badge]');
  const list = panel.querySelector('[data-sponsorship-evidence]');
  if (!block) return;
  const data = info || { status: 'unspecified', evidence: [] };
  const meta = SPONSORSHIP_BADGE[data.status] || SPONSORSHIP_BADGE.unspecified;
  badge.className = 'sponsorship-badge ' + meta.klass;
  badge.textContent = meta.label;
  list.innerHTML = '';
  for (const line of data.evidence || []) {
    const li = document.createElement('li');
    li.textContent = line;
    list.appendChild(li);
  }
  block.classList.remove('hidden');
}

function renderChips(panel, sel, bucket, items, placedKeys) {
  const el = panel.querySelector(sel);
  el.innerHTML = '';
  if (!items || items.length === 0) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  const label = document.createElement('span');
  label.className = 'chip-label';
  label.textContent = BUCKET_LABELS[bucket];
  el.appendChild(label);
  const klass = BUCKET_TO_CLASS[bucket];
  for (const it of items) {
    const c = document.createElement('span');
    c.className = 'chip ' + klass + (placedKeys.has(it.toLowerCase()) ? '' : ' unplaced');
    c.textContent = it;
    c.title = placedKeys.has(it.toLowerCase()) ? 'Placed in tailored CV' : 'Not surfaced in CV';
    el.appendChild(c);
  }
}

function renderEditor(panel, state) {
  const $ = (sel) => panel.querySelector(sel);
  const cv = state.cv;
  $('[data-summary]').value = cv.professional_summary || '';

  const skillsEl = $('[data-skills]');
  skillsEl.innerHTML = '';
  (cv.skills || []).forEach((s, i) => {
    const row = document.createElement('div');
    row.className = 'skill-row';
    row.innerHTML = `
      <div class="skill-cat">${escapeHtml(s.category)}</div>
      <label>items (comma-separated)
        <textarea data-skill-items="${i}" rows="2">${escapeHtml(s.items.join(', '))}</textarea>
      </label>
    `;
    skillsEl.appendChild(row);
  });

  const expEl = $('[data-experience]');
  expEl.innerHTML = '';
  (cv.experience || []).forEach((e, ei) => {
    const block = document.createElement('div');
    block.className = 'role-block';
    block.innerHTML = `
      <div class="role-header">
        ${escapeHtml(e.role)} @ ${escapeHtml(e.company)}
        <span class="role-meta">${escapeHtml(e.date || '')} • ${escapeHtml(e.location || '')}</span>
      </div>
    `;
    e.bullet_pool.forEach((b, bi) => {
      const row = document.createElement('div');
      row.className = 'bullet-row';
      row.innerHTML = `<textarea data-exp-bullet="${ei}.${bi}" rows="2">${escapeHtml(b.text)}</textarea>`;
      block.appendChild(row);
    });
    expEl.appendChild(block);
  });

  const projEl = $('[data-projects]');
  projEl.innerHTML = '';
  (cv.projects || []).forEach((p, pi) => {
    const block = document.createElement('div');
    block.className = 'project-block';
    block.innerHTML = `
      <div class="project-header">
        ${escapeHtml(p.name)}
        <span class="project-meta">${escapeHtml(p.location || '')}</span>
      </div>
    `;
    p.bullet_pool.forEach((b, bi) => {
      const row = document.createElement('div');
      row.className = 'bullet-row';
      row.innerHTML = `<textarea data-proj-bullet="${pi}.${bi}" rows="2">${escapeHtml(b.text)}</textarea>`;
      block.appendChild(row);
    });
    projEl.appendChild(block);
  });
}

function collectEdits(panel, state) {
  const cv = state.cv;
  cv.professional_summary = panel.querySelector('[data-summary]').value.trim();
  panel.querySelectorAll('[data-skill-items]').forEach((el) => {
    const i = parseInt(el.dataset.skillItems, 10);
    const items = el.value.split(',').map((s) => s.trim()).filter(Boolean);
    if (cv.skills[i]) cv.skills[i].items = items;
  });
  panel.querySelectorAll('[data-exp-bullet]').forEach((el) => {
    const [ei, bi] = el.dataset.expBullet.split('.').map((n) => parseInt(n, 10));
    if (cv.experience[ei]?.bullet_pool[bi]) cv.experience[ei].bullet_pool[bi].text = el.value.trim();
  });
  panel.querySelectorAll('[data-proj-bullet]').forEach((el) => {
    const [pi, bi] = el.dataset.projBullet.split('.').map((n) => parseInt(n, 10));
    if (cv.projects[pi]?.bullet_pool[bi]) cv.projects[pi].bullet_pool[bi].text = el.value.trim();
  });
}

function setPdf(state, previewEl, previewEmpty, base64) {
  if (state.pdfUrl) URL.revokeObjectURL(state.pdfUrl);
  const blob = b64ToBlob(base64, 'application/pdf');
  state.pdfUrl = URL.createObjectURL(blob);
  previewEl.src = state.pdfUrl;
  previewEl.classList.add('visible');
  previewEmpty.classList.add('hidden');
}

function b64ToBlob(b64, type) {
  const bin = atob(b64);
  const len = bin.length;
  const arr = new Uint8Array(len);
  for (let i = 0; i < len; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type });
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      if (j.detail) msg += `: ${typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)}`;
    } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

function setBusy(buttons, busy) { buttons.forEach((b) => { if (b) b.disabled = busy; }); }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
function ts() { return new Date().toTimeString().slice(0, 8); }
function logInfo(el, msg) { el.insertAdjacentHTML('beforeend', `<div class="info">[${ts()}] ${escapeHtml(msg)}</div>`); el.scrollTop = el.scrollHeight; }
function logOk(el, msg)   { el.insertAdjacentHTML('beforeend', `<div class="ok">[${ts()}] ${escapeHtml(msg)}</div>`); el.scrollTop = el.scrollHeight; }
function logErr(el, msg)  { el.insertAdjacentHTML('beforeend', `<div class="err">[${ts()}] ${escapeHtml(msg)}</div>`); el.scrollTop = el.scrollHeight; }

// ─── Bulk import ───────────────────────────────────────────────────────────
function wireBulkUI() {
  const cookieEl = document.getElementById('liCookie');
  cookieEl.value = localStorage.getItem(LI_COOKIE_KEY) || '';
  cookieEl.addEventListener('change', () => {
    const v = cookieEl.value.trim();
    if (v) localStorage.setItem(LI_COOKIE_KEY, v);
    else localStorage.removeItem(LI_COOKIE_KEY);
  });
  document.getElementById('bulkParseBtn').addEventListener('click', parseLinks);
  document.getElementById('bulkGenerateAllBtn').addEventListener('click', generateAll);
  document.getElementById('bulkDownloadZipBtn').addEventListener('click', downloadAllZip);
  document.getElementById('bulkClearBtn').addEventListener('click', clearBulk);

  const linksEl = document.getElementById('bulkLinks');
  const gutterEl = document.getElementById('bulkLinksGutter');
  const updateGutter = () => {
    const lines = Math.max(1, linksEl.value.split('\n').length);
    let out = '';
    for (let i = 1; i <= lines; i++) out += (i > 1 ? '\n' : '') + i;
    gutterEl.textContent = out;
  };
  linksEl.addEventListener('input', updateGutter);
  linksEl.addEventListener('scroll', () => { gutterEl.scrollTop = linksEl.scrollTop; });
  updateGutter();
}

async function parseLinks() {
  const raw = document.getElementById('bulkLinks').value.trim();
  if (!raw) return;
  const urls = [...new Set(raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))];
  if (!urls.length) return;
  const liCookie = (document.getElementById('liCookie').value || '').trim() || null;
  const parseBtn = document.getElementById('bulkParseBtn');
  parseBtn.disabled = true;
  setBulkProgress(`Parsing ${urls.length} links…`);
  try {
    const r = await postJSON(API.bulkParse, { urls, li_cookie: liCookie });
    for (const it of r.items) {
      const job = bulkJobs.get(it.url) || { url: it.url };
      job.company = it.company || job.company || '';
      job.jd = it.jd || '';
      job.source = it.source || '';
      job.error = it.error || null;
      job.status = it.error ? 'error' : 'scraped';
      bulkJobs.set(it.url, job);
    }
    renderBulkList();
    document.getElementById('bulkSidebar').classList.remove('hidden');
    const ok = r.items.filter((i) => !i.error).length;
    setBulkProgress(`Parsed: ${ok} ok, ${r.items.length - ok} error`);
    document.getElementById('bulkGenerateAllBtn').disabled = [...bulkJobs.values()].every((j) => !j.jd);
  } catch (e) {
    setBulkProgress(`Parse failed: ${e.message}`, 'err');
  } finally {
    parseBtn.disabled = false;
  }
}

function renderBulkList() {
  const list = document.getElementById('bulkList');
  list.innerHTML = '';
  let i = 1;
  for (const job of bulkJobs.values()) {
    const row = buildBulkRow(job);
    row.querySelector('.bulk-index').textContent = ordinal(i++);
    list.appendChild(row);
  }
}

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function buildBulkRow(job) {
  const row = document.createElement('div');
  row.className = 'bulk-row';
  row.dataset.key = jobKey(job);
  row.innerHTML = `
    <span class="bulk-index"></span>
    <span class="bulk-dot"></span>
    <div class="bulk-row-body">
      <div class="bulk-row-company"></div>
      <a class="bulk-row-link" target="_blank" rel="noopener noreferrer"></a>
      <div class="bulk-row-meta"></div>
    </div>
    <button class="btn-link bulk-row-open" title="Open">open</button>
  `;
  row.addEventListener('click', (e) => {
    if (e.target.closest('.bulk-row-link')) return;
    openBulkJob(jobKey(job));
  });
  bulkRowApplyState(row, job);
  return row;
}

function renderBulkRow(job) {
  const sel = `.bulk-row[data-key="${cssEsc(jobKey(job))}"]`;
  const row = document.querySelector(sel);
  if (row) bulkRowApplyState(row, job);
}

function bulkRowApplyState(row, job) {
  row.classList.remove('is-scraped', 'is-running', 'is-done', 'is-error');
  row.classList.add(`is-${job.status || 'scraped'}`);
  row.querySelector('.bulk-row-company').textContent = job.company || '(no company)';
  const linkEl = row.querySelector('.bulk-row-link');
  if (job.url) {
    linkEl.href = job.url;
    linkEl.textContent = job.url.replace(/^https?:\/\//, '').slice(0, 60);
    linkEl.title = job.url;
    linkEl.classList.remove('hidden');
  } else {
    linkEl.classList.add('hidden');
  }
  let meta = '';
  if (job.error) meta = `⚠ ${job.error}`;
  else if (job.status === 'done') meta = job.pdfFilename || 'ready';
  else if (job.status === 'running') meta = 'generating…';
  else if (job.createdAt) meta = `saved ${shortDate(job.createdAt)}`;
  row.querySelector('.bulk-row-meta').textContent = meta;
  row.querySelector('.bulk-row-meta').classList.toggle('hidden', !meta);
}

function shortDate(iso) {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

function cssEsc(s) {
  return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
}

async function openBulkJob(key) {
  const job = bulkJobs.get(key);
  if (!job) return;
  // Lazy-load from server if this is a restored row with only a generation id.
  if (!job.cv && job.genId) {
    job.status = 'running';
    renderBulkRow(job);
    try {
      const rec = await fetch(`${API.generations}/${encodeURIComponent(job.genId)}`).then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      });
      job.cv = rec.cv;
      job.report = rec.report;
      job.jd = rec.jd || job.jd;
      job.company = rec.company || job.company;
      job.pdfFilename = rec.pdf_filename || job.pdfFilename;
      job.createdAt = rec.created_at || job.createdAt;
      job.status = 'done';
      const render = await postJSON(API.render, { cv: job.cv, company: job.company });
      job.pdfBase64 = render.pdf_base64;
      job.pdfFilename = render.download_filename;
    } catch (e) {
      job.status = 'error';
      job.error = `Load failed: ${e.message}`;
      renderBulkRow(job);
      return;
    }
    renderBulkRow(job);
  }
  for (const [tabId, st] of tabs) {
    if (st.bulkUrl === (job.url || null) && st.generationId === (job.genId || null)) {
      activateTab(tabId); return;
    }
  }
  const tabId = createTab({
    title: job.company || 'Job',
    company: job.company || '',
    jd: job.jd || '',
    cv: job.cv || null,
    pdfFilename: job.pdfFilename || null,
    report: job.report || null,
    bulkUrl: job.url || null,
    generationId: job.genId || null,
  });
  const state = tabs.get(tabId);
  if (state.cv) hydrateTabPanel(state, job.pdfBase64);
}

function hydrateTabPanel(state, pdfBase64) {
  const panel = document.querySelector(`.panel[data-tab-id="${state.id}"]`);
  if (!panel || !state.cv) return;
  if (state.report) {
    renderReport(panel, state);
    panel.querySelector('[data-report]').classList.remove('hidden');
  }
  renderEditor(panel, state);
  panel.querySelector('[data-editor]').classList.remove('hidden');
  panel.querySelector('[data-apply]').disabled = false;
  panel.querySelector('[data-save]').disabled = false;
  if (pdfBase64) {
    const previewEl = panel.querySelector('[data-preview]');
    const previewEmpty = panel.querySelector('[data-preview-empty]');
    setPdf(state, previewEl, previewEmpty, pdfBase64);
  }
  // If this generation is already in the applied list, mark the button.
  if (state.generationId) {
    const found = appliedRecords.find((a) => a.generation_id === state.generationId);
    if (found) state.appliedId = found.id;
  }
  refreshTabAppliedUI(panel, state);
  refreshTabOutreachUI(panel, state);
  loadCachedOutreach(panel, state);
  setTabStatus(state.id, 'ready');
}

async function generateAll() {
  const todo = [...bulkJobs.values()].filter((j) => j.jd && j.status !== 'done' && j.status !== 'running');
  if (!todo.length) { setBulkProgress('Nothing to generate.'); return; }
  const genBtn = document.getElementById('bulkGenerateAllBtn');
  genBtn.disabled = true;
  let done = 0, errors = 0;
  let cursor = 0;
  setBulkProgress(`Generating 0 / ${todo.length}…`);

  async function worker() {
    while (cursor < todo.length) {
      const job = todo[cursor++];
      job.status = 'running';
      renderBulkRow(job);
      try {
        const useRac = !!document.getElementById('bulkUseRac')?.checked;
        const data = await postJSON(API.tailor, {
          company: job.company || _companyFallback(job.url),
          jd: job.jd,
          rewrite_summary: false,
          enrich_skills: true,
          use_rac: useRac,
        });
        job.cv = data.cv;
        job.report = data.report;
        job.pdfBase64 = data.pdf_base64;
        job.pdfFilename = data.download_filename;
        job.status = 'done';
        job.error = null;
        for (const [tabId, st] of tabs) {
          if (st.bulkUrl === job.url) {
            st.cv = job.cv;
            st.report = job.report;
            st.pdfFilename = job.pdfFilename;
            hydrateTabPanel(st, job.pdfBase64);
            break;
          }
        }
        done++;
      } catch (e) {
        job.status = 'error';
        job.error = e.message;
        errors++;
      }
      renderBulkRow(job);
      setBulkProgress(`Generating ${done + errors} / ${todo.length}${errors ? ` (${errors} error)` : ''}…`);
    }
  }
  await Promise.all(Array.from({ length: Math.min(BULK_CONCURRENCY, todo.length) }, worker));
  setBulkProgress(`Done: ${done} ok, ${errors} error`);
  genBtn.disabled = false;
  document.getElementById('bulkDownloadZipBtn').disabled = [...bulkJobs.values()].filter((j) => j.cv).length === 0;
}

function _companyFallback(url) {
  try { return new URL(url).hostname.replace(/^www\./, '').split('.')[0]; }
  catch { return 'Company'; }
}

async function downloadAllZip() {
  const items = [...bulkJobs.values()]
    .filter((j) => j.cv)
    .map((j) => ({ cv: j.cv, company: j.company || _companyFallback(j.url) }));
  if (!items.length) { setBulkProgress('No generated resumes to zip.'); return; }
  const btn = document.getElementById('bulkDownloadZipBtn');
  btn.disabled = true;
  setBulkProgress(`Building ZIP of ${items.length}…`);
  try {
    const r = await fetch(API.bulkZip, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `Vikrant_Indi_Resumes_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setBulkProgress(`Downloaded ${a.download}`);
  } catch (e) {
    setBulkProgress(`ZIP failed: ${e.message}`, 'err');
  } finally {
    btn.disabled = false;
  }
}

function clearBulk() {
  if (!confirm('Clear all bulk jobs from the list?')) return;
  bulkJobs.clear();
  renderBulkList();
  document.getElementById('bulkSidebar').classList.add('hidden');
  document.getElementById('bulkGenerateAllBtn').disabled = true;
  document.getElementById('bulkDownloadZipBtn').disabled = true;
  setBulkProgress('');
}

function setBulkProgress(msg, klass = '') {
  const el = document.getElementById('bulkProgress');
  el.className = 'bulk-progress muted' + (klass ? ` ${klass}` : '');
  el.textContent = msg;
}

function syncTabToBulk(state, data) {
  const key = state.bulkUrl || (state.generationId ? `gen:${state.generationId}` : null);
  if (!key) return;
  let j = bulkJobs.get(key);
  if (!j) {
    j = { url: state.bulkUrl || '', genId: state.generationId, company: state.company, jd: state.jd, status: 'done' };
    bulkJobs.set(key, j);
    document.getElementById('bulkSidebar').classList.remove('hidden');
    renderBulkList();
  }
  j.cv = state.cv;
  if (data && data.pdf_base64) j.pdfBase64 = data.pdf_base64;
  if (data && data.download_filename) j.pdfFilename = data.download_filename;
  if (data && data.generation_id) j.genId = data.generation_id;
  j.report = state.report;
  j.status = 'done';
  j.error = null;
  renderBulkRow(j);
  document.getElementById('bulkDownloadZipBtn').disabled = false;
}

function syncTabMetaToBulk(state) {
  if (!state.bulkUrl) return;
  const j = bulkJobs.get(state.bulkUrl);
  if (!j) return;
  j.company = state.company;
  j.jd = state.jd;
  renderBulkRow(j);
}

// ─── Master CV drawer ──────────────────────────────────────────────────
let masterDraft = null;

function wireMasterDrawer() {
  // Save + Generate-RAC live in the new view-header; everything else moved to the rail.
  const saveBtn = document.getElementById('masterSaveBtn');
  if (saveBtn) saveBtn.addEventListener('click', saveMasterDraft);
  const racBtn = document.getElementById('masterRacBtn');
  if (racBtn) racBtn.addEventListener('click', generateMasterRac);
}

async function generateMasterRac() {
  const btn = document.getElementById('masterRacBtn');
  btn.disabled = true;
  setMasterDrawerStatus('Generating RAC variants (≈10-30s per bullet, capped 4 concurrent)…');
  try {
    const r = await fetch('/api/master/generate-rac', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ only_missing: true }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    masterCV = await r.json();
    masterDraft = JSON.parse(JSON.stringify(masterCV));
    renderMasterDrawer();
    setMasterDrawerStatus('RAC variants generated.', 'ok');
  } catch (e) {
    setMasterDrawerStatus(`RAC generation failed: ${e.message}`, 'err');
  } finally {
    btn.disabled = false;
  }
}

function openMasterDrawer() { switchView('master'); }
function closeMasterDrawer() { /* no-op — view persists; leaveMasterView() handles cleanup */ }

function setMasterDrawerStatus(msg, klass = '') {
  const el = document.getElementById('masterDrawerStatus');
  el.textContent = msg;
  el.className = 'muted' + (klass ? ` ${klass}` : '');
}

function renderMasterDrawer() {
  const body = document.getElementById('masterDrawerBody');
  body.innerHTML = '';
  body.appendChild(renderMasterSummary());
  body.appendChild(renderMasterSkills());
  body.appendChild(renderMasterExperience());
  body.appendChild(renderMasterProjects());
}

function renderMasterSummary() {
  const sec = document.createElement('section');
  sec.className = 'master-section';
  sec.innerHTML = `<h3>Professional summary</h3>`;
  const ta = document.createElement('textarea');
  ta.rows = 4;
  ta.value = masterDraft.professional_summary || '';
  ta.addEventListener('input', () => { masterDraft.professional_summary = ta.value; });
  sec.appendChild(ta);
  return sec;
}

function renderMasterSkills() {
  const sec = document.createElement('section');
  sec.className = 'master-section';
  sec.innerHTML = `<h3>Skills</h3>`;
  const list = document.createElement('div');
  list.className = 'master-skills';
  masterDraft.skills = masterDraft.skills || [];

  function redraw() {
    list.innerHTML = '';
    masterDraft.skills.forEach((s, i) => {
      const card = document.createElement('div');
      card.className = 'master-card';
      card.innerHTML = `
        <div class="master-card-row">
          <input class="master-input" data-cat placeholder="Category" />
          <button class="btn-link master-del" title="Delete category">delete</button>
        </div>
        <textarea class="master-textarea" data-items rows="2" placeholder="items, comma-separated"></textarea>
      `;
      const catEl = card.querySelector('[data-cat]');
      const itemsEl = card.querySelector('[data-items]');
      catEl.value = s.category;
      itemsEl.value = (s.items || []).join(', ');
      catEl.addEventListener('input', () => { masterDraft.skills[i].category = catEl.value; });
      itemsEl.addEventListener('input', () => {
        masterDraft.skills[i].items = itemsEl.value.split(',').map((x) => x.trim()).filter(Boolean);
      });
      card.querySelector('.master-del').addEventListener('click', () => {
        masterDraft.skills.splice(i, 1);
        redraw();
      });
      list.appendChild(card);
    });
  }
  redraw();
  sec.appendChild(list);
  const addBtn = document.createElement('button');
  addBtn.className = 'btn btn-add';
  addBtn.textContent = '+ Add skill category';
  addBtn.addEventListener('click', () => {
    masterDraft.skills.push({ category: 'New Category', items: [] });
    redraw();
  });
  sec.appendChild(addBtn);
  return sec;
}

function renderMasterExperience() {
  const sec = document.createElement('section');
  sec.className = 'master-section';
  sec.innerHTML = `<h3>Experience</h3>`;
  masterDraft.experience = masterDraft.experience || [];
  const list = document.createElement('div');

  function redraw() {
    list.innerHTML = '';
    masterDraft.experience.forEach((e, ei) => {
      const block = document.createElement('div');
      block.className = 'master-card master-role';
      block.innerHTML = `
        <div class="master-grid-2">
          <input class="master-input" data-role placeholder="Role" />
          <input class="master-input" data-company placeholder="Company" />
        </div>
        <div class="master-grid-2">
          <input class="master-input" data-date placeholder="Date (e.g. Jan 2023 – Present)" />
          <input class="master-input" data-loc placeholder="Location" />
        </div>
        <div class="master-bullets" data-bullets></div>
        <div class="master-card-actions">
          <button class="btn btn-add" data-add-bullet>+ Add bullet</button>
          <button class="btn-link master-del" data-del-role>delete role</button>
        </div>
      `;
      block.querySelector('[data-role]').value = e.role || '';
      block.querySelector('[data-company]').value = e.company || '';
      block.querySelector('[data-date]').value = e.date || '';
      block.querySelector('[data-loc]').value = e.location || '';
      block.querySelector('[data-role]').addEventListener('input', (ev) => { masterDraft.experience[ei].role = ev.target.value; });
      block.querySelector('[data-company]').addEventListener('input', (ev) => { masterDraft.experience[ei].company = ev.target.value; });
      block.querySelector('[data-date]').addEventListener('input', (ev) => { masterDraft.experience[ei].date = ev.target.value; });
      block.querySelector('[data-loc]').addEventListener('input', (ev) => { masterDraft.experience[ei].location = ev.target.value; });

      const bulletsEl = block.querySelector('[data-bullets]');
      // Header row labeling the two columns
      const head = document.createElement('div');
      head.className = 'master-bullet-row master-bullet-head';
      head.innerHTML = `
        <span class="master-bullet-idx"></span>
        <div class="master-bullet-col-head">Original (Action · Context · Result)</div>
        <div class="master-bullet-col-head">RAC (Result · Action · Context)</div>
        <span></span>
      `;
      bulletsEl.appendChild(head);

      function redrawBullets() {
        // Wipe everything below the header.
        while (bulletsEl.children.length > 1) bulletsEl.removeChild(bulletsEl.lastChild);
        const pool = e.bullet_pool || [];
        pool.forEach((b, bi) => {
          const row = document.createElement('div');
          row.className = 'master-bullet-row';
          row.innerHTML = `
            <span class="master-bullet-idx">${bi + 1}</span>
            <textarea class="master-textarea" data-col="src" rows="2"></textarea>
            <textarea class="master-textarea master-textarea-rac" data-col="rac" rows="2" placeholder="(empty — click 'Generate RAC for missing' in the header)"></textarea>
            <button class="btn-link master-del" title="Delete">×</button>
          `;
          const srcTa = row.querySelector('[data-col=src]');
          const racTa = row.querySelector('[data-col=rac]');
          srcTa.value = b.text || '';
          racTa.value = b.text_rac || '';
          srcTa.addEventListener('input', () => { masterDraft.experience[ei].bullet_pool[bi].text = srcTa.value; });
          racTa.addEventListener('input', () => { masterDraft.experience[ei].bullet_pool[bi].text_rac = racTa.value; });
          row.querySelector('.master-del').addEventListener('click', () => {
            masterDraft.experience[ei].bullet_pool.splice(bi, 1);
            redrawBullets();
          });
          bulletsEl.appendChild(row);
        });
      }
      e.bullet_pool = e.bullet_pool || [];
      redrawBullets();

      block.querySelector('[data-add-bullet]').addEventListener('click', () => {
        masterDraft.experience[ei].bullet_pool.push({ text: '', tags: [] });
        redrawBullets();
      });
      block.querySelector('[data-del-role]').addEventListener('click', () => {
        if (!confirm(`Delete role "${e.role} @ ${e.company}"?`)) return;
        masterDraft.experience.splice(ei, 1);
        redraw();
      });
      list.appendChild(block);
    });
  }
  redraw();
  sec.appendChild(list);

  const addBtn = document.createElement('button');
  addBtn.className = 'btn btn-add';
  addBtn.textContent = '+ Add experience role';
  addBtn.addEventListener('click', () => {
    masterDraft.experience.push({ company: '', role: '', date: '', location: '', bullet_pool: [] });
    redraw();
  });
  sec.appendChild(addBtn);
  return sec;
}

function renderMasterProjects() {
  const sec = document.createElement('section');
  sec.className = 'master-section';
  sec.innerHTML = `<h3>Projects</h3>`;
  masterDraft.projects = masterDraft.projects || [];
  const list = document.createElement('div');

  function redraw() {
    list.innerHTML = '';
    masterDraft.projects.forEach((p, pi) => {
      const block = document.createElement('div');
      block.className = 'master-card master-project';
      block.innerHTML = `
        <div class="master-grid-2">
          <input class="master-input" data-name placeholder="Project name" />
          <input class="master-input" data-loc placeholder="Location / link" />
        </div>
        <div class="master-bullets" data-bullets></div>
        <div class="master-card-actions">
          <button class="btn btn-add" data-add-bullet>+ Add bullet</button>
          <button class="btn-link master-del" data-del-project>delete project</button>
        </div>
      `;
      block.querySelector('[data-name]').value = p.name || '';
      block.querySelector('[data-loc]').value = p.location || '';
      block.querySelector('[data-name]').addEventListener('input', (ev) => { masterDraft.projects[pi].name = ev.target.value; });
      block.querySelector('[data-loc]').addEventListener('input', (ev) => { masterDraft.projects[pi].location = ev.target.value; });

      const bulletsEl = block.querySelector('[data-bullets]');
      const head = document.createElement('div');
      head.className = 'master-bullet-row master-bullet-head';
      head.innerHTML = `
        <span class="master-bullet-idx"></span>
        <div class="master-bullet-col-head">Original (Action · Context · Result)</div>
        <div class="master-bullet-col-head">RAC (Result · Action · Context)</div>
        <span></span>
      `;
      bulletsEl.appendChild(head);

      function redrawBullets() {
        while (bulletsEl.children.length > 1) bulletsEl.removeChild(bulletsEl.lastChild);
        p.bullet_pool = p.bullet_pool || [];
        p.bullet_pool.forEach((b, bi) => {
          const row = document.createElement('div');
          row.className = 'master-bullet-row';
          row.innerHTML = `
            <span class="master-bullet-idx">${bi + 1}</span>
            <textarea class="master-textarea" data-col="src" rows="2"></textarea>
            <textarea class="master-textarea master-textarea-rac" data-col="rac" rows="2" placeholder="(empty — click 'Generate RAC for missing' in the header)"></textarea>
            <button class="btn-link master-del" title="Delete">×</button>
          `;
          const srcTa = row.querySelector('[data-col=src]');
          const racTa = row.querySelector('[data-col=rac]');
          srcTa.value = b.text || '';
          racTa.value = b.text_rac || '';
          srcTa.addEventListener('input', () => { masterDraft.projects[pi].bullet_pool[bi].text = srcTa.value; });
          racTa.addEventListener('input', () => { masterDraft.projects[pi].bullet_pool[bi].text_rac = racTa.value; });
          row.querySelector('.master-del').addEventListener('click', () => {
            masterDraft.projects[pi].bullet_pool.splice(bi, 1);
            redrawBullets();
          });
          bulletsEl.appendChild(row);
        });
      }
      redrawBullets();

      block.querySelector('[data-add-bullet]').addEventListener('click', () => {
        masterDraft.projects[pi].bullet_pool.push({ text: '', tags: [] });
        redrawBullets();
      });
      block.querySelector('[data-del-project]').addEventListener('click', () => {
        if (!confirm(`Delete project "${p.name}"?`)) return;
        masterDraft.projects.splice(pi, 1);
        redraw();
      });
      list.appendChild(block);
    });
  }
  redraw();
  sec.appendChild(list);

  const addBtn = document.createElement('button');
  addBtn.className = 'btn btn-add';
  addBtn.textContent = '+ Add project';
  addBtn.addEventListener('click', () => {
    masterDraft.projects.push({ name: '', location: '', bullet_pool: [] });
    redraw();
  });
  sec.appendChild(addBtn);
  return sec;
}

// ─── History hydration ─────────────────────────────────────────────────
async function hydrateHistory() {
  let items = [];
  try {
    const r = await fetch(API.generations);
    if (!r.ok) return;
    items = await r.json();
  } catch { return; }
  if (!items.length) return;
  for (const gs of items) {
    const url = gs.source_url || '';
    const key = url || `gen:${gs.id}`;
    if (bulkJobs.has(key)) continue;
    bulkJobs.set(key, {
      url,
      genId: gs.id,
      company: gs.company || '',
      jd: '',
      cv: null,
      pdfBase64: null,
      pdfFilename: gs.pdf_filename || '',
      report: null,
      status: 'done',
      error: null,
      createdAt: gs.created_at,
      applied: !!gs.applied,
    });
  }
  if (bulkJobs.size) {
    document.getElementById('bulkSidebar').classList.remove('hidden');
    renderBulkList();
  }
}

// ─── Applied: tab inline + drawer ──────────────────────────────────────
let appliedRecords = [];

// ─── Outreach (per-tab panel) ──────────────────────────────────────────
function refreshTabOutreachUI(panel, state) {
  const block = panel.querySelector('[data-outreach]');
  if (!block) return;
  block.classList.toggle('hidden', !state.generationId);
  const findBtn = panel.querySelector('[data-outreach-find]');
  findBtn.disabled = !state.generationId;
}

async function loadCachedOutreach(panel, state) {
  if (!state.generationId) return;
  try {
    const r = await fetch(`${API.outreach}/${encodeURIComponent(state.generationId)}`);
    if (!r.ok) return;
    const txt = await r.text();
    if (!txt || txt === 'null') return;
    const rec = JSON.parse(txt);
    if (rec && rec.contacts) {
      renderOutreach(panel, rec);
      panel.querySelector('[data-outreach]').querySelector('details').open = true;
    }
  } catch {}
}

async function runOutreachDiscover(panel, state, refresh) {
  if (!state.generationId) return;
  const findBtn = panel.querySelector('[data-outreach-find]');
  const refreshBtn = panel.querySelector('[data-outreach-refresh]');
  const statusEl = panel.querySelector('[data-outreach-status]');
  findBtn.disabled = true;
  refreshBtn.disabled = true;
  statusEl.textContent = 'Searching Hunter + scoring + writing drafts (≈30s)…';
  statusEl.classList.remove('err');
  try {
    const r = await fetch(API.outreachDiscover, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ generation_id: state.generationId, top_k: 10, refresh: !!refresh }),
    });
    if (!r.ok) {
      let msg = `${r.status}`;
      try {
        const j = await r.json();
        if (j.detail) msg += `: ${typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)}`;
      } catch (_) {}
      throw new Error(msg);
    }
    const rec = await r.json();
    renderOutreach(panel, rec);
    statusEl.textContent = `${(rec.contacts || []).length} contacts ranked.`;
  } catch (e) {
    statusEl.textContent = `Outreach failed: ${e.message}`;
    statusEl.classList.add('err');
  } finally {
    findBtn.disabled = false;
    refreshBtn.disabled = false;
  }
}

function renderOutreach(panel, rec) {
  const meta = panel.querySelector('[data-outreach-meta]');
  const list = panel.querySelector('[data-outreach-list]');
  const refreshBtn = panel.querySelector('[data-outreach-refresh]');
  list.innerHTML = '';
  if (!rec || !rec.contacts || !rec.contacts.length) {
    meta.textContent = '· no contacts';
    list.innerHTML = `<div class="muted" style="padding:0.5rem 0;">No contacts returned. Try refining the company name, or your Hunter free tier may be exhausted for the month.</div>`;
    refreshBtn.classList.remove('hidden');
    return;
  }
  meta.textContent = `· ${rec.contacts.length} ranked (saved ${shortDate(rec.created_at)})`;
  refreshBtn.classList.remove('hidden');
  rec.contacts.forEach((oc, i) => list.appendChild(buildContactCard(oc, i + 1, rec.generation_id, i)));
}

function buildContactCard(oc, rank, genId, contactIndex) {
  const sc = oc.scored;
  const c = sc.contact;
  const draft = oc.draft;
  const card = document.createElement('div');
  card.className = 'outreach-card';
  const first = (c.name || '').split(' ')[0] || 'them';
  card.innerHTML = `
    <div class="outreach-card-head">
      <span class="outreach-rank">#${rank}</span>
      <div class="outreach-who">
        <div class="outreach-name"></div>
        <div class="outreach-title muted"></div>
      </div>
      <div class="outreach-score-block">
        <span class="outreach-cat"></span>
        <span class="outreach-score"></span>
      </div>
    </div>
    <div class="outreach-actions-row">
      <a class="btn btn-ghost outreach-li" target="_blank" rel="noopener noreferrer">LinkedIn ↗</a>
      <a class="btn btn-ghost outreach-mailto">Email ↗</a>
      <button class="btn btn-ghost outreach-reveal" title="Calls Hunter.io email-finder (counts against your monthly Hunter quota)">Find email</button>
      <span class="outreach-email-pill muted"></span>
      <span class="outreach-reveal-status muted"></span>
    </div>
    <div class="outreach-shared muted"></div>
    <div class="outreach-msg">
      <div class="outreach-msg-head">
        <span>LinkedIn note <span class="muted outreach-note-len"></span></span>
        <button class="btn-link" data-copy-li>copy</button>
      </div>
      <textarea class="master-textarea outreach-note" rows="3"></textarea>
    </div>
    <div class="outreach-msg">
      <div class="outreach-msg-head">
        <span>Email</span>
        <button class="btn-link" data-copy-email>copy</button>
      </div>
      <input class="master-input outreach-subject" placeholder="subject" />
      <textarea class="master-textarea outreach-body" rows="6"></textarea>
    </div>
  `;
  card.querySelector('.outreach-name').textContent = c.name || '(unknown)';
  const titleBits = [c.title, c.organization_name].filter(Boolean).join(' · ');
  card.querySelector('.outreach-title').textContent = titleBits || c.headline || '';
  const cat = card.querySelector('.outreach-cat');
  cat.textContent = sc.category || 'other';
  cat.classList.add(`cat-${sc.category || 'other'}`);
  card.querySelector('.outreach-score').textContent = `${sc.score}/100`;

  const liEl = card.querySelector('.outreach-li');
  if (c.linkedin_url) {
    liEl.href = c.linkedin_url;
  } else {
    liEl.classList.add('hidden');
  }

  const mailtoEl = card.querySelector('.outreach-mailto');
  const emailPill = card.querySelector('.outreach-email-pill');
  const revealBtn = card.querySelector('.outreach-reveal');
  const revealStatus = card.querySelector('.outreach-reveal-status');

  function applyEmailState(email, status) {
    c.email = email || null;
    if (email) {
      mailtoEl.classList.remove('hidden');
      const sj = card.querySelector('.outreach-subject');
      const bd = card.querySelector('.outreach-body');
      mailtoEl.href = mailtoHref(email, sj ? sj.value : draft.email_subject, bd ? bd.value : draft.email_body);
      emailPill.textContent = email;
      revealBtn.classList.add('hidden');
    } else {
      mailtoEl.classList.add('hidden');
      emailPill.textContent = status === 'locked'
        ? '✱ email locked'
        : '✱ no email on file';
      revealBtn.classList.remove('hidden');
    }
  }
  applyEmailState(c.email, c.email_status);

  revealBtn.addEventListener('click', async () => {
    if (genId == null || contactIndex == null) return;
    if (!confirm('This calls Hunter email-finder for this contact and counts against your monthly Hunter quota. Continue?')) return;
    revealBtn.disabled = true;
    revealStatus.textContent = 'Revealing…';
    revealStatus.classList.remove('err');
    try {
      const r = await fetch(API.outreachReveal, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ generation_id: genId, contact_index: contactIndex }),
      });
      if (!r.ok) {
        let msg = `${r.status}`;
        try {
          const j = await r.json();
          if (j.detail) msg += `: ${typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)}`;
        } catch (_) {}
        throw new Error(msg);
      }
      const updated = await r.json();
      const newEmail = updated.scored.contact.email;
      const newStatus = updated.scored.contact.email_status;
      applyEmailState(newEmail, newStatus);
      revealStatus.textContent = newEmail ? '✓ revealed' : '';
    } catch (e) {
      revealStatus.textContent = `Reveal failed: ${e.message}`;
      revealStatus.classList.add('err');
    } finally {
      revealBtn.disabled = false;
    }
  });

  const sharedEl = card.querySelector('.outreach-shared');
  sharedEl.textContent = (sc.shared_signals || []).join(' · ') || '';

  const noteEl = card.querySelector('.outreach-note');
  const noteLen = card.querySelector('.outreach-note-len');
  noteEl.value = draft.linkedin_note || '';
  function updateLen() { noteLen.textContent = `${noteEl.value.length}/300`; }
  updateLen();
  noteEl.addEventListener('input', updateLen);

  const subjEl = card.querySelector('.outreach-subject');
  const bodyEl = card.querySelector('.outreach-body');
  subjEl.value = draft.email_subject || '';
  bodyEl.value = draft.email_body || '';

  const refreshMailto = () => {
    if (!c.email) return;
    mailtoEl.href = mailtoHref(c.email, subjEl.value, bodyEl.value);
  };
  subjEl.addEventListener('input', refreshMailto);
  bodyEl.addEventListener('input', refreshMailto);

  card.querySelector('[data-copy-li]').addEventListener('click', () => copyText(noteEl.value));
  card.querySelector('[data-copy-email]').addEventListener('click', () => {
    copyText(`Subject: ${subjEl.value}\n\n${bodyEl.value}`);
  });

  return card;
}

function mailtoHref(email, subject, body) {
  const s = encodeURIComponent(subject || '');
  const b = encodeURIComponent(body || '');
  return `mailto:${email}?subject=${s}&body=${b}`;
}

function copyText(s) {
  navigator.clipboard?.writeText(s).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = s;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch {}
    ta.remove();
  });
}

function refreshTabAppliedUI(panel, state) {
  const wrap = panel.querySelector('[data-applied-wrap]');
  if (!wrap) return;
  wrap.classList.remove('hidden');
  const btn = panel.querySelector('[data-applied-btn]');
  const isApplied = !!state.appliedId;
  btn.textContent = isApplied ? '✓ Applied' : 'Mark applied';
  btn.classList.toggle('btn-success', isApplied);
}

async function refreshAppliedCount() {
  try {
    const r = await fetch(API.applied);
    if (!r.ok) return;
    appliedRecords = await r.json();
    const badge = document.getElementById('railAppliedBadge');
    if (badge) {
      badge.textContent = appliedRecords.length;
      badge.classList.toggle('hidden', appliedRecords.length === 0);
    }
    // Mark tabs whose generationId matches an applied record
    const byGen = new Map(appliedRecords.filter((a) => a.generation_id).map((a) => [a.generation_id, a.id]));
    for (const [, state] of tabs) {
      if (state.generationId && byGen.has(state.generationId)) {
        state.appliedId = byGen.get(state.generationId);
        const panel = document.querySelector(`.panel[data-tab-id="${state.id}"]`);
        if (panel) refreshTabAppliedUI(panel, state);
      }
    }
  } catch {}
}

function wireAppliedDrawer() { /* now wired via rail; view enter hook handles refresh */ }
function openAppliedDrawer() { switchView('applied'); }
function closeAppliedDrawer() { /* no-op */ }

function renderAppliedDrawer() {
  const body = document.getElementById('appliedDrawerBody');
  body.innerHTML = '';
  if (!appliedRecords.length) {
    body.innerHTML = `<div class="muted" style="padding:1rem;">No applications yet. Generate a resume and click <strong>Mark applied</strong>.</div>`;
    return;
  }
  for (const rec of appliedRecords) {
    const card = document.createElement('div');
    card.className = 'applied-card';
    card.innerHTML = `
      <div class="applied-card-head">
        <div>
          <div class="applied-company"></div>
          <div class="applied-title muted"></div>
        </div>
        <div class="applied-controls">
          <select class="applied-status" data-status></select>
          <button class="btn-link applied-del" title="Delete">×</button>
        </div>
      </div>
      <div class="applied-meta">
        <a class="applied-link" target="_blank" rel="noopener noreferrer"></a>
        <input class="master-input applied-date" type="date" data-date />
      </div>
      <textarea class="master-textarea applied-notes" rows="2" placeholder="Notes"></textarea>
    `;
    card.querySelector('.applied-company').textContent = rec.company || '(no company)';
    card.querySelector('.applied-title').textContent = rec.job_title || '';
    const linkEl = card.querySelector('.applied-link');
    if (rec.job_link) {
      linkEl.href = rec.job_link;
      linkEl.textContent = rec.job_link.replace(/^https?:\/\//, '').slice(0, 60);
    } else {
      linkEl.classList.add('hidden');
    }
    const sel = card.querySelector('[data-status]');
    for (const s of APPLIED_STATUSES) {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = s;
      if (s === rec.status) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.classList.add(`is-${rec.status}`);
    sel.addEventListener('change', async () => {
      const newStatus = sel.value;
      sel.className = `applied-status is-${newStatus}`;
      try {
        await fetch(`${API.applied}/${rec.id}`, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ status: newStatus }),
        });
        rec.status = newStatus;
      } catch (e) {}
    });
    const dateEl = card.querySelector('[data-date]');
    dateEl.value = rec.applied_at || '';
    dateEl.addEventListener('change', async () => {
      try {
        await fetch(`${API.applied}/${rec.id}`, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ applied_at: dateEl.value }),
        });
      } catch {}
    });
    const notesEl = card.querySelector('.applied-notes');
    notesEl.value = rec.notes || '';
    let notesT;
    notesEl.addEventListener('input', () => {
      clearTimeout(notesT);
      notesT = setTimeout(async () => {
        try {
          await fetch(`${API.applied}/${rec.id}`, {
            method: 'PATCH',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ notes: notesEl.value }),
          });
        } catch {}
      }, 400);
    });
    card.querySelector('.applied-del').addEventListener('click', async () => {
      if (!confirm(`Remove ${rec.company} from applied list?`)) return;
      try {
        const r = await fetch(`${API.applied}/${rec.id}`, { method: 'DELETE' });
        if (r.ok) {
          appliedRecords = appliedRecords.filter((x) => x.id !== rec.id);
          renderAppliedDrawer();
          refreshAppliedCount();
        }
      } catch {}
    });
    body.appendChild(card);
  }
}

async function markApplied(state, panel) {
  const today = new Date().toISOString().slice(0, 10);
  const payload = {
    company: state.company || 'Untitled',
    job_title: '',
    job_link: state.bulkUrl || '',
    applied_at: today,
    status: 'applied',
    notes: '',
    generation_id: state.generationId || null,
  };
  try {
    const r = await fetch(API.applied, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    const rec = await r.json();
    state.appliedId = rec.id;
    refreshTabAppliedUI(panel, state);
    refreshAppliedCount();
  } catch (e) {
    console.error('mark applied failed', e);
  }
}

async function saveMasterDraft() {
  if (!masterDraft) return;
  const saveBtn = document.getElementById('masterSaveBtn');
  saveBtn.disabled = true;
  setMasterDrawerStatus('Saving…');
  // Drop empty bullets before sending so we don't bloat the bank.
  const clean = JSON.parse(JSON.stringify(masterDraft));
  for (const e of clean.experience || []) {
    e.bullet_pool = (e.bullet_pool || []).filter((b) => (b.text || '').trim());
  }
  for (const p of clean.projects || []) {
    p.bullet_pool = (p.bullet_pool || []).filter((b) => (b.text || '').trim());
  }
  try {
    const r = await fetch(API.masterSave, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(clean),
    });
    if (!r.ok) {
      let msg = `${r.status}`;
      try {
        const j = await r.json();
        if (j.detail) msg += `: ${typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)}`;
      } catch (_) {}
      throw new Error(msg);
    }
    masterCV = await r.json();
    setMasterDrawerStatus('Saved.', 'ok');
    setTimeout(closeMasterDrawer, 600);
  } catch (e) {
    setMasterDrawerStatus(`Save failed: ${e.message}`, 'err');
  } finally {
    saveBtn.disabled = false;
  }
}

// ─── Preferences drawer ────────────────────────────────────────────────
let prefsDraft = null;

function wirePrefsDrawer() {
  const saveBtn = document.getElementById('prefsSaveBtn');
  if (saveBtn) saveBtn.addEventListener('click', savePrefs);
}

function openPrefsDrawer() { switchView('settings'); }
function closePrefsDrawer() { /* no-op */ }

function setPrefsStatus(msg, klass = '') {
  const el = document.getElementById('prefsStatus');
  el.textContent = msg;
  el.className = 'muted' + (klass ? ` ${klass}` : '');
}

function renderPrefsDrawer() {
  const body = document.getElementById('prefsDrawerBody');
  const p = prefsDraft || {};
  body.innerHTML = `
    <section class="master-section"><h3>Target roles</h3>
      <textarea class="master-textarea" id="pf_roles" rows="2" placeholder="One per line. e.g. Data Engineer&#10;Analytics Engineer"></textarea>
    </section>
    <section class="master-section"><h3>Locations</h3>
      <textarea class="master-textarea" id="pf_locations" rows="2" placeholder="United States&#10;Remote"></textarea>
      <label class="muted"><input type="checkbox" id="pf_remote_ok"> Include remote jobs</label>
    </section>
    <section class="master-section"><h3>Hard filters</h3>
      <label class="muted"><input type="checkbox" id="pf_visa"> I need visa sponsorship (reject jobs explicitly saying no)</label>
      <div class="master-grid-2" style="margin-top:0.4rem;">
        <label>Languages I speak<input class="master-input" id="pf_my_langs" placeholder="English, Spanish" /></label>
        <label>Max required YoE<input class="master-input" id="pf_max_yoe" type="number" min="0" max="30" placeholder="e.g. 7" /></label>
      </div>
      <div class="master-grid-2" style="margin-top:0.4rem;">
        <label>Min salary (USD)<input class="master-input" id="pf_salary_min" type="number" placeholder="e.g. 120000" /></label>
        <label>Posted within (days)<input class="master-input" id="pf_post_age" type="number" min="1" max="30" /></label>
      </div>
    </section>
    <section class="master-section"><h3>Keywords</h3>
      <label>Must contain at least one of (OR)<textarea class="master-textarea" id="pf_kw_inc" rows="2" placeholder="Python, Spark, dbt"></textarea></label>
      <label>Exclude if any present<textarea class="master-textarea" id="pf_kw_exc" rows="2" placeholder="contract, intern, junior"></textarea></label>
    </section>
    <section class="master-section"><h3>Companies</h3>
      <label>Boost (substring match)<textarea class="master-textarea" id="pf_co_inc" rows="2" placeholder="Stripe&#10;Anthropic"></textarea></label>
      <label>Block<textarea class="master-textarea" id="pf_co_exc" rows="2" placeholder="ACME Corp"></textarea></label>
    </section>
    <section class="master-section"><h3>Auto-generate resumes</h3>
      <div class="master-grid-2">
        <label>Top N per run<input class="master-input" id="pf_autogen_n" type="number" min="0" max="20" /></label>
        <label>Min score threshold (0-100)<input class="master-input" id="pf_min_score" type="number" min="0" max="100" /></label>
      </div>
    </section>
  `;
  const csv = (arr) => (arr || []).join(', ');
  const nl = (arr) => (arr || []).join('\n');
  body.querySelector('#pf_roles').value = nl(p.roles);
  body.querySelector('#pf_locations').value = nl(p.locations);
  body.querySelector('#pf_remote_ok').checked = !!p.remote_ok;
  body.querySelector('#pf_visa').checked = !!p.visa_sponsorship_needed;
  body.querySelector('#pf_my_langs').value = csv(p.my_languages || ['English']);
  body.querySelector('#pf_max_yoe').value = p.max_required_yoe ?? '';
  body.querySelector('#pf_salary_min').value = p.salary_min_usd ?? '';
  body.querySelector('#pf_post_age').value = p.post_age_days_max ?? 7;
  body.querySelector('#pf_kw_inc').value = csv(p.keywords_include);
  body.querySelector('#pf_kw_exc').value = csv(p.keywords_exclude);
  body.querySelector('#pf_co_inc').value = nl(p.companies_include);
  body.querySelector('#pf_co_exc').value = nl(p.companies_exclude);
  body.querySelector('#pf_autogen_n').value = p.autogen_top_n ?? 5;
  body.querySelector('#pf_min_score').value = p.autogen_min_score ?? 70;
}

async function savePrefs() {
  const $ = (id) => document.getElementById(id);
  const lines = (id) => $(id).value.split('\n').map(s => s.trim()).filter(Boolean);
  const csv = (id) => $(id).value.split(',').map(s => s.trim()).filter(Boolean);
  const intOrNull = (id) => { const v = $(id).value.trim(); return v ? parseInt(v, 10) : null; };
  const payload = {
    roles: lines('pf_roles'),
    locations: lines('pf_locations'),
    remote_ok: $('pf_remote_ok').checked,
    visa_sponsorship_needed: $('pf_visa').checked,
    my_languages: csv('pf_my_langs').length ? csv('pf_my_langs') : ['English'],
    max_required_yoe: intOrNull('pf_max_yoe'),
    salary_min_usd: intOrNull('pf_salary_min'),
    post_age_days_max: parseInt($('pf_post_age').value || '7', 10),
    keywords_include: csv('pf_kw_inc'),
    keywords_exclude: csv('pf_kw_exc'),
    companies_include: lines('pf_co_inc'),
    companies_exclude: lines('pf_co_exc'),
    autogen_top_n: parseInt($('pf_autogen_n').value || '5', 10),
    autogen_min_score: parseInt($('pf_min_score').value || '70', 10),
  };
  setPrefsStatus('Saving…');
  try {
    const r = await fetch(API.preferences, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    setPrefsStatus('Saved.', 'ok');
    setTimeout(closePrefsDrawer, 500);
  } catch (e) {
    setPrefsStatus(`Save failed: ${e.message}`, 'err');
  }
}

// ─── Discovered-jobs drawer ────────────────────────────────────────────
let discoveredJobs = [];
let discoveryPollTimer = null;

function wireDiscoveredDrawer() {
  // Keep the in-view widgets wired; open/close handled by the rail router.
  document.getElementById('discoveryRunBtn').addEventListener('click', runDiscovery);
  document.getElementById('discoveredFilter').addEventListener('input', () => renderDiscoveredList());
  document.getElementById('discoveredHideRejected').addEventListener('change', () => renderDiscoveredList());
  document.getElementById('discoveredHideApplied').addEventListener('change', () => renderDiscoveredList());
}

async function refreshDiscoveredCount() {
  try {
    const r = await fetch(API.discoveryJobs);
    if (!r.ok) return;
    const items = await r.json();
    discoveredJobs = items;
    const visible = items.filter(j => !j.applied && !j.rejected).length;
    const badge = document.getElementById('railDiscoveredBadge');
    if (badge) {
      badge.textContent = visible;
      badge.classList.toggle('hidden', visible === 0);
    }
    const curatedCount = items.filter(j => !j.rejected).length;
    const cBadge = document.getElementById('railCuratedBadge');
    if (cBadge) {
      cBadge.textContent = curatedCount;
      cBadge.classList.toggle('hidden', curatedCount === 0);
    }
  } catch {}
}

function openDiscoveredDrawer() { switchView('discovered'); }
function closeDiscoveredDrawer() { /* no-op */ }

async function refreshDiscoveryRunStatus() {
  try {
    const r = await fetch(API.discoveryStatus);
    if (!r.ok) return;
    const txt = await r.text();
    const rec = (!txt || txt === 'null') ? null : JSON.parse(txt);
    const el = document.getElementById('discoveredRunStatus');
    const btn = document.getElementById('discoveryRunBtn');
    if (!rec) { el.textContent = ''; btn.disabled = false; return; }
    const running = rec.finished_at == null;
    if (running) {
      el.textContent = `Running… (started ${shortDate(rec.started_at)})`;
      btn.disabled = true;
      if (!discoveryPollTimer) discoveryPollTimer = setInterval(refreshDiscoveryRunStatus, 5000);
    } else {
      if (discoveryPollTimer) { clearInterval(discoveryPollTimer); discoveryPollTimer = null; }
      btn.disabled = false;
      const e = rec.error ? ` · ⚠ ${rec.error}` : '';
      el.textContent = `Last run ${shortDate(rec.finished_at)} · ${rec.added} added, ${rec.dup_skipped} dup, ${rec.rejected} filtered, ${rec.autogen_count} auto-tailored${e}`;
      // Refresh the list now that the run finished.
      refreshDiscoveredCount().then(renderDiscoveredList);
    }
  } catch {}
}

async function runDiscovery() {
  const btn = document.getElementById('discoveryRunBtn');
  btn.disabled = true;
  try {
    const r = await fetch(API.discoveryRun, { method: 'POST' });
    if (!r.ok) {
      let msg = `${r.status}`;
      try { const j = await r.json(); if (j.detail) msg += `: ${j.detail}`; } catch {}
      throw new Error(msg);
    }
    refreshDiscoveryRunStatus();
  } catch (e) {
    document.getElementById('discoveredRunStatus').textContent = `Failed: ${e.message}`;
    btn.disabled = false;
  }
}

function renderDiscoveredList() {
  const filter = (document.getElementById('discoveredFilter').value || '').toLowerCase();
  const hideRejected = document.getElementById('discoveredHideRejected').checked;
  const hideApplied = document.getElementById('discoveredHideApplied').checked;
  const list = document.getElementById('discoveredList');
  list.innerHTML = '';
  let shown = 0;
  for (const j of discoveredJobs) {
    if (hideRejected && j.rejected) continue;
    if (hideApplied && j.applied) continue;
    if (filter) {
      const blob = `${j.company} ${j.title}`.toLowerCase();
      if (!blob.includes(filter)) continue;
    }
    list.appendChild(buildDiscoveredCard(j));
    shown++;
  }
  document.getElementById('discoveredCount').textContent = `${shown} of ${discoveredJobs.length}`;
}

function buildDiscoveredCard(j) {
  const card = document.createElement('div');
  card.className = 'discovered-card' + (j.rejected ? ' is-rejected' : '') + (j.applied ? ' is-applied' : '');
  const sponsorClass = ({
    available: 'ok', not_available: 'err', mentioned: 'warn', unspecified: 'muted'
  })[j.sponsorship_status] || 'muted';
  card.innerHTML = `
    <div class="discovered-head">
      <div class="discovered-titles">
        <div class="discovered-title"></div>
        <div class="discovered-company muted"></div>
      </div>
      <div class="discovered-score-wrap">
        <span class="discovered-score"></span>
      </div>
    </div>
    <div class="discovered-meta muted"></div>
    <div class="discovered-pills">
      <span class="pill sponsor-pill"></span>
      <span class="pill yoe-pill hidden"></span>
      <span class="pill lang-pill hidden"></span>
      <span class="pill salary-pill hidden"></span>
      <span class="pill posted-pill hidden"></span>
    </div>
    <div class="discovered-actions">
      <a class="btn btn-ghost discovered-link" target="_blank" rel="noopener noreferrer">Open posting ↗</a>
      <button class="btn btn-primary discovered-tailor">Generate resume</button>
      <button class="btn discovered-mark-applied">Mark applied</button>
      <button class="btn-link discovered-delete">delete</button>
      <span class="discovered-status muted"></span>
    </div>
    <div class="discovered-rejection muted hidden"></div>
  `;
  card.querySelector('.discovered-title').textContent = j.title || '(no title)';
  card.querySelector('.discovered-company').textContent = j.company || '(no company)';
  const meta = [j.location, j.posted_at].filter(Boolean).join(' · ');
  card.querySelector('.discovered-meta').textContent = meta;
  card.querySelector('.discovered-score').textContent = `${j.score}/100`;

  const sp = card.querySelector('.sponsor-pill');
  sp.className = `pill sponsor-pill ${sponsorClass}`;
  sp.textContent = `visa: ${j.sponsorship_status || 'unspecified'}`;

  if (j.yoe_required != null) {
    const e = card.querySelector('.yoe-pill');
    e.classList.remove('hidden');
    e.textContent = `${j.yoe_required}+ YoE`;
  }
  if ((j.languages_required || []).length) {
    const e = card.querySelector('.lang-pill');
    e.classList.remove('hidden');
    e.textContent = `lang: ${(j.languages_required || []).join(', ')}`;
  }
  if (j.salary) {
    const e = card.querySelector('.salary-pill');
    e.classList.remove('hidden');
    e.textContent = j.salary;
  }
  if (j.posted_at) {
    const e = card.querySelector('.posted-pill');
    e.classList.remove('hidden');
    e.textContent = j.posted_at;
  }

  const linkEl = card.querySelector('.discovered-link');
  if (j.application_link) {
    linkEl.href = j.application_link;
  } else {
    linkEl.classList.add('hidden');
  }

  if (j.rejected) {
    const r = card.querySelector('.discovered-rejection');
    r.classList.remove('hidden');
    r.textContent = `Rejected by filter: ${j.rejection_reason}`;
  }

  const tailorBtn = card.querySelector('.discovered-tailor');
  if (j.generation_id) {
    tailorBtn.textContent = 'Open resume';
    tailorBtn.classList.add('btn-success');
    tailorBtn.addEventListener('click', () => openGenerationInTab(j.generation_id, j.application_link));
  } else {
    tailorBtn.addEventListener('click', () => tailorDiscoveredJob(j, card));
  }

  const appliedBtn = card.querySelector('.discovered-mark-applied');
  if (j.applied) {
    appliedBtn.textContent = '✓ Applied';
    appliedBtn.classList.add('btn-success');
    appliedBtn.disabled = true;
  } else {
    appliedBtn.addEventListener('click', () => markDiscoveredApplied(j, card));
  }

  card.querySelector('.discovered-delete').addEventListener('click', async () => {
    if (!confirm(`Remove this job from your discovered list?`)) return;
    await fetch(`${API.discoveryJobs}/${j.id}`, { method: 'DELETE' });
    discoveredJobs = discoveredJobs.filter(x => x.id !== j.id);
    renderDiscoveredList();
    refreshDiscoveredCount();
  });

  return card;
}

async function tailorDiscoveredJob(job, card) {
  const btn = card.querySelector('.discovered-tailor');
  const status = card.querySelector('.discovered-status');
  btn.disabled = true;
  status.textContent = 'Tailoring (≈30s)…';
  try {
    const r = await fetch(API.tailor, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        company: job.company || 'Company',
        jd: job.description,
        source_url: job.application_link || null,
        rewrite_summary: false,
        enrich_skills: true,
        use_rac: !!document.getElementById('bulkUseRac')?.checked,
      }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    const data = await r.json();
    await fetch(`${API.discoveryJobs}/${job.id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ generation_id: data.generation_id }),
    });
    status.textContent = `Generated ✓ (${data.download_filename})`;
    job.generation_id = data.generation_id;
    btn.textContent = 'Open resume';
    btn.classList.add('btn-success');
    btn.disabled = false;
    btn.onclick = () => openGenerationInTab(data.generation_id, job.application_link);
  } catch (e) {
    status.textContent = `Failed: ${e.message}`;
    btn.disabled = false;
  }
}

async function markDiscoveredApplied(job, card) {
  const today = new Date().toISOString().slice(0, 10);
  try {
    const r = await fetch(API.applied, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        company: job.company || 'Untitled',
        job_title: job.title || '',
        job_link: job.application_link || '',
        applied_at: today,
        status: 'applied',
        notes: '',
        generation_id: job.generation_id || null,
      }),
    });
    if (!r.ok) throw new Error(`${r.status}`);
    const appRec = await r.json();
    await fetch(`${API.discoveryJobs}/${job.id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ applied: true, applied_id: appRec.id }),
    });
    job.applied = true;
    job.applied_id = appRec.id;
    renderDiscoveredList();
    refreshDiscoveredCount();
    refreshAppliedCount();
  } catch (e) {
    card.querySelector('.discovered-status').textContent = `Failed: ${e.message}`;
  }
}

async function openGenerationInTab(genId, sourceUrl) {
  if (!genId) return;
  // Reuse the bulk-job system: insert/lookup, then call openBulkJob.
  const key = sourceUrl || `gen:${genId}`;
  if (!bulkJobs.has(key)) {
    bulkJobs.set(key, {
      url: sourceUrl || '',
      genId,
      company: '',
      jd: '',
      cv: null,
      pdfBase64: null,
      pdfFilename: '',
      report: null,
      status: 'done',
      error: null,
      createdAt: '',
      applied: false,
    });
    document.getElementById('bulkSidebar').classList.remove('hidden');
    renderBulkList();
  }
  closeDiscoveredDrawer();
  await openBulkJob(key);
}


// ─── Curated view ──────────────────────────────────────────────────────
const curatedSelection = new Set();   // job.id of selected rows

function wireCuratedView() {
  const filter = document.getElementById('curatedFilter');
  if (!filter) return;
  filter.addEventListener('input', renderCuratedList);
  document.getElementById('curatedHideApplied').addEventListener('change', renderCuratedList);
  document.getElementById('curatedRegionFilter').addEventListener('change', renderCuratedList);
  document.getElementById('curatedSelectAll').addEventListener('change', () => {
    const visible = visibleCuratedJobs();
    const sel = document.getElementById('curatedSelectAll');
    if (sel.checked) visible.forEach((j) => curatedSelection.add(j.id));
    else visible.forEach((j) => curatedSelection.delete(j.id));
    renderCuratedList();
  });
  document.getElementById('curatedAddToBulkBtn').addEventListener('click', addCuratedToBulk);
}

function classifyRegion(loc) {
  // Mirrors src/sheets_sync.py — keep simple, just enough to filter.
  const s = (loc || '').toLowerCase();
  if (!s) return 'Other';
  if (/(united kingdom|london|manchester|england|scotland|wales|\buk\b)/i.test(s)) return 'UK';
  if (/(uae|dubai|abu dhabi)/i.test(s)) return 'UAE';
  if (/(australia|new zealand|\baus\b|\bnz\b|sydney|melbourne|auckland)/i.test(s)) return 'Australia & NZ';
  if (/(united states|\busa?\b|new york|california|texas|florida|illinois)/i.test(s)) return 'US';
  if (/(europe|germany|france|spain|italy|netherlands|berlin|paris|amsterdam|dublin)/i.test(s)) return 'Europe';
  return 'Other';
}

function visibleCuratedJobs() {
  const filter = (document.getElementById('curatedFilter')?.value || '').toLowerCase();
  const hideApplied = !!document.getElementById('curatedHideApplied')?.checked;
  const region = document.getElementById('curatedRegionFilter')?.value || '';
  return (discoveredJobs || []).filter((j) => {
    if (j.rejected) return false;
    if (hideApplied && j.applied) return false;
    if (filter && !`${j.company} ${j.title} ${j.location}`.toLowerCase().includes(filter)) return false;
    if (region && classifyRegion(j.location) !== region) return false;
    return true;
  });
}

function renderCuratedList() {
  const list = document.getElementById('curatedList');
  if (!list) return;
  list.innerHTML = '';
  const visible = visibleCuratedJobs();
  visible.forEach((j) => list.appendChild(buildCuratedCard(j)));
  document.getElementById('curatedCount').textContent = `${visible.length} of ${(discoveredJobs || []).filter((j) => !j.rejected).length}`;
  // Sync select-all checkbox state
  const sel = document.getElementById('curatedSelectAll');
  if (sel) {
    sel.checked = visible.length > 0 && visible.every((j) => curatedSelection.has(j.id));
  }
  updateCuratedAddBtn();
}

function updateCuratedAddBtn() {
  const btn = document.getElementById('curatedAddToBulkBtn');
  if (!btn) return;
  const count = curatedSelection.size;
  btn.textContent = `Add ${count} to bulk import`;
  btn.disabled = count === 0;
}

function buildCuratedCard(j) {
  const card = document.createElement('div');
  card.className = 'discovered-card curated-card' + (j.applied ? ' is-applied' : '');
  const region = classifyRegion(j.location);
  card.innerHTML = `
    <div class="curated-head">
      <input type="checkbox" class="curated-check" />
      <div class="discovered-titles">
        <div class="discovered-title"></div>
        <div class="discovered-company muted"></div>
      </div>
      <div class="discovered-score-wrap">
        <span class="pill region-pill"></span>
        <span class="discovered-score"></span>
      </div>
    </div>
    <div class="discovered-meta muted"></div>
    <div class="curated-actions">
      <a class="btn btn-ghost curated-link" target="_blank" rel="noopener noreferrer">Open ↗</a>
      <span class="discovered-status muted"></span>
    </div>
  `;
  card.querySelector('.discovered-title').textContent = j.title || '(no title)';
  card.querySelector('.discovered-company').textContent = j.company || '(no company)';
  card.querySelector('.region-pill').textContent = region;
  card.querySelector('.discovered-score').textContent = `${j.score}/100`;
  card.querySelector('.discovered-meta').textContent = [j.location, j.posted_at, j.salary].filter(Boolean).join(' · ');
  const linkEl = card.querySelector('.curated-link');
  if (j.application_link) linkEl.href = j.application_link;
  else linkEl.classList.add('hidden');

  const check = card.querySelector('.curated-check');
  check.checked = curatedSelection.has(j.id);
  check.addEventListener('change', () => {
    if (check.checked) curatedSelection.add(j.id);
    else curatedSelection.delete(j.id);
    updateCuratedAddBtn();
  });
  // Also let user click anywhere on the card body (except link) to toggle
  card.addEventListener('click', (e) => {
    if (e.target.closest('a, button, input')) return;
    check.checked = !check.checked;
    if (check.checked) curatedSelection.add(j.id);
    else curatedSelection.delete(j.id);
    updateCuratedAddBtn();
  });
  return card;
}

function addCuratedToBulk() {
  const selectedIds = Array.from(curatedSelection);
  if (!selectedIds.length) return;
  const byId = new Map((discoveredJobs || []).map((j) => [j.id, j]));
  const urls = selectedIds
    .map((id) => byId.get(id))
    .filter((j) => j && j.application_link)
    .map((j) => j.application_link);
  if (!urls.length) {
    alert('Selected jobs have no application_link.');
    return;
  }
  // Switch to Compose, expand bulk panel, append URLs to textarea.
  switchView('compose');
  const links = document.getElementById('bulkLinks');
  const existing = (links.value || '').trim();
  const merged = (existing ? existing + '\n' : '') + urls.join('\n');
  links.value = merged;
  links.dispatchEvent(new Event('input'));   // refresh line-number gutter
  // Open the bulk panel.
  const details = document.getElementById('bulkPanel');
  if (details) details.open = true;
  // Scroll into view + clear selection so user knows it landed.
  links.scrollIntoView({ behavior: 'smooth', block: 'start' });
  curatedSelection.clear();
  updateCuratedAddBtn();
}
