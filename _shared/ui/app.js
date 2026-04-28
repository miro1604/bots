// Matrix orchestrator UI app

// === Matrix rain ===
(function rain() {
  const canvas = document.getElementById('rain');
  const ctx = canvas.getContext('2d');
  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);
  const chars = 'アァカサタナハマヤャラワABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.split('');
  const fontSize = 14;
  let columns = Math.floor(canvas.width / fontSize);
  let drops = Array(columns).fill(0);
  function draw() {
    ctx.fillStyle = 'rgba(0,0,0,0.05)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#00ff41';
    ctx.font = fontSize + 'px monospace';
    columns = Math.floor(canvas.width / fontSize);
    while (drops.length < columns) drops.push(0);
    for (let i = 0; i < columns; i++) {
      const text = chars[Math.floor(Math.random() * chars.length)];
      ctx.fillText(text, i * fontSize, drops[i] * fontSize);
      if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) drops[i] = 0;
      drops[i]++;
    }
  }
  setInterval(draw, 50);
})();

// === API ===
async function api(path, method='GET', body=null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}

// === Render ===
function fmtTime(iso) {
  if (!iso) return '?';
  try {
    return new Date(iso).toISOString().replace('T', ' ').slice(0, 19);
  } catch { return iso; }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Escapoi HTML JA muuta URL:t klikattaviksi linkeiksi.
// Käytä kaikkialla missä renderöidään vapaata käyttäjän/orchestratorin tekstiä
// (älä käytä attribuuttiarvoissa kuten title=, placeholder=).
function linkify(str) {
  if (!str) return '';
  // 1) escapoi HTML ensin (XSS-suoja)
  let safe = escapeHtml(str);
  // 2) http(s)://, www., file:// URL:t — sallitut merkit pois lopusta (.,;:!?)
  const urlRe = /\b((?:https?:\/\/|www\.|file:\/\/\/?)[^\s<>"']+[^\s<>"'.,;:!?])/gi;
  safe = safe.replace(urlRe, (m) => {
    const href = m.startsWith('www.') ? `https://${m}` : m;
    return `<a href="${href}" target="_blank" rel="noopener noreferrer" class="auto-link">${m}</a>`;
  });
  // 3) Polut C:\... tai /usr/... → ei aktiivisia linkkejä mutta merkitse code-tyylillä
  return safe;
}

// FOCUS-MODE 2026-04-28: vain finance / crypto-finance / betting
const FOCUS_BOTS = new Set(['finance', 'crypto-finance', 'betting']);

// AUTO-CLEANUP 2026-04-28: dismissed-id:t säilyvät localStorage:ssa
function _getDismissed(key) {
  try { return new Set(JSON.parse(localStorage.getItem(key) || '[]')); }
  catch { return new Set(); }
}
function _addDismissed(key, id) {
  const s = _getDismissed(key);
  s.add(id);
  localStorage.setItem(key, JSON.stringify([...s]));
}
const DISMISSED_THREADS = 'orch_dismissed_threads_v1';
const DISMISSED_ACTIONS = 'orch_dismissed_actions_v1';

async function renderBots() {
  const allBots = await api('/api/bots');
  const bots = allBots.filter(b => FOCUS_BOTS.has(b.slug));
  const grid = document.getElementById('bots-grid');
  grid.innerHTML = bots.map(b => {
    let statusClass = 'status-offline';
    if (b.online) statusClass = 'status-online';
    else if (b.last_heartbeat) statusClass = 'status-stale';
    const age = b.heartbeat_age_min !== null && b.heartbeat_age_min !== undefined
      ? `${b.heartbeat_age_min}min ago` : 'no hb';
    const at = b.all_time || {};
    const allTimeBits = [];
    if (at.syntheses) allTimeBits.push(`syntheses: <span class="green-bright">${at.syntheses}</span>`);
    if (at.ideas) allTimeBits.push(`ideas: <span class="green-bright">${at.ideas}</span>`);
    if (at.evolution_proposals) allTimeBits.push(`evo: <span class="green-bright">${at.evolution_proposals}</span>`);
    if (at.research_runs) allTimeBits.push(`research: <span class="green-bright">${at.research_runs}</span>`);
    const allTimeStr = allTimeBits.length ? allTimeBits.join(' • ') : '<span class="muted">no all-time data</span>';

    const personasStr = b.personas_active != null
      ? `${b.personas_active}/${b.personas} active` : `${b.personas}`;

    const goalsBadge = b.has_goals
      ? '<span class="goals-badge done">GOALS</span>'
      : '<span class="goals-badge muted">no goals</span>';

    return `
      <div class="bot-card">
        <div class="name ${statusClass}">${escapeHtml(b.slug)} ${goalsBadge}</div>
        <div class="meta">
          <span>personas: <span class="green-bright">${personasStr}</span></span>
          <span>cycle: <span class="green-bright">${b.cycle ?? '-'}</span></span>
          <span>state: <span class="green-bright">${escapeHtml(b.state ?? '-')}</span></span>
          <span>${age}</span>
        </div>
        ${b.current_topic ? `
          <div class="meta current-topic">
            🔬 <span class="muted">tutkii nyt:</span> <span class="amber">${linkify(b.current_topic)}</span>
          </div>
        ` : ''}
        <div class="meta all-time">
          📊 <span class="muted">all-time:</span> ${allTimeStr}
        </div>
      </div>`;
  }).join('');
}

async function renderPersonas() {
  const allPersonas = await api('/api/personas');
  const personas = allPersonas.filter(p => FOCUS_BOTS.has(p.bot));
  const grid = document.getElementById('personas-grid');
  const byBot = {};
  for (const p of personas) {
    if (!byBot[p.bot]) byBot[p.bot] = [];
    byBot[p.bot].push(p);
  }
  grid.innerHTML = Object.entries(byBot).map(([bot, list]) => {
    const activeCount = list.filter(p => p.active !== false).length;
    return `
      <div class="persona-card">
        <div class="name">◢ ${escapeHtml(bot)} <span class="muted">(${activeCount}/${list.length} active)</span></div>
        <div class="meta" style="flex-direction: column; gap: 0.3rem;">
          ${list.map(p => {
            const isActive = p.active !== false;
            const cls = isActive ? 'green-bright' : 'muted';
            const btnLabel = isActive ? 'DISABLE' : 'ENABLE';
            return `
              <div class="persona-row" data-bot="${escapeHtml(bot)}" data-slug="${escapeHtml(p.slug)}">
                <span class="${cls}">${isActive ? '●' : '○'} ${escapeHtml(p.slug)}</span>
                <button class="persona-toggle" data-bot="${escapeHtml(bot)}" data-slug="${escapeHtml(p.slug)}" data-active="${isActive}">${btnLabel}</button>
                <span class="muted persona-oneliner"> — ${linkify(p.persona_one_liner || '')}</span>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }).join('');

  // Bind toggle
  grid.querySelectorAll('.persona-toggle').forEach(btn => {
    btn.addEventListener('click', async () => {
      const bot = btn.dataset.bot;
      const slug = btn.dataset.slug;
      const isActive = btn.dataset.active === 'true';
      btn.disabled = true;
      const r = await api('/api/personas/toggle', 'POST', { bot, slug, active: !isActive });
      if (r.ok) await renderPersonas();
      btn.disabled = false;
    });
  });
}

async function renderSyntheses_DEPRECATED() {  // Recent Debates poistettu UI:sta 2026-04-26
  const list = await api('/api/syntheses');
  const el = document.getElementById('syntheses-list');
  if (!list.length) { el.innerHTML = '<span class="muted">No syntheses yet.</span>'; return; }
  el.innerHTML = list.map(s => `
    <div class="synthesis-item">
      <div class="header">
        <span><strong>${escapeHtml(s.bot)}</strong> // ${escapeHtml(s.mode || '?')} // ${escapeHtml(s.debate_id || '?')}</span>
        <span class="muted">${fmtTime(s.ts)}</span>
      </div>
      <div class="muted" style="font-size:0.75rem; margin-bottom:0.3rem;">scenario: ${linkify(s.scenario_preview || '')}</div>
      <div class="body">${linkify(s.synthesis || '(empty)')}</div>
    </div>
  `).join('');
}

async function renderDecisions() {
  // Pending: orchestrator-eskaloimat päätökset jotka odottavat sun panoksesi
  const pending = await api('/api/decisions_pending');
  const log = await api('/api/decisions');
  const el = document.getElementById('decisions-list');
  const badge = document.getElementById('decisions-badge');
  if (badge) {
    badge.textContent = pending.length;
    badge.classList.toggle('has-items', pending.length > 0);
  }

  let html = '';
  if (pending.length) {
    html += `<div class="muted" style="margin-bottom:0.5rem;font-size:0.85rem;">⚠ ${pending.length} ORCHESTRATORIN ESKALOIMAA PÄÄTÖSTÄ — vastaa MESSAGE-kentän kautta tai kommentoi suoraan kortissa:</div>`;
    pending.forEach(p => {
      const opts = (p.options || []).map(o => `<code>${escapeHtml(o)}</code>`).join(' / ');
      html += `
        <div class="pending-decision priority-${escapeHtml(p.priority)}">
          <div class="meta">${fmtTime(p.ts)} // <strong>${escapeHtml(p.bot)}</strong> // ${escapeHtml(p.priority || 'medium')}</div>
          <div class="question">${linkify(p.question)}</div>
          ${opts ? `<div class="options muted" style="font-size:0.75rem;">vaihtoehdot: ${opts}</div>` : ''}
          <div class="muted" style="font-size:0.75rem;">${linkify(p.reason || '')}</div>
          <div style="margin-top:0.5rem;">
            <input data-decide-id="${escapeHtml(p.id)}" placeholder="Vastauksesi..." />
            <button data-decide-submit="${escapeHtml(p.id)}">DECIDE</button>
          </div>
        </div>
      `;
    });
  } else {
    html += '<div class="muted" style="font-size:0.85rem; margin-bottom:1rem;">✓ Ei pending-päätöksiä — orchestrator hoitaa kaiken.</div>';
  }

  if (log.length) {
    html += `<div class="muted" style="margin-top:1rem;font-size:0.85rem;">📜 Aiemmat päätökset:</div>`;
    log.slice().reverse().slice(0, 10).forEach(d => {
      html += `
        <div class="decision-item type-${escapeHtml(d.type || 'comment')}">
          <div class="meta">${fmtTime(d.ts)} // ${escapeHtml(d.type || '?')} // ${escapeHtml(d.target || '-')}</div>
          <div>${linkify(d.text || '')}</div>
        </div>
      `;
    });
  }
  el.innerHTML = html;

  // Bind decide-buttons
  el.querySelectorAll('button[data-decide-submit]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const qid = btn.dataset.decideSubmit;
      const inp = el.querySelector(`input[data-decide-id="${qid}"]`);
      const text = inp ? inp.value.trim() : '';
      if (!text) { alert('Tyhjä päätös'); return; }
      btn.textContent = 'DECIDING...';
      btn.disabled = true;
      const r = await api('/api/decisions_pending/decide', 'POST', { question_id: qid, decision: text });
      if (r.ok) {
        btn.textContent = 'DONE';
        setTimeout(() => renderDecisions(), 1500);
      } else {
        btn.textContent = 'DECIDE';
        btn.disabled = false;
        alert('Tallennus epäonnistui');
      }
    });
  });
}

