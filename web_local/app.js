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
  await hydrateHistory();
  refreshAppliedCount();
})();

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
      const data = await postJSON(API.tailor, { company, jd, source_url, rewrite_summary: false, enrich_skills: true });
      state.cv = data.cv;
      state.report = data.report;
      state.pdfFilename = data.download_filename;
      state.generationId = data.generation_id || null;
      setPdf(state, previewEl, previewEmpty, data.pdf_base64);
      syncTabToBulk(state, data);
      refreshTabAppliedUI(panel, state);
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
        const data = await postJSON(API.tailor, {
          company: job.company || _companyFallback(job.url),
          jd: job.jd,
          rewrite_summary: false,
          enrich_skills: true,
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
  document.getElementById('editMasterBtn').addEventListener('click', openMasterDrawer);
  document.getElementById('masterCloseBtn').addEventListener('click', closeMasterDrawer);
  document.getElementById('masterBackdrop').addEventListener('click', closeMasterDrawer);
  document.getElementById('masterSaveBtn').addEventListener('click', saveMasterDraft);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !document.getElementById('masterDrawer').classList.contains('hidden')) {
      closeMasterDrawer();
    }
  });
}

function openMasterDrawer() {
  if (!masterCV) return;
  masterDraft = JSON.parse(JSON.stringify(masterCV));
  renderMasterDrawer();
  document.getElementById('masterBackdrop').classList.remove('hidden');
  document.getElementById('masterDrawer').classList.remove('hidden');
  document.getElementById('masterDrawer').setAttribute('aria-hidden', 'false');
}

function closeMasterDrawer() {
  document.getElementById('masterBackdrop').classList.add('hidden');
  document.getElementById('masterDrawer').classList.add('hidden');
  document.getElementById('masterDrawer').setAttribute('aria-hidden', 'true');
  masterDraft = null;
  setMasterDrawerStatus('');
}

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
      function redrawBullets() {
        bulletsEl.innerHTML = '';
        const pool = e.bullet_pool || [];
        pool.forEach((b, bi) => {
          const row = document.createElement('div');
          row.className = 'master-bullet-row';
          row.innerHTML = `
            <span class="master-bullet-idx">${bi + 1}</span>
            <textarea class="master-textarea" rows="2"></textarea>
            <button class="btn-link master-del" title="Delete">×</button>
          `;
          const ta = row.querySelector('textarea');
          ta.value = b.text || '';
          ta.addEventListener('input', () => { masterDraft.experience[ei].bullet_pool[bi].text = ta.value; });
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
      function redrawBullets() {
        bulletsEl.innerHTML = '';
        p.bullet_pool = p.bullet_pool || [];
        p.bullet_pool.forEach((b, bi) => {
          const row = document.createElement('div');
          row.className = 'master-bullet-row';
          row.innerHTML = `
            <span class="master-bullet-idx">${bi + 1}</span>
            <textarea class="master-textarea" rows="2"></textarea>
            <button class="btn-link master-del" title="Delete">×</button>
          `;
          const ta = row.querySelector('textarea');
          ta.value = b.text || '';
          ta.addEventListener('input', () => { masterDraft.projects[pi].bullet_pool[bi].text = ta.value; });
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
    const btn = document.getElementById('openAppliedBtn');
    if (btn) btn.textContent = `Applied jobs (${appliedRecords.length})`;
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

function wireAppliedDrawer() {
  document.getElementById('openAppliedBtn').addEventListener('click', openAppliedDrawer);
  document.getElementById('appliedBackdrop').addEventListener('click', closeAppliedDrawer);
  document.getElementById('appliedCloseBtn').addEventListener('click', closeAppliedDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !document.getElementById('appliedDrawer').classList.contains('hidden')) {
      closeAppliedDrawer();
    }
  });
}

async function openAppliedDrawer() {
  await refreshAppliedCount();
  renderAppliedDrawer();
  document.getElementById('appliedBackdrop').classList.remove('hidden');
  document.getElementById('appliedDrawer').classList.remove('hidden');
}
function closeAppliedDrawer() {
  document.getElementById('appliedBackdrop').classList.add('hidden');
  document.getElementById('appliedDrawer').classList.add('hidden');
}

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
