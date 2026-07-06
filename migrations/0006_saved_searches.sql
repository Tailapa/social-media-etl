-- Saved Searches: persisted filter presets, natural-language scraping
-- prompts, and frequently asked AI questions (frontend success criteria
-- "Saved Searches"). Kept as one table with a `kind` discriminator rather
-- than three tables since all three are just "a name + a JSON payload the
-- frontend knows how to re-apply" with no relational structure of their own.

create table if not exists saved_searches (
    id uuid primary key default uuid_generate_v4(),
    name text not null,
    kind text not null check (kind in ('filter', 'scrape_prompt', 'ai_question')),
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);
create index if not exists idx_saved_searches_kind on saved_searches(kind) where deleted_at is null;
drop trigger if exists trg_saved_searches_updated_at on saved_searches;
create trigger trg_saved_searches_updated_at before update on saved_searches
    for each row execute function set_updated_at();
