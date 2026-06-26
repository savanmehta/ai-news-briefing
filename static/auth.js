/* ─── Supabase Auth ───────────────────────────────────────────────── */
const auth = {
  client: null,
  session: null,
};

async function initAuth() {
  try {
    const { supabase_url, supabase_anon_key } = window.APP_CONFIG;
    auth.client = window.supabase.createClient(supabase_url, supabase_anon_key);
  } catch (e) {
    console.error('Failed to init Supabase client', e);
    return;
  }

  const { data } = await auth.client.auth.getSession();
  auth.session = data.session;
  renderAuthUI();

  auth.client.auth.onAuthStateChange((_event, session) => {
    auth.session = session;
    renderAuthUI();
    if (typeof onAuthChanged === 'function') onAuthChanged();
  });

  document.getElementById('login-btn').addEventListener('click', () => {
    if (auth.session) {
      auth.client.auth.signOut();
    } else {
      auth.client.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: window.location.origin },
      });
    }
  });
}

function renderAuthUI() {
  const btn = document.getElementById('login-btn');
  if (!btn) return;

  if (auth.session) {
    const user = auth.session.user;
    const name = user.user_metadata?.full_name || user.email;
    btn.textContent = `Sign out (${name})`;
  } else {
    btn.textContent = 'Sign in with Google';
  }
}

function getAccessToken() {
  return auth.session?.access_token || null;
}

function isLoggedIn() {
  return !!auth.session;
}

/* ─── Authenticated API helpers ──────────────────────────────────── */
async function authFetch(url, options = {}) {
  const token = getAccessToken();
  if (!token) throw new Error('Not signed in');

  const headers = { ...(options.headers || {}), Authorization: `Bearer ${token}` };
  return fetch(url, { ...options, headers });
}

async function toggleFavorite(articleId, isFavorited) {
  const method = isFavorited ? 'DELETE' : 'POST';
  const res = await authFetch(`/api/favorites/${articleId}`, { method });
  if (!res.ok) throw new Error('Favorite toggle failed');
  return res.json();
}

async function markRead(articleId) {
  const res = await authFetch(`/api/read/${articleId}`, { method: 'POST' });
  if (!res.ok) throw new Error('Mark read failed');
  return res.json();
}

document.addEventListener('DOMContentLoaded', initAuth);
