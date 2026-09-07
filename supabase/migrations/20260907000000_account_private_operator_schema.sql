-- Private per-operator account schema.
-- Every row is owner-only via RLS. anon has no table access.
-- Camera URLs/credentials, ROI, crew photos, and avatars stay in the
-- authenticated user's own rows / storage folder. Never use user_metadata
-- for authorization.

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.set_updated_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text not null default '',
  venue_name text not null default '',
  avatar_path text,
  setup_completed boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.cameras (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  external_id text,
  name text not null,
  zone text not null default '',
  protocol text not null default 'rtsp',
  source_url text not null default '',
  main_source_url text not null default '',
  username text not null default '',
  password text not null default '',
  vendor text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint cameras_protocol_check check (
    protocol in ('webcam', 'rtsp', 'phone', 'onvif', 'tapo', 'webrtc')
  )
);

create table if not exists public.roi_bays (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  camera_id uuid references public.cameras (id) on delete cascade,
  external_id text,
  name text not null,
  bay_type text not null default 'vehicle_bay',
  roi double precision[] not null,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint roi_bays_roi_len check (array_length(roi, 1) = 4),
  constraint roi_bays_type_check check (bay_type in ('vehicle_bay', 'tool_area'))
);

create table if not exists public.crew_identities (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  display_name text not null,
  role text not null default 'technician',
  photo_path text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists cameras_user_external_id_uidx
  on public.cameras (user_id, external_id)
  where external_id is not null;

create unique index if not exists roi_bays_user_external_id_uidx
  on public.roi_bays (user_id, external_id)
  where external_id is not null;

create index if not exists cameras_user_id_idx on public.cameras (user_id);
create index if not exists roi_bays_user_id_idx on public.roi_bays (user_id);
create index if not exists roi_bays_camera_id_idx on public.roi_bays (camera_id);
create index if not exists crew_identities_user_id_idx on public.crew_identities (user_id);

comment on table public.profiles is 'Owner-only operator profile. Visible in the signed-in console, never to other users.';
comment on table public.cameras is 'Owner-only camera protocol, URL, and credentials.';
comment on table public.roi_bays is 'Owner-only ROI / bay geometry.';
comment on table public.crew_identities is 'Owner-only enrolled crew identities.';
comment on column public.cameras.source_url is 'Private camera stream URL. RLS owner-only.';
comment on column public.cameras.password is 'Private camera credential. RLS owner-only.';

alter table public.profiles enable row level security;
alter table public.cameras enable row level security;
alter table public.roi_bays enable row level security;
alter table public.crew_identities enable row level security;

revoke all on table public.profiles from anon, public;
revoke all on table public.cameras from anon, public;
revoke all on table public.roi_bays from anon, public;
revoke all on table public.crew_identities from anon, public;

grant select, insert, update, delete on table public.profiles to authenticated;
grant select, insert, update, delete on table public.cameras to authenticated;
grant select, insert, update, delete on table public.roi_bays to authenticated;
grant select, insert, update, delete on table public.crew_identities to authenticated;

drop policy if exists profiles_select_own on public.profiles;
drop policy if exists profiles_insert_own on public.profiles;
drop policy if exists profiles_update_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));
create policy profiles_insert_own on public.profiles
  for insert to authenticated
  with check (id = (select auth.uid()));
create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

drop policy if exists cameras_select_own on public.cameras;
drop policy if exists cameras_insert_own on public.cameras;
drop policy if exists cameras_update_own on public.cameras;
drop policy if exists cameras_delete_own on public.cameras;
create policy cameras_select_own on public.cameras
  for select to authenticated
  using (user_id = (select auth.uid()));
create policy cameras_insert_own on public.cameras
  for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy cameras_update_own on public.cameras
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
create policy cameras_delete_own on public.cameras
  for delete to authenticated
  using (user_id = (select auth.uid()));

drop policy if exists roi_bays_select_own on public.roi_bays;
drop policy if exists roi_bays_insert_own on public.roi_bays;
drop policy if exists roi_bays_update_own on public.roi_bays;
drop policy if exists roi_bays_delete_own on public.roi_bays;
create policy roi_bays_select_own on public.roi_bays
  for select to authenticated
  using (user_id = (select auth.uid()));
create policy roi_bays_insert_own on public.roi_bays
  for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy roi_bays_update_own on public.roi_bays
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
create policy roi_bays_delete_own on public.roi_bays
  for delete to authenticated
  using (user_id = (select auth.uid()));

drop policy if exists crew_identities_select_own on public.crew_identities;
drop policy if exists crew_identities_insert_own on public.crew_identities;
drop policy if exists crew_identities_update_own on public.crew_identities;
drop policy if exists crew_identities_delete_own on public.crew_identities;
create policy crew_identities_select_own on public.crew_identities
  for select to authenticated
  using (user_id = (select auth.uid()));
create policy crew_identities_insert_own on public.crew_identities
  for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy crew_identities_update_own on public.crew_identities
  for update to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
create policy crew_identities_delete_own on public.crew_identities
  for delete to authenticated
  using (user_id = (select auth.uid()));

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function private.set_updated_at();

drop trigger if exists cameras_set_updated_at on public.cameras;
create trigger cameras_set_updated_at
  before update on public.cameras
  for each row execute function private.set_updated_at();

drop trigger if exists roi_bays_set_updated_at on public.roi_bays;
create trigger roi_bays_set_updated_at
  before update on public.roi_bays
  for each row execute function private.set_updated_at();

drop trigger if exists crew_identities_set_updated_at on public.crew_identities;
create trigger crew_identities_set_updated_at
  before update on public.crew_identities
  for each row execute function private.set_updated_at();

create or replace function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  meta_name text;
  meta_venue text;
begin
  meta_name := coalesce(
    nullif(trim(new.raw_user_meta_data ->> 'display_name'), ''),
    nullif(split_part(coalesce(new.email, ''), '@', 1), ''),
    'Operator'
  );
  meta_venue := coalesce(nullif(trim(new.raw_user_meta_data ->> 'venue_name'), ''), '');
  insert into public.profiles (id, display_name, venue_name)
  values (new.id, meta_name, meta_venue)
  on conflict (id) do nothing;
  return new;
end;
$$;

revoke all on function private.handle_new_user() from public, anon, authenticated;

do $$
begin
  grant usage on schema private to supabase_auth_admin;
  grant execute on function private.handle_new_user() to supabase_auth_admin;
exception
  when undefined_object then
    null;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function private.handle_new_user();

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'account-private',
  'account-private',
  false,
  5242880,
  array['image/jpeg', 'image/png', 'image/webp', 'image/gif']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists account_private_select_own on storage.objects;
drop policy if exists account_private_insert_own on storage.objects;
drop policy if exists account_private_update_own on storage.objects;
drop policy if exists account_private_delete_own on storage.objects;

create policy account_private_select_own
  on storage.objects
  for select
  to authenticated
  using (
    bucket_id = 'account-private'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy account_private_insert_own
  on storage.objects
  for insert
  to authenticated
  with check (
    bucket_id = 'account-private'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy account_private_update_own
  on storage.objects
  for update
  to authenticated
  using (
    bucket_id = 'account-private'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  )
  with check (
    bucket_id = 'account-private'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy account_private_delete_own
  on storage.objects
  for delete
  to authenticated
  using (
    bucket_id = 'account-private'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