async function renderDora() {
  const data = await api('/api/dora');
  const t = document.getElementById('dora-table');
  const raw = document.getElementById('dora-raw');
  raw.textContent = data.latest_report || '(no report yet)';
  if (!data.per_bot || !Object.keys(data.per_bot).length) {
    t.innerHTML = '<span class="muted">No DORA data yet. Run: python _shared/scripts/dora_collector.py</span>';
    return;
  }
  const headers = Object.keys(Object.values(data.per_bot)[0]);
  t.innerHTML = `
    <table>
      <thead><tr><th>bot</th>${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr></thead>
      <tbody>
        ${Object.entries(data.per_bot).map(([bot, row]) => `
          <tr>
            <td><strong>${escapeHtml(bot)}</strong></td>
            ${headers.map(h => `<td>${escapeHtml(row[h] || '')}</td>`).join('')}
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function renderGoals(force = false) {
  const data = await api('/api/goals');
  const list = document.getElementById('goals-list');

  // KORJAUS: jos goals-listalla on jo textarea:t ja ne sisältävät käyttäjän
  // kirjoittamia muutoksia (jotka eivät ole tallennettu), älä korvaa.
  // Force=true ohittaa tämän (kutsutaan SAVE-jälkeen).
  if (!force && list.children.length > 0) {
    // Tarkista onko textarea:n arvo eroa palvelimelta tulleesta → käyttäjä on muokannut
    const taList = list.querySelectorAll('textarea[data-bot]');
    let userHasEdits = false;
    taList.forEach(ta => {
      const bot = ta.dataset.bot;
      const serverText = (data[bot] && data[bot].raw_text) || '';
      if (ta.value !== serverText) userHasEdits = true;
    });
    if (userHasEdits) {
      // Käyttäjä on kesken kirjoittamisen, älä häiritse. Mutta päivitä smart-badge:t.
      taList.forEach(ta => {
        const bot = ta.dataset.bot;
        const info = data[bot] || {};
        const card = ta.closest('.goals-card');
        if (card) {
          const badge = card.querySelector('.smart-badge');
          if (badge) {
            badge.textContent = info.smart_exists ? 'SMART tallennettu' : 'odottaa SMART-muotoilua';
            badge.classList.toggle('done', !!info.smart_exists);
          }
        }
      });
      return;
    }
  }

  const html = Object.entries(data).map(([bot, info]) => {
    const smartBadge = info.smart_exists
      ? '<span class="smart-badge done">SMART tallennettu</span>'
      : '<span class="smart-badge">odottaa SMART-muotoilua</span>';
    return `
      <div class="goals-card">
        <div class="name">
          <span>${escapeHtml(bot)}</span>
          ${smartBadge}
        </div>
        <textarea data-bot="${escapeHtml(bot)}" placeholder="Mihin tähtää, mittarit, ÄLÄ-tavoitteet... vapaata tekstiä, mä muotoilen.">${escapeHtml(info.raw_text || '')}</textarea>
        <div class="row">
          <button data-save-bot="${escapeHtml(bot)}">SAVE</button>
          ${info.smart_exists ? `<button class="show-smart-btn" data-show-smart="${escapeHtml(bot)}">→ Näytä SMART-versio</button>` : ''}
        </div>
      </div>
    `;
  }).join('');
  list.innerHTML = html;

  // Bind "Näytä SMART"
  list.querySelectorAll('button[data-show-smart]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const bot = btn.dataset.showSmart;
      const r = await fetch(`/api/goals/smart?bot=${encodeURIComponent(bot)}`, { credentials: 'include' });
      const data = await r.json();
      const card = btn.closest('.goals-card');
      let preview = card.querySelector('.smart-preview');
      if (preview) { preview.remove(); return; }
      preview = document.createElement('pre');
      preview.className = 'smart-preview';
      preview.textContent = data.content || '(GOALS.md ei löydy)';
      card.appendChild(preview);
    });
  });

  // Bind save
  list.querySelectorAll('button[data-save-bot]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const bot = btn.dataset.saveBot;
      const ta = list.querySelector(`textarea[data-bot="${bot}"]`);
      const text = ta ? ta.value.trim() : '';
      if (!text) { alert('Tyhjä tavoiteteksti'); return; }
      const r = await api('/api/goals/save', 'POST', { bot, text });
      if (r.ok) {
        btn.textContent = 'SAVED → orchestrator muotoilee';
        btn.style.background = 'var(--green-bright)';
        btn.style.color = 'var(--bg)';
        setTimeout(() => renderGoals(true), 1200);
      } else {
        alert('Tallennus epäonnistui');
      }
    });
  });
}

