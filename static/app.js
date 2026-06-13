/* ─── State ───────────────────────────────────────────────── */
const state = {
  stories: [],
  role: 'Developer',
  detailLevel: 'short',
  activeTopics: new Set(['Models', 'Agents', 'Infrastructure', 'Research', 'Policy', 'Open Source']),
  hasApiKey: false,
};

/* ─── Init ────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  setTodayDate();
  bindMenuToggle();
  bindRoleButtons();
  bindTopicCheckboxes();
  bindDetailToggle();
  await checkStatus();
  await loadNews();
});

function setTodayDate() {
  const el = document.getElementById('today-date');
  el.textContent = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
  });
}

async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    state.hasApiKey = data.has_api_key;

    if (!data.has_api_key) {
      document.getElementById('api-banner').style.display = 'flex';
    }

    const digestBtn = document.getElementById('digest-btn');
    if (digestBtn) {
      if (!data.digest_configured) {
        digestBtn.title =
          'Add DIGEST_EMAIL_TO, DIGEST_EMAIL_FROM and GMAIL_APP_PASSWORD to .env to enable';
        digestBtn.textContent = '📧 Configure Email First';
        digestBtn.disabled = true;
      } else {
        digestBtn.disabled = false;
        digestBtn.addEventListener('click', sendDigest);
        if (data.next_digest) {
          const t = new Date(data.next_digest)
            .toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
          digestBtn.title = `Next scheduled digest: ${t}`;
        }
      }
    }
  } catch (_) {}
}

/* ─── News Loading ────────────────────────────────────────── */
async function loadNews() {
  try {
    const res = await fetch('/api/news');
    const data = await res.json();
    state.stories = data.stories || [];
    updateSidebarStats(data.last_updated);
    renderStories();
  } catch (e) {
    showToast('Failed to load news — is the server running?', 'error');
    document.getElementById('stories-grid').innerHTML = '';
    document.getElementById('empty-state').style.display = 'block';
  }
}

async function refreshNews() {
  const btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.classList.add('spinning');

  try {
    const res = await fetch('/api/refresh', { method: 'POST' });
    const data = await res.json();
    state.stories = data.stories || [];
    updateSidebarStats(data.last_updated);
    renderStories();
    showToast(`Refreshed — ${data.count} stories loaded`, 'success');
  } catch (e) {
    showToast('Refresh failed', 'error');
  } finally {
    btn.disabled = false;
    btn.classList.remove('spinning');
  }
}

/* ─── Filtering ───────────────────────────────────────────── */
function getFilteredStories() {
  if (state.activeTopics.size === 0) return [];
  if (state.activeTopics.size === 6) return state.stories;

  return state.stories.filter(story => {
    const topics = story.topics || [];
    return topics.length === 0 || topics.some(t => state.activeTopics.has(t));
  });
}

/* ─── Rendering ───────────────────────────────────────────── */
function renderStories() {
  const grid = document.getElementById('stories-grid');
  const emptyState = document.getElementById('empty-state');
  const filtered = getFilteredStories();

  document.getElementById('stat-count').textContent = state.stories.length;
  document.getElementById('stat-filtered').textContent = filtered.length;

  if (filtered.length === 0) {
    grid.innerHTML = '';
    emptyState.style.display = 'block';
    return;
  }

  emptyState.style.display = 'none';
  grid.innerHTML = filtered.map(renderCard).join('');
}

function renderCard(story) {
  const topicTags = (story.topics || []).map(t => {
    const cls = topicClass(t);
    return `<span class="topic-tag ${cls}">${escHtml(t)}</span>`;
  }).join('');

  const catClass = 'cat-' + (story.category || 'industry').toLowerCase().replace(/\s+/g, '-');
  const dateStr = formatDate(story.published);

  return `
    <div class="story-card" id="card-${story.id}">
      <div class="card-meta">
        <span class="source-badge ${catClass}">${escHtml(story.source)}</span>
        ${story.topics && story.topics.length ? `<div class="card-topics">${topicTags}</div>` : ''}
        ${dateStr ? `<span class="story-date">${dateStr}</span>` : ''}
      </div>

      <h3 class="story-title">
        <a href="${escHtml(story.url)}" target="_blank" rel="noopener">${escHtml(story.title)}</a>
      </h3>

      <div class="story-summary" id="summary-${story.id}">
        ${escHtml(story.summary || 'No summary available.')}
      </div>

      <div class="card-actions">
        <button
          class="btn btn-primary"
          data-action="personalize"
          data-id="${story.id}"
          ${!state.hasApiKey ? 'disabled title="Add ANTHROPIC_API_KEY to enable"' : ''}
        >
          ✦ Personalize
        </button>
        <button
          class="btn btn-secondary"
          data-action="angles"
          data-id="${story.id}"
          ${!state.hasApiKey ? 'disabled title="Add ANTHROPIC_API_KEY to enable"' : ''}
        >
          Content Angles
        </button>
        <a href="${escHtml(story.url)}" target="_blank" rel="noopener" class="btn btn-ghost">
          Read ↗
        </a>
      </div>

      <div class="angles-panel" id="angles-${story.id}" style="display:none"></div>
    </div>
  `;
}

