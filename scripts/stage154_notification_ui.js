/* Stage154: ROI Specialists publication notifications; local clear/read state only. */
(() => {
  'use strict';
  const URL = './data/roi-notifications.json';
  const CLEAR_KEY = 'rhp154-notifications-cleared-through';
  const READ_KEY = 'rhp154-notifications-read-through';
  let events = [];
  let panel = null;
  let bell = null;
  let badge = null;
  let list = null;
  let status = null;
  const timeStamp = v => Date.parse(v || '') || 0;
  const stored = key => { try { return timeStamp(localStorage.getItem(key)); } catch (_) { return 0; } };
  const save = (key,value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const visible = () => events.filter(e => timeStamp(e.created_at) > stored(CLEAR_KEY));
  const dateFmt = v => { try { return new Date(v).toLocaleString('en-IE',{timeZone:'Europe/Dublin',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hour12:false}); } catch (_) { return String(v||''); } };
  const fmtPick = (horse) => horse ? `<strong>${escape(horse)}</strong>` : '<em>No selection</em>';
  const route = c => [String(c.meeting||''),String(c.time||'')].filter(Boolean).join(' · ');
  function updateBadge() {
    const unread = visible().filter(e => timeStamp(e.created_at) > stored(READ_KEY)).length;
    if (badge) { badge.textContent = unread > 99 ? '99+' : String(unread); badge.hidden = unread === 0; }
    if (bell) bell.setAttribute('aria-label', unread ? `Notifications, ${unread} unread` : 'Notifications');
  }
  function render() {
    if (!list) return;
    const shown = visible();
    updateBadge();
    status.textContent = shown.length ? `${shown.length} updates on this device` : 'No notifications to display';
    if (!shown.length) {
      list.innerHTML = '<p class="rhp154-empty">There are no notifications here. New selection reviews will appear automatically when published.</p>';
      return;
    }
    list.innerHTML = shown.map(event => {
      const changes = Array.isArray(event.changes) ? event.changes : [];
      let details = '';
      if (changes.length) {
        details = `<div class="rhp154-change-list">${changes.map(change => `
          <div class="rhp154-change">
            <strong>${escape(route(change))}</strong>
            ${change.race_type ? `<small>${escape(change.race_type)}</small>` : ''}
            <p>Rank 1: ${fmtPick(change.rank1_before)} <span aria-label="changed to">→</span> ${fmtPick(change.rank1_after)}</p>
            ${(change.rank2_before || change.rank2_after) && change.rank2_before !== change.rank2_after ? `<p>Rank 2: ${fmtPick(change.rank2_before)} → ${fmtPick(change.rank2_after)}</p>` : ''}
            ${change.model ? `<small>Specialist: ${escape(change.model.replaceAll('_', ' '))}</small>` : ''}
          </div>`).join('')}</div>`;
      } else if (event.kind === 'review') {
        details = '<p class="rhp154-zero">No horses moved between the top selections in this review.</p>';
      } else {
        details = `<p class="rhp154-zero">${Number(event.selection_count)||0} races assessed. Visit Hot Pots to see the selections.</p>`;
      }
      return `<article class="rhp154-event"><h3>${escape(event.title||'Hot Pots update')}</h3>
        <time>${escape(dateFmt(event.created_at))} · Racing ${escape(event.date||'')}</time>
        <p>${escape(event.summary||'')}</p>
        ${details}</article>`;
    }).join('');
  }
  async function refresh() {
    try {
      const response = await fetch(`${URL}?t=${Date.now()}`, {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const json = await response.json();
      events = Array.isArray(json.events) ? json.events : [];
      render();
    } catch (_) {
      if (!events.length && status) status.textContent = 'Notification history is not published yet. Check again after the first successful selection update.';
    }
  }
  function open() {
    if (!panel) return;
    panel.hidden = false;
    document.body.classList.add('rhp154-open');
    save(READ_KEY, new Date().toISOString());
    render();
    refresh();
    const close = panel.querySelector('[data-rhp154-close]');
    if (close) close.focus();
  }
  function close() {
    if (!panel) return;
    panel.hidden = true;
    document.body.classList.remove('rhp154-open');
    if (bell) bell.focus();
  }
  function initialize() {
    if (document.getElementById('rhp154-bell')) return;
    const toolbar = document.querySelector('.rhp-main-toolbar');
    if (!toolbar) return;
    bell = document.createElement('button');
    bell.id = 'rhp154-bell'; bell.type = 'button';
    bell.className = 'rhp-header-settings rhp154-bell';
    bell.setAttribute('aria-label','Notifications');
    bell.innerHTML = '<span aria-hidden="true">🔔</span><span class="rhp154-bell-word">Updates</span><span id="rhp154-badge" class="rhp154-badge" hidden></span>';
    badge = bell.querySelector('#rhp154-badge');
    const settings = toolbar.querySelector('#rhp-advanced-shortcut');
    if (settings) settings.before(bell); else toolbar.appendChild(bell);
    panel = document.createElement('div');
    panel.id = 'rhp154-notifications'; panel.hidden = true;
    panel.innerHTML = `<div class="rhp154-backdrop" data-rhp154-close></div>
      <section class="rhp154-sheet" role="dialog" aria-modal="true" aria-labelledby="rhp154-title">
        <div class="rhp154-head"><h2 id="rhp154-title">🔔 Hot Pots updates</h2>
          <button type="button" class="action small outline" data-rhp154-close aria-label="Close notifications">✕ Close</button></div>
        <p id="rhp154-status" class="hint" aria-live="polite">Checking updates…</p>
        <div class="rhp154-actions"><button type="button" id="rhp154-refresh" class="action small outline">↻ Refresh</button>
          <button type="button" id="rhp154-clear" class="action small outline">🗑 Clear notifications</button></div>
        <div id="rhp154-list" aria-live="polite"></div>
        <p class="hint">Clear notifications removes them from this device's list only. It does not delete the selection history or disable future push alerts.</p>
      </section>`;
    document.body.appendChild(panel);
    list = panel.querySelector('#rhp154-list');
    status = panel.querySelector('#rhp154-status');
    bell.addEventListener('click', open);
    panel.querySelectorAll('[data-rhp154-close]').forEach(el => el.addEventListener('click', close));
    panel.querySelector('#rhp154-refresh').addEventListener('click', refresh);
    panel.querySelector('#rhp154-clear').addEventListener('click', () => {
      // This is deliberately local to the device. No server-side archive or user subscription is changed.
      save(CLEAR_KEY, new Date().toISOString());
      save(READ_KEY, new Date().toISOString());
      render();
    });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !panel.hidden) close(); });
    refresh();
    // Poll gently, independent of odds auto-refresh. This works in the Home Screen PWA.
    setInterval(refresh, 60_000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    try {
      const u = new URL(window.location.href);
      if (u.searchParams.get('updates') === '1') {
        setTimeout(open, 450);
        u.searchParams.delete('updates');
        if (window.history?.replaceState) window.history.replaceState(null, '', u.toString());
      }
    } catch (_) { }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
