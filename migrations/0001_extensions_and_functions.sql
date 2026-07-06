-- Extensions required by the platform: uuid generation + pgvector for
-- semantic search embeddings (Supabase ships pgvector as a first-class
-- extension).
create extension if not exists "uuid-ossp";
create extension if not exists vector;
create extension if not exists pg_trgm; -- trigram indexes for fuzzy keyword search

-- Shared trigger function: every table with an `updated_at` column uses this
-- so we never rely on application code remembering to bump it.
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;