/* ─── Event Delegation for Card Buttons ───────────────────── */
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;

  const action = btn.dataset.action;
  const id = btn.dataset.id;

  if (action === 'personalize') await handlePersonalize(id, btn);
  if (action === 'angles') await handleAngles(id, btn);
  if (action === 'copy') handleCopy(btn);
});

/* ─── Personalize ─────────────────────────────────────────── */
async function handlePersonalize(storyId, btn) {
  if (btn.disabled || btn.classList.contains('done')) return;

  const summaryEl = document.getElementById(`summary-${storyId}`);
  btn.disabled = true;
  btn.textContent = '✦ Personalizing…';
  summaryEl.classList.add('loading');

  try {
    const res = await fetch('/api/rewrite', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        story_id: storyId,
        role: state.role,
        detail_level: state.detailLevel,
      }),
    });

    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    summaryEl.textContent = data.rewritten_summary;
    summaryEl.classList.remove('loading');
    summaryEl.classList.add('personalized');
    btn.textContent = `✓ For ${state.role}`;
    btn.classList.add('done');
    btn.disabled = false;
  } catch (e) {
    summaryEl.classList.remove('loading');
    btn.textContent = '✦ Personalize';
    btn.disabled = false;
    showToast('Personalization failed', 'error');
  }
}

/* ─── Content Angles ──────────────────────────────────────── */
async function handleAngles(storyId, btn) {
  const panel = document.getElementById(`angles-${storyId}`);

  if (panel.style.display === 'block') {
    panel.style.display = 'none';
    btn.classList.remove('active');
    btn.textContent = 'Content Angles';
    return;
  }

  btn.classList.add('active');
  btn.textContent = 'Loading…';
  panel.style.display = 'block';

  if (panel.dataset.loaded === 'true') {
    btn.textContent = 'Hide Angles';
    return;
  }

  panel.innerHTML = '<div class="angles-loading">Generating content angles…</div>';

  try {
    const res = await fetch('/api/content-angles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ story_id: storyId }),
    });

    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    panel.dataset.loaded = 'true';
    panel.dataset.linkedin = data.linkedin_hook || '';
    panel.dataset.newsletter = data.newsletter_angle || '';

    const talkingHtml = (data.talking_points || [])
      .map(p => `<li>${escHtml(p)}</li>`)
      .join('');

    panel.innerHTML = `
      <div class="angles-header">Content Angles</div>

      <div class="angle-block">
        <div class="angle-label">💼 LinkedIn Hook</div>
        <div class="angle-text">${escHtml(data.linkedin_hook || '')}</div>
        <div class="angle-copy-row">
          <button class="btn-copy" data-action="copy" data-copy-key="linkedin" data-id="${storyId}">Copy</button>
        </div>
      </div>

      <div class="angle-block">
        <div class="angle-label">📧 Newsletter Angle</div>
        <div class="angle-text">${escHtml(data.newsletter_angle || '')}</div>
        <div class="angle-copy-row">
          <button class="btn-copy" data-action="copy" data-copy-key="newsletter" data-id="${storyId}">Copy</button>
        </div>
      </div>

      <div class="angle-block">
        <div class="angle-label">🎯 Talking Points</div>
        <ul class="talking-points">${talkingHtml}</ul>
      </div>
    `;

    btn.textContent = 'Hide Angles';
  } catch (e) {
    panel.innerHTML = '<p style="font-size:13px;color:#ef4444;padding:8px 0">Failed to generate angles.</p>';
    btn.classList.remove('active');
    btn.textContent = 'Content Angles';
    showToast('Failed to generate content angles', 'error');
  }
}