async function renderGeneralInputs() {
  const allThreads = await api('/api/general_inputs');
  const dismissed = _getDismissed(DISMISSED_THREADS);
  const threads = allThreads.filter(t => !dismissed.has(t.thread_id));
  const el = document.getElementById('general-inputs-list');
  if (!threads.length) {
    el.innerHTML = '<span class="muted">Ei keskusteluja vielä.</span>';
    return;
  }

  // Säilytä käyttäjän kesken-kirjoitetut reply-tekstit
  const userTyping = {};
  el.querySelectorAll('textarea[data-thread-id]').forEach(ta => {
    if (ta.value.trim()) userTyping[ta.dataset.threadId] = ta.value;
  });

  el.innerHTML = threads.map(t => {
    const messagesHtml = t.messages.map(m => {
      const isOrch = m.role === 'orchestrator';
      const cls = isOrch ? 'msg-orch' : 'msg-user';
      const label = isOrch ? '📡 ORCH' : '👤 SINÄ';
      return `
        <div class="msg ${cls}">
          <div class="msg-meta">${label} // ${fmtTime(m.ts)}</div>
          <div class="msg-text">${linkify(m.text)}</div>
        </div>
      `;
    }).join('');
    const needReply = t.needs_user_reply ? ' <span class="amber">[ORCH-VASTAUS — KOMMENTOI]</span>' : '';
    const savedTyping = userTyping[t.thread_id] || '';
    return `
      <div class="thread">
        <div class="thread-header">
          <strong>Thread ${escapeHtml(t.thread_id)}</strong> — ${escapeHtml(t.target_bot || 'all')}
          ${t.subject ? '// ' + escapeHtml(t.subject) : ''}
          ${needReply}
          <span class="muted" style="margin-left:auto;">${t.n_messages} viestiä</span>
          <button class="dismiss-btn" data-dismiss-thread="${escapeHtml(t.thread_id)}" title="Piilota tämä thread (OK, ei enää tarvita)">✕ OK</button>
        </div>
        <div class="thread-messages">${messagesHtml}</div>
        <div class="thread-reply">
          <textarea data-thread-id="${escapeHtml(t.thread_id)}" data-target-bot="${escapeHtml(t.target_bot || '')}" placeholder="Vastaa thread:iin (jatka kunnes tyytyväinen)..." rows="2">${escapeHtml(savedTyping)}</textarea>
          <button data-thread-reply="${escapeHtml(t.thread_id)}">REPLY</button>
        </div>
      </div>
    `;
  }).join('');

  // DISMISS-napit threadeille
  el.querySelectorAll('button[data-dismiss-thread]').forEach(btn => {
    btn.addEventListener('click', () => {
      _addDismissed(DISMISSED_THREADS, btn.dataset.dismissThread);
      renderGeneralInputs();
    });
  });

  el.querySelectorAll('button[data-thread-reply]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const tid = btn.dataset.threadReply;
      const ta = el.querySelector(`textarea[data-thread-id="${tid}"]`);
      const text = ta ? ta.value.trim() : '';
      if (!text) { alert('Tyhjä viesti'); return; }
      const targetBot = ta.dataset.targetBot || 'orchestrator-system';
      btn.textContent = 'SENDING...';
      btn.disabled = true;
      const r = await api('/api/general_inputs', 'POST', {
        thread_id: tid, target_bot: targetBot, subject: '', text, type: 'comment',
      });
      if (r.ok) {
        if (ta) ta.value = '';
        btn.textContent = 'SENT — orch vastaa 30-90s';
        setTimeout(() => renderGeneralInputs(), 30000);
        setTimeout(() => renderGeneralInputs(), 60000);
        setTimeout(() => renderGeneralInputs(), 90000);
        setTimeout(() => { btn.disabled = false; btn.textContent = 'REPLY'; }, 95000);
      } else {
        btn.textContent = 'REPLY';
        btn.disabled = false;
      }
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const sendBtn = document.getElementById('msg-send');
  if (sendBtn) {
    sendBtn.addEventListener('click', async () => {
      const target = document.getElementById('msg-target').value;
      const subject = document.getElementById('msg-subject').value;
      const text = document.getElementById('msg-text').value.trim();
      if (!text) { alert('Tyhjä viesti'); return; }
      const r = await api('/api/general_inputs', 'POST',
        { target_bot: target, subject, text, type: 'comment' });
      if (r.ok) {
        document.getElementById('msg-text').value = '';
        document.getElementById('msg-subject').value = '';
        await renderGeneralInputs();
      }
    });
  }
});

