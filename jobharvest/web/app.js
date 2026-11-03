'use strict';
const $ = id => document.getElementById(id);
let offset = 0, total = 0, selected = null, csrf = '', collecting = false, lastRun = null, searchSequence = 0, lastSiteResult = '';
const pageSize = 25;
let view = 'matches', careerProfile = null;
const emptyDetailContent = $('detail').firstElementChild.cloneNode(true);
function resetDetail() {
  selected = null;
  $('detail').classList.remove('is-open');
  $('detail').replaceChildren(emptyDetailContent.cloneNode(true));
  document.querySelectorAll('.job-card.selected').forEach(card => card.classList.remove('selected'));
}
const viewCopy = {
  matches: ['My matches', 'Your experience. New possibilities.', 'Ranked around your career, skills and preferences. A thoughtful place to start.'],
  remote: ['Remote jobs', 'Great work. Wherever you are.', 'Remote opportunities matched to your career. Check the hiring location before applying.'],
  saved: ['Saved jobs', 'Good possibilities, kept close.', 'Your shortlist, your notes and the opportunities you want to come back to.'],
  all: ['All jobs', 'A wider world of opportunity.', 'Explore the full collection across roles, locations and companies.']
};
function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function note(text) { $('notice').hidden = !text; $('notice').textContent = text || ''; }
async function api(url, options) { const response = await fetch(url, options); const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Request failed'); return value; }
function date(value) { if (!value) return 'Unknown'; const d = new Date(value); return Number.isNaN(d.getTime()) ? value : d.toLocaleString(); }
function params() { const p = new URLSearchParams(); for (const [key, value] of new FormData($('filtersForm'))) if (value) p.set(key, value); if (view === 'remote') p.set('remote', 'true'); if (view === 'saved') p.set('saved', 'true'); return p; }
function options(id, values) { const s = $(id), signature = JSON.stringify(values); if (s.dataset.options === signature) return; s.dataset.options = signature; const old = s.value; while (s.options.length > 1) s.remove(1); for (const value of values) { const o = el('option', value); o.value = value; s.append(o); } s.value = old; }
async function stats() {
  const s = await api('/api/stats'); csrf = s.csrf_token;
  for (const [id, key] of [['activeCount','active'],['companyCount','companies'],['remoteCount','remote'],['totalCount','total']]) $(id).textContent = s[key].toLocaleString();
  $('schedule').textContent = `Daily · ${s.schedule.time} · ${s.schedule.timezone}`;
  $('schedule').title = `Next schedule: ${date(s.next_collection)}`;
  options('domain', s.domains); options('level', s.levels); options('source', s.configured_sources.filter(x => x.enabled).map(x => x.name));
  collecting = s.collector.running || s.last_run?.status === 'running';
  $('addSitesBtn').disabled = collecting; $('starterSitesBtn').disabled = collecting;
  $('sitesProgress').textContent = s.collector.progress || '';
  const boards = $('companyBoards'); boards.replaceChildren();
  for (const source of s.configured_sources.filter(x => x.company && !x.name.endsWith('-example'))) {
    const row = el('div', undefined, 'company-board');
    row.append(el('b', source.company), el('span', `${source.type} · ${source.enabled ? 'Enabled · daily collection' : 'Disabled'} · ${source.name}`));
    const health = s.source_health?.[source.name];
    if (health) {
      row.append(el('span', `Last collection: ${health.status} · ${health.fetched} fetched · ${health.inserted} new · ${date(health.started_at)}`));
      if (health.error) row.append(el('p', health.error, 'board-note' + (health.status === 'failed' ? ' failed' : '')));
    } else if (source.enabled) row.append(el('span', 'Waiting for the first collection'));
    if (source.career_url) { const link = el('a', 'Open career board ↗'); link.href = source.career_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(link); }
    boards.append(row);
  }
  if (!boards.children.length) boards.append(el('p', 'No company boards added yet.', 'help'));
  const discovery = s.collector.result?.discovery;
  if (discovery && JSON.stringify(discovery) !== lastSiteResult) {
    lastSiteResult = JSON.stringify(discovery); const report = $('sitesReport'); report.replaceChildren();
    report.append(el('p', s.collector.result.message, 'help'));
    for (const site of discovery.sites) report.append(el('div', `${site.company}: ${site.status === 'found' ? site.sources.join(', ') : site.message}`, 'site-report' + (site.status !== 'found' ? ' failed' : '')));
  }
  $('collectBtn').disabled = collecting; $('collectBtn').textContent = collecting ? 'Collecting jobs…' : '↻ Refresh job collection';
  if (lastRun !== null && s.last_run && lastRun !== s.last_run.id) note('Collection started. You can keep browsing while jobs are gathered.');
  lastRun = s.last_run?.id || 0;
  $('collectionNote').textContent = s.last_run ? `Last collection: ${s.last_run.status} · ${date(s.last_run.started_at)}` : 'No collection yet. Collect your first jobs.';
  return s;
}
async function search() {
  const sequence = ++searchSequence;
  $('resultsCount').textContent = 'Finding opportunities…';
  $('jobList').replaceChildren(el('p', 'Comparing roles with your career profile…', 'help'));
  $('prevBtn').disabled = true; $('nextBtn').disabled = true;
  const p = params(); $('exportLink').href = '/api/export.csv?' + p;
  p.set('limit', pageSize); p.set('offset', offset);
  const personal = view === 'matches' || view === 'remote';
  const data = await api((personal ? '/api/matches?' : '/api/jobs?') + p);
  if (sequence !== searchSequence) return;
  total = data.total; $('resultsCount').textContent = `${total.toLocaleString()} ${total === 1 ? 'job' : 'jobs'} found`;
  const list = $('jobList'); list.replaceChildren();
  for (const job of data.jobs) {
    const card = el('article', undefined, 'job-card' + (job.id === selected ? ' selected' : ''));
    const open = el('button', undefined, 'job-open'); open.type = 'button';
    const top = el('div', undefined, 'card-top'); top.append(el('div', job.company.slice(0, 2).toUpperCase(), 'company-avatar'));
    const heading = el('div'); heading.append(el('div', job.role, 'job-title'), el('div', job.company, 'company')); top.append(heading);
    if (job.match) { const badge = el('span', undefined, 'fit-badge'); badge.append(el('b', job.match.score), el('small', '/ 100 fit')); top.append(badge); }
    open.append(top);
    const meta = el('div', undefined, 'job-meta'); meta.append(el('span', '⌖ ' + job.location), el('span', '· ' + job.employment_type)); open.append(meta);
    const tags = el('div', undefined, 'tags'); if (job.workplace_type === 'remote') tags.append(el('span', 'Remote', 'tag remote'));
    const displaySkills = job.match?.matched_skills.length ? job.match.matched_skills.map(name => ({name,inferred:true})) : job.skills;
    for (const skill of displaySkills.slice(0, 4)) { const t = el('span', skill.name, 'tag'); t.title = skill.inferred ? 'Mentioned in job text; may be optional' : 'Provided by source'; tags.append(t); }
    if (job.match) tags.append(el('span', ({compatible:'Location compatible',review:'Check eligibility',mismatch:'Location mismatch'})[job.match.eligibility], 'tag location-' + job.match.eligibility));
    open.append(tags);
    if (job.match) open.append(el('p', job.match.matched_skills.length ? '✦ ' + job.match.matched_skills.slice(0,4).join(' · ') : job.match.label, 'card-reason'));
    open.addEventListener('click', async () => { try { selected = job.id; await showJob(job.id); document.querySelectorAll('.job-card.selected').forEach(c => c.classList.remove('selected')); card.classList.add('selected'); } catch (e) { note(e.message); } });
    card.append(open);
    const foot = el('div', undefined, 'card-foot');
    const seen = job.last_seen_at ? new Date(job.last_seen_at).toLocaleDateString() : 'Unknown';
    foot.append(el('span', `${job.sources.map(s => s.source).join(' · ')} · Seen ${seen}`));
    const save = el('button', job.preference?.status === 'saved' ? '✓ Saved' : '＋ Save job', 'save-card' + (job.preference?.status === 'saved' ? ' saved' : ''));
    save.type = 'button'; save.setAttribute('aria-label', 'Save ' + job.role);
    save.addEventListener('click', async () => { try { await setPreference(job, job.preference?.status === 'saved' ? 'none' : 'saved'); await search(); if (selected === job.id) await showJob(job.id); } catch(e) { note(e.message); } });
    foot.append(save); card.append(foot); list.append(card);
  }
  if (!data.jobs.length) list.append(el('div', data.needs_profile ? 'Add your résumé and target roles in Career profile to find your matches.' : view === 'saved' ? 'A good opportunity is worth keeping. Save a job to start your shortlist.' : 'No matching opportunities here yet. Clear a filter, review your location preferences or refresh the collection.', 'no-results'));
  $('prevBtn').disabled = offset === 0; $('nextBtn').disabled = offset + pageSize >= total;
  $('pageLabel').textContent = total ? `${offset + 1}–${Math.min(offset + pageSize, total)} of ${total.toLocaleString()}` : '0 results';
}
async function showJob(id) {
  const job = await api('/api/jobs/' + id), panel = $('detail'); panel.replaceChildren();
  panel.dataset.jobId = id;
  const head = el('div', undefined, 'detail-head');
  const close = el('button', 'Close details ×', 'quiet detail-close'); close.type = 'button'; close.addEventListener('click', resetDetail);
  head.append(close, el('p', 'JOB DETAILS · ' + (job.active ? 'ACTIVE' : 'INACTIVE'), 'eyebrow'), el('h2', job.role), el('p', job.company, 'company'));
  const body = el('div', undefined, 'detail-body'), facts = el('div', undefined, 'facts');
  if (job.match) {
    const fit = el('section', undefined, 'fit-panel'); fit.append(el('h3', `${job.match.score}/100 · ${job.match.label}`));
    for (const [heading, items, cls] of [['Why it may fit',job.match.reasons,''],['Gaps to review',job.match.gaps,''],['Before you apply',job.match.warnings,'warning-list']]) {
      if (items.length) { fit.append(el('b', heading)); const ul = el('ul', undefined, cls); for (const item of items) ul.append(el('li',item)); fit.append(ul); }
    }
    fit.append(el('p',job.match.method + '. Salary and visa eligibility are not inferred.', 'fit-method')); body.append(fit);
  }
  const actions = el('div', undefined, 'detail-actions');
  const save = el('button', job.preference.status === 'saved' ? '✓ Saved · remove' : '＋ Save job', 'quiet');
  save.addEventListener('click', async () => { try { await setPreference(job, job.preference.status === 'saved' ? 'none' : 'saved'); await showJob(id); await search(); } catch(e) { note(e.message); } });
  const hide = el('button', job.preference.status === 'hidden' ? 'Restore to my matches' : 'Hide from my matches', 'quiet');
  hide.addEventListener('click', async () => { try { const restore = job.preference.status === 'hidden'; await setPreference(job,restore ? 'none' : 'hidden'); panel.classList.remove('is-open'); note(restore ? 'Restored to your matches.' : 'Hidden from your matches. You can find it again in All jobs.'); await search(); } catch(e) { note(e.message); } });
  const queue = el('button','Review with Career-Ops ↗','primary');
  queue.addEventListener('click', async () => { queue.disabled = true; try { const r = await post('/api/career-ops/queue',{job_id:id}); queue.textContent = r.status === 'existing' ? '✓ Already in review inbox' : '✓ Added to review inbox'; note(r.message + '. The job is queued for deeper evaluation; no application was sent.'); } catch(e) { queue.disabled=false; note(e.message); } });
  actions.append(save,queue,hide); body.append(actions);
  const notesLabel = el('label','Your notes','job-notes'), notes = el('textarea'); notes.rows=2; notes.maxLength=4000; notes.value=job.preference.notes; notesLabel.append(notes);
  const saveNotes = el('button','Save notes','quiet'), notesStatus = el('span','','notes-status'); notesStatus.setAttribute('role','status');
  saveNotes.addEventListener('click',async()=>{ try { await post('/api/preferences',{job_id:id,status:job.preference.status,notes:notes.value}); job.preference.notes=notes.value; notesStatus.textContent=' Notes saved'; } catch(e) { notesStatus.textContent=e.message; } });
  body.append(notesLabel,saveNotes,notesStatus);
  const inferred = value => value ? ' · inferred' : '';
  const years = job.experience_min_years === null ? 'Unknown' : job.experience_max_years === null ? `${job.experience_min_years}+ years` : `${job.experience_min_years}–${job.experience_max_years} years`;
  for (const [label, value] of [['Location', job.location], ['Workplace', job.workplace_type], ['Domain' + inferred(job.domain_inferred), job.domain], ['Experience' + inferred(job.experience_inferred), job.experience_level], ['Years mentioned · inferred', years], ['Employment', job.employment_type], ['Salary', job.salary || 'Not provided'], ['Country', job.country || 'Not provided']]) { const f = el('div', undefined, 'fact'); f.append(el('span', label), el('b', value)); facts.append(f); }
  body.append(facts, el('h3', 'Original listings'));
  for (const source of job.sources) { const link = el('a', `Open on ${source.source} ↗` + (!source.active ? ' (inactive)' : ''), 'source-link'); const url = new URL(source.url); if (['https:', 'http:'].includes(url.protocol)) link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; body.append(link); }
  if (job.skills.length) { body.append(el('h3', 'Skills')); const tags = el('div', undefined, 'tags'); for (const skill of job.skills) tags.append(el('span', skill.name + (skill.inferred ? ' · inferred' : ''), 'tag')); body.append(tags); }
  if (job.experience_evidence) body.append(el('p', job.experience_evidence + '. Verify the requirement in the description.', 'detail-note'));
  body.append(el('h3', 'Job description'), el('div', job.description || 'No description supplied by this source.', 'description'));
  body.append(el('p', `Published: ${date(job.published_at)}\nFirst collected: ${date(job.first_seen_at)}\nLast seen: ${date(job.last_seen_at)}\nSaved versions: ${job.version_count}`, 'detail-note'));
  panel.append(head, body); panel.classList.add('is-open');
}
async function history() {
  const { runs } = await api('/api/runs'); const list = $('historyList'); list.replaceChildren();
  if (!runs.length) list.append(el('p', 'No collections yet.'));
  for (const run of runs) { const node = el('div', undefined, 'run'), title = el('div', undefined, 'run-title'); title.append(el('b', `${date(run.started_at)} · ${run.trigger}`), el('span', run.status, 'status ' + run.status)); node.append(title);
    for (const source of run.sources) { node.append(el('p', `${source.source}: ${source.status} · ${source.fetched} fetched · ${source.inserted} new · ${source.updated} updated · ${source.unchanged} unchanged · ${source.closed} inactive · ${source.filtered} filtered · ${source.invalid} invalid`)); if (source.error) node.append(el('p', source.error, 'error')); } list.append(node); }
  $('historyDialog').showModal();
}
$('filtersForm').addEventListener('submit', e => { e.preventDefault(); offset = 0; search().catch(e => note(e.message)); });
$('resetBtn').addEventListener('click', () => { $('filtersForm').reset(); offset = 0; search().catch(e => note(e.message)); });
$('prevBtn').addEventListener('click', () => { offset = Math.max(0, offset - pageSize); search().catch(e => note(e.message)); });
$('nextBtn').addEventListener('click', () => { offset += pageSize; search().catch(e => note(e.message)); });
$('historyBtn').addEventListener('click', () => history().catch(e => note(e.message)));
$('closeHistory').addEventListener('click', () => $('historyDialog').close());
$('sitesBtn').addEventListener('click', () => { $('sitesDialog').showModal(); stats().catch(e => note(e.message)); });
$('closeSites').addEventListener('click', () => $('sitesDialog').close());
async function addSites(body) {
  try {
    $('addSitesBtn').disabled = true; $('starterSitesBtn').disabled = true;
    await api('/api/company-sites', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-JobHarvest-Token': csrf}, body: JSON.stringify(body)});
    collecting = true; note('Finding company boards and collecting jobs. You can keep browsing.'); await stats();
  } catch (e) { $('sitesProgress').textContent = e.message; $('addSitesBtn').disabled = false; $('starterSitesBtn').disabled = false; }
}
$('sitesForm').addEventListener('submit', e => { e.preventDefault(); addSites({sites: $('sitesInput').value}); });
$('starterSitesBtn').addEventListener('click', () => addSites({starter: true}));
$('collectBtn').addEventListener('click', async () => { try { $('collectBtn').disabled = true; await api('/api/collect', { method: 'POST', headers: { 'X-JobHarvest-Token': csrf } }); collecting = true; note('Collection started. Sources within their polling interval will be skipped.'); await stats(); } catch (e) { $('collectBtn').disabled = false; note(e.message); } });
async function refresh() { try { const was = collecting; const s = await stats(); if (was && !collecting) { offset = 0; await search(); note(s.collector.result?.message || `Collection finished: ${s.last_run?.status || 'complete'}. Open collection history for source details.`); } } catch (e) { note('Cannot reach JobHarvest. Start the dashboard and refresh this page.'); } }
(async () => { try { await stats(); await search(); } catch (e) { note(e.message); } })();
setInterval(refresh, 5000);
document.addEventListener('keydown', e => { if (e.key === 'Escape') resetDetail(); });

async function post(url, body) {
  return api(url, {method:'POST', headers:{'Content-Type':'application/json','X-JobHarvest-Token':csrf}, body:JSON.stringify(body)});
}
async function setPreference(job, status) {
  await post('/api/preferences',{job_id:job.id,status,notes:job.preference?.notes || ''});
}
async function loadProfile() {
  const data = await api('/api/profile'); careerProfile = data.profile;
  $('profileHeadline').textContent = careerProfile.roles.join(' · ') || 'Tell us where you want to go';
  $('profileSummary').textContent = [careerProfile.city,careerProfile.country].filter(Boolean).join(', ') + '  /  ' + careerProfile.skills.slice(0,5).join(' · ') + (careerProfile.years_experience !== null ? '  /  ' + careerProfile.years_experience + '+ years' : '');
  $('greeting').textContent = careerProfile.name ? 'YOUR NEXT CHAPTER, ' + careerProfile.name.split(' ')[0].toUpperCase() : "A LITTLE CLOSER TO WHAT'S NEXT";
  $('repoLabel').textContent = data.connection.connected ? 'VigneshsparrowEsh / career-ops' : 'Repository not connected';
  document.querySelector('.connection>div').firstChild.textContent = data.connection.connected ? 'Career-Ops connected' : 'Career-Ops local files';
  return careerProfile;
}
async function openProfile() {
  try {
    const profile = await loadProfile(), form = $('profileForm');
    for (const [key,value] of Object.entries(profile)) {
      const input=form.elements.namedItem(key); if(!input) continue;
      if(input.type==='checkbox') input.checked=Boolean(value);
      else input.value=Array.isArray(value) ? value.join(', ') : value ?? '';
    }
    $('profileError').textContent=''; $('profileDialog').showModal();
  } catch(e) { note(e.message); }
}
for(const id of ['profileBtn','editProfileBtn','profileStripBtn']) $(id).addEventListener('click',openProfile);
$('closeProfile').addEventListener('click',()=>$('profileDialog').close());
$('profileForm').addEventListener('submit',async event=>{
  event.preventDefault(); const form=$('profileForm'), data={};
  for(const [key,value] of new FormData(form)) data[key]=value;
  for(const key of ['roles','skills','authorized_countries','preferred_locations']) data[key]=(data[key] || '').split(',').map(s=>s.trim()).filter(Boolean);
  for(const key of ['remote_only','open_to_relocation']) data[key]=form.elements.namedItem(key).checked;
  data.years_experience=data.years_experience==='' ? null : Number(data.years_experience);
  $('saveProfileBtn').disabled=true; $('profileError').textContent='';
  try { await post('/api/profile',data); await loadProfile(); $('profileDialog').close(); offset=0; await search(); note('Profile saved. Your matches now use your updated career preferences.'); }
  catch(e) { $('profileError').textContent=e.message; }
  finally { $('saveProfileBtn').disabled=false; }
});
for(const button of document.querySelectorAll('[data-view]')) button.addEventListener('click',()=>{
  view=button.dataset.view; offset=0; resetDetail();
  for (const name of ['min_score','eligible_only','include_restricted']) $('filtersForm').elements.namedItem(name).disabled = view === 'saved' || view === 'all';
  for(const other of document.querySelectorAll('[data-view]')) { other.classList.toggle('active',other===button); other.setAttribute('aria-current',other===button ? 'page' : 'false'); }
  const [label,title,subtitle]=viewCopy[view];
  $('viewCrumb').textContent=label; $('viewTitle').textContent=title; $('viewSubtitle').textContent=subtitle;
  $('resultsEyebrow').textContent= view==='saved' ? 'YOUR SHORTLIST' : view==='all' ? 'THE FULL COLLECTION' : 'SORTED BY PRELIMINARY FIT';
  $('matchNote').textContent= view==='all' || view==='saved' ? 'Open any job for a preliminary fit breakdown. Collection dates are observations; confirm current availability on the source.' : 'Fit compares titles, skills, experience and location. Location mismatches are hidden by default. Confirm eligibility and current availability before applying.';
  search().catch(e=>note(e.message));
});
loadProfile().catch(e=>note(e.message));
