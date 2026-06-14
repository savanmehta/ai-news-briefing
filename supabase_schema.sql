-- Run this in the Supabase SQL Editor

create table if not exists articles (
  id text primary key,
  title text not null,
  url text not null,
  source text,
  author text,
  category text,
  summary text,
  published timestamptz,
  topics text[] default '{}',
  fetched_at timestamptz default now()
);

create index if not exists idx_articles_published on articles (published desc);
create index if not exists idx_articles_fetched_at on articles (fetched_at desc);

-- Allow authenticated/anon users to read articles (read-only, no per-row restrictions needed)
alter table articles disable row level security;
grant select on articles to anon, authenticated;

-- If the table already exists from before, run this to add the new column:
alter table articles add column if not exists author text;

-- ── Phase 2: user accounts ──────────────────────────────────────────────────

create table if not exists profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  role text default 'Developer',
  topics text[] default '{"Models","Agents","Infrastructure","Research","Policy","Open Source"}',
  daily_digest boolean default true,
  weekly_digest boolean default false,
  created_at timestamptz default now()
);

create table if not exists favorites (
  user_id uuid references auth.users(id) on delete cascade,
  article_id text references articles(id) on delete cascade,
  saved_at timestamptz default now(),
  primary key (user_id, article_id)
);

create table if not exists read_articles (
  user_id uuid references auth.users(id) on delete cascade,
  article_id text references articles(id) on delete cascade,
  read_at timestamptz default now(),
  primary key (user_id, article_id)
);

-- Row Level Security: users can only see/edit their own rows
alter table profiles enable row level security;
alter table favorites enable row level security;
alter table read_articles enable row level security;

create policy "Users manage own profile" on profiles
  for all using (auth.uid() = user_id);

create policy "Users manage own favorites" on favorites
  for all using (auth.uid() = user_id);

create policy "Users manage own read articles" on read_articles
  for all using (auth.uid() = user_id);

-- Profile rows are created lazily by the backend (service role) on first
-- authenticated request — avoids fragile auth.users triggers that can
-- block sign-up entirely if they error.
drop trigger if exists on_auth_user_created on auth.users;
drop function if exists public.handle_new_user();