async function renderUserActions() {
  const allItems = await api('/api/user_actions');
  const dismissed = _getDismissed(DISMISSED_ACTIONS);
  const items = allItems.filter(it => !dismissed.has(it.id));
  const sec = document.getElementById('user-actions');
  const list = document.getElementById('user-actions-list');
  const badge1 = document.getElementById('user-actions-count');
  const badge2 = document.getElementById('action-badge');

  badge1.textContent = items.length;
  badge2.textContent = items.length;
  badge2.classList.toggle('has-items', items.length > 0);

  // Säilytä käyttäjän kirjoittamat kommentit ennen re-renderiä
  const commentTyping = {};
  list.querySelectorAll('textarea[data-comment-action]').forEach(ta => {
    if (ta.value.trim()) commentTyping[ta.dataset.commentAction] = ta.value;
  });

  // Säilytä myös step-kohtaiset kommenttikentät
  const stepCommentTyping = {};
  list.querySelectorAll('textarea[data-step-comment-action]').forEach(ta => {
    if (ta.value.trim()) {
      const k = `${ta.dataset.stepCommentAction}::${ta.dataset.stepCommentStep}`;
      stepCommentTyping[k] = ta.value;
    }
  });
  // Säilytä auki olevat step-kommenttipaneelit
  const openStepPanels = new Set();
  list.querySelectorAll('.step-comment-panel:not(.hidden)').forEach(p => {
    openStepPanels.add(`${p.dataset.actionId}::${p.dataset.stepId}`);
  });

  if (!items.length) {
    sec.classList.add('hidden');
    return;
  }
  sec.classList.remove('hidden');

  // Ryhmitä bot:n mukaan, järjestys säilytetään (priority-sort backendissä)
  const byBot = new Map();
  for (const it of items) {
    const k = it.bot || 'unknown';
    if (!byBot.has(k)) byBot.set(k, []);
    byBot.get(k).push(it);
  }

  const renderStep = (ua, s) => {
    const help = s.help ? `<div class="step-help">💡 ${linkify(s.help)}</div>` : '';
    const skipped = s.skipped_by_orchestrator ? ' <span style="color:var(--amber)">(skipattu orch:lla)</span>' : '';
    const stepCommentKey = `${ua.id}::${s.step_id}`;
    const savedStepText = stepCommentTyping[stepCommentKey] || '';
    const panelOpen = openStepPanels.has(stepCommentKey) || savedStepText;
    const stepReplies = (s.comments || []).map(c => `
      <div class="step-comment-prev"><span class="muted">[${escapeHtml((c.ts||'').slice(0,19))}]</span> ${linkify(c.text || '')}</div>
    `).join('');
    return `
      <div class="step-row ${s.done ? 'done' : ''}" data-action-id="${escapeHtml(ua.id)}" data-step-id="${escapeHtml(s.step_id)}">
        <label class="step">
          <input type="checkbox" ${s.done ? 'checked disabled' : ''} />
          <div class="step-body">
            <span class="step-text">${linkify(s.text)}${skipped}</span>
            ${help}
            ${stepReplies}
          </div>
          <button class="step-comment-toggle" data-toggle-action="${escapeHtml(ua.id)}" data-toggle-step="${escapeHtml(s.step_id)}" title="Kommentoi tätä yksittäistä stepiä">💬</button>
        </label>
        <div class="step-comment-panel ${panelOpen ? '' : 'hidden'}" data-action-id="${escapeHtml(ua.id)}" data-step-id="${escapeHtml(s.step_id)}">
          <textarea data-step-comment-action="${escapeHtml(ua.id)}" data-step-comment-step="${escapeHtml(s.step_id)}" placeholder="Kommentoi vain tätä stepiä... (orchestrator tietää mihin viittaat)" rows="2">${escapeHtml(savedStepText)}</textarea>
          <button data-step-comment-submit="${escapeHtml(ua.id)}::${escapeHtml(s.step_id)}">SEND</button>
        </div>
      </div>
    `;
  };

  const renderUACard = (ua) => {
    const stepsHtml = ua.steps.map(s => renderStep(ua, s)).join('');
    const doneCount = ua.steps.filter(s => s.done).length;
    const allDone = doneCount === ua.steps.length && ua.steps.length > 0;
    const orchNote = ua.orchestrator_note ? `<div class="orch-note">📡 ORCHESTRATOR: ${linkify(ua.orchestrator_note)}</div>` : '';
    const orchQs = (ua.orchestrator_questions || []).map(q => `<div class="orch-question">❓ ${linkify(q.ask || '')}</div>`).join('');
    const reissueBadge = ua.previous_attempt_failed
      ? `<span class="ua-badge reissue" title="Aiempi yritys ei ratkaissut — orchestrator antoi paremmat ohjeet">⟳ UUSIKSI</span>` : '';

    return `
      <div class="user-action-card${ua.previous_attempt_failed ? ' reissue' : ''}" data-ua-id="${escapeHtml(ua.id)}">
        <button class="dismiss-btn dismiss-action" data-dismiss-action="${escapeHtml(ua.id)}" title="Piilota (OK, ei enää tarvita)">✕ OK</button>
        <div class="title">${reissueBadge}${linkify(ua.action_title || ua.question || '?')}</div>
        <div class="meta">
          urgency: ${escapeHtml(ua.urgency)} // ~${ua.estimated_time_min} min
          // related: ${escapeHtml(ua.related_question_id || '-')}
        </div>
        <div class="ua-question">Q: ${linkify(ua.question || '?')}</div>
        ${orchNote}
        ${orchQs}
        <div class="ua-steps">${stepsHtml}</div>
        <div class="progress">
          <span>${doneCount} / ${ua.steps.length} tehty</span>
          ${allDone ? `<button class="confirm-btn" data-confirm-action="${escapeHtml(ua.id)}">CONFIRM ALL DONE → AGENTILLE</button>` : '<span class="muted">raksita kohdat tehdyiksi</span>'}
        </div>
        <div class="comment-input">
          <textarea data-comment-action="${escapeHtml(ua.id)}" placeholder="Yleinen huomio koko tehtävälle (jos haluat kommentoida vain yhtä stepiä → käytä 💬 kunkin stepin vieressä)" rows="2"></textarea>
          <button data-comment-submit="${escapeHtml(ua.id)}">SEND COMMENT</button>
        </div>
      </div>
    `;
  };

  let html = '';
  for (const [bot, uas] of byBot.entries()) {
    const blockingCount = uas[0]?._bot_pending || 0;
    const blockBadge = blockingCount >= 3
      ? `<span class="ua-badge blocking" title="${escapeHtml(bot)} blokkaa ${blockingCount} muuta kysymystä">🚧 BLOKKAA ${blockingCount}</span>` : '';
    html += `
      <div class="ua-bot-group">
        <div class="ua-bot-header">
          <span class="ua-bot-name">${escapeHtml(bot)}</span>
          ${blockBadge}
          <span class="muted">— ${uas.length} tehtävä${uas.length === 1 ? '' : 'ä'}</span>
        </div>
        ${uas.map(renderUACard).join('')}
      </div>
    `;
  }
  list.innerHTML = html;

  // Palauta säilötyt yleiskommentit
  list.querySelectorAll('textarea[data-comment-action]').forEach(ta => {
    const aid = ta.dataset.commentAction;
    if (commentTyping[aid]) ta.value = commentTyping[aid];
  });

  // Bind step toggles
  list.querySelectorAll('.step input[type="checkbox"]:not(:disabled)').forEach(cb => {
    cb.addEventListener('change', async (e) => {
      const row = e.target.closest('.step-row');
      const aid = row.dataset.actionId;
      const sid = row.dataset.stepId;
      await api('/api/user_actions/step_done', 'POST', { action_id: aid, step_id: sid });
      await renderUserActions();
    });
  });

  // Bind step-comment toggle (💬 button)
  list.querySelectorAll('.step-comment-toggle').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const aid = btn.dataset.toggleAction;
      const sid = btn.dataset.toggleStep;
      const panel = list.querySelector(`.step-comment-panel[data-action-id="${aid}"][data-step-id="${sid}"]`);
      if (panel) {
        panel.classList.toggle('hidden');
        if (!panel.classList.contains('hidden')) {
          const ta = panel.querySelector('textarea');
          if (ta) ta.focus();
        }
      }
    });
  });

  // Bind step-comment submit
  list.querySelectorAll('button[data-step-comment-submit]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const [aid, sid] = btn.dataset.stepCommentSubmit.split('::');
      const ta = list.querySelector(`textarea[data-step-comment-action="${aid}"][data-step-comment-step="${sid}"]`);
      const text = ta ? ta.value.trim() : '';
      if (!text) { alert('Tyhjä kommentti'); return; }
      btn.textContent = 'SENDING...';
      btn.disabled = true;
      const r = await api('/api/user_actions/comment', 'POST', { action_id: aid, step_id: sid, text });
      if (r.ok) {
        btn.textContent = 'SENT — orch vastaa 30-90s';
        if (ta) ta.value = '';
        setTimeout(() => renderUserActions(), 30000);
        setTimeout(() => renderUserActions(), 60000);
        setTimeout(() => renderUserActions(), 90000);
        setTimeout(() => { btn.disabled = false; btn.textContent = 'SEND'; }, 95000);
      } else {
        btn.textContent = 'SEND';
        btn.disabled = false;
        alert('Tallennus epäonnistui');
      }
    });
  });

  // Bind confirm-all-done buttons (per-card)
  list.querySelectorAll('button[data-confirm-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      await api('/api/user_actions/complete', 'POST', { action_id: btn.dataset.confirmAction });
      // Auto-cleanup: piilota heti
      _addDismissed(DISMISSED_ACTIONS, btn.dataset.confirmAction);
      await renderUserActions();
    });
  });

  // Dismiss-napit (✕ OK) — piilota ilman backend-kuittausta
  list.querySelectorAll('button[data-dismiss-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      _addDismissed(DISMISSED_ACTIONS, btn.dataset.dismissAction);
      renderUserActions();
    });
  });

  // Yleiskommentit per-UA
  list.querySelectorAll('button[data-comment-submit]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const aid = btn.dataset.commentSubmit;
      const ta = list.querySelector(`textarea[data-comment-action="${aid}"]`);
      const text = ta ? ta.value.trim() : '';
      if (!text) { alert('Tyhjä kommentti'); return; }
      btn.textContent = 'SENDING...';
      btn.disabled = true;
      const r = await api('/api/user_actions/comment', 'POST', { action_id: aid, text });
      if (r.ok) {
        btn.textContent = 'SENT — orchestrator vastaa 30-90s';
        if (ta) ta.value = '';
        setTimeout(() => renderUserActions(), 30000);
        setTimeout(() => renderUserActions(), 60000);
        setTimeout(() => renderUserActions(), 90000);
        setTimeout(() => { btn.disabled = false; btn.textContent = 'SEND COMMENT'; }, 95000);
      } else {
        btn.textContent = 'SEND COMMENT';
        btn.disabled = false;
        alert('Tallennus epäonnistui');
      }
    });
  });
}