/* ─── Copy to Clipboard ───────────────────────────────────── */
function handleCopy(btn) {
  const key = btn.dataset.copyKey;
  const id = btn.dataset.id;
  const panel = document.getElementById(`angles-${id}`);

  let text = '';
  if (key === 'linkedin') text = panel.dataset.linkedin || '';
  if (key === 'newsletter') text = panel.dataset.newsletter || '';

  if (!text) return;

  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = 'Copy';
      btn.classList.remove('copied');
    }, 1800);
  });
}

/* ─── Mobile Menu Toggle ──────────────────────────────────── */
function bindMenuToggle() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  const toggle = document.getElementById('menu-toggle');

  const closeMenu = () => {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
  };

  toggle.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    overlay.classList.toggle('open');
  });

  overlay.addEventListener('click', closeMenu);

  // Close menu after selecting a role on mobile
  document.getElementById('role-list').addEventListener('click', (e) => {
    if (e.target.closest('.role-btn') && window.innerWidth <= 900) closeMenu();
  });
}

/* ─── Role Binding ────────────────────────────────────────── */
function bindRoleButtons() {
  document.getElementById('role-list').addEventListener('click', (e) => {
    const btn = e.target.closest('.role-btn');
    if (!btn) return;

    document.querySelectorAll('.role-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.role = btn.dataset.role;

    // Reset all personalized summaries so they can be re-personalized
    document.querySelectorAll('.story-summary.personalized').forEach(el => {
      el.classList.remove('personalized');
    });
    document.querySelectorAll('.btn-primary.done').forEach(btn => {
      btn.textContent = '✦ Personalize';
      btn.classList.remove('done');
    });
  });
}

/* ─── Topic Binding ───────────────────────────────────────── */
function bindTopicCheckboxes() {
  document.getElementById('topic-list').addEventListener('change', (e) => {
    const checkbox = e.target;
    if (checkbox.type !== 'checkbox') return;

    const topic = checkbox.value;
    if (checkbox.checked) {
      state.activeTopics.add(topic);
    } else {
      state.activeTopics.delete(topic);
    }
    renderStories();
  });
}

/* ─── Detail Level Toggle ─────────────────────────────────── */
function bindDetailToggle() {
  document.getElementById('detail-toggle').addEventListener('click', (e) => {
    const btn = e.target.closest('.toggle-btn');
    if (!btn) return;

    document.querySelectorAll('#detail-toggle .toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.detailLevel = btn.dataset.mode;

    // Reset personalized summaries since detail level changed
    document.querySelectorAll('.story-summary.personalized').forEach(el => {
      el.classList.remove('personalized');
    });
    document.querySelectorAll('.btn-primary.done').forEach(b => {
      b.textContent = '✦ Personalize';
      b.classList.remove('done');
    });
  });
}

/* ─── Sidebar Stats ───────────────────────────────────────── */
function updateSidebarStats(lastUpdated) {
  if (lastUpdated) {
    const d = new Date(lastUpdated);
    const timeStr = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    document.getElementById('last-updated').textContent = `Updated ${timeStr}`;
  }
}

/* ─── Helpers ─────────────────────────────────────────────── */
function topicClass(topic) {
  const map = {
    'Models': 'models',
    'Agents': 'agents',
    'Infrastructure': 'infrastructure',
    'Research': 'research',
    'Policy': 'policy',
    'Open Source': 'open-source',
  };
  return map[topic] || 'models';
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diffMs = now - d;
    const diffH = diffMs / 3600000;
    const diffD = diffMs / 86400000;

    if (diffH < 1) return 'just now';
    if (diffH < 24) return `${Math.floor(diffH)}h ago`;
    if (diffD < 7) return `${Math.floor(diffD)}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch (_) {
    return '';
  }
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ─── Send Digest ─────────────────────────────────────────── */
async function sendDigest() {
  const btn = document.getElementById('digest-btn');
  btn.disabled = true;
  btn.textContent = '📧 Sending…';

  try {
    const res = await fetch('/api/send-digest', { method: 'POST' });
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail || 'Send failed');

    btn.textContent = '✓ Digest Sent';
    btn.classList.add('sent');
    showToast(`Digest sent — ${data.stories_sent} stories to ${data.to}`, 'success');

    setTimeout(() => {
      btn.textContent = '📧 Send Digest Now';
      btn.classList.remove('sent');
      btn.disabled = false;
    }, 4000);
  } catch (e) {
    btn.textContent = '📧 Send Digest Now';
    btn.disabled = false;
    showToast(e.message, 'error');
  }
}

/* ─── Toast Notifications ─────────────────────────────────── */
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('hiding');
    toast.addEventListener('animationend', () => toast.remove());
  }, 3500);
}
