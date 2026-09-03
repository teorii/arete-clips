-- Canonical Postgres schema (Supabase or any Postgres 14+).
--
-- The SQLAlchemy models in server/models.py mirror this and are what
-- create_all() builds on SQLite for zero-setup local runs. Run this file when
-- you point DATABASE_URL at real Postgres, so you get native enums, partial
-- indexes and jsonb rather than the SQLite-compatible approximations.

create extension if not exists pgcrypto;

do $$ begin
  create type clip_status as enum ('pending_upload', 'uploaded', 'ready', 'failed');
  create type visibility as enum ('private', 'unlisted', 'public');
  create type trigger_type as enum ('hotkey', 'auto', 'manual');
  create type rendition_status as enum ('absent', 'queued', 'ready', 'failed');
  create type storage_tier as enum ('hot', 'infrequent', 'archive');
exception when duplicate_object then null; end $$;

create table if not exists users (
  id            uuid primary key,
  handle        text unique not null,
  tier          text not null default 'free',
  quota_bytes   bigint not null default 5368709120,  -- 5 GiB
  created_at    timestamptz not null default now()
);

create table if not exists games (
  id            serial primary key,
  name          text not null,
  process_name  text unique not null   -- what the client matches against
);

create table if not exists clips (
  -- UUIDv7, generated client-side by the API. Time-ordered, so inserts land at
  -- the right edge of the index instead of scattering across it.
  id            uuid primary key,
  -- Random base62. Nothing enumerable belongs in a public share URL, which is
  -- why this is not just the id.
  public_slug   text unique not null,
  owner_id      uuid not null references users(id),
  game_id       int references games(id),          -- null = unrecognized game

  title         text,
  status        clip_status not null default 'pending_upload',
  visibility    visibility not null default 'unlisted',
  trigger_type  trigger_type not null default 'hotkey',

  duration_ms   int not null check (duration_ms > 0),
  source_bytes  bigint,
  storage_key   text,
  -- sha256 of the source file. The dedup key for the same viral clip being
  -- re-uploaded by thousands of people.
  content_hash  char(64),

  -- Client clock. May lag by days if the machine was offline, and may simply
  -- be wrong. Clamped against uploaded_at before it is trusted.
  captured_at   timestamptz not null,
  -- Server clock. The trustworthy one.
  uploaded_at   timestamptz,
  created_at    timestamptz not null default now(),

  -- encoder, gpu, capture api, resolution, measured fps impact, dropped frames
  capture_meta  jsonb,

  storage_tier  storage_tier not null default 'hot',
  -- Denormalized from the event stream. A row per view in this table would
  -- turn a viral clip into a write storm.
  view_count    bigint not null default 0,
  deleted_at    timestamptz
);

-- The dominant authenticated read: one user's library, newest first, keyset
-- paginated. Partial index because soft-deleted rows are never listed.
create index if not exists ix_clips_owner_captured
  on clips (owner_id, captured_at desc, id desc)
  where deleted_at is null;

-- Dedup lookup on re-upload.
create index if not exists ix_clips_content_hash
  on clips (content_hash) where content_hash is not null;

create table if not exists clip_renditions (
  clip_id       uuid not null references clips(id) on delete cascade,
  label         text not null,                     -- source | 720p | thumb
  status        rendition_status not null default 'absent',
  -- The key, not a URL. Domains and signing schemes change; keys do not.
  storage_key   text,
  bytes         bigint,
  width         int,
  height        int,
  created_at    timestamptz not null default now(),
  primary key (clip_id, label)
);

-- Sweep clips a client abandoned between the hotkey press and the first byte.
create index if not exists ix_clips_stranded
  on clips (created_at)
  where status = 'pending_upload';


-- ---------------------------------------------------------------------------
-- Partitioning: deliberately NOT enabled here, and it is worth knowing why.
--
-- Monthly range partitioning on captured_at makes retention a DROP PARTITION
-- instead of a mass DELETE, which is the difference between an instant
-- metadata operation and hours of vacuum pressure.
--
-- The catch: Postgres requires every unique constraint on a partitioned table
-- to include all partition key columns. So `public_slug unique` would have to
-- become `unique (public_slug, captured_at)`, and resolving a share URL would
-- need the capture date the viewer does not have. The options are a separate
-- unpartitioned slug -> (id, captured_at) lookup table, or encoding the month
-- into the slug itself.
--
-- At the write rate this service actually has, roughly 14 inserts/second, one
-- well-indexed unpartitioned table is fine. Revisit when index maintenance
-- starts showing up in write latency, not before.
-- ---------------------------------------------------------------------------