async function renderInbox() {
  const data = await api('/api/inbox');
  const items = data.open || [];
  const list = document.getElementById('inbox-list');
  const badge = document.getElementById('inbox-badge');
  const summary = document.getElementById('inbox-summary');

  badge.textContent = items.length;
  badge.classList.toggle('has-items', items.length > 0);
  summary.textContent = `// ${items.length} avointa, ${data.n_answered || 0} vastattu, ${data.n_escalated || 0} eskaloitu`;

  if (!items.length) {
    list.innerHTML = '<span class="muted">No open questions. Agentit hoitavat homman.</span>';
    return;
  }
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  items.sort((a,b) => (order[a.priority] ?? 4) - (order[b.priority] ?? 4));
  list.innerHTML = items.map(it => `
    <div class="inbox-item priority-${escapeHtml(it.priority || 'medium')}">
      <div class="header">
        <span><strong>[${escapeHtml(it.id)}]</strong> ${escapeHtml(it.bot)} // ${escapeHtml(it.type)}</span>
        <span class="muted">${escapeHtml(it.priority)} // ${fmtTime(it.ts)}</span>
      </div>
      <div class="question">${linkify(it.question)}</div>
      ${it.options && it.options.length ? `<div class="options">Options: ${it.options.map(o => escapeHtml(o)).join(' / ')}</div>` : ''}
      ${it.context ? `<div class="muted" style="font-size:0.75rem;">${linkify(it.context)}</div>` : ''}
      <div class="muted" style="font-size:0.7rem; margin-top:0.4rem;">
        Vastaa CLI:llä: <code>python _shared/scripts/agent_inbox.py answer --id ${escapeHtml(it.id)} --decision "..." --reason "..."</code>
      </div>
    </div>
  `).join('');
}

