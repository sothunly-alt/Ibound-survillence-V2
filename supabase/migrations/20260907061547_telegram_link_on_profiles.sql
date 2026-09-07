-- Store the operator's Telegram DM link after they /start the bot.
-- chat_id is private to the profile owner (existing RLS).

alter table public.profiles
  add column if not exists telegram_chat_id text,
  add column if not exists telegram_user_id text,
  add column if not exists telegram_username text,
  add column if not exists telegram_linked_at timestamptz;

create unique index if not exists profiles_telegram_chat_id_uidx
  on public.profiles (telegram_chat_id)
  where telegram_chat_id is not null;

comment on column public.profiles.telegram_chat_id is
  'Telegram chat id captured when the signed-in operator /starts the bot.';
comment on column public.profiles.telegram_user_id is
  'Telegram user id for the linked operator.';
comment on column public.profiles.telegram_username is
  'Optional @username at link time.';
comment on column public.profiles.telegram_linked_at is
  'When the operator last linked Telegram via /start.';
