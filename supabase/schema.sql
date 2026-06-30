-- Brickforge schema (v0.1) — run in Supabase SQL editor.
-- Designed to grow into marketplace + fulfillment in later phases.

-- ============================================================
-- Identity (extends Supabase auth.users)
-- ============================================================
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  handle text unique,
  display_name text,
  is_designer boolean default false,            -- gate for marketplace publishing
  created_at timestamptz default now()
);

-- ============================================================
-- LDraw parts catalog (seeded by worker/ingest_ldraw.py)
-- ============================================================
create table if not exists public.parts (
  ldraw_id text primary key,                    -- e.g. "3001" for 2x4 brick
  name text not null,
  category text,
  bricklink_id text,                            -- mapping for pricing
  rebrickable_id text,
  width_studs int,
  length_studs int,
  height_plates int,
  is_common boolean default false,              -- top 2000 SKUs we'd stock for fulfillment
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);
create index if not exists parts_category_idx on public.parts(category);
create index if not exists parts_common_idx on public.parts(is_common);

create table if not exists public.colors (
  ldraw_code int primary key,                   -- LDraw color code
  name text not null,
  hex text not null,                            -- "#FF0000"
  is_transparent boolean default false,
  bricklink_id text,
  rebrickable_id text
);

-- ============================================================
-- Designs — what users prompt for and we generate
-- ============================================================
create type design_status as enum ('queued','running','succeeded','failed','flagged');

create table if not exists public.designs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.profiles(id) on delete set null,
  prompt text not null,
  status design_status not null default 'queued',
  -- Generation params
  grid_size int default 20,                     -- voxel grid; chunked stitching for >20
  chunked boolean default false,
  -- Outputs (Supabase Storage paths)
  ldr_path text,                                -- canonical .ldr
  preview_path text,                            -- .png render
  step_video_path text,                         -- optional animated build
  -- Aggregate stats
  total_bricks int,
  total_unique_parts int,
  estimated_price_cents int,                    -- USD cents, from BrickLink avg
  -- Metadata
  is_public boolean default false,              -- marketplace listing
  list_price_cents int,                         -- if designer is selling
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists designs_user_idx on public.designs(user_id);
create index if not exists designs_status_idx on public.designs(status);
create index if not exists designs_public_idx on public.designs(is_public) where is_public;

-- Generation attempts — for debugging + cost tracking + retry UX
create table if not exists public.generations (
  id uuid primary key default gen_random_uuid(),
  design_id uuid references public.designs(id) on delete cascade,
  attempt int not null,
  worker_run_id text,                           -- Modal call id
  gpu_seconds numeric,
  cost_cents numeric,
  brick_rejections int,                         -- LegoGPT stability rejections
  regenerations int,
  error text,
  started_at timestamptz default now(),
  ended_at timestamptz
);
create index if not exists generations_design_idx on public.generations(design_id);

-- Bricks within a design — for parts list, instructions, marketplace fulfillment matching
create table if not exists public.design_bricks (
  id bigserial primary key,
  design_id uuid references public.designs(id) on delete cascade,
  ldraw_id text references public.parts(ldraw_id),
  color_code int references public.colors(ldraw_code),
  pos_x int,
  pos_y int,
  pos_z int,
  rotation_deg int default 0,
  step_index int                                -- which instruction step adds this brick
);
create index if not exists design_bricks_design_idx on public.design_bricks(design_id);
create index if not exists design_bricks_step_idx on public.design_bricks(design_id, step_index);

-- ============================================================
-- Marketplace (Phase 4+) — table exists from day 1 so we never need a migration nightmare
-- ============================================================
create table if not exists public.listings (
  id uuid primary key default gen_random_uuid(),
  design_id uuid references public.designs(id) on delete cascade unique,
  seller_id uuid references public.profiles(id),
  title text not null,
  description text,
  price_cents int not null,
  fulfillable boolean default false,            -- true if all parts in our stock catalog
  published_at timestamptz,
  archived_at timestamptz
);

create table if not exists public.purchases (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid references public.listings(id),
  buyer_id uuid references public.profiles(id),
  amount_cents int not null,
  fee_cents int not null,                       -- our cut
  stripe_payment_intent text,
  fulfillment_requested boolean default false,
  shipped_at timestamptz,
  created_at timestamptz default now()
);

-- ============================================================
-- RLS — enable; specific policies added as routes are built.
-- ============================================================
alter table public.profiles enable row level security;
alter table public.designs enable row level security;
alter table public.generations enable row level security;
alter table public.design_bricks enable row level security;
alter table public.listings enable row level security;
alter table public.purchases enable row level security;
