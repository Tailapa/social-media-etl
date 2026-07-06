-- Tables created via a direct Postgres connection (as this project's
-- migrations are, through `scripts/run_migrations.py` / `SUPABASE_DB_URL`)
-- are NOT automatically exposed to PostgREST the way Supabase's own SQL
-- editor does it: `anon`/`authenticated`/`service_role` need explicit grants
-- on every table (and on tables created by *future* migrations) or every
-- PostgREST request gets `permission denied for table ...` (42501).
grant usage on schema public to anon, authenticated, service_role;

grant all privileges on all tables in schema public to service_role;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant select on all tables in schema public to anon;

grant all privileges on all sequences in schema public to service_role;
grant usage on all sequences in schema public to authenticated, anon;

grant execute on all functions in schema public to service_role, authenticated, anon;

-- Apply the same grants automatically to tables/sequences/functions added
-- by any future migration, so this only ever needs to be run once.
alter default privileges in schema public
    grant all privileges on tables to service_role;
alter default privileges in schema public
    grant select, insert, update, delete on tables to authenticated;
alter default privileges in schema public
    grant select on tables to anon;
alter default privileges in schema public
    grant all privileges on sequences to service_role;
alter default privileges in schema public
    grant usage on sequences to authenticated, anon;
alter default privileges in schema public
    grant execute on functions to service_role, authenticated, anon;
