-- File metadata table used by the Flask file manager (SupabaseStorage backend)
create table if not exists public.files (
  id            text primary key,
  name          text not null,
  size          bigint not null default 0,
  content_type  text not null default 'application/octet-stream',
  storage_path  text not null,
  created_at    timestamptz not null default now()
);

create index if not exists files_created_at_idx on public.files (created_at desc);
create index if not exists files_name_idx on public.files (lower(name));

-- The app talks to the table with the service-role key, so lock it down for anon users.
alter table public.files enable row level security;

-- Private storage bucket holding the file contents.
insert into storage.buckets (id, name, public)
values ('files', 'files', false)
on conflict (id) do nothing;
