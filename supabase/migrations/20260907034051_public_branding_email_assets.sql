-- Public brand assets for transactional email (logo only).
-- Readable by anyone with the object URL. Writes stay service-role only.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'branding',
  'branding',
  true,
  1048576,
  array['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists branding_public_read on storage.objects;

create policy branding_public_read
  on storage.objects
  for select
  to public
  using (bucket_id = 'branding');