async function renderQuota() {
  const q = await api('/api/quota');
  const el = document.getElementById('quota');
  if (!q || !q.pressure_pct) {
    el.innerHTML = 'QUOTA: <span class="muted">--</span>';
    return;
  }
  const pct = parseFloat(q.pressure_pct);
  const cls = pct < 30 ? 'green-bright' : pct < 60 ? 'amber' : 'red';
  el.innerHTML = `QUOTA: <span class="${cls}">${pct.toFixed(1)}%</span> <span class="muted">(${q.weight_used}/${q.weight_max})</span>`;
}

// === Submit decision ===
document.getElementById('decision-submit').addEventListener('click', async () => {
  const type = document.getElementById('decision-type').value;
  const target = document.getElementById('decision-target').value;
  const text = document.getElementById('decision-text').value.trim();
  if (!text) { alert('Päätös tarvitsee tekstin'); return; }
  await api('/api/decisions', 'POST', { type, target, text });
  document.getElementById('decision-text').value = '';
  document.getElementById('decision-target').value = '';
  await renderDecisions();
});

async function renderAgencyMetrics() {
  const items = await api('/api/agency_metrics');
  const el = document.getElementById('agency-table');
  if (!el) return;
  if (!items || !items.length) { el.innerHTML = '<div class="muted">ei mittauksia</div>'; return; }
  // Sortaa score:n mukaan
  items.sort((a, b) => (b.score || 0) - (a.score || 0));
  const rows = items.map(m => {
    const score = (m.score || 0).toFixed(1);
    const color = m.score >= 50 ? 'green-bright' : m.score >= 25 ? 'amber' : 'muted';
    return `<tr>
      <td><strong>${escapeHtml(m.bot)}</strong></td>
      <td class="${color}">${score}</td>
      <td>${m.cycles || 0}</td>
      <td>${m.calls_24h || 0}</td>
      <td>${(m.compactness_ratio || 0).toFixed(3)}</td>
      <td>${m.strategies || 0}</td>
      <td>${m.learnings || 0}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table>
    <thead><tr><th>bot</th><th>score</th><th>cycles</th><th>calls/24h</th><th>compactness</th><th>strategies</th><th>learnings</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function renderOrchestratorTodo() {
  const data = await api('/api/orchestrator_todo');
  const el = document.getElementById('orch-todo-list');
  const badge = document.getElementById('orch-todo-count');
  if (!el) return;
  const pending = data.pending || [];
  if (badge) badge.textContent = pending.length;
  if (!pending.length) { el.innerHTML = '<div class="muted">ei pending todoja</div>'; return; }
  // Top-15 näytetään
  const top = pending.slice(0, 15);
  el.innerHTML = top.map(t => {
    const prio = (t.priority || 'medium').toUpperCase();
    const prioCls = prio === 'HIGH' ? 'amber' : (prio === 'LOW' ? 'muted' : 'green-bright');
    const target = t.target_bot || t.target || '';
    const cat = t.category || '';
    const title = t.title || (t.raw || '').slice(0, 100);
    const desc = t.description || t.detail || '';
    return `<div class="orch-todo-item">
      <div class="meta"><span class="${prioCls}">${prio}</span> // ${escapeHtml(target)} // ${escapeHtml(cat)}</div>
      <div class="title">${linkify(title)}</div>
      ${desc ? `<div class="muted" style="font-size:0.75rem;">${linkify(desc.slice(0, 300))}</div>` : ''}
    </div>`;
  }).join('');
  if (pending.length > 15) {
    el.innerHTML += `<div class="muted" style="margin-top:0.5rem;">+ ${pending.length - 15} muuta pending</div>`;
  }
}

// === Refresh loop ===
async function refreshAll() {
  try {
    await Promise.all([
      renderUserActions(), renderBots(), renderPersonas(),
      renderDecisions(), renderDora(),
      renderQuota(), renderInbox(), renderGoals(), renderGeneralInputs(),
      renderAgencyMetrics(), renderOrchestratorTodo(),
    ]);
    document.getElementById('last-refresh').textContent = `last refresh: ${fmtTime(new Date().toISOString())}`;
  } catch (e) { console.error(e); }
}

// Nav active state
document.querySelectorAll('.topbar nav a').forEach(a => {
  a.addEventListener('click', () => {
    document.querySelectorAll('.topbar nav a').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
  });
});

refreshAll();
setInterval(refreshAll, 30000); // 30s refresh
